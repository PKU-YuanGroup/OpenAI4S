from __future__ import annotations

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


def _fork(store, root, *, branch_id):
    checkpoint = store.create_session_checkpoint(
        root_frame_id=root,
        branch_id=root,
        reason="fork base",
        workspace_tree_id="a" * 64,
        action_cursor=0,
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
    branch = _fork(store, root, branch_id="branch-alt")
    _turn_with_refs(
        store,
        root,
        "only on the fork",
        [_ref("child.csv", "v-child000001")],
    )

    service = NotebookExportService(store)
    inherited = service._referenced_artifacts(root, branch)
    versions = {item["version_id"] for item in inherited}
    assert "v-base0000001" in versions, inherited


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
