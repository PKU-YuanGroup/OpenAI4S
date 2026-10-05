"""Direct contracts for small metadata repositories."""

from __future__ import annotations

import itertools
import json
import sqlite3
import threading
import uuid

import pytest

from openai4s.config import Config
from openai4s.storage.metadata import (
    CompactionRepository,
    EndpointRepository,
    FolderRepository,
    HostCallRepository,
    NotesRepository,
    judge_audit_args,
    redact_stored_judge_args_preview,
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

    assert repository.rename(zulu["folder_id"], "Beta") is True
    # A matched row whose value does not change is still a match, and an id no
    # row has is reported rather than silently accepted.
    assert repository.rename(zulu["folder_id"], "Beta") is True
    assert repository.rename("fold_missing", "Beta") is False
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


def test_judge_audit_preview_drops_state_and_params(tmp_path):
    """host.judge previews keep a safe template id and fixed markers.

    One sentinel sits inside the first 500 characters of the raw dump and
    another past that cut, so a truncation of the original arguments would
    still publish the first. Params, an unsafe template string, and a
    charset-safe id the registry does not resolve are the same kind of
    caller text. ``"a" * 100`` is stored as ``<unknown template>``.
    """

    store = _store(tmp_path)
    now, _calls = _clock(8000)
    repository = HostCallRepository(store._conn, store._lock, clock_ms=now)
    near = f"SENTINEL-02-{uuid.uuid4()}"
    far = f"SENTINEL-02-{uuid.uuid4()}"
    param_secret = f"SENTINEL-02-{uuid.uuid4()}"
    state = {"nest": {"secret": near, "pad": "x" * 600, "tail": far}}
    raw = [{"template": "system.probe", "state": state}]
    dumped = json.dumps(raw, ensure_ascii=False)
    assert dumped.find(near) < 500
    assert dumped.find(far) > 500
    invalid = f"bad template {near}"
    long_id = "b" * 101
    safe_id = "a" * 100

    repository.log(
        method="judge",
        args=raw,
        ok=True,
        result={"status": "ok", "template_id": "system.probe"},
    )
    repository.log(
        method="judge",
        args=[
            {
                "template": "features.custom",
                "state": state,
                "params": {"specs": param_secret},
            }
        ],
        ok=True,
    )
    repository.log(
        method="judge",
        args=[{"template": invalid, "state": {"secret": near}}],
        ok=False,
    )
    repository.log(
        method="judge",
        args=[{"template": long_id, "state": {"secret": far}}],
        ok=False,
    )
    repository.log(
        method="judge",
        args=[{"template": safe_id, "state": {"secret": near}}],
        ok=True,
    )
    repository.log(
        method="judge",
        args=[{"template": "system.probe", "state": {"secret": near}}, {"leak": far}],
        ok=False,
    )
    repository.log(method="judge", args=f"not-a-list {near}", ok=False)
    repository.log(
        method="web_fetch",
        args=[{"url": f"https://example.test/{near}"}],
        ok=True,
    )

    with sqlite3.connect(store.db_path) as independent:
        rows = independent.execute(
            "SELECT method, args_preview, result_preview, result_digest, ok "
            "FROM host_call_log ORDER BY created_at"
        ).fetchall()

    assert len(rows) == 8
    judge_rows = rows[:7]
    state_only = json.dumps([{"state": "<redacted judge state>"}], ensure_ascii=False)
    for row in judge_rows:
        assert row[0] == "judge"
        preview = row[1]
        assert near not in preview
        assert far not in preview
        assert param_secret not in preview
        assert invalid not in preview
        assert long_id not in preview
        assert near not in (row[2] or "")
        assert far not in (row[2] or "")
        assert row[3] and len(row[3]) == 64
        assert "<redacted secret" not in preview
    assert judge_rows[0][1] == json.dumps(
        [{"template": "system.probe", "state": "<redacted judge state>"}],
        ensure_ascii=False,
    )
    assert "system.probe" in judge_rows[0][1]
    assert judge_rows[1][1] == json.dumps(
        [
            {
                "template": "features.custom",
                "state": "<redacted judge state>",
                "params": "<redacted judge params>",
            }
        ],
        ensure_ascii=False,
    )
    assert param_secret not in judge_rows[1][1]
    assert judge_rows[2][1] == json.dumps(
        [{"template": "<invalid template>", "state": "<redacted judge state>"}],
        ensure_ascii=False,
    )
    assert judge_rows[3][1] == judge_rows[2][1]
    assert judge_rows[4][1] == json.dumps(
        [{"template": "<unknown template>", "state": "<redacted judge state>"}],
        ensure_ascii=False,
    )
    assert safe_id not in judge_rows[4][1]
    assert judge_rows[5][1] == state_only
    assert judge_rows[6][1] == state_only
    control = rows[7]
    assert control[0] == "web_fetch"
    assert control[1] == json.dumps(
        [{"url": f"https://example.test/{near}"}], ensure_ascii=False
    )
    assert near in control[1]
    store.close()


def test_judge_audit_outputs_are_fixed_points_of_the_stored_rewrite():
    """Every projection ``judge_audit_args`` emits survives a forced v33 re-run.

    The two shapes a new write produces, and that the old rewriter destroyed,
    are included: a params marker, and ``<invalid template>``.
    """

    samples = [
        [{"template": "system.probe", "state": {"secret": "x"}}],
        [
            {
                "template": "features.custom",
                "state": {"secret": "x"},
                "params": {"specs": "y"},
            }
        ],
        [{"template": "bad template", "state": {"secret": "x"}}],
        [{"template": "bad template", "state": {"secret": "x"}, "params": {"p": "y"}}],
        [{"template": "MRN-0012345-HIV", "state": {"secret": "x"}}],
        [
            {
                "template": "MRN-0012345-HIV",
                "state": {"secret": "x"},
                "params": {"specs": "y"},
            }
        ],
        [{"template": "a" * 100, "state": {"secret": "x"}}],
        [{"template": "b" * 101, "state": {"secret": "x"}}],
        [{"template": "system.probe", "state": {"secret": "x"}}, {"leak": "z"}],
        f"not-a-list {'x'}",
        [],
        None,
    ]
    for args in samples:
        projected = judge_audit_args(args)
        stored = json.dumps(projected, ensure_ascii=False)
        assert redact_stored_judge_args_preview(stored) == stored
        again = json.loads(redact_stored_judge_args_preview(stored))
        assert again == projected
    params_marker = json.dumps(
        [
            {
                "template": "features.custom",
                "state": "<redacted judge state>",
                "params": "<redacted judge params>",
            }
        ],
        ensure_ascii=False,
    )
    invalid_marker = json.dumps(
        [{"template": "<invalid template>", "state": "<redacted judge state>"}],
        ensure_ascii=False,
    )
    assert redact_stored_judge_args_preview(params_marker) == params_marker
    assert redact_stored_judge_args_preview(invalid_marker) == invalid_marker


def test_registered_template_ids_are_kept_and_unknown_ids_are_not():
    """A charset match is not an allowlist. The registry is.

    Every id ``get_template`` can resolve is copied through. A future
    template that the projection does not ask the registry about would be
    stored as ``<unknown template>``. ``MRN-0012345-HIV`` matches the
    charset and is not a template, so the audit must not keep it.
    """

    import openai4s.judgment.registry as registry
    from openai4s.judgment.registry import get_template

    get_template("system.probe")
    template_ids = sorted(registry._TEMPLATES)
    assert "system.probe" in template_ids
    assert "features.custom" in template_ids
    assert "literature.screen" in template_ids
    for template_id in template_ids:
        projected = judge_audit_args(
            [
                {
                    "template": template_id,
                    "state": {"secret": "x"},
                    "params": {"specs": "y"},
                }
            ]
        )
        assert projected == [
            {
                "template": template_id,
                "state": "<redacted judge state>",
                "params": "<redacted judge params>",
            }
        ]
        stored = json.dumps(projected, ensure_ascii=False)
        assert redact_stored_judge_args_preview(stored) == stored
    unknown = judge_audit_args(
        [{"template": "MRN-0012345-HIV", "state": {"patient": "x"}}]
    )
    assert unknown == [
        {"template": "<unknown template>", "state": "<redacted judge state>"}
    ]
    assert "MRN-0012345-HIV" not in json.dumps(unknown)


def test_raw_previews_shaped_like_a_projection_are_still_projected():
    """Only the exact text a new write stores is left alone.

    Each sample parses into the projection's shape but was not written by
    ``judge_audit_args``: an id that is no template, another key order,
    other separators, an escaped character, a duplicated ``state`` key that
    hid a raw value. None of them may survive the stored rewrite as it is.
    """

    mrn = "MRN-0012345-HIV"
    marker = "<redacted judge state>"
    unknown = json.dumps(
        [{"template": "<unknown template>", "state": marker}], ensure_ascii=False
    )
    samples = {
        json.dumps([{"template": mrn, "state": marker}]): unknown,
        json.dumps(
            [{"template": mrn, "state": marker, "params": "<redacted judge params>"}]
        ): unknown,
        json.dumps([{"template": mrn, "state": marker}], separators=(",", ":")): None,
        json.dumps([{"state": marker, "template": "system.probe"}]): None,
        '[{"template": "system.probe", "state": "\\u003credacted judge state>"}]': None,
        '[{"state": "SECRET-02-dup", "state": "<redacted judge state>"}]': None,
        json.dumps([{"state": marker, "params": "<redacted judge params>"}]): None,
    }
    for raw, expected in samples.items():
        rewritten = redact_stored_judge_args_preview(raw)
        assert rewritten != raw, raw
        assert mrn not in rewritten
        assert "SECRET-02-dup" not in rewritten
        if expected is not None:
            assert rewritten == expected
        # The rewrite is itself a fixed point.
        assert redact_stored_judge_args_preview(rewritten) == rewritten


def test_a_failing_template_registry_keeps_no_caller_text(monkeypatch):
    """The registry is experimental. Its failure must not reach migration 33.

    Any exception while the bundled templates load is the same answer as an
    unknown id: the caller's text is not kept, and the stored rewrite still
    returns a projection instead of raising.
    """

    import openai4s.judgment.registry as registry

    def broken(_template_id):
        raise ValueError("template registered twice")

    monkeypatch.setattr(registry, "get_template", broken)
    projected = judge_audit_args([{"template": "system.probe", "state": {"s": "x"}}])
    assert projected == [
        {"template": "<unknown template>", "state": "<redacted judge state>"}
    ]
    raw = json.dumps([{"template": "system.probe", "state": {"s": "x"}}])
    assert json.loads(redact_stored_judge_args_preview(raw)) == projected


def test_judge_error_results_are_stored_without_a_digest(tmp_path):
    """A judge soft-fail can repeat the caller's id; its hash would too.

    ``unknown template: MRN-…`` hashed with SHA-256 is recoverable by trying
    candidate ids. The row still says an error happened. A successful judge
    result and other methods' errors keep their digests.
    """

    import hashlib

    store = _store(tmp_path)
    now, _calls = _clock(9000)
    repository = HostCallRepository(store._conn, store._lock, clock_ms=now)
    mrn = "MRN-0012345-HIV"
    echoed = {"error": f"unknown template: {mrn}"}
    repository.log(
        method="judge",
        args=[{"template": mrn, "state": {"p": "x"}}],
        ok=False,
        result=echoed,
    )
    repository.log(
        method="judge",
        args=[{"template": "system.probe", "state": {"p": "x"}}],
        ok=True,
        result={"verdict": "yes"},
    )
    repository.log(
        method="web_fetch",
        args=["https://example.test/"],
        ok=False,
        result={"error": "fetch failed"},
    )
    rows = store._conn.execute(
        "SELECT method,ok,result_preview,result_digest FROM host_call_log "
        "ORDER BY created_at"
    ).fetchall()
    judge_error, judge_ok, fetch_error = (dict(row) for row in rows)
    assert judge_error["result_digest"] is None
    assert json.loads(judge_error["result_preview"])["error"] is True
    assert isinstance(judge_ok["result_digest"], str)
    assert len(judge_ok["result_digest"]) == 64
    assert isinstance(fetch_error["result_digest"], str)
    echoed_digest = hashlib.sha256(
        json.dumps(
            echoed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    stored = json.dumps([dict(row) for row in rows])
    assert echoed_digest not in stored
    assert mrn not in stored
