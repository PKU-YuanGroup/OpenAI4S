"""Direct contracts for projects, frames, messages, steps, and cell logs."""

from __future__ import annotations

import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from openai4s.config import Config
from openai4s.storage.frames import FrameRepository
from openai4s.store import get_store


class _Clock:
    def __init__(self, start: int = 1000) -> None:
        self._next = start
        self.calls: list[int] = []

    def __call__(self) -> int:
        value = self._next
        self._next += 1
        self.calls.append(value)
        return value


def _repository(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    clock = _Clock()
    repository = FrameRepository(
        store._conn,
        store._lock,
        clock_ms=clock,
    )
    return store, repository, clock


def test_frame_hierarchy_scope_updates_tokens_and_commit(tmp_path):
    store, repository, clock = _repository(tmp_path)
    assert repository._connection is store._conn
    assert repository._lock is store._lock

    root = repository.new_frame(
        project_id="science",
        kind="turn",
        name="Root",
        model="model-a",
    )
    child = repository.new_frame(
        parent_id=root,
        project_id="wrong",
        kind="delegate",
        depth=1,
    )
    orphan = repository.new_frame(
        parent_id="missing",
        project_id="legacy",
        kind="delegate",
    )

    assert repository.resolve_frame_scope(child) == {
        "frame_id": child,
        "root_frame_id": root,
        "project_id": "science",
    }
    assert repository.resolve_frame_scope(None, fallback_project="fallback") == {
        "frame_id": None,
        "root_frame_id": None,
        "project_id": "fallback",
    }
    assert repository.resolve_frame_scope(
        "unknown", fallback_project="fallback"
    ) == {
        "frame_id": "unknown",
        "root_frame_id": "unknown",
        "project_id": "fallback",
    }
    assert repository.get_frame(orphan)["root_frame_id"] == orphan
    assert repository.get_frame(orphan)["project_id"] == "legacy"

    repository.update_frame(root, runtime_env="struct", status="done")
    repository.update_frame(root)
    repository.add_frame_tokens(
        root,
        input_tokens=10,
        output_tokens=3,
        cost_usd=0.25,
    )
    repository.add_frame_tokens(root, input_tokens=2, cost_usd=0.5)
    assert clock.calls == [1000, 1001, 1002, 1003, 1004, 1005]

    with sqlite3.connect(store.db_path) as independent:
        row = independent.execute(
            "SELECT root_frame_id,project_id,status,runtime_env,input_tokens,"
            "output_tokens,cost_usd,created_at,updated_at FROM frames "
            "WHERE frame_id=?",
            (root,),
        ).fetchone()
    assert row == (
        root,
        "science",
        "done",
        "struct",
        12,
        3,
        0.75,
        1000,
        1005,
    )
    assert repository.get_frame("missing") is None


def test_project_create_replace_update_and_derived_listing(tmp_path):
    _store, repository, clock = _repository(tmp_path)
    first = repository.create_project(
        project_id="alpha",
        name="Alpha",
        description="first",
        context="context-a",
        is_example=True,
    )
    repository.create_project(project_id="beta", name="Beta")
    root = repository.new_frame(project_id="alpha", name="Conversation")
    repository.new_frame(parent_id=root, project_id="beta", kind="delegate")

    assert first == {
        "project_id": "alpha",
        "name": "Alpha",
        "description": "first",
        "context": "context-a",
        "is_example": 1,
        "created_at": 1000,
        "updated_at": 1000,
    }
    projects = {row["project_id"]: row for row in repository.list_projects()}
    assert projects["alpha"]["conversation_count"] == 1
    assert projects["alpha"]["last_active_at"] == 1002
    assert projects["beta"]["conversation_count"] == 0
    assert projects["beta"]["last_active_at"] == 1001

    repository.update_project("alpha", name="Renamed", context="new")
    repository.update_project("alpha")
    assert clock.calls == [1000, 1001, 1002, 1003, 1004]
    updated = repository.get_project("alpha")
    assert updated["name"] == "Renamed"
    assert updated["created_at"] == 1000
    assert updated["updated_at"] == 1004

    replaced = repository.create_project(project_id="alpha", name="Replacement")
    assert replaced["description"] == ""
    assert replaced["context"] == ""
    assert replaced["is_example"] == 0
    assert replaced["created_at"] == 1005
    assert repository.get_project("missing") is None


def test_message_sequence_metadata_backdating_and_concurrent_commit(tmp_path):
    store, repository, clock = _repository(tmp_path)
    root = repository.new_frame()

    backdated = repository.add_message(
        root_frame_id=root,
        frame_id=root,
        role="user",
        content="first",
        metadata={},
        created_at=0,
    )
    unicode_message = repository.add_message(
        root_frame_id=root,
        role="assistant",
        content="第二",
        metadata={"语言": "中文"},
    )
    assert backdated["seq"] == 0
    assert backdated["created_at"] == 0
    assert unicode_message["seq"] == 1
    assert unicode_message["created_at"] == 1001

    def append(index):
        return repository.add_message(
            root_frame_id=root,
            role="assistant",
            content=f"message-{index}",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        added = list(pool.map(append, range(20)))
    assert {message["seq"] for message in added} == set(range(2, 22))
    assert repository.message_count(root) == 22

    messages = repository.list_messages(root, start=0, limit=2)
    assert messages == [
        {
            "role": "user",
            "content": "first",
            "metadata": None,
            "created_at": 0,
            "seq": 0,
        },
        {
            "role": "assistant",
            "content": "第二",
            "metadata": '{"语言": "中文"}',
            "created_at": 1001,
            "seq": 1,
        },
    ]
    with sqlite3.connect(store.db_path) as independent:
        assert independent.execute(
            "SELECT COUNT(*) FROM messages WHERE root_frame_id=?",
            (root,),
        ).fetchone() == (22,)

    with pytest.raises(TypeError):
        repository.add_message(
            root_frame_id=root,
            role="assistant",
            content="bad metadata",
            metadata={"bad": object()},
        )
    assert repository.message_count(root) == 22


def test_steps_preserve_json_fallback_offsets_and_timestamp_only_update(tmp_path):
    store, repository, clock = _repository(tmp_path)
    frame = repository.new_frame()
    first = repository.add_step(
        step_id="step-1",
        frame_id=frame,
        kind="search",
        title="Search",
        input={},
    )
    second = repository.add_step(
        step_id="step-2",
        frame_id=frame,
        kind="code",
        input={"value": object()},
        status="done",
    )
    repository.update_step(
        "step-1",
        status="done",
        output={},
        title="Searched",
        summary="complete",
    )
    repository.update_step("step-2")
    assert first == {"step_id": "step-1", "seq": 0, "created_at": 1001}
    assert second == {"step_id": "step-2", "seq": 1, "created_at": 1002}
    assert clock.calls == [1000, 1001, 1002, 1003, 1004]

    with store._lock:
        store._conn.execute(
            "UPDATE frame_steps SET input='not-json',output='' WHERE step_id=?",
            ("step-2",),
        )
        store._conn.commit()
    steps = repository.list_steps(frame, start=-100)
    assert repository.step_count(frame) == 2
    assert steps[0]["input"] == {}
    assert steps[0]["output"] == {}
    assert steps[0]["title"] == "Searched"
    assert steps[0]["summary"] == "complete"
    assert steps[1]["input"] == "not-json"
    assert steps[1]["output"] == ""
    with sqlite3.connect(store.db_path) as independent:
        assert independent.execute(
            "SELECT updated_at FROM frame_steps WHERE step_id='step-2'"
        ).fetchone() == (1004,)


def test_frame_browse_detail_and_regex_search_contracts(tmp_path):
    _store, repository, _clock = _repository(tmp_path)
    alpha = repository.new_frame(
        project_id="science", name="Alpha session", status="ready"
    )
    child = repository.new_frame(
        parent_id=alpha,
        name="Child",
        kind="delegate",
        depth=1,
    )
    beta = repository.new_frame(
        project_id="other", name="Beta", status="failed"
    )
    repository.log_cell(
        frame_id=alpha,
        root_frame_id=alpha,
        cell_seq=0,
        code="x = 1",
        result={"id": "cell-a", "stdout": "protein result"},
    )
    repository.log_cell(
        frame_id=alpha,
        root_frame_id=alpha,
        cell_seq=1,
        code="print(x)",
        result={"id": "cell-b", "stdout": "1"},
    )

    assert [
        row["frame_id"]
        for row in repository.browse_frames(project_id="science")
    ] == [alpha]
    assert {
        row["frame_id"]
        for row in repository.browse_frames(
            project_id="all", roots_only=False, limit=20
        )
    } == {alpha, child, beta}
    assert [
        row["frame_id"]
        for row in repository.browse_frames(
            project_id=None, status="failed", roots_only=False
        )
    ] == [beta]

    detail = repository.frame_detail(alpha, page=1, page_size=1)
    assert detail["frame"]["frame_id"] == alpha
    assert [cell["producing_cell_id"] for cell in detail["cells"]] == ["cell-b"]
    assert detail["children"] == [
        {
            "frame_id": child,
            "kind": "delegate",
            "name": "Child",
            "status": "processing",
            "depth": 1,
        }
    ]
    assert detail["n_pages"] == 2
    assert detail["last_page"] is True
    assert repository.frame_detail("missing") is None

    assert [
        row["frame_id"]
        for row in repository.search_frames("PROTEIN", project_id="science")
    ] == [alpha]
    assert [
        row["frame_id"]
        for row in repository.search_frames("beta", project_id="all")
    ] == [beta]
    with pytest.raises(re.error):
        repository.search_frames("[")


def test_execution_log_status_json_fallback_append_only_and_clock(tmp_path):
    store, repository, clock = _repository(tmp_path)
    frame = repository.new_frame(project_id="science")
    first_id = repository.log_cell(
        frame_id=frame,
        root_frame_id=frame,
        project_id="science",
        code="compute()",
        result={
            "id": "cell-1",
            "stdout": "ok",
            "stderr": "",
            "interrupted": True,
            "usage": {"wall_s": 2.5, "cpu_s": 1.5, "peak_rss_kb": 4096},
        },
        cell_index=7,
        figures=["figure.png"],
        files_read=["input.csv"],
        files_written=["output.csv"],
    )
    error_id = repository.log_cell(
        frame_id=frame,
        root_frame_id=frame,
        code="fail()",
        result={"id": "cell-2", "error": "Interrupted", "interrupted": True},
    )
    assert first_id == "cell-1"
    assert error_id == "cell-2"
    assert repository.cell_count(frame) == 2
    assert repository.latest_state_revision(frame) == 8
    cells = repository.list_cells(frame)
    assert cells[0]["status"] == "interrupted"
    assert cells[0]["figures"] == ["figure.png"]
    assert cells[0]["files_read"] == ["input.csv"]
    assert cells[0]["files_written"] == ["output.csv"]
    assert cells[0]["state_revision"] == 7
    assert cells[0]["generation_id"] is None
    assert cells[1]["status"] == "interrupted"
    assert cells[1]["state_revision"] == 8
    assert repository.cell_detail("missing") is None

    group = store.append_action_group(
        root_frame_id=frame,
        turn_id="turn-cell-1",
        kind="execution",
    )
    store.allocate_execution_attempt(
        group_id=group["group_id"],
        producing_cell_id="cell-1",
        state_revision=7,
        generation_id="generation-python-1",
    )
    associated = repository.cell_detail("cell-1")
    assert associated["state_revision"] == 7
    assert associated["generation_id"] == "generation-python-1"
    assert repository.list_cells(frame)[0]["generation_id"] == "generation-python-1"

    failed_group = store.append_action_group(
        root_frame_id=frame,
        turn_id="turn-failed-before-log",
        kind="execution",
    )
    store.allocate_execution_attempt(
        group_id=failed_group["group_id"],
        producing_cell_id="cell-failed-before-log",
        state_revision=9,
    )
    assert repository.latest_state_revision(frame) == 9

    with store._lock:
        store._conn.execute(
            "UPDATE execution_log SET figures='bad',files_read=NULL,"
            "files_written='{}' WHERE producing_cell_id='cell-2'"
        )
        store._conn.commit()
    malformed = repository.cell_detail("cell-2")
    assert malformed["figures"] == []
    assert malformed["files_read"] == []
    assert malformed["files_written"] == {}

    with pytest.raises(sqlite3.IntegrityError):
        repository.log_cell(
            frame_id=frame,
            root_frame_id=frame,
            code="replacement()",
            result={"id": "cell-1"},
        )
    original = repository.cell_detail("cell-1")
    assert original["code"] == "compute()"
    assert original["status"] == "interrupted"
    assert original["created_at"] == 1001
    assert clock.calls == [1000, 1001, 1002, 1003]

    with pytest.raises(TypeError):
        repository.log_cell(
            frame_id=frame,
            code="bad()",
            result={"id": "bad-cell"},
            figures=[object()],
        )
    assert repository.cell_detail("bad-cell") is None
    with sqlite3.connect(store.db_path) as independent:
        assert independent.execute(
            "SELECT COUNT(*) FROM execution_log"
        ).fetchone() == (2,)


def test_execution_log_orders_equal_timestamps_by_state_revision(tmp_path):
    store, repository, _clock = _repository(tmp_path)
    frame = repository.new_frame(project_id="science")
    repository.log_cell(
        frame_id=frame,
        root_frame_id=frame,
        code="first()",
        result={"id": "zzz-first"},
        cell_index=1,
    )
    repository.log_cell(
        frame_id=frame,
        root_frame_id=frame,
        code="second()",
        result={"id": "aaa-second"},
        cell_index=2,
    )
    with store._lock:
        store._conn.execute(
            "UPDATE execution_log SET created_at=2000 WHERE root_frame_id=?",
            (frame,),
        )
        store._conn.commit()

    cells = repository.list_cells(frame)
    assert [cell["producing_cell_id"] for cell in cells] == [
        "zzz-first",
        "aaa-second",
    ]
    assert [cell["state_revision"] for cell in cells] == [1, 2]


def test_execution_log_consumes_matching_attempt_revision_without_reallocating(
    tmp_path,
):
    store, repository, _clock = _repository(tmp_path)
    frame = repository.new_frame(project_id="science")
    group = store.append_action_group(
        root_frame_id=frame,
        turn_id="turn-1",
        kind="execution",
    )
    store.allocate_execution_attempt(
        group_id=group["group_id"],
        producing_cell_id="cell-reserved",
        state_revision=1,
        generation_id="generation-1",
    )

    repository.log_cell(
        frame_id=frame,
        root_frame_id=frame,
        code="run()",
        result={"id": "cell-reserved"},
        cell_index=1,
        state_revision=1,
    )

    detail = repository.cell_detail("cell-reserved")
    assert detail["state_revision"] == 1
    assert detail["generation_id"] == "generation-1"
    store.allocate_execution_attempt(
        group_id=group["group_id"],
        producing_cell_id="cell-wrong",
        state_revision=3,
    )
    with pytest.raises(ValueError, match="durable execution attempt"):
        repository.log_cell(
            frame_id=frame,
            root_frame_id=frame,
            code="wrong()",
            result={"id": "cell-wrong"},
            cell_index=2,
            state_revision=2,
        )


def test_delete_frame_preserves_exact_legacy_aggregate_boundary(tmp_path):
    store, repository, _clock = _repository(tmp_path)
    root = repository.new_frame(project_id="science")
    child = repository.new_frame(parent_id=root, kind="delegate")
    repository.add_message(root_frame_id=root, role="user", content="message")
    repository.log_cell(
        frame_id=child,
        root_frame_id=root,
        code="work()",
        result={"id": "cell-delete"},
    )
    repository.add_step(step_id="root-step", frame_id=root, kind="code")
    repository.add_step(step_id="child-step", frame_id=child, kind="code")
    with store._lock:
        store._conn.execute(
            "INSERT INTO plans(plan_id,frame_id,project_id,steps,status,created_at,"
            "updated_at) VALUES('root-plan',?,?,'[]','draft',1,1)",
            (root, "science"),
        )
        store._conn.execute(
            "INSERT INTO plans(plan_id,frame_id,project_id,steps,status,created_at,"
            "updated_at) VALUES('child-plan',?,?,'[]','draft',1,1)",
            (child, "science"),
        )
        store._conn.execute(
            "INSERT INTO annotations(annotation_id,root_frame_id,artifact_id,"
            "rel_x,rel_y,number,body,status,created_at,updated_at) "
            "VALUES('annotation',?,'artifact',0,0,1,'body','open',1,1)",
            (root,),
        )
        store._conn.executemany(
            "INSERT INTO permission_rules(rule_id,scope,scope_id,tool,pattern,"
            "decision,created_at,updated_at) VALUES(?,?,?,?,?,?,1,1)",
            [
                ("root-rule", "conversation", root, "bash", "*", "ask"),
                ("child-rule", "conversation", child, "bash", "*", "ask"),
            ],
        )
        store._conn.commit()

    repository.delete_frame(root)
    with sqlite3.connect(store.db_path) as independent:
        counts = {
            table: independent.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "frames",
                "messages",
                "execution_log",
                "annotations",
            )
        }
        steps = independent.execute(
            "SELECT step_id FROM frame_steps ORDER BY step_id"
        ).fetchall()
        plans = independent.execute(
            "SELECT plan_id FROM plans ORDER BY plan_id"
        ).fetchall()
        rules = independent.execute(
            "SELECT rule_id FROM permission_rules ORDER BY rule_id"
        ).fetchall()
    assert counts == {
        "frames": 0,
        "messages": 0,
        "execution_log": 0,
        "annotations": 0,
    }
    # The legacy frame deletion only targets root-owned step/plan/rule rows.
    assert steps == [("child-step",)]
    assert plans == [("child-plan",)]
    assert rules == [("child-rule",)]


def test_delete_project_cascade_paths_rows_and_single_commit(tmp_path):
    store, repository, _clock = _repository(tmp_path)
    repository.create_project(project_id="science", name="Science")
    root = repository.new_frame(project_id="science")
    child = repository.new_frame(parent_id=root, kind="delegate")
    repository.add_message(root_frame_id=root, role="user", content="message")
    repository.log_cell(
        frame_id=child,
        root_frame_id=root,
        project_id="science",
        code="work()",
        result={"id": "cell-project"},
    )
    repository.add_step(step_id="project-step", frame_id=child, kind="code")

    with store._lock:
        cursor = store._conn
        cursor.execute(
            "INSERT INTO artifacts(artifact_id,project_id,root_frame_id,filename,"
            "created_at,updated_at) VALUES('artifact','science',?,'result.csv',1,1)",
            (root,),
        )
        cursor.execute(
            "INSERT INTO artifact_versions(version_id,artifact_id,path,frame_id,"
            "created_at) VALUES('version','artifact','/tmp/result.csv',?,1)",
            (child,),
        )
        cursor.execute(
            "INSERT INTO lineage_edges(edge_id,input_version_id,output_version_id,"
            "created_at) VALUES('edge','version','version',1)"
        )
        cursor.execute(
            "INSERT INTO plans(plan_id,frame_id,project_id,steps,status,created_at,"
            "updated_at) VALUES('plan',?,'science','[]','draft',1,1)",
            (child,),
        )
        cursor.execute(
            "INSERT INTO annotations(annotation_id,root_frame_id,artifact_id,"
            "rel_x,rel_y,number,body,status,created_at,updated_at) "
            "VALUES('annotation',?,'artifact',0,0,1,'body','open',1,1)",
            (root,),
        )
        cursor.executemany(
            "INSERT INTO permission_rules(rule_id,scope,scope_id,tool,pattern,"
            "decision,created_at,updated_at) VALUES(?,?,?,?,?,?,1,1)",
            [
                ("project-rule", "project", "science", "bash", "*", "ask"),
                ("frame-rule", "conversation", child, "bash", "*", "ask"),
                ("global-rule", "global", "", "bash", "*", "ask"),
            ],
        )
        cursor.execute(
            "INSERT INTO folders(folder_id,project_id,name,created_at) "
            "VALUES('folder','science','Folder',1)"
        )
        cursor.execute(
            "INSERT INTO notes(note_id,project_id,body,created_at) "
            "VALUES('note','science','Note',1)"
        )
        cursor.execute(
            "INSERT INTO memories(memory_id,project_id,block,content,created_at) "
            "VALUES('memory','science','general','Memory',1)"
        )
        cursor.execute(
            "INSERT INTO compaction_archives(archive_id,frame_id,project_id,"
            "created_at) VALUES('archive',?,'science',1)",
            (child,),
        )
        cursor.execute(
            "INSERT INTO host_call_log(call_id,frame_id,method,created_at) "
            "VALUES('call',?,'query',1)",
            (child,),
        )
        cursor.executemany(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,1)",
            [(f"fb:{root}:1", "up"), (f"fb:{child}:1", "down")],
        )
        cursor.commit()

    result = repository.delete_project("science")
    assert result["stale_paths"] == ["/tmp/result.csv"]
    assert set(result["frame_ids"]) == {root, child}

    with sqlite3.connect(store.db_path) as independent:
        for table in (
            "projects",
            "frames",
            "messages",
            "execution_log",
            "frame_steps",
            "artifacts",
            "artifact_versions",
            "lineage_edges",
            "folders",
            "notes",
            "memories",
            "compaction_archives",
            "host_call_log",
            "plans",
            "annotations",
        ):
            assert independent.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone() == (0,)
        assert independent.execute(
            "SELECT rule_id FROM permission_rules"
        ).fetchall() == [("global-rule",)]
        assert independent.execute(
            "SELECT COUNT(*) FROM settings WHERE key LIKE 'fb:%'"
        ).fetchone() == (0,)


def test_composite_writes_use_late_bound_store_compatibility_callbacks(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    calls = []
    repository = FrameRepository(
        store._conn,
        store._lock,
        clock_ms=_Clock(),
        get_frame=lambda frame_id: calls.append(("get_frame", frame_id))
        or {"frame_id": frame_id},
        resolve_frame_scope=lambda frame_id, **kwargs: calls.append(
            ("resolve_scope", frame_id, kwargs)
        )
        or {"root_frame_id": "late-root", "project_id": "late-project"},
        get_project=lambda project_id: calls.append(("get_project", project_id))
        or {"project_id": project_id, "source": "late"},
    )

    child = repository.new_frame(parent_id="parent", project_id="fallback")
    project = repository.create_project(project_id="project", name="Project")

    assert repository.get_frame(child)["root_frame_id"] == "late-root"
    assert repository.get_frame(child)["project_id"] == "late-project"
    assert project == {"project_id": "project", "source": "late"}
    assert calls == [
        ("get_frame", "parent"),
        ("resolve_scope", "parent", {"fallback_project": "fallback"}),
        ("get_project", "project"),
    ]
