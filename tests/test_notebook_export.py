from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile

import pytest

from openai4s.server.notebook_export import NotebookExportService


class _Store:
    def list_branch_messages(self, root_frame_id, *, branch_id=None, limit=None):
        # A session with cells and no conversation. Present rather than absent
        # because the export's port requires it: a store that cannot answer
        # this cannot be exported, and discovering that at runtime behind a
        # `getattr` fallback is how the Inputs section came to render nothing
        # in production while its tests were green.
        assert limit is None, "the export took the store's paging default"
        return []

    def list_cells(self, root_frame_id):
        assert root_frame_id == "root-1"
        return [
            {
                "producing_cell_id": "cell-python",
                "cell_index": 1,
                "state_revision": 11,
                "generation_id": "generation-python-1",
                "kernel_id": "python:gen-1",
                "language": "python",
                "status": "ok",
                "origin": "agent",
                "code": "value = 21 * 2\nprint(value)\n",
                "stdout": "42\n",
                "stderr": "",
                "error": None,
                "figures": ["plot.png"],
                "files_read": ["input.csv"],
                "files_written": ["plot.png"],
                "created_at": 1000,
            },
            {
                "producing_cell_id": "cell-r",
                "cell_index": 2,
                "state_revision": 12,
                "generation_id": "generation-r-1",
                "kernel_id": "r:gen-1",
                "language": "r",
                "status": "error",
                "origin": "user",
                "code": "stop('boom')\n",
                "stdout": "",
                "stderr": "warning\n",
                "error": "Error: boom\ntrace line\n",
                "figures": [],
                "files_read": [],
                "files_written": [],
                "created_at": 2000,
            },
        ]


def test_python_and_r_exports_are_separate_read_only_notebooks():
    service = NotebookExportService(_Store())
    python = service.notebook("root-1", "python")
    r = service.notebook("root-1", "r")

    assert python["nbformat"] == 4
    assert python["metadata"]["kernelspec"]["name"] == "python3"
    assert r["metadata"]["kernelspec"]["name"] == "ir"
    assert len(python["cells"]) == len(r["cells"]) == 1
    py_cell = python["cells"][0]
    assert py_cell["id"] == "cell-python"
    assert py_cell["metadata"]["openai4s"]["history_is_read_only"] is True
    assert py_cell["metadata"]["openai4s"]["state_revision"] == 11
    assert py_cell["metadata"]["openai4s"]["generation_id"] == "generation-python-1"
    assert r["cells"][0]["metadata"]["openai4s"]["state_revision"] == 12
    assert r["cells"][0]["metadata"]["openai4s"]["generation_id"] == "generation-r-1"
    assert py_cell["outputs"][0] == {
        "name": "stdout",
        "output_type": "stream",
        "text": ["42\n"],
    }
    assert "plot.png" in py_cell["outputs"][1]["text"][0]
    r_outputs = r["cells"][0]["outputs"]
    assert [output["output_type"] for output in r_outputs] == ["stream", "error"]
    assert r_outputs[1]["ename"] == "OpenAI4SCellError"
    assert r_outputs[1]["traceback"] == ["Error: boom\n", "trace line\n"]


def test_export_bundle_is_deterministic_and_manifest_checksums_match():
    service = NotebookExportService(_Store())
    first = service.bundle("root-1")
    second = service.bundle("root-1")
    assert first == second

    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        names = sorted(archive.namelist())
        assert names == [
            "manifest.json",
            "root-1.python.ipynb",
            "root-1.r.ipynb",
        ]
        manifest = json.loads(archive.read("manifest.json"))
        for item in manifest["files"]:
            data = archive.read(item["name"])
            assert item["size"] == len(data)
            assert item["sha256"] == hashlib.sha256(data).hexdigest()


def test_unknown_notebook_language_is_rejected():
    with pytest.raises(ValueError, match="python or r"):
        NotebookExportService(_Store()).notebook("root-1", "julia")


# -- the reading form ---------------------------------------------------------


def test_markdown_keeps_both_languages_in_execution_order():
    """The bundle already splits them; splitting is what this must not do.

    A session's record is the interleaving -- which R cell answered which
    Python cell -- and two files cannot carry it. The heading of every cell
    names its index, language and state revision so a reader can cite one.
    """
    service = NotebookExportService(_Store())

    text = service.markdown("root-1").decode("utf-8")

    assert text.index("value = 21 * 2") < text.index("stop('boom')")
    assert "## Cell 1 — python (state revision 11)" in text
    assert "## Cell 2 — r (state revision 12)" in text
    assert "```python" in text and "```r" in text
    assert "42" in text


def test_markdown_keeps_a_failed_cell_and_says_it_failed():
    """Dropping it would make the document describe a run that went smoothly."""
    service = NotebookExportService(_Store())

    text = service.markdown("root-1").decode("utf-8")

    assert "Error:" in text
    assert "Error: boom" in text
    assert "Stderr:" in text and "warning" in text
    assert "Artifacts: `plot.png`" in text


def test_the_export_descriptor_names_markdown_as_markdown():
    """A `.md` served as `application/zip` is a download nothing will open."""
    service = NotebookExportService(_Store())

    exported = service.export("root-1", language="markdown")

    assert exported["filename"].endswith(".md")
    assert exported["content_type"].startswith("text/markdown")
    assert exported["immutable"] is True
    assert exported["data"] == service.markdown("root-1")


# --------------------------------------------------------------------------
# the Inputs section, driven through the REAL Store
# --------------------------------------------------------------------------
#
# The first version of these tests used a hand-written stub exposing
# `artifact_refs` at the top level of a message row. The product does not
# produce that shape: `gateway.update_message_metadata` writes the refs into
# `metadata`, and `list_messages` hands `metadata` back as a JSON *string*. So
# the stub asserted a shape this repository never emits, the export rendered
# nothing at all in production, and the tests were green. A stub's shape is one
# the test author chose; only the real Store's is evidence.


def _real_store(tmp_path):
    from openai4s.config import Config
    from openai4s.store import get_store

    config = Config(data_dir=tmp_path / "data")
    config.ensure_dirs()
    return config, get_store(config.db_path)


def _turn_with_refs(store, root, text, refs):
    """A user message carrying refs exactly as the gateway writes them."""
    message = store.add_message(root_frame_id=root, role="user", content=text)
    message_id = message["message_id"] if isinstance(message, dict) else message
    store.update_message_metadata(message_id, {"artifact_refs": list(refs)})
    return message_id


def _ref(name, version, **extra):
    return {"display_name": name, "version_id": version, **extra}


def test_markdown_names_the_versions_the_real_store_recorded(tmp_path):
    """The production row shape, not a shape this test invented."""
    _config, store = _real_store(tmp_path)
    root = store.new_frame(kind="turn", project_id="p")
    _turn_with_refs(
        store,
        root,
        "compare @cohort.csv#v-aaa111222333 against last week",
        [_ref("cohort.csv", "v-aaa111222333", sha256="f" * 64)],
    )
    _turn_with_refs(
        store, root, "now add @notes.md", [_ref("notes.md", "v-bbb444555666")]
    )

    text = NotebookExportService(store).markdown(root).decode("utf-8")

    assert "## Inputs" in text
    assert "cohort.csv" in text and "v-aaa111222333" in text
    assert "notes.md" in text and "v-bbb444555666" in text


def test_markdown_does_not_export_the_turn_text(tmp_path):
    """Only the provenance. The message body is the researcher's unpublished
    thinking and a different decision from naming the file it pointed at."""
    _config, store = _real_store(tmp_path)
    root = store.new_frame(kind="turn", project_id="p")
    _turn_with_refs(
        store, root, "compare against last week", [_ref("cohort.csv", "v-aaa111222333")]
    )

    text = NotebookExportService(store).markdown(root).decode("utf-8")
    assert "against last week" not in text
    assert "cohort.csv" in text


def test_markdown_reads_past_the_stores_default_message_page(tmp_path):
    """`list_branch_messages` defaults to `limit=300`.

    Taking that default drops the inputs of every turn before the last three
    hundred, and a provenance list that is quietly partial is worse than
    absent: a reader cannot tell it is looking at a subset.
    """
    _config, store = _real_store(tmp_path)
    root = store.new_frame(kind="turn", project_id="p")
    # The filler comes FIRST. The default page is the *oldest* 300, so a
    # reference in an early turn would survive it and prove nothing -- the
    # reference has to sit past the page boundary for the limit to matter.
    for index in range(320):
        store.add_message(root_frame_id=root, role="user", content=f"filler {index}")
    _turn_with_refs(store, root, "the last turn", [_ref("latest.csv", "v-late000001")])

    text = NotebookExportService(store).markdown(root).decode("utf-8")
    assert "latest.csv" in text
    assert "v-late000001" in text


def test_markdown_deduplicates_by_version_not_by_name(tmp_path):
    """Two turns citing one pinned version are one input, and the same version
    referenced under an alias is still one input -- but two *different*
    versions of one file are two, which is the whole point of pinning."""
    _config, store = _real_store(tmp_path)
    root = store.new_frame(kind="turn", project_id="p")
    _turn_with_refs(store, root, "one", [_ref("cohort.csv", "v-same00000001")])
    _turn_with_refs(store, root, "two", [_ref("cohort.csv", "v-same00000001")])
    _turn_with_refs(store, root, "alias", [_ref("cohort-copy.csv", "v-same00000001")])
    _turn_with_refs(store, root, "later", [_ref("cohort.csv", "v-later0000002")])

    text = NotebookExportService(store).markdown(root).decode("utf-8")
    assert text.count("v-same00000001") == 1
    assert text.count("v-later0000002") == 1


def test_markdown_says_nothing_about_inputs_when_there_were_none(tmp_path):
    """An empty section is a claim too -- that the question was asked and the
    answer was none."""
    _config, store = _real_store(tmp_path)
    root = store.new_frame(kind="turn", project_id="p")
    store.add_message(root_frame_id=root, role="user", content="no references here")

    text = NotebookExportService(store).markdown(root).decode("utf-8")
    assert "Inputs" not in text


def test_markdown_ignores_metadata_that_is_not_the_expected_shape(tmp_path):
    """Metadata is free-form JSON. A strict parse is what keeps the Inputs
    section a statement about references rather than about whatever else a
    future writer puts in there."""
    _config, store = _real_store(tmp_path)
    root = store.new_frame(kind="turn", project_id="p")
    message = store.add_message(root_frame_id=root, role="user", content="x")
    message_id = message["message_id"] if isinstance(message, dict) else message
    store.update_message_metadata(
        message_id,
        {"artifact_refs": ["not-a-dict", {"version_id": "v-noname00001"}, {}]},
    )

    text = NotebookExportService(store).markdown(root).decode("utf-8")
    assert "Inputs" not in text


def test_markdown_says_exactly_how_many_inputs_it_did_not_list(tmp_path):
    """A bounded list that does not admit the bound reads as complete, and a
    remainder that is off by one is a different kind of lie."""
    from openai4s.server import notebook_export

    _config, store = _real_store(tmp_path)
    root = store.new_frame(kind="turn", project_id="p")
    cap = notebook_export._MAX_RENDERED_INPUTS
    refs = [_ref(f"file-{n}.csv", f"v-{n:012d}") for n in range(cap + 7)]
    _turn_with_refs(store, root, "many", refs)

    text = NotebookExportService(store).markdown(root).decode("utf-8")
    assert "and 7 more" in text
    assert "v-000000000000" in text
    # The 201st and everything after it is genuinely absent, not merely
    # uncounted.
    assert f"v-{cap:012d}" not in text


def _fork(store, root, *, branch_id, message_cursor=None):
    checkpoint = store.create_session_checkpoint(
        root_frame_id=root,
        branch_id=root,
        reason="fork base",
        workspace_tree_id="a" * 64,
        action_cursor=0,
        message_cursor=message_cursor,
    )
    store.fork_session_branch(
        root_frame_id=root,
        from_checkpoint_id=checkpoint["checkpoint_id"],
        branch_id=branch_id,
    )
    return branch_id


def test_a_fork_inherits_the_inputs_its_parent_was_given(tmp_path):
    """`list_messages` returns the frame's rows; the branch projector returns
    the branch's. Reading the frame meant a fork's export claimed inputs from
    turns that are not on it, and -- worse -- missed the ones it inherited."""
    _config, store = _real_store(tmp_path)
    root = store.new_frame(kind="turn", project_id="p")
    _turn_with_refs(store, root, "before the fork", [_ref("base.csv", "v-base0000001")])

    # The fork point is a real position in the conversation. Without a
    # `message_cursor` the checkpoint has no position, so the projector cannot
    # tell "before the fork" from "after it" -- every message lands on every
    # branch, inheritance passes for the wrong reason, and isolation is never
    # exercised at all.
    before = store.list_messages(root, limit=None)
    branch = _fork(store, root, branch_id="branch-alt", message_cursor=len(before))

    # One turn on each side of the fork.
    _turn_with_refs(
        store, root, "only on the parent", [_ref("parent.csv", "v-parent00001")]
    )
    child = store.add_message(
        root_frame_id=root,
        role="user",
        content="only on the child",
        branch_id=branch,
    )
    child_id = child["message_id"] if isinstance(child, dict) else child
    store.update_message_metadata(
        child_id, {"artifact_refs": [_ref("child.csv", "v-child000001")]}
    )

    service = NotebookExportService(store)
    inherited = {
        item["version_id"] for item in service._referenced_artifacts(root, branch)
    }
    parent_side = {
        item["version_id"] for item in service._referenced_artifacts(root, root)
    }

    assert "v-base0000001" in inherited, inherited
    assert "v-child000001" in inherited, inherited
    # Isolation, in both directions -- the half a cursorless fork cannot test.
    assert "v-parent00001" not in inherited, inherited
    assert "v-child000001" not in parent_side, parent_side


def test_the_export_asks_for_the_active_branch(tmp_path):
    """End to end through the service the route calls, so the branch actually
    reaches the projector rather than being defaulted away."""
    from openai4s.server.session_domain import SessionDomainService

    _config, store = _real_store(tmp_path)
    root = store.new_frame(kind="turn", project_id="p")
    _turn_with_refs(store, root, "use it", [_ref("cohort.csv", "v-e2e00000001")])

    asked: list = []
    real = store.list_branch_messages

    def spy(root_frame_id, *, branch_id=None, limit=300):
        asked.append((root_frame_id, branch_id, limit))
        return real(root_frame_id, branch_id=branch_id, limit=limit)

    store.list_branch_messages = spy  # type: ignore[method-assign]
    try:
        text = NotebookExportService(store).markdown(root).decode("utf-8")
    finally:
        store.list_branch_messages = real  # type: ignore[method-assign]

    assert asked, "the export never asked the branch projector"
    assert asked[0][1] == root
    assert asked[0][2] is None
    assert "v-e2e00000001" in text
    assert SessionDomainService is not None


def test_the_gateway_markdown_export_carries_the_inputs(tmp_path):
    """Through `SessionDomainService.notebook_export`, which is what the route
    calls -- including its `active_session_branch` lookup.

    The earlier "E2E" spied on the store from a directly-constructed
    `NotebookExportService`. That proves the service passes what it is given
    and says nothing about whether the layer above passes the right branch, or
    calls it at all.
    """
    from openai4s.server.session_domain import SessionDomainService

    _config, store = _real_store(tmp_path)
    root = store.new_frame(kind="turn", project_id="p")
    _turn_with_refs(
        store,
        root,
        "use @cohort.csv#v-gw0000000001",
        [_ref("cohort.csv", "v-gw0000000001")],
    )

    domain = SessionDomainService.__new__(SessionDomainService)
    domain.store = store
    domain.notebooks = NotebookExportService(store)
    exported = domain.notebook_export(root, language="markdown")

    payload = exported.get("data") if isinstance(exported, dict) else exported
    text = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
    assert "## Inputs" in text, text[:400]
    assert "cohort.csv" in text
    assert "v-gw0000000001" in text


def test_a_reader_error_is_not_swallowed_into_the_paged_default(tmp_path):
    """The deleted `except TypeError` would have caught this.

    It was written to tolerate a store that will not take `branch_id`. It would
    equally have caught a `TypeError` raised *inside* the reader and fallen
    back to the paged default -- the same silent-partial failure the Inputs
    section exists to avoid, in a shape that is harder to see. A reader that
    breaks must break loudly.
    """

    class _Broken(_Store):
        def list_branch_messages(self, root_frame_id, *, branch_id=None, limit=None):
            raise TypeError("something inside the reader is wrong")

    with pytest.raises(TypeError, match="inside the reader"):
        NotebookExportService(_Broken()).markdown("root-1")


class _CleanStore:
    """One session as a researcher saw it: analysis, a failed attempt, an
    environment probe, the completion RPC, narration and progress lines."""

    def __init__(self) -> None:
        self.cells = [
            {
                "producing_cell_id": "c-plot",
                "cell_index": 4,
                "state_revision": 4,
                "language": "python",
                "status": "ok",
                "visibility": "scientific",
                "code": "ax = df.age.hist()\nplt.savefig('plot.png')\n",
                "stdout": "plotted 120 ages\n",
                "stderr": "",
                "error": None,
                "figures": ["plot.png"],
                "created_at": 1000,
            },
            {
                "producing_cell_id": "c-failed",
                "cell_index": 5,
                "language": "python",
                "status": "error",
                "visibility": "scientific",
                "code": "df.agee.mean()\n",
                "error": "AttributeError: 'DataFrame' object has no attribute 'agee'",
                "created_at": 2000,
            },
            {
                "producing_cell_id": "c-probe",
                "cell_index": 6,
                "language": "python",
                "status": "ok",
                "visibility": "scientific",
                "code": "import sklearn\nprint(sklearn.__version__)\n",
                "stdout": "1.5.0\n",
                "created_at": 2500,
            },
            {
                "producing_cell_id": "c-fetch",
                "cell_index": 7,
                "language": "python",
                "status": "ok",
                "visibility": "scientific",
                "code": "page = host.web_fetch('https://example.org')\n",
                "created_at": 3000,
            },
            {
                "producing_cell_id": "c-scratch",
                "cell_index": 8,
                "language": "python",
                "status": "ok",
                "visibility": "scratch",
                "code": "x = 1\n",
                "created_at": 3500,
            },
            {
                "producing_cell_id": "c-submit",
                "cell_index": 9,
                "language": "python",
                "status": "ok",
                "visibility": "scientific",
                "code": "host.submit_output({'summary': 'done'})\n",
                "created_at": 4000,
            },
            {
                "producing_cell_id": "c-r",
                "cell_index": 10,
                "language": "r",
                "status": "ok",
                "visibility": "scientific",
                "code": "summary(cars)\n",
                "created_at": 4500,
            },
        ]

    def list_cells(self, root_frame_id, *, branch_id=None):
        return list(self.cells)

    def list_branch_messages(self, root_frame_id, *, branch_id=None, limit=None):
        return [
            {
                "message_id": "m-user",
                "role": "user",
                "content": "plot ages",
                "created_at": 500,
            },
            {
                "message_id": "m-plan",
                "role": "assistant",
                "content": "First I plot the age distribution.",
                "created_at": 999,
            },
            {
                "message_id": "m-progress",
                "role": "assistant",
                "content": "I have prepared a Python cell and am running it now; "
                "its actual output will be recorded in the Notebook.",
                "created_at": 998,
            },
            {
                "message_id": "m-status",
                "role": "assistant",
                "content": "The cell finished. Output:\n```text\nplotted\n```",
                "created_at": 1001,
            },
            {
                "message_id": "m-final",
                "role": "assistant",
                "content": "Ages are right-skewed; see [plot.png](/api/artifacts/a1).",
                "created_at": 5000,
            },
        ]

    def list_artifacts(self, query):
        return [
            {"artifact_id": "a1", "root_frame_id": "root-1", "filename": "plot.png"}
        ]

    def list_versions(self, artifact_id):
        return [
            {
                "version_id": "v-plot",
                "producing_cell_id": "c-plot",
                "filename": "plot.png",
                "created_at": 1000,
                "ordinal": 1,
            }
        ]

    def get_frame(self, frame_id):
        return {"frame_id": frame_id, "root_frame_id": frame_id}


_PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"


def test_the_clean_notebook_is_one_a_person_can_read_and_rerun():
    """NB-1: the export was the raw execution log -- failed cells, probes, the
    completion RPC, no figures, no narration. The clean form keeps the analysis
    that ran, embeds its figures, interleaves the agent's narration as
    Markdown, and says what it left out."""
    reads: list[str] = []

    def read(version_id):
        reads.append(version_id)
        return _PNG

    service = NotebookExportService(_CleanStore(), read_version_bytes=read)
    exported = service.export("root-1", variant="clean", title="Age QC / 年龄")
    document = json.loads(exported["data"])

    assert exported["filename"] == "Age QC _ 年龄.ipynb"
    assert exported["content_type"] == "application/x-ipynb+json"
    kinds = [cell["cell_type"] for cell in document["cells"]]
    assert kinds == ["markdown", "markdown", "code", "code", "markdown"]
    header, plan, plot, fetch, final = document["cells"]
    assert "1 failed and 2 diagnostic" in "".join(header["source"])
    assert "".join(plan["source"]).startswith("First I plot")
    # Progress lines and the user's own words are not narration.
    text = json.dumps(document)
    assert "I have prepared a Python cell" not in text
    assert "The cell finished" not in text
    assert "plot ages" not in text
    # Renumbered, figure embedded, links into the daemon flattened.
    assert [plot["execution_count"], fetch["execution_count"]] == [1, 2]
    images = [o for o in plot["outputs"] if o["output_type"] == "display_data"]
    assert images and images[0]["data"]["image/png"] == base64.b64encode(_PNG).decode()
    assert reads == ["v-plot"]
    assert "".join(fetch["source"]).startswith(
        "# Needs an OpenAI4S kernel: host.web_fetch"
    )
    assert "".join(final["source"]).strip() == "Ages are right-skewed; see plot.png."
    cells_text = json.dumps(document["cells"])
    for gone in ("agee", "sklearn", "submit_output", "x = 1", "summary(cars)"):
        assert gone not in cells_text
    assert document["metadata"]["openai4s"]["left_out"] == {
        "failed_cells": 1,
        "diagnostic_cells": 2,
    }


def test_the_clean_notebook_leaves_out_cells_that_only_keep_the_books():
    """NB-4: the cleaned export kept ``host.env.list`` / ``host.todo_write``
    cells while its header said "0 diagnostic cells". A cell that only keeps
    the session's books is diagnostic; one that also analyses stays."""

    def cell(cell_id: str, index: int, code: str) -> dict:
        return {
            "producing_cell_id": cell_id,
            "cell_index": index,
            "language": "python",
            "status": "ok",
            "visibility": "scientific",
            "code": code,
            "stdout": "{'ok': True}\n",
            "created_at": 3000 + index,
        }

    store = _CleanStore()
    store.cells += [
        cell("c-envs", 20, 'host.env.list(["openpyxl", "scipy"])\n'),
        cell("c-todos", 21, 'host.todo_write([{"content": "QC"}])\n'),
        cell("c-install", 22, 'host.env.create("work", ["statsmodels"])\n'),
        cell(
            "c-step",
            23,
            'import sys\nprint(sys.version)\nhost.plan_update("s1", "completed")\n',
        ),
        cell("c-mixed", 24, "host.todo_write(todos)\nprint(df.describe())\n"),
    ]
    service = NotebookExportService(store, read_version_bytes=lambda _v: None)
    document = service.clean_notebook("root-1", title="QC")

    kept = [
        cell["metadata"]["openai4s"]["producing_cell_id"]
        for cell in document["cells"]
        if cell["cell_type"] == "code"
    ]
    assert kept == ["c-plot", "c-fetch", "c-mixed"]
    assert document["metadata"]["openai4s"]["left_out"] == {
        "failed_cells": 1,
        "diagnostic_cells": 6,
    }
    assert "1 failed and 6 diagnostic" in "".join(document["cells"][0]["source"])
    text = json.dumps(document["cells"])
    for gone in ("host.env.list", "host.env.create", "plan_update"):
        assert gone not in text

    # The raw execution log is the record and still has every cell.
    raw = json.loads(service.export("root-1", language="python")["data"])
    raw_ids = {c["metadata"]["openai4s"]["producing_cell_id"] for c in raw["cells"]}
    assert {"c-envs", "c-todos", "c-install", "c-step", "c-mixed"} <= raw_ids


def test_the_clean_notebook_lists_a_figure_it_cannot_embed():
    service = NotebookExportService(_CleanStore(), read_version_bytes=lambda _v: None)
    document = service.clean_notebook("root-1")
    plot = next(c for c in document["cells"] if c["cell_type"] == "code")
    assert not [o for o in plot["outputs"] if o["output_type"] == "display_data"]
    assert "[OpenAI4S artifacts] plot.png" in json.dumps(plot["outputs"])


def test_raw_exports_are_unchanged_by_the_clean_variant():
    store = _CleanStore()
    raw = json.loads(
        NotebookExportService(store).export("root-1", language="python")["data"]
    )
    assert len(raw["cells"]) == 6  # every python cell, including the failed one
    assert "variant" not in raw["metadata"]["openai4s"]


def test_the_route_serves_the_clean_notebook_under_the_session_title(tmp_path):
    """Through the real route: `variant=clean` reaches the service, the saved
    name is the session title (RFC 5987, so a Chinese title survives), and an
    unknown variant or a clean bundle is refused rather than guessed."""
    import uuid

    from openai4s.config import Config, LLMConfig
    from openai4s.server import gateway as gateway_mod
    from openai4s.server.contract import API_ROOT

    class _Hub:
        def emitter(self, root_frame_id):
            return lambda event: None

        def broadcast(self, root_frame_id, event):
            return None

    cfg = Config(
        data_dir=tmp_path / "data", llm=LLMConfig(provider="deepseek", api_key="k")
    )
    cfg.ensure_dirs()
    token = "tok-03-" + uuid.uuid4().hex
    (cfg.data_dir / "access-token").write_text(token, encoding="utf-8")
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    try:
        handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
        root = runner.store.new_frame(kind="turn", project_id="default", status="done")
        runner.store.update_frame(root, name="年龄 QC")
        sent: list[tuple] = []

        def get(query):
            handler = object.__new__(handler_cls)
            handler.headers = {gateway_mod._TOKEN_HEADER: token, "Content-Length": "0"}
            handler.path = f"{API_ROOT}/frames/{root}/notebook/export?{query}"
            handler.command = "GET"
            handler.close_connection = False
            handler._correlation_id = "req-nb1"
            handler._send = lambda code, body, ctype, extra=None: sent.append(
                (code, body, ctype, extra or {})
            )
            handler._json = lambda obj, code=200: sent.append((code, obj, "json", {}))
            handler._route("GET")
            return sent[-1]

        code, body, ctype, extra = get("variant=clean")
        assert code == 200 and ctype == "application/x-ipynb+json"
        assert (
            "filename*=UTF-8''%E5%B9%B4%E9%BE%84%20QC.ipynb"
            in extra["Content-Disposition"]
        )
        assert json.loads(body)["metadata"]["openai4s"]["variant"] == "clean"
        assert get("variant=bogus")[0] == 400
        assert get("variant=clean&language=bundle")[0] == 400
        raw_code, raw_body, _ctype, _extra = get("language=python")
        assert raw_code == 200
        assert "variant" not in json.loads(raw_body)["metadata"]["openai4s"]

        # A session nobody renamed is shown by its summary; so is its file.
        runner.store.update_frame(root, name="", task_summary="Cohort QC")
        _code, _body, _ctype, extra = get("variant=clean")
        assert 'filename="Cohort QC.ipynb"' in extra["Content-Disposition"]
    finally:
        runner.close()
