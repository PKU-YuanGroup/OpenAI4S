"""NB-1 (owner decision 39): a Cell that only runs a workspace script keeps it.

The review session's first Cell was ``exec(open("qc_analysis.py").read())``;
the agent later deleted the file, so the Notebook and both ``.ipynb`` exports
held a call that could no longer run. The script is now read just before the
Cell runs and kept with its execution record.
"""

from __future__ import annotations

import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from openai4s.execution import CellRequest
from openai4s.kernel import KernelSupervisor
from openai4s.server.cell_run import (
    INLINE_SCRIPT_MAX_BYTES,
    CellExecutionService,
    read_workspace_script,
    script_run_target,
)
from openai4s.server.execution_views import ExecutionViewService
from openai4s.server.notebook_export import NotebookExportService

SCRIPT = 'import math\nresult = math.sqrt(16)\nprint("qc done", result)\n'


@pytest.mark.parametrize(
    ("code", "language", "expected"),
    [
        ('exec(open("qc_analysis.py").read())', "python", "qc_analysis.py"),
        ("exec(open('qc_analysis.py').read())\n", "python", "qc_analysis.py"),
        (
            'exec(open("scripts/qc.py", encoding="utf-8").read())',
            "python",
            "scripts/qc.py",
        ),
        ('exec(open("qc.py", "r").read(), globals())', "python", "qc.py"),
        (
            'exec(compile(open("qc.py").read(), "qc.py", "exec"))',
            "python",
            "qc.py",
        ),
        ('exec(Path("qc.py").read_text())', "python", "qc.py"),
        (
            "# run the QC step\nimport runpy\nrunpy.run_path('qc.py')\n",
            "python",
            "qc.py",
        ),
        ('from runpy import run_path\nrun_path("qc.py")', "python", "qc.py"),
        ("%run qc.py", "python", "qc.py"),
        ("%run -i 'my qc.py' --flag", "python", "my qc.py"),
        ('source("analysis.R")', "r", "analysis.R"),
        ("source('analysis.R', echo = TRUE)", "r", "analysis.R"),
        ('# R step\nsource(file = "analysis.R")\n', "r", "analysis.R"),
    ],
)
def test_a_cell_that_only_runs_a_script_names_it(code, language, expected):
    assert script_run_target(code, language) == expected


@pytest.mark.parametrize(
    ("code", "language"),
    [
        ('exec(open("qc.py").read())\nprint("after")', "python"),
        ('import os\nexec(open("qc.py").read())', "python"),
        ("exec(open(name).read())", "python"),
        ('exec(open("qc.py", "w").read())', "python"),
        ('exec("print(1)")', "python"),
        ("%run -m package.module", "python"),
        ('print(open("qc.py").read())', "python"),
        ('source("a.R")\nsource("b.R")', "r"),
        ('x <- 1\nsource("a.R")', "r"),
        ('exec(open("qc.py").read())', "r"),
        ("", "python"),
    ],
)
def test_ordinary_code_names_no_script(code, language):
    assert script_run_target(code, language) is None


def test_the_script_is_read_whole_with_its_digest(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "qc.py").write_text(SCRIPT, encoding="utf-8")

    script = read_workspace_script(tmp_path, "./scripts/qc.py")

    assert script is not None
    assert script["path"] == "scripts/qc.py"
    assert script["content"] == SCRIPT
    assert script["size_bytes"] == len(SCRIPT.encode("utf-8"))
    assert script["truncated"] is False
    assert len(script["sha256"]) == 64


def test_a_long_script_keeps_its_head_and_says_so(tmp_path):
    body = "x = 1\n" * (INLINE_SCRIPT_MAX_BYTES // 6 + 100)
    (tmp_path / "long.py").write_text(body, encoding="utf-8")

    script = read_workspace_script(tmp_path, "long.py")

    assert script is not None
    assert script["truncated"] is True
    assert script["sha256"] is None
    assert len(script["content"].encode("utf-8")) <= INLINE_SCRIPT_MAX_BYTES
    assert script["size_bytes"] == len(body)


def test_nothing_outside_the_workspace_is_read_into_the_record(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "secret.py"
    secret.write_text("TOKEN = 'not for the record'\n", encoding="utf-8")

    assert read_workspace_script(workspace, str(secret)) is None
    assert read_workspace_script(workspace, "../secret.py") is None
    assert read_workspace_script(workspace, "~/secret.py") is None
    assert read_workspace_script(workspace, "missing.py") is None
    if hasattr(os, "symlink"):
        (workspace / "link.py").symlink_to(secret)
        assert read_workspace_script(workspace, "link.py") is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a FIFO")
def test_a_fifo_is_never_opened_for_reading(tmp_path):
    os.mkfifo(tmp_path / "pipe.py")
    assert read_workspace_script(tmp_path, "pipe.py") is None


class _Ports:
    """The minimum ports a successful Cell walks through."""

    def __init__(self, store, on_run=None):
        self.store = store
        self.on_run = on_run

    def build(self):
        from openai4s.execution import CaptureResult
        from openai4s.server.cell_run import CellExecutionPorts

        return CellExecutionPorts(
            prepare_language=lambda session, language: None,
            kernel_id=lambda session, language: language,
            snapshot=lambda workspace: {},
            protect_versions=lambda session: None,
            safety_refusal=lambda session, code, origin: None,
            run=self.run,
            capture=lambda *args: CaptureResult(),
            emit_artifact_step=lambda *args: None,
            record_cell=self.store.log_cell,
        )

    def run(self, session, request, cell_id, on_chunk, lease):
        if self.on_run is not None:
            self.on_run()
        return {"stdout": "qc done 4.0\n", "stderr": "", "error": None}


def _store_and_session(tmp_path):
    from openai4s.config import Config
    from openai4s.store import get_store

    store = get_store(Config(data_dir=tmp_path / "data").db_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = SimpleNamespace(
        root_frame_id=store.new_frame(project_id="project-1"),
        project_id="project-1",
        workspace=workspace,
        cell_index=0,
        kernels=KernelSupervisor(),
    )
    return store, session


def test_the_record_keeps_the_script_as_it_ran_after_the_file_is_gone(tmp_path):
    store, session = _store_and_session(tmp_path)
    script_path = session.workspace / "qc_analysis.py"
    script_path.write_text(SCRIPT, encoding="utf-8")
    events: list[dict] = []
    # The Cell itself rewrites the file while it runs; the record keeps what
    # it was when the Cell started.
    ports = _Ports(store, on_run=lambda: script_path.write_text("changed\n"))
    service = CellExecutionService(ports.build(), id_factory=lambda: "cell-qc")

    service.execute(
        session,
        CellRequest('exec(open("qc_analysis.py").read())', "agent", stream=True),
        events.append,
    )
    script_path.unlink()

    finished = next(e for e in events if e["type"] == "notebook_cell_finished")
    assert finished["inlined_script"]["content"] == SCRIPT
    [cell] = store.list_cells(session.root_frame_id)
    assert cell["code"] == 'exec(open("qc_analysis.py").read())'
    assert cell["inlined_script"]["path"] == "qc_analysis.py"
    assert cell["inlined_script"]["content"] == SCRIPT
    assert store.cell_detail("cell-qc")["inlined_script"]["content"] == SCRIPT

    view = ExecutionViewService(store=store, format_timestamp=lambda value: None)
    [entry] = view.execution_log(session.root_frame_id)["entries"]
    assert entry["inlined_script"]["content"] == SCRIPT
    store.close()


def test_an_ordinary_cell_carries_no_script(tmp_path):
    store, session = _store_and_session(tmp_path)
    (session.workspace / "qc_analysis.py").write_text(SCRIPT, encoding="utf-8")
    ports = _Ports(store)
    service = CellExecutionService(ports.build(), id_factory=lambda: "cell-plain")

    service.execute(
        session,
        CellRequest('print(open("qc_analysis.py").read())', "agent", stream=False),
        lambda event: None,
    )

    [cell] = store.list_cells(session.root_frame_id)
    assert cell["inlined_script"] is None
    store.close()


@pytest.mark.skipif(
    sqlite3.sqlite_version_info < (3, 35, 0), reason="needs ALTER TABLE DROP COLUMN"
)
def test_a_schema_43_database_gains_the_column_on_upgrade(tmp_path):
    from openai4s.config import Config
    from openai4s.storage.migrations import SCHEMA_VERSION
    from openai4s.store import get_store

    db = Config(data_dir=tmp_path).db_path
    store = get_store(db)
    frame = store.new_frame(project_id="p")
    store.log_cell(frame_id=frame, root_frame_id=frame, code="x = 1", result={})
    store.close()
    with sqlite3.connect(str(db)) as connection:
        connection.execute("ALTER TABLE execution_log DROP COLUMN inlined_script")
        connection.execute("DELETE FROM schema_migrations WHERE version=44")
        connection.execute("PRAGMA user_version = 43")

    upgraded = get_store(db)
    try:
        assert upgraded.schema_state()["version"] == SCHEMA_VERSION == 44
        # A Cell recorded before the upgrade has no script: what its file
        # held then is not known now.
        [old] = upgraded.list_cells(frame)
        assert old["inlined_script"] is None
        upgraded.log_cell(
            frame_id=frame,
            root_frame_id=frame,
            code='exec(open("a.py").read())',
            result={},
            inlined_script={"path": "a.py", "content": "y = 2\n"},
        )
        assert [c["inlined_script"] for c in upgraded.list_cells(frame)][1] == {
            "path": "a.py",
            "content": "y = 2\n",
        }
    finally:
        upgraded.close()


def test_a_malformed_script_is_refused_at_the_store(tmp_path):
    store, session = _store_and_session(tmp_path)
    with pytest.raises(ValueError):
        store.log_cell(
            frame_id=session.root_frame_id,
            code="x",
            result={},
            inlined_script={"path": "", "content": "x"},
        )
    store.close()


# -- exports ------------------------------------------------------------------


class _ExportStore:
    def __init__(self, script):
        self.script = script

    def list_branch_messages(self, root_frame_id, *, branch_id=None, limit=None):
        return []

    def list_cells(self, root_frame_id):
        return [
            {
                "producing_cell_id": "cell-qc",
                "cell_index": 1,
                "state_revision": 1,
                "kernel_id": "python",
                "language": "python",
                "status": "ok",
                "origin": "agent",
                "visibility": "scientific",
                "code": 'exec(open("qc_analysis.py").read())',
                "stdout": "qc done 4.0\n",
                "stderr": "",
                "error": None,
                "figures": [],
                "files_read": ["qc_analysis.py"],
                "files_written": [],
                "inlined_script": self.script,
                "created_at": 1000,
            }
        ]


def _whole():
    return {
        "path": "qc_analysis.py",
        "content": SCRIPT,
        "size_bytes": len(SCRIPT),
        "truncated": False,
        "sha256": "0" * 64,
    }


def _source(document):
    [cell] = [
        c
        for c in document["cells"]
        if c["cell_type"] == "code"
        and c["metadata"].get("openai4s", {}).get("producing_cell_id")
    ]
    return "".join(cell["source"]), cell


def test_both_ipynb_exports_carry_the_script_and_rerun_without_the_file():
    service = NotebookExportService(_ExportStore(_whole()))

    for document in (
        service.notebook("root-1", "python"),
        service.clean_notebook("root-1", "python"),
    ):
        source, cell = _source(document)
        assert source.startswith("# inlined from qc_analysis.py as it was when run")
        # The call is kept as a comment, so nothing reads the missing file.
        assert '# exec(open("qc_analysis.py").read())\n' in source
        assert source.endswith(SCRIPT)
        namespace: dict = {}
        exec(compile(source, "<export>", "exec"), namespace)  # noqa: S102
        assert namespace["result"] == 4.0
    raw_source, raw_cell = _source(service.notebook("root-1", "python"))
    assert raw_cell["metadata"]["openai4s"]["inlined_script"] == {
        "path": "qc_analysis.py",
        "size_bytes": len(SCRIPT),
        "sha256": "0" * 64,
        "truncated": False,
    }


def test_the_markdown_export_shows_the_script_under_the_cell():
    text = NotebookExportService(_ExportStore(_whole())).markdown("root-1").decode()
    assert "Script `qc_analysis.py` as it ran:" in text
    assert "result = math.sqrt(16)" in text


def test_a_truncated_script_is_not_inlined_and_the_cell_says_why():
    script = dict(_whole(), truncated=True, size_bytes=900_000, sha256=None)
    service = NotebookExportService(_ExportStore(script))
    source, _cell = _source(service.notebook("root-1", "python"))
    assert source.startswith("# qc_analysis.py was 900000 bytes when it ran")
    assert source.endswith('exec(open("qc_analysis.py").read())')
    assert "math.sqrt" not in source


def test_a_cell_without_a_script_exports_unchanged():
    service = NotebookExportService(_ExportStore(None))
    source, cell = _source(service.notebook("root-1", "python"))
    assert source == 'exec(open("qc_analysis.py").read())'
    assert "inlined_script" not in cell["metadata"]["openai4s"]
    assert json.dumps(service.clean_notebook("root-1", "python"))


def test_the_chinese_clean_export_uses_the_chinese_header():
    class _ZhStore(_ExportStore):
        def list_branch_messages(self, root_frame_id, *, branch_id=None, limit=None):
            return [
                {
                    "message_id": "m1",
                    "role": "assistant",
                    "content": "先运行质控脚本，检查每个样本的文库大小。",
                    "created_at": 500,
                }
            ]

    document = NotebookExportService(_ZhStore(_whole())).clean_notebook(
        "root-1", "python"
    )
    source, _cell = _source(document)
    assert source.startswith("# 内联自 qc_analysis.py（运行时内容）")
