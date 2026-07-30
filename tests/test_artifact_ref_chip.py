"""An `@file` reference is a record, not a token the backend re-parses forever.

The reference used to exist only as `@name#v-id` inside the message text. Two
things followed from that, and both were reproducible before this module:

1. **The same sibling-session file could be referenced exactly once.**
   Materialisation refuses when the live filename is already taken, and after
   the first successful reference *it* is what took it. The second turn naming
   the same file came back `materialise_failed` and the model received nothing
   -- for a reference that had worked one turn earlier.
2. **Two artifacts sharing a name were mutually exclusive.** Referencing a
   sibling's `shared.csv` while this session had its own hit the same refusal,
   so the pinned form -- whose entire purpose is that a name is not an identity
   -- could not actually deliver the version it named.

And nothing durable said what the model read: after a cross-session copy the
text names the *source* version while the bytes came from the local one, so
reopen, branch and export could only re-parse a string into the wrong answer.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import threading
import uuid
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from openai4s.config import Config, LLMConfig
from openai4s.server import artifact_refs
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.store import get_store

# ---------------------------------------------------------------------------
# scaffolding
# ---------------------------------------------------------------------------


class _Hub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emitter(self, root_frame_id: str):
        def emit(event: dict) -> None:
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id: str, event: dict) -> None:
        self.emitter(root_frame_id)(event)

    def has_subscriber(self, root_frame_id: str) -> bool:
        del root_frame_id
        return False

    def drop_frame(self, root_frame_id: str) -> None:
        del root_frame_id


def _cfg(tmp_path, **extra) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        **extra,
    )


def _seed(cfg, store, root_frame_id, project_id, filename, payload):
    """One artifact version whose live file and frozen snapshot are separate.

    They are two files in reality, and pointing both at one path makes the
    pinning assertions unable to fail.
    """
    versions = Path(cfg.data_dir) / "artifact-versions"
    versions.mkdir(parents=True, exist_ok=True)
    version_id = f"v-{uuid.uuid4().hex[:12]}"
    snapshot = versions / f"{version_id}__{filename}"
    snapshot.write_bytes(payload)
    return store.record_cell_artifact(
        path=str(snapshot),
        filename=filename,
        content_type="text/csv",
        size_bytes=len(payload),
        checksum=hashlib.sha256(payload).hexdigest(),
        producing_cell_id=None,
        frame_id=root_frame_id,
        root_frame_id=root_frame_id,
        project_id=project_id,
        snapshot_path=str(snapshot),
    )


def _data_service(cfg, store, frame_id, workspace: Path):
    """The real Host data service -- the one thing that must not be faked here.

    Every defect below lives in what `materialise_artifact` refuses and when, so
    a stub that always succeeds would assert nothing.
    """
    from openai4s.host.data import HostDataService

    workspace.mkdir(parents=True, exist_ok=True)

    def _resolve(path, must_exist=False):
        target = (workspace / path).resolve()
        if must_exist and not target.exists():
            raise FileNotFoundError(target)
        return target

    return HostDataService(
        store=store, config=cfg, frame_id=lambda: frame_id, resolve_path=_resolve
    )


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


# ---------------------------------------------------------------------------
# the two refusals
# ---------------------------------------------------------------------------


def test_a_sibling_file_can_be_referenced_in_more_than_one_turn(tmp_path):
    """The second send used to fail on the copy the first send made."""
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    theirs = store.new_frame(kind="turn", project_id="p")
    mine = store.new_frame(kind="turn", project_id="p")
    seeded = _seed(cfg, store, theirs, "p", "shared.csv", b"col\n1\n")
    service = _data_service(cfg, store, mine, tmp_path / "ws")

    def _materialise(version_id, name):
        return service.materialise_artifact(
            {"version_id": version_id, "filename": name}
        )

    text = f"@shared.csv#{seeded['version_id']}"
    kwargs = dict(
        store=store, root_frame_id=mine, project_id="p", materialise=_materialise
    )
    first, first_problems = artifact_refs.resolve_message_refs(text, **kwargs)
    second, second_problems = artifact_refs.resolve_message_refs(text, **kwargs)

    assert first_problems == []
    assert second_problems == [], "the second turn refused a reference that worked"
    assert "col" in first and "col" in second

    # And it is the same copy, not a second one: a reference repeated across a
    # long conversation must not fill the session with duplicates of one file.
    local = store.list_artifacts({"root_frame_id": mine})
    assert len(local) == 1


def test_two_artifacts_that_share_a_name_are_both_referenceable(tmp_path):
    """A name is not an identity -- which is the whole point of pinning.

    This session has its own `shared.csv`. Referencing a sibling's file of the
    same name used to be refused outright, so the pinned reference could not
    deliver the version it named.
    """
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    theirs = store.new_frame(kind="turn", project_id="p")
    mine = store.new_frame(kind="turn", project_id="p")
    sibling = _seed(cfg, store, theirs, "p", "shared.csv", b"THEIR BYTES\n")

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "shared.csv").write_bytes(b"MY OWN BYTES\n")
    service = _data_service(cfg, store, mine, workspace)

    resolved, problems = artifact_refs.resolve_message_refs(
        f"@shared.csv#{sibling['version_id']}",
        store=store,
        root_frame_id=mine,
        project_id="p",
        materialise=lambda version_id, name: service.materialise_artifact(
            {"version_id": version_id, "filename": name}
        ),
    )

    assert problems == []
    assert "THEIR BYTES" in resolved
    # The session's own file is untouched: disambiguating must not be a
    # euphemism for overwriting what the user already had.
    assert (workspace / "shared.csv").read_bytes() == b"MY OWN BYTES\n"


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------


def test_a_resolved_reference_reports_the_six_fields_the_chip_needs(tmp_path):
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    theirs = store.new_frame(kind="turn", project_id="p")
    mine = store.new_frame(kind="turn", project_id="p")
    payload = b"a,b\n1,2\n"
    seeded = _seed(cfg, store, theirs, "p", "cohort.csv", payload)
    service = _data_service(cfg, store, mine, tmp_path / "ws")

    recorded: list[dict] = []
    resolved, problems = artifact_refs.resolve_message_refs(
        f"@cohort.csv#{seeded['version_id']}",
        store=store,
        root_frame_id=mine,
        project_id="p",
        materialise=lambda version_id, name: service.materialise_artifact(
            {"version_id": version_id, "filename": name}
        ),
        on_resolved=recorded.append,
    )

    assert problems == []
    assert len(recorded) == 1
    ref = recorded[0]
    assert set(ref) == {
        "artifact_id",
        "version_id",
        "sha256",
        "display_name",
        "source_session",
        "materialized_target",
    }
    assert ref["display_name"] == "cohort.csv"
    assert ref["sha256"] == hashlib.sha256(payload).hexdigest()
    # The record names the session the reference came FROM and the version the
    # model actually read -- which is the local copy, not the token's version.
    assert ref["source_session"] == theirs
    assert ref["materialized_target"] == ref["version_id"]
    assert ref["version_id"] != seeded["version_id"]
    assert store.get_artifact(ref["artifact_id"])["root_frame_id"] == mine
    # ...and the prompt says so too, rather than citing a version this session
    # cannot resolve.
    assert ref["materialized_target"] in resolved


def test_a_reference_the_budget_dropped_is_not_recorded_as_sent(tmp_path, monkeypatch):
    """A record of a file the model never received is worse than no record."""
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="p")
    monkeypatch.setattr(artifact_refs, "MAX_TOTAL_REF_BYTES", 32)
    seeded = _seed(cfg, store, root, "p", "big.csv", b"x" * 4096)

    recorded: list[dict] = []
    _resolved, problems = artifact_refs.resolve_message_refs(
        f"@big.csv#{seeded['version_id']}",
        store=store,
        root_frame_id=root,
        project_id="p",
        on_resolved=recorded.append,
    )

    assert [p["code"] for p in problems] == ["ref_budget_exhausted"]
    assert recorded == []


def test_a_chip_that_was_deleted_before_send_leaves_nothing_behind(tmp_path):
    """The composer is a plain textarea, so deleting the token is the deletion.

    Locked in rather than assumed: the moment anything materialises at insert
    time instead of at send, an abandoned chip starts leaving an unreferenced
    Artifact and a lineage edge in the session.
    """
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    theirs = store.new_frame(kind="turn", project_id="p")
    mine = store.new_frame(kind="turn", project_id="p")
    seeded = _seed(cfg, store, theirs, "p", "shared.csv", b"col\n1\n")
    service = _data_service(cfg, store, mine, tmp_path / "ws")

    calls: list[str] = []

    def _materialise(version_id, name):
        calls.append(version_id)
        return service.materialise_artifact(
            {"version_id": version_id, "filename": name}
        )

    _resolved, problems = artifact_refs.resolve_message_refs(
        "never mind, ignore that",  # the token was typed and then removed
        store=store,
        root_frame_id=mine,
        project_id="p",
        materialise=_materialise,
    )

    assert problems == []
    assert calls == []
    assert store.list_artifacts({"root_frame_id": mine}) == []
    assert store.lineage_edges_for(seeded["version_id"], "down") == []


# ---------------------------------------------------------------------------
# persistence, through the real send path and the real route
# ---------------------------------------------------------------------------


def _runner(monkeypatch, tmp_path):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, max_turns=1), _Hub())
    monkeypatch.setattr(
        gateway_mod, "build_dispatcher", lambda *a, **k: _StubDispatcher()
    )
    monkeypatch.setattr(runner, "_wire_delegation", lambda state: None)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
    monkeypatch.setattr(
        gateway_mod, "chat", lambda *a, **k: {"content": "Read it.", "usage": {}}
    )
    runner.skills = SimpleNamespace(system_context="", bootstrap_code="")
    return runner


class _StubDispatcher:
    """Only the control plane runs in these turns; no cell, no kernel."""

    def __init__(self) -> None:
        self.last_output = None
        self.active_env_bin = None
        self.active_r_env = None

    def __call__(self, method, args):
        del method, args
        return {"ok": True}


def test_the_send_path_persists_the_reference_it_actually_sent(monkeypatch, tmp_path):
    runner = _runner(monkeypatch, tmp_path)
    store = runner.store
    frame_id = store.new_frame(kind="turn", project_id="default", status="ready")
    payload = b"gene,count\nTP53,7\n"
    seeded = _seed(runner.cfg, store, frame_id, "default", "counts.csv", payload)

    # The turn exhausts its one configured turn without a completion signal,
    # which is beside the point: the reference is resolved and stamped on the
    # user message before the loop runs at all, and that is what is under test.
    result = runner.run_message(
        frame_id, "default", f"summarise @counts.csv#{seeded['version_id']}"
    )
    assert result["frame_id"] == frame_id

    user = [m for m in store.list_messages(frame_id) if m["role"] == "user"][0]
    stored = json.loads(user["metadata"] or "{}")
    refs = stored.get("artifact_refs")
    assert refs, "the reference the turn sent was not persisted with the message"
    assert refs[0]["version_id"] == seeded["version_id"]
    assert refs[0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert refs[0]["display_name"] == "counts.csv"
    assert refs[0]["source_session"] == frame_id
    assert refs[0]["materialized_target"] is None


def test_reopening_a_session_gets_the_reference_back_from_the_route(
    monkeypatch, tmp_path
):
    """Over real HTTP: reopen reads this route and nothing else.

    A direct call to the projection would not notice the route dropping the
    field, and that is exactly what reopen depends on.
    """
    port = _free_port()
    runner = _runner(monkeypatch, tmp_path)
    runner.cfg.port = port
    store = runner.store
    frame_id = store.new_frame(kind="turn", project_id="default", status="ready")
    seeded = _seed(
        runner.cfg, store, frame_id, "default", "counts.csv", b"gene,count\n"
    )
    runner.run_message(
        frame_id, "default", f"look at @counts.csv#{seeded['version_id']}"
    )

    handler = gateway_mod.make_handler(runner.cfg, runner.hub, runner)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    token = local_auth.load_or_mint(runner.cfg.data_dir)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        try:
            conn.request(
                "GET",
                f"/api/v1/frames/{frame_id}/messages",
                headers={local_auth.TOKEN_HEADER: token},
            )
            response = conn.getresponse()
            assert response.status == 200
            body = json.loads(response.read() or b"{}")
        finally:
            conn.close()
    finally:
        httpd.shutdown()
        httpd.server_close()

    user = [m for m in body["messages"] if m["role"] == "user"][0]
    assert user["artifact_refs"], "the route dropped the message's references"
    assert user["artifact_refs"][0]["version_id"] == seeded["version_id"]
    assert user["artifact_refs"][0]["display_name"] == "counts.csv"


def test_a_branch_projection_carries_the_reference_with_the_message(
    monkeypatch, tmp_path
):
    """Branching reads the same rows, so the record has to travel with them."""
    runner = _runner(monkeypatch, tmp_path)
    store = runner.store
    frame_id = store.new_frame(kind="turn", project_id="default", status="ready")
    seeded = _seed(
        runner.cfg, store, frame_id, "default", "counts.csv", b"gene,count\n"
    )
    runner.run_message(
        frame_id, "default", f"look at @counts.csv#{seeded['version_id']}"
    )

    branch_id = store.active_session_branch(frame_id)
    rows = store.list_branch_message_boundaries(frame_id, branch_id=branch_id)
    user = [r for r in rows if r["role"] == "user"][0]
    assert json.loads(user["metadata"] or "{}")["artifact_refs"][0]["version_id"] == (
        seeded["version_id"]
    )


def test_metadata_is_merged_into_a_message_rather_than_replacing_it(tmp_path):
    """The blob is shared; an overwrite here would silently drop a co-tenant."""
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    frame_id = store.new_frame(kind="turn", project_id="p")
    message = store.add_message(
        root_frame_id=frame_id,
        role="user",
        content="hi",
        metadata={"kept": "yes"},
    )

    merged = store.update_message_metadata(
        message["message_id"], {"artifact_refs": [{"version_id": "v-1"}]}
    )
    assert merged["kept"] == "yes"
    assert merged["artifact_refs"][0]["version_id"] == "v-1"
    assert store.update_message_metadata("m-does-not-exist", {"x": 1}) is None
