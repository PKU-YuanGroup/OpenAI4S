from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from openai4s.server.notebook_export import NotebookExportService


class _Store:
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


class _StoreWithRefs(_Store):
    """The same cells, plus the messages that named the files they read."""

    def __init__(self, messages=None):
        self.messages = (
            messages
            if messages is not None
            else [
                {
                    "role": "user",
                    "content": "compare @cohort.csv#v-aaa111222333 against last week",
                    "artifact_refs": [
                        {
                            "display_name": "cohort.csv",
                            "version_id": "v-aaa111222333",
                            "sha256": "f" * 64,
                            "source_session": "root-other",
                        }
                    ],
                },
                {"role": "assistant", "content": "done", "artifact_refs": []},
                {
                    "role": "user",
                    "content": "now add @notes.md",
                    "artifact_refs": [
                        {
                            "display_name": "notes.md",
                            "version_id": "v-bbb444555666",
                            "sha256": "e" * 64,
                            "source_session": "root-1",
                        }
                    ],
                },
            ]
        )

    def list_messages(self, root_frame_id, *, branch_id=None, limit=None):
        assert root_frame_id == "root-1"
        return list(self.messages)


def test_markdown_names_the_artifact_versions_the_session_was_given():
    """The document's own purpose is what makes this a gap.

    It says it exists "for reading it and for pasting it somewhere that is not
    Jupyter -- an issue, a lab notebook, a supplementary methods section". A
    methods section whose inputs are unnamed is the one kind of incomplete that
    matters: the reader cannot tell which version of which file produced the
    numbers, and the session *knows*, because the reference was pinned to a
    version when the turn was sent.

    Rendering cells only meant every `@file#version` a researcher chose was
    dropped from the export while the UI showed it as a chip.
    """
    service = NotebookExportService(_StoreWithRefs())
    text = service.markdown("root-1").decode("utf-8")

    assert "cohort.csv" in text
    assert "v-aaa111222333" in text
    assert "notes.md" in text
    assert "v-bbb444555666" in text
    # The cells are still the body of the document.
    assert "value = 21 * 2" in text
    assert "stop('boom')" in text


def test_markdown_says_nothing_about_inputs_when_there_were_none():
    """An empty section is a claim too -- that the question was asked and the
    answer was none. A session with no references should read exactly as it
    does today rather than gaining a heading with nothing under it."""
    service = NotebookExportService(_StoreWithRefs(messages=[]))
    text = service.markdown("root-1").decode("utf-8")

    assert "Inputs" not in text
    assert "value = 21 * 2" in text


def test_markdown_does_not_export_the_prompt_text_with_the_reference():
    """Only the provenance. The message body is the researcher's unpublished
    thinking and a different decision from naming the file it pointed at."""
    service = NotebookExportService(_StoreWithRefs())
    text = service.markdown("root-1").decode("utf-8")

    assert "compare " not in text
    assert "against last week" not in text
    assert "now add" not in text


def test_markdown_names_a_repeated_reference_once():
    """Two turns citing the same pinned version are one input, not two."""
    ref = {
        "display_name": "cohort.csv",
        "version_id": "v-aaa111222333",
        "sha256": "f" * 64,
        "source_session": "root-1",
    }
    service = NotebookExportService(
        _StoreWithRefs(
            messages=[
                {"role": "user", "content": "one", "artifact_refs": [ref]},
                {"role": "user", "content": "two", "artifact_refs": [dict(ref)]},
            ]
        )
    )
    text = service.markdown("root-1").decode("utf-8")

    assert text.count("v-aaa111222333") == 1


def test_markdown_reads_past_the_stores_default_message_page():
    """`Store.list_messages` defaults to `limit=300`.

    Taking that default would drop the inputs of every turn before the last
    three hundred, and a provenance list that is quietly partial is worse than
    absent: a reader cannot tell it is looking at a subset. The stub below
    refuses the defaulted call so the test fails if the explicit `limit=None`
    is ever dropped.
    """

    class _Paged(_Store):
        def list_messages(self, root_frame_id, *, branch_id=None, limit=300):
            assert limit is None, "the export took the store's paging default"
            return [
                {
                    "role": "user",
                    "content": "old turn",
                    "artifact_refs": [
                        {
                            "display_name": "early.csv",
                            "version_id": "v-early0000001",
                        }
                    ],
                }
            ]

    text = NotebookExportService(_Paged()).markdown("root-1").decode("utf-8")
    assert "early.csv" in text
    assert "v-early0000001" in text


def test_markdown_says_when_it_stops_listing_inputs():
    """A bounded list that does not admit the bound reads as complete."""
    from openai4s.server import notebook_export

    refs = [
        {"display_name": f"file-{n}.csv", "version_id": f"v-{n:012d}"}
        for n in range(notebook_export._MAX_RENDERED_INPUTS + 7)
    ]
    service = NotebookExportService(
        _StoreWithRefs(
            messages=[{"role": "user", "content": "x", "artifact_refs": refs}]
        )
    )
    text = service.markdown("root-1").decode("utf-8")

    assert "and 7 more" in text
    assert "file-0.csv" in text
