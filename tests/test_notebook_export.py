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
