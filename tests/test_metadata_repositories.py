"""Direct contracts for small metadata repositories."""

from __future__ import annotations

import itertools
import json
import sqlite3
import threading

import pytest

from openai4s.config import Config
from openai4s.storage.metadata import (
    CompactionRepository,
    EndpointRepository,
    FolderRepository,
    HostCallRepository,
    NotesRepository,
)
from openai4s.store import get_store


def _clock(start=1000):
    ticks = itertools.count(start)
    calls = []

    def now():
        value = next(ticks)
        calls.append(value)
        return value

    return now, calls


def _store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


def test_notes_add_projection_order_filter_and_delete_commit(tmp_path):
    store = _store(tmp_path)
    now, _calls = _clock()
    repository = NotesRepository(store._conn, store._lock, clock_ms=now)

    first = repository.add(
        project_id="science",
        content="first body",
        title="First title",
    )
    second = repository.add(
        project_id="science",
        content="second body",
    )
    repository.add(project_id="other", content="hidden")

    assert set(first) == {
        "note_id",
        "project_id",
        "content",
        "created_at",
        "updated_at",
    }
    assert first["note_id"].startswith("note_")
    assert len(first["note_id"]) == len("note_") + 12
    assert first["created_at"] == first["updated_at"] == 1000
    assert repository.list("science") == [
        {
            "note_id": second["note_id"],
            "project_id": "science",
            "content": "second body",
            "title": None,
            "created_at": 1001,
            "updated_at": 1001,
        },
        {
            "note_id": first["note_id"],
            "project_id": "science",
            "content": "first body",
            "title": "First title",
            "created_at": 1000,
            "updated_at": 1000,
        },
    ]

    repository.delete(first["note_id"])
    with sqlite3.connect(store.db_path) as independent:
        assert independent.execute(
            "SELECT note_id FROM notes WHERE project_id='science'"
        ).fetchall() == [(second["note_id"],)]


def test_folders_order_rename_assignment_and_delete(tmp_path):
    store = _store(tmp_path)
    now, _calls = _clock(2000)
    repository = FolderRepository(store._conn, store._lock, clock_ms=now)
    frame_id = store.new_frame(project_id="science")

    zulu = repository.create(project_id="science", name="Zulu")
    alpha = repository.create(project_id="science", name="Alpha")
    repository.create(project_id="other", name="Hidden")

    assert zulu == {
        "folder_id": zulu["folder_id"],
        "project_id": "science",
        "name": "Zulu",
        "created_at": 2000,
    }
    assert zulu["folder_id"].startswith("fold_")
    assert len(zulu["folder_id"]) == len("fold_") + 10
    assert [row["name"] for row in repository.list("science")] == [
        "Alpha",
        "Zulu",
    ]

    repository.rename(zulu["folder_id"], "Beta")
    assert [row["name"] for row in repository.list("science")] == [
        "Alpha",
        "Beta",
    ]
    repository.set_frame_folder(frame_id, alpha["folder_id"])
    assert store.get_frame(frame_id)["folder_id"] == alpha["folder_id"]

    repository.delete(alpha["folder_id"])
    with sqlite3.connect(store.db_path) as independent:
        assert independent.execute(
            "SELECT folder_id FROM frames WHERE frame_id=?",
            (frame_id,),
        ).fetchone() == (None,)
        assert (
            independent.execute(
                "SELECT 1 FROM folders WHERE folder_id=?",
                (alpha["folder_id"],),
            ).fetchone()
            is None
        )


def test_folder_delete_keeps_two_separate_commits():
    class RecordingConnection:
        def __init__(self):
            self.executions = []
            self.commits = 0

        def execute(self, sql, params):
            self.executions.append((sql, params))

        def commit(self):
            self.commits += 1

    connection = RecordingConnection()
    repository = FolderRepository(
        connection,
        threading.RLock(),
        clock_ms=lambda: 0,
    )

    repository.delete("fold_x")

    assert connection.executions == [
        (
            "UPDATE frames SET folder_id=NULL WHERE folder_id=?",
            ("fold_x",),
        ),
        ("DELETE FROM folders WHERE folder_id=?", ("fold_x",)),
    ]
    assert connection.commits == 2


def test_endpoint_dynamic_insert_partial_update_and_order(tmp_path):
    store = _store(tmp_path)
    now, calls = _clock(3000)
    repository = EndpointRepository(store._conn, store._lock, clock_ms=now)

    assert (
        repository.upsert(
            "later",
            url="http://127.0.0.1:20001",
            status="registered",
            created_at=50,
        )
        is None
    )
    assert (
        repository.upsert(
            "earlier",
            url="https://example.test",
            status="live",
            created_at=10,
        )
        is None
    )
    assert [row["name"] for row in repository.list()] == ["earlier", "later"]

    repository.upsert("later", status="starting", created_at=5)
    rows = {row["name"]: row for row in repository.list()}
    assert rows["later"]["url"] == "http://127.0.0.1:20001"
    assert rows["later"]["status"] == "starting"
    assert rows["later"]["created_at"] == 5
    assert rows["later"]["updated_at"] == 3002
    assert calls == [3000, 3001, 3002]

    with sqlite3.connect(store.db_path) as independent:
        assert independent.execute(
            "SELECT status,created_at,updated_at FROM managed_endpoints "
            "WHERE name='later'"
        ).fetchone() == ("starting", 5, 3002)


def test_compaction_archive_unicode_shape_and_clock_after_serialization(tmp_path):
    store = _store(tmp_path)
    now, calls = _clock(4000)
    repository = CompactionRepository(store._conn, store._lock, clock_ms=now)
    compacted = [
        {"role": "user", "content": "蛋白质"},
        {"role": "assistant", "content": "done"},
    ]

    archive_id = repository.archive(
        frame_id=None,
        summary="摘要",
        compacted=compacted,
    )

    assert archive_id.startswith("ca-")
    assert len(archive_id) == len("ca-") + 12
    with sqlite3.connect(store.db_path) as independent:
        assert independent.execute(
            "SELECT frame_id,project_id,summary,compacted,n_messages,created_at "
            "FROM compaction_archives WHERE archive_id=?",
            (archive_id,),
        ).fetchone() == (
            None,
            "default",
            "摘要",
            json.dumps(compacted, ensure_ascii=False),
            2,
            4000,
        )
    assert calls == [4000]

    with pytest.raises(TypeError):
        repository.archive(
            frame_id="frame",
            summary="bad",
            compacted=[{"value": object()}],
            project_id="science",
        )
    assert calls == [4000]


def test_compaction_archive_persists_context_v2_linkage(tmp_path):
    store = _store(tmp_path)
    repository = CompactionRepository(store._conn, store._lock, clock_ms=lambda: 5000)
    archive_id = repository.archive(
        frame_id="root-context",
        project_id="science",
        branch_id="branch-a",
        ledger_cursor={"group_id": "ag-1", "ordinal": 4},
        recovery_pointer={"checkpoint_id": "cp-1"},
        generation_id="generation-1",
        metadata={"kernel_restarted": False},
        summary="summary",
        handoff="structured handoff",
        compacted=[{"role": "tool", "content": "preview"}],
        context_before={"total": 900},
        context_after={"total": 300},
        artifact_refs=[{"artifact_id": "a-1", "version_id": "v-1", "sha256": "a" * 64}],
    )

    archived = repository.list("root-context")
    assert [item["archive_id"] for item in archived] == [archive_id]
    item = archived[0]
    assert item["branch_id"] == "branch-a"
    assert item["ledger_cursor"] == {"group_id": "ag-1", "ordinal": 4}
    assert item["recovery_pointer"] == {"checkpoint_id": "cp-1"}
    assert item["context_before"]["total"] == 900
    assert item["context_after"]["total"] == 300
    assert item["artifact_refs"][0]["version_id"] == "v-1"


def test_host_call_log_scrubs_skips_truncates_and_commits(tmp_path):
    store = _store(tmp_path)
    now, calls = _clock(5000)
    repository = HostCallRepository(store._conn, store._lock, clock_ms=now)
    secret = "raw-secret-must-not-appear"
    long_args = [{"text": "数" * 600}]

    repository.log(
        method="web_fetch",
        args=long_args,
        ok=True,
        frame_id="frame-1",
    )
    repository.log(
        method="credentials_set",
        args=[{"name": "token", "value": secret}],
        ok=False,
        frame_id="frame-1",
    )
    repository.log(method="write_file", args=[{object()}], ok=False)
    repository.log(method="credentials_get", args=[{"name": "token"}], ok=True)
    repository.log(method="credentials_list", args=[], ok=True)

    with sqlite3.connect(store.db_path) as independent:
        rows = independent.execute(
            "SELECT call_id,frame_id,method,args_preview,ok,created_at "
            "FROM host_call_log ORDER BY created_at"
        ).fetchall()

    assert len(rows) == 3
    assert all(row[0].startswith("hc-") and len(row[0]) == 15 for row in rows)
    assert rows[0][1:] == (
        "frame-1",
        "web_fetch",
        json.dumps(long_args, ensure_ascii=False)[:500],
        1,
        5000,
    )
    assert rows[1][1:] == (
        "frame-1",
        "credentials_set",
        "<redacted secret args>",
        0,
        5001,
    )
    assert secret not in rows[1][3]
    assert rows[2][1:] == (
        None,
        "write_file",
        "<unserializable>",
        0,
        5002,
    )
    assert calls == [5000, 5001, 5002]
