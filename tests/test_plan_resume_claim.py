"""Resuming a paused plan is a claim, and two callers cannot both win it.

Driven over real HTTP against a real ``ThreadingHTTPServer``, because the whole
defect lives in the gap between "the route decided" and "the job wrote": a
direct call to ``PlanService.resume_execution`` serialises itself and shows
nothing, and asserting on status codes alone would not notice two jobs running
the same steps. So the turn is counted, not inferred.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from http.server import ThreadingHTTPServer

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth


class _Hub:
    """The minimum SessionRunner + the plan emitter need from the WS hub."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def emitter(self, root_frame_id):
        def emit(event):
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id, event):
        self.emitter(root_frame_id)(event)

    def has_subscriber(self, root_frame_id):
        del root_frame_id
        return False

    def drop_frame(self, root_frame_id):
        del root_frame_id


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _paused_plan(store, frame_id):
    return store.create_plan(
        frame_id=frame_id,
        project_id="science",
        title="resumable",
        rationale="",
        confidence="high",
        steps=[
            {
                "id": "s1",
                "title": "step 1",
                "detail": "do it",
                "deliverables": ["out1.csv"],
            }
        ],
        status="paused",
    )


def test_two_concurrent_resume_posts_start_exactly_one_turn(tmp_path):
    """One 202, one 409, and the plan's remaining steps run once.

    The transition used to be a read followed by an unconditional write: the
    route read ``paused`` and the job it spawned wrote ``executing`` whatever
    it found. On the ThreadingHTTPServer both requests read the same ``paused``
    row, both were accepted, and both turns executed the same steps -- twice
    the compute, and two agents writing the same deliverables.
    """
    port = _free_port()
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        host="127.0.0.1",
        port=port,
    )
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub, start_idle_sweeper=False)
    store = runner.store
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    plan = _paused_plan(store, frame_id)

    lock = threading.Lock()
    turns: list[str] = []

    def _run_message(root_frame_id, project_id, seed, model, plan=False):
        del project_id, model, plan
        with lock:
            turns.append(seed)
        # Still running while the loser races: an accepted second resume would
        # overlap this one rather than follow it.
        time.sleep(0.3)
        return {"status": "completed", "frame_id": root_frame_id}

    runner.plans.run_message = _run_message

    # Force the interleaving the race needs instead of hoping for it: both
    # requests come back from the status lookup holding `paused` before either
    # is allowed to act on it. Only the first two lookups are held -- the
    # winner's own turn looks the plan up again and must not block on a barrier
    # nobody else will reach.
    barrier = threading.Barrier(2, timeout=15)
    lookups = {"n": 0}
    real_lookup = store.get_plan_by_frame

    def _synchronised_lookup(fid):
        row = real_lookup(fid)
        with lock:
            lookups["n"] += 1
            held = lookups["n"] <= 2
        if held:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:  # pragma: no cover - timeout
                pass
        return row

    store.get_plan_by_frame = _synchronised_lookup

    handler_cls = gateway_mod.make_handler(cfg, hub, runner)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    token = local_auth.load_or_mint(cfg.data_dir)
    replies: list[tuple[int, dict]] = []

    def _resume():
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        try:
            conn.request(
                "POST",
                f"/api/v1/frames/{frame_id}/plan/resume",
                body=b"{}",
                headers={
                    "Content-Type": "application/json",
                    local_auth.TOKEN_HEADER: token,
                },
            )
            response = conn.getresponse()
            body = json.loads(response.read() or b"{}")
            with lock:
                replies.append((response.status, body))
        finally:
            conn.close()

    try:
        callers = [threading.Thread(target=_resume) for _ in range(2)]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(30)
        assert all(not caller.is_alive() for caller in callers)

        assert sorted(code for code, _ in replies) == [202, 409]
        refused = next(body for code, body in replies if code == 409)
        assert refused["code"] == "plan_not_paused"
        # The loser is told what it lost to, not merely that it lost.
        assert "executing" in refused["error"]

        deadline = time.time() + 20
        while time.time() < deadline and store.get_plan(plan["plan_id"])[
            "status"
        ] not in ("completed", "failed"):
            time.sleep(0.05)

        assert len(turns) == 1, f"the plan's steps ran {len(turns)} times"
        assert store.get_plan(plan["plan_id"])["status"] == "completed"
    finally:
        store.get_plan_by_frame = real_lookup
        httpd.shutdown()
        httpd.server_close()
        runner.close()


def test_the_claim_is_the_update_itself_not_a_status_read(tmp_path):
    """The repository-level contract the route stands on.

    ``update`` writes the status unconditionally, so any guard built on top of
    it is a read-then-write. The compare-and-swap carries the expectation into
    the UPDATE and lets ``rowcount`` decide, which is the only part of this
    that is atomic with the write.
    """
    from openai4s.store import get_store

    store = get_store(Config(data_dir=tmp_path).db_path)
    try:
        frame_id = store.new_frame(kind="turn", project_id="science")
        plan = _paused_plan(store, frame_id)

        assert (
            store.compare_and_set_plan_status(
                plan["plan_id"], expected="paused", new_status="executing"
            )
            is True
        )
        assert store.get_plan(plan["plan_id"])["status"] == "executing"
        # Second attempt loses: the row no longer matches the expectation.
        assert (
            store.compare_and_set_plan_status(
                plan["plan_id"], expected="paused", new_status="executing"
            )
            is False
        )
        assert store.get_plan(plan["plan_id"])["status"] == "executing"
        # A row that does not exist is a loss, not a crash and not an insert.
        assert (
            store.compare_and_set_plan_status(
                "plan-missing", expected="paused", new_status="executing"
            )
            is False
        )
    finally:
        store.close()


def test_a_resume_that_wins_the_claim_still_reports_per_status_refusals(tmp_path):
    """The refusal text is per-status, and the CAS must not flatten it.

    The caller's next move differs by status -- approve a draft, wait for an
    executing one, do nothing for a finished one -- so the claim reports the
    status it lost to rather than a single "cannot resume".
    """
    port = _free_port()
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        host="127.0.0.1",
        port=port,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    store = runner.store
    try:
        frame_id = store.new_frame(kind="turn", project_id="science")
        assert runner.plans.claim_resume(frame_id) == {
            "ok": False,
            "plan_id": None,
            "plan_status": None,
            "error": "no plan to resume",
        }

        plan = _paused_plan(store, frame_id)
        for status in ("draft", "executing", "completed", "failed", "discarded"):
            store.update_plan(plan["plan_id"], status=status)
            claim = runner.plans.claim_resume(frame_id)
            assert claim["ok"] is False, status
            assert claim["plan_status"] == status
            assert status in claim["error"]
            # A lost claim leaves the row exactly where it was.
            assert store.get_plan(plan["plan_id"])["status"] == status

        store.update_plan(plan["plan_id"], status="paused")
        claim = runner.plans.claim_resume(frame_id)
        assert claim["ok"] is True
        assert claim["plan"]["status"] == "executing"
    finally:
        runner.close()


# --------------------------------------------------------------------------
# approve: the same race, on the path that never got the fix
# --------------------------------------------------------------------------


def _draft_plan(store, frame_id):
    return store.create_plan(
        frame_id=frame_id,
        project_id="science",
        title="approvable",
        rationale="",
        confidence="high",
        steps=[
            {
                "id": "s1",
                "title": "step 1",
                "detail": "do it",
                "deliverables": ["out1.csv"],
            }
        ],
        status="draft",
    )


def test_two_concurrent_approve_posts_start_exactly_one_turn(tmp_path):
    """Approve had the shape the resume race was fixed for, and none of the fix.

    ``run_execution`` read the status and then wrote ``executing``
    unconditionally, and the route reaches it only after answering 202 on a
    background thread. So on the ThreadingHTTPServer both POSTs read ``draft``,
    both were accepted, and both turns executed the same steps against the same
    session -- twice the compute, and two agents writing the same deliverables.

    Counted rather than inferred, for the reason this file exists: asserting on
    status codes alone would not notice two jobs running the same plan.
    """
    port = _free_port()
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        host="127.0.0.1",
        port=port,
    )
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub, start_idle_sweeper=False)
    store = runner.store
    frame_id = store.new_frame(kind="turn", project_id="science", status="ready")
    plan = _draft_plan(store, frame_id)

    lock = threading.Lock()
    turns: list[str] = []

    def _run_message(root_frame_id, project_id, seed, model, plan=False):
        del project_id, model, plan
        with lock:
            turns.append(seed)
        # Still running while the loser races: an accepted second approve would
        # overlap this one rather than follow it.
        time.sleep(0.3)
        return {"status": "completed", "frame_id": root_frame_id}

    runner.plans.run_message = _run_message

    # Both callers come back from the status lookup holding `draft` before
    # either may act on it. Only the first two lookups are held; the winner's
    # own turn looks the plan up again and must not block on a barrier nobody
    # else will reach.
    barrier = threading.Barrier(2, timeout=15)
    lookups = {"n": 0}
    real_lookup = store.get_plan_by_frame

    def _synchronised_lookup(fid):
        row = real_lookup(fid)
        with lock:
            lookups["n"] += 1
            held = lookups["n"] <= 2
        if held:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:  # pragma: no cover - timeout
                pass
        return row

    store.get_plan_by_frame = _synchronised_lookup

    handler_cls = gateway_mod.make_handler(cfg, hub, runner)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    token = local_auth.load_or_mint(cfg.data_dir)
    replies: list[tuple[int, dict]] = []

    def _approve():
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        try:
            conn.request(
                "POST",
                f"/api/v1/frames/{frame_id}/plan/approve",
                body=b"{}",
                headers={
                    "Content-Type": "application/json",
                    local_auth.TOKEN_HEADER: token,
                },
            )
            response = conn.getresponse()
            body = json.loads(response.read() or b"{}")
            with lock:
                replies.append((response.status, body))
        finally:
            conn.close()

    try:
        callers = [threading.Thread(target=_approve) for _ in range(2)]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(30)
        assert all(not caller.is_alive() for caller in callers)

        assert sorted(code for code, _ in replies) == [202, 409]
        refused = next(body for code, body in replies if code == 409)
        assert refused["code"] == "plan_not_draft"
        # The loser is told what it lost to, not merely that it lost.
        assert "executing" in refused["error"]

        deadline = time.time() + 20
        while time.time() < deadline and store.get_plan(plan["plan_id"])[
            "status"
        ] not in ("completed", "failed"):
            time.sleep(0.05)

        assert len(turns) == 1, f"the plan's steps ran {len(turns)} times"
        assert store.get_plan(plan["plan_id"])["status"] == "completed"
    finally:
        store.get_plan_by_frame = real_lookup
        httpd.shutdown()
        httpd.server_close()
        runner.close()


def test_the_approval_claim_reports_per_status_refusals(tmp_path):
    """Only a draft is approvable, and the refusal names what it found."""
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        host="127.0.0.1",
        port=_free_port(),
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    store = runner.store
    try:
        frame_id = store.new_frame(kind="turn", project_id="science")
        assert runner.plans.claim_approval(frame_id) == {
            "ok": False,
            "plan_id": None,
            "plan_status": None,
            "error": "no plan to approve",
        }

        plan = _draft_plan(store, frame_id)
        for status in ("paused", "executing", "completed", "failed", "discarded"):
            store.update_plan(plan["plan_id"], status=status)
            claim = runner.plans.claim_approval(frame_id)
            assert claim["ok"] is False, status
            assert claim["plan_status"] == status
            assert status in claim["error"]
            # A lost claim leaves the row exactly where it was.
            assert store.get_plan(plan["plan_id"])["status"] == status

        store.update_plan(plan["plan_id"], status="draft")
        claim = runner.plans.claim_approval(frame_id)
        assert claim["ok"] is True
        assert claim["plan"]["status"] == "executing"
    finally:
        runner.close()
