"""What a session accepts while a turn is already running.

Three refusals that all used to arrive in the wrong place, or in the wrong
shape, or not at all:

* A message had no text limit. The only bound was `_MAX_JSON_BODY_BYTES` — the
  *session archive* cap, 128 MiB — standing in as a chat-message cap. The
  message is persisted and replayed into every later turn, so an 8 MiB paste
  is eight times the whole context window on its own and the session is
  bricked. Compaction cannot rescue it, because summarising the message means
  sending it.
* A session pinned to a deleted model revision was refused by
  `bind_model_revision` with a 409 — inside the worker thread, after the
  client had already been told the turn was accepted. Over HTTP it arrived as
  **200** with `{"status": "failed", "error": …}`, the same soft-dictionary
  shape six other routes were fixed for.
* A full queue raised `QueueDepthExceeded`, which reached the client as **500
  internal_error**. Nothing failed internally; the user asked for more than the
  cap, and a client that retries 5xx would loop against a queue that cannot
  accept anything until they wait or cancel.

Everything here drives the real handler, because every one of these bugs lived
in the gap between a function that behaved correctly and the route that
reported it.
"""

from __future__ import annotations

import json
import threading

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


class _Client:
    """The real Handler with only its byte sink replaced.

    Calling `SessionRunner.submit_message` directly would have passed against
    every defect above: each one is a mismatch between what the method does and
    what the route reports.
    """

    def __init__(self, tmp_path):
        self.cfg = Config(
            data_dir=tmp_path,
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
            max_turns=1,
        )
        self.runner = gateway_mod.SessionRunner(self.cfg, _Hub())
        self.store = self.runner.store
        self.store.create_project(name="p", description="", context="")
        self.project_id = [p["project_id"] for p in self.store.list_projects()][0]
        self.frame_id = self.runner.create_session(self.project_id)
        self._handler_class = gateway_mod.make_handler(self.cfg, _Hub(), self.runner)
        self._token = local_auth.read_token(tmp_path) or ""

    def post(self, path, body):
        handler = object.__new__(self._handler_class)
        handler._correlation_id = "req-1"
        sent: dict = {}

        def _send(code, payload, ctype, extra=None):
            sent["code"] = code
            sent["body"] = json.loads(payload.decode("utf-8"))

        handler._send = _send
        handler.command = "POST"
        handler.path = f"/api/v1{path}"
        handler.headers = {
            "Content-Length": "0",
            local_auth.TOKEN_HEADER: self._token,
        }
        handler._body = lambda: body
        handler._route("POST")
        return sent["code"], sent["body"]

    def post_method(self, method, path, body):
        handler = object.__new__(self._handler_class)
        handler._correlation_id = "req-1"
        sent: dict = {}

        def _send(code, payload, ctype, extra=None):
            sent["code"] = code
            sent["body"] = json.loads(payload.decode("utf-8"))

        handler._send = _send
        handler.command = method
        handler.path = f"/api/v1{path}"
        handler.headers = {
            "Content-Length": "0",
            local_auth.TOKEN_HEADER: self._token,
        }
        handler._body = lambda: body
        handler._route(method)
        return sent["code"], sent["body"]

    def message(self, text, **extra):
        return self.post(f"/frames/{self.frame_id}/message", {"request": text, **extra})


@pytest.fixture
def client(tmp_path):
    return _Client(tmp_path)


# --------------------------------------------------------------------------
# the text budget
# --------------------------------------------------------------------------


def test_a_message_that_would_brick_the_session_is_refused(client):
    """Fixed sizes, not multiples of the constant under test.

    8 MiB is what the old path accepted; 200,000 is the limit. Writing this as
    `MAX_MESSAGE_CHARS + 1` would keep passing if the cap were raised to 128
    MiB, which is the state being fixed.
    """
    status, body = client.message("x" * (8 * 1024 * 1024))
    assert status == 413
    assert body["code"] == "message_too_large"
    assert "8,388,608" in body["error"] and "200,000" in body["error"]


def test_the_refusal_happens_before_anything_is_written_or_queued(client):
    """The point of refusing at admission. Persisting first and refusing after
    would leave the message in the history it was refused for — replayed into
    every later turn, exactly the outcome the cap exists to prevent."""
    client.message("x" * (8 * 1024 * 1024))
    assert client.store.list_messages(client.frame_id) == []
    assert client.runner.executions.snapshot(client.frame_id).get("queue") == []


def test_a_client_that_does_not_wait_is_refused_just_as_loudly(client):
    """`wait=false` answers 202 before the turn runs. If the check lived in the
    worker thread, this caller would be told "accepted" and would never learn
    otherwise."""
    status, body = client.message("x" * (8 * 1024 * 1024), wait=False)
    assert status == 413
    assert body["code"] == "message_too_large"


def test_an_ordinary_long_paste_still_goes_through(client):
    """A limit nobody can reach is not a limit; one that catches real use is a
    bug. 199,000 characters is a very long paste and must be accepted."""
    _state, finish = _hold_the_head(client)
    try:
        status, _body = client.message("x" * 199_000, wait=False)
        assert status == 202
    finally:
        finish()


def test_the_cap_governs_typed_text_and_not_referenced_files(client):
    """`@name` references are expanded inside `run_message`, long after
    admission, and carry their own `MAX_REF_BYTES` budget. If the cap were
    applied to the expanded text instead, eight legitimate references would be
    refused as one oversized message."""
    from openai4s.server import artifact_refs

    assert artifact_refs.MAX_REFS * artifact_refs.MAX_REF_BYTES > (
        gateway_mod.MAX_MESSAGE_CHARS
    ), "a full set of references exceeds the message cap, so the two must be separate"
    _state, finish = _hold_the_head(client)
    try:
        status, _body = client.message("look at @a.csv and @b.csv", wait=False)
        assert status == 202
    finally:
        finish()


# --------------------------------------------------------------------------
# frozen model identity
# --------------------------------------------------------------------------


def test_a_dangling_model_pin_is_a_conflict_not_a_two_hundred(client):
    """The defect this file was opened for. `bind_model_revision` raised 409
    correctly; it ran in the worker thread, so the client saw 200 and a failure
    dictionary — indistinguishable from success to anything checking status."""
    client.store.update_frame(
        client.frame_id, model_profile_id="prof-gone", model_profile_revision=7
    )
    status, body = client.message("continue please")
    assert status == 409
    assert body["code"] == "model_revision_unavailable"


def test_the_identity_is_frozen_when_send_is_pressed(client):
    """Not when the turn reaches the head of the queue. A follow-up typed
    against one configuration must not adopt another because the user changed
    profiles while it waited."""
    client.store.update_frame(
        client.frame_id, model_profile_id="prof-gone", model_profile_revision=7
    )
    status, _body = client.message("queued behind something", wait=False)
    assert status == 409, "the refusal waited for the dequeue instead of the send"


# --------------------------------------------------------------------------
# a full queue
# --------------------------------------------------------------------------


def _hold_the_head(client):
    """Occupy the queue head so an accepted turn stays queued and never runs.

    Every test here is about *admission*. Letting an accepted turn actually
    execute would spawn a real turn thread that outlives the test, write to a
    store the fixture then closes -- which surfaced as an intermittent
    `Cannot operate on a closed database` in whatever test ran next -- and
    attempt a live LLM call from an offline suite.
    """
    state = client.runner._state(client.frame_id, client.project_id)
    ticket = client.runner._queue_execution(
        state, owner="lifecycle", owner_id="hold", reason="test hold"
    )
    running = threading.Event()
    release = threading.Event()

    def _hold():
        with client.runner.executions.admitted(ticket, cancel_event=state.cancel):
            running.set()
            release.wait(30)

    thread = threading.Thread(target=_hold, daemon=True)
    thread.start()
    assert running.wait(5)

    def _finish():
        release.set()
        thread.join(10)
        assert not thread.is_alive()

    return state, _finish


def _fill_the_queue(client):
    state, finish = _hold_the_head(client)
    for index in range(64):
        client.runner._queue_execution(
            state, owner="agent", owner_id=f"j{index}", reason="fill"
        )
    return finish


def test_a_full_queue_says_so_instead_of_claiming_a_server_fault(client):
    finish = _fill_the_queue(client)
    try:
        status, body = client.message("one more", wait=False)
        assert (
            status == 429
        ), "500 tells a client to retry something that cannot succeed"
        assert body["code"] == "queue_full"
        assert "cancel a queued one" in body["error"]
    finally:
        finish()


def test_the_cap_is_reached_by_queueing_and_not_by_a_single_message(client):
    """A depth cap that a long message could trip would conflate two different
    limits and give the user the wrong instruction for whichever they hit."""
    finish = _fill_the_queue(client)
    try:
        status, body = client.message("x" * (8 * 1024 * 1024), wait=False)
        # text is checked first, because that refusal is about this message
        # rather than about the queue behind it
        assert status == 413
        assert body["code"] == "message_too_large"
    finally:
        finish()


# --------------------------------------------------------------------------
# pinned annotations are consumed only by a message the server accepted
# --------------------------------------------------------------------------


def _pin(client, *, body="look at this peak"):
    """One open annotation on a real artifact in this session."""
    return client.store.add_annotation(
        root_frame_id=client.frame_id,
        artifact_id="artifact-under-pin",
        artifact_name="plot.png",
        rel_x=0.5,
        rel_y=0.5,
        body=body,
    )["annotation_id"]


def _status(client, annotation_id):
    return (client.store.get_annotation(annotation_id) or {}).get("status")


def test_a_refused_message_does_not_burn_the_pins_it_carried(client):
    """`mark_annotations_sent` ran *before* `submit_message`.

    Every refusal this route can make happens inside `submit_message` -- its
    own docstring says so, and the oversized-text 413 below is one of them --
    so a message that was never accepted had already flipped its pins to
    `sent`. `mark_sent` is one-way (`WHERE status='open'`, and nothing sets it
    back), so the comments were gone for good: not on the turn, because there
    was no turn, and not in the composer either.

    The browser said the opposite in as many words -- "POST failed →
    annotations were never consumed server-side" -- and reconciled against the
    server on that basis, so the UI faithfully reported the loss as success.
    """
    annotation_id = _pin(client)
    assert _status(client, annotation_id) == "open"

    status, _body = client.message(
        "x" * (8 * 1024 * 1024), annotation_ids=[annotation_id]
    )

    assert status == 413, status
    assert _status(client, annotation_id) == "open", (
        "a refused message consumed its pinned comments; they cannot be "
        "reopened, so the user's annotations are lost"
    )


def test_an_accepted_message_still_consumes_them(client):
    """The other half: the fix must not make pins un-consumable, or every turn
    would re-send the same comments forever."""
    annotation_id = _pin(client)

    status, _body = client.message(
        "summarise the figure", annotation_ids=[annotation_id]
    )

    assert status in (200, 202), status
    assert _status(client, annotation_id) == "sent"


# --------------------------------------------------------------------------
# exactly-once admission, not at-most-once
# --------------------------------------------------------------------------


def test_an_already_sent_pin_does_not_re_enter_a_prompt(client):
    """`get_annotation` filtered no status, so a consumed pin came back."""
    annotation_id = _pin(client)
    status, _ = client.message("first", annotation_ids=[annotation_id])
    assert status in (200, 202), status
    assert _status(client, annotation_id) == "sent"

    seen: list = []
    real = client.runner.submit_message

    def spy(frame, project, text, *args, **kwargs):
        seen.append(text)
        return real(frame, project, text, *args, **kwargs)

    client.runner.submit_message = spy  # type: ignore[method-assign]
    try:
        client.message("second", annotation_ids=[annotation_id])
    finally:
        client.runner.submit_message = real  # type: ignore[method-assign]

    assert seen, "the second message never reached the runner"
    assert "look at this peak" not in seen[0], seen[0]


def test_a_repeated_id_is_carried_once(client):
    annotation_id = _pin(client)
    seen: list = []
    real = client.runner.submit_message

    def spy(frame, project, text, *args, **kwargs):
        seen.append(text)
        return real(frame, project, text, *args, **kwargs)

    client.runner.submit_message = spy  # type: ignore[method-assign]
    try:
        client.message(
            "go", annotation_ids=[annotation_id, annotation_id, annotation_id]
        )
    finally:
        client.runner.submit_message = real  # type: ignore[method-assign]

    assert seen[0].count("look at this peak") == 1, seen[0]


def test_two_concurrent_requests_do_not_both_carry_one_pin(client):
    """A reservation is one atomic UPDATE, so exactly one wins the row.

    Both threads are held at a barrier so they reserve as close to
    simultaneously as the runtime allows; without the atomic claim both read
    the same `open` row and both quote it.
    """
    import threading

    annotation_id = _pin(client)
    start = threading.Barrier(2)
    carried: list[str] = []
    lock = threading.Lock()
    real = client.runner.submit_message

    def spy(frame, project, text, *args, **kwargs):
        with lock:
            carried.append(text)
        return real(frame, project, text, *args, **kwargs)

    client.runner.submit_message = spy  # type: ignore[method-assign]

    def send(label):
        start.wait(timeout=5)
        client.message(label, annotation_ids=[annotation_id])

    threads = [threading.Thread(target=send, args=(f"turn-{n}",)) for n in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
    finally:
        client.runner.submit_message = real  # type: ignore[method-assign]

    with_pin = [text for text in carried if "look at this peak" in text]
    assert len(with_pin) == 1, f"{len(with_pin)} messages carried the same pin"
    assert _status(client, annotation_id) == "sent"


def test_a_finalize_failure_after_acceptance_is_not_reported_as_a_refusal(
    client, monkeypatch
):
    """The turn is running. Answering "not accepted" would make the client
    retry and send the work twice; the pins stay reserved, which is neither
    lost nor double-spent, and the answer says `pending` rather than claiming
    they were consumed."""
    annotation_id = _pin(client)

    def boom(reservation_id):
        raise RuntimeError("the store went away")

    monkeypatch.setattr(client.store, "finalize_annotations_sent", boom)
    status, _body = client.message("go", annotation_ids=[annotation_id])

    assert status in (200, 202), status
    assert _status(client, annotation_id) == "reserved"


def test_a_refused_message_releases_only_its_own_reservation(client):
    """Two pins, one carried by a refused message and one by nothing. The
    release must put back exactly what this request claimed."""
    mine = _pin(client)
    theirs = _pin(client, body="somebody else's pin")

    status, _ = client.message("x" * (8 * 1024 * 1024), annotation_ids=[mine])
    assert status == 413, status
    assert _status(client, mine) == "open"
    assert _status(client, theirs) == "open"


def test_an_id_that_is_not_a_string_never_reaches_the_query(client):
    """`annotation_ids` comes from a JSON body, so it can hold a number, a
    nested object, or anything else the client sends. A reservation must not
    pass one of those to the store as a query parameter."""
    annotation_id = _pin(client)
    status, _ = client.message(
        "go", annotation_ids=[annotation_id, 17, {"nested": "object"}, None, ""]
    )

    assert status in (200, 202), status
    assert _status(client, annotation_id) == "sent"


def test_the_accepted_answer_says_what_became_of_the_pins(client):
    """A 202 says "accepted, watch elsewhere", so it is the one place a client
    can learn whether its pins were consumed -- and whether it may retry."""
    annotation_id = _pin(client)
    status, body = client.message("go", annotation_ids=[annotation_id], wait=False)

    assert status == 202, (status, body)
    assert body["annotations"] == "sent"
    assert body["annotation_reservation_id"]


def test_a_message_with_no_pins_says_nothing_about_them(client):
    """An absent field and a field saying "none" are different claims; the
    second invites a client to reconcile something that never existed."""
    status, body = client.message("go", wait=False)
    assert status == 202, (status, body)
    assert "annotations" not in body
    assert "annotation_reservation_id" not in body


def test_a_pending_consume_is_reported_as_pending(client, monkeypatch):
    def boom(reservation_id):
        raise RuntimeError("the store went away")

    monkeypatch.setattr(client.store, "finalize_annotations_sent", boom)
    annotation_id = _pin(client)
    status, body = client.message("go", annotation_ids=[annotation_id], wait=False)

    assert status == 202, (status, body)
    assert body["annotations"] == "pending"
    # ...and the id it names is the one a reconcile would ask about.
    assert _status(client, annotation_id) == "reserved"


# --------------------------------------------------------------------------
# a reserved pin is held, and the public routes must respect that
# --------------------------------------------------------------------------


def _reserve(client, annotation_id, reservation_id="resv-test"):
    return client.store.reserve_annotations(
        root_frame_id=client.frame_id,
        annotation_ids=[annotation_id],
        reservation_id=reservation_id,
    )


def test_a_reserved_pin_cannot_be_edited_out_from_under_the_turn(client):
    """`PATCH /annotations/{id}` set `status` from the request body with no
    check at all, so a client could move a `reserved` row to `open` or `sent`
    while the turn holding it was still in flight -- breaking exactly-once from
    outside the admission path entirely."""
    annotation_id = _pin(client)
    assert _reserve(client, annotation_id)

    status, _body = client.post(f"/annotations/{annotation_id}", {"status": "open"})

    assert status == 409, status
    assert _status(client, annotation_id) == "reserved"


def test_a_reserved_pin_cannot_be_deleted_out_from_under_the_turn(client):
    annotation_id = _pin(client)
    assert _reserve(client, annotation_id)

    status, _body = client.post_method("DELETE", f"/annotations/{annotation_id}", {})

    assert status == 409, status
    assert client.store.get_annotation(annotation_id) is not None


def test_an_open_pin_is_still_editable_and_deletable(client):
    """The guard must be about the reservation, not about annotations."""
    annotation_id = _pin(client)
    status, _ = client.post(f"/annotations/{annotation_id}", {"body": "edited"})
    assert status == 200, status
    assert client.store.get_annotation(annotation_id)["body"] == "edited"

    status, _ = client.post_method("DELETE", f"/annotations/{annotation_id}", {})
    assert status == 200, status
    assert client.store.get_annotation(annotation_id) is None


def test_a_finalize_names_the_exact_set_it_reserved(client):
    """All-or-none over an *expected* set, not "whatever is still reserved".

    Finalising by reservation id alone would consume a row that something else
    had since attached to the same id, and would silently succeed having
    consumed fewer rows than the prompt quoted.
    """
    first = _pin(client)
    second = _pin(client, body="second pin")
    claimed = client.store.reserve_annotations(
        root_frame_id=client.frame_id,
        annotation_ids=[first, second],
        reservation_id="resv-exact",
    )
    assert len(claimed) == 2

    # One row disappears underneath the reservation.
    client.store._annotations._execute(
        "UPDATE annotations SET status='open', reservation_id=NULL "
        "WHERE annotation_id=?",
        (second,),
    )

    consumed = client.store.finalize_annotations_sent(
        "resv-exact", expected_ids=[first, second]
    )
    assert consumed is False, "a partial finalize reported success"
    # All-or-none: the surviving row is not consumed either.
    assert _status(client, first) == "reserved"


def test_a_zero_row_claim_reports_none_rather_than_sent(client):
    """A concurrent loser claims nothing. Saying `sent` would tell the client
    its pins were consumed by a message that never carried them."""
    annotation_id = _pin(client)
    assert _reserve(client, annotation_id, "resv-winner")

    status, body = client.message("go", annotation_ids=[annotation_id], wait=False)
    assert status == 202, (status, body)
    assert body.get("annotations") == "none", body
    # The winner still holds it.
    assert _status(client, annotation_id) == "reserved"


def test_a_reservation_cannot_be_released_from_another_frame(client, tmp_path):
    """Scoping is what makes a reservation id safe to accept from a caller.

    `release`/`finalize` keyed on the reservation id alone would let a request
    in one session free or consume a claim held in another -- the id is the
    only thing they check, and ids travel in responses.
    """
    mine = _pin(client)
    other_frame = client.store.new_frame(kind="turn", project_id="p")
    claimed = client.store.reserve_annotations(
        root_frame_id=client.frame_id,
        annotation_ids=[mine],
        reservation_id="resv-scoped",
    )
    assert claimed

    # A caller naming the right reservation but the wrong frame gets nothing.
    assert (
        client.store.release_annotations("resv-scoped", root_frame_id=other_frame) == 0
    )
    assert _status(client, mine) == "reserved"
    assert (
        client.store.finalize_annotations_sent(
            "resv-scoped", root_frame_id=other_frame, expected_ids=[mine]
        )
        is False
    )
    assert _status(client, mine) == "reserved"

    # ...and the owning frame still can.
    assert (
        client.store.finalize_annotations_sent(
            "resv-scoped", root_frame_id=client.frame_id, expected_ids=[mine]
        )
        is True
    )
    assert _status(client, mine) == "sent"


def test_a_reservation_id_is_unique_enough_to_be_a_key(client):
    """A short id would collide across sessions and across restarts, and the
    thing it keys is a claim on someone's unpublished comment."""
    seen = set()
    for index in range(50):
        annotation_id = _pin(client, body=f"pin {index}")
        _status_before = _status(client, annotation_id)
        status, body = client.message(
            f"turn {index}", annotation_ids=[annotation_id], wait=False
        )
        assert status == 202
        reservation = body["annotation_reservation_id"]
        assert reservation not in seen
        seen.add(reservation)
        assert len(reservation) >= 20, reservation


def test_the_waiting_branch_reports_the_same_admission_facts(client):
    """`wait:true` and `wait:false` are two projections of one turn.

    A client that waits must not be told less about its own pins than one that
    does not -- it is the branch a script uses, and the branch with no socket
    to reconcile from later.
    """
    annotation_id = _pin(client)
    status, body = client.message("go", annotation_ids=[annotation_id], wait=True)

    assert status in (200, 202), (status, body)
    assert body.get("annotations") == "sent", body
    assert body.get("annotation_reservation_id"), body


def test_the_waiting_branch_reports_pending_too(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("the store went away")

    monkeypatch.setattr(client.store, "finalize_annotations_sent", boom)
    annotation_id = _pin(client)
    status, body = client.message("go", annotation_ids=[annotation_id], wait=True)

    assert status in (200, 202), (status, body)
    assert body.get("annotations") == "pending", body
    assert _status(client, annotation_id) == "reserved"


# --------------------------------------------------------------------------
# the admission ledger: what a reconcile reads after a lost response
# --------------------------------------------------------------------------


def test_an_accepted_admission_is_recorded_with_its_correlation(client):
    """The 202 can be lost -- a dropped connection, a closed tab, a reload.

    The client then knows only that it sent *something*. Without a durable
    record tying the reservation to the request, the job and the message, there
    is nothing to reconcile against, and the only options are to resend (double
    work) or to abandon the pins (silent loss).
    """
    annotation_id = _pin(client)
    status, body = client.message("go", annotation_ids=[annotation_id], wait=False)
    assert status == 202

    record = client.store.get_admission(body["annotation_reservation_id"])
    assert record is not None, "the admission was not recorded"
    assert record["root_frame_id"] == client.frame_id
    assert record["state"] == "sent"
    assert record["job_id"] == body["job_id"]
    assert record["request_id"] == body["request_id"]
    assert record["annotation_ids"] == [annotation_id]


def test_a_reconcile_answers_what_happened_to_a_lost_response(client):
    """The recovery path, driven the way a reloaded browser would use it."""
    annotation_id = _pin(client)
    _status_code, body = client.message(
        "go", annotation_ids=[annotation_id], wait=False
    )
    reservation = body["annotation_reservation_id"]

    reconciled = client.runner.reconcile_admission(client.frame_id, reservation)
    assert reconciled["state"] == "sent"
    assert reconciled["annotations"] == [annotation_id]
    assert reconciled["request_id"] == body["request_id"]


def test_a_reconcile_from_another_frame_learns_nothing(client):
    """A reservation id is a value a client holds, so the lookup is scoped."""
    annotation_id = _pin(client)
    _status_code, body = client.message(
        "go", annotation_ids=[annotation_id], wait=False
    )
    other = client.store.new_frame(kind="turn", project_id="p")

    assert (
        client.runner.reconcile_admission(other, body["annotation_reservation_id"])
        is None
    )


def test_a_refused_admission_is_recorded_as_released(client):
    """A reconcile must be able to say "released, retry" as confidently as it
    says "sent, do not"."""
    annotation_id = _pin(client)
    status, _ = client.message("x" * (8 * 1024 * 1024), annotation_ids=[annotation_id])
    assert status == 413

    records = client.store.list_admissions(client.frame_id)
    assert records, "the refused admission left no trace to reconcile against"
    assert records[-1]["state"] == "released"
    assert _status(client, annotation_id) == "open"


def test_a_reservation_stranded_by_a_crash_is_recovered_at_startup(client, tmp_path):
    """A process that dies between reserve and finalize leaves `reserved` rows
    that nothing will ever release. On the next start they are neither sent nor
    available, and no live request holds them -- so recovery puts them back."""
    annotation_id = _pin(client)
    client.store.reserve_annotations(
        root_frame_id=client.frame_id,
        annotation_ids=[annotation_id],
        reservation_id="resv-crashed",
    )
    client.store.record_admission(
        reservation_id="resv-crashed",
        root_frame_id=client.frame_id,
        annotation_ids=[annotation_id],
        request_id="req-crashed",
        job_id="job-crashed",
        state="reserved",
    )
    assert _status(client, annotation_id) == "reserved"

    recovered = client.store.recover_stranded_admissions()

    assert recovered == 1, recovered
    assert _status(client, annotation_id) == "open"
    assert client.store.get_admission("resv-crashed")["state"] == "released"


def test_a_packaged_session_does_not_export_a_live_reservation(client, tmp_path):
    """A reservation belongs to a request in *this* process.

    Exporting `reserved` would hand a recipient a pin held by a turn that will
    never run on their machine -- permanently invisible in their composer, with
    no request to release it. A session package is a snapshot of what the work
    *is*, not of what one process was mid-way through doing.
    """
    annotation_id = _pin(client)
    client.store.reserve_annotations(
        root_frame_id=client.frame_id,
        annotation_ids=[annotation_id],
        reservation_id="resv-packaged",
    )
    assert _status(client, annotation_id) == "reserved"

    exported = client.store.list_annotations(client.frame_id)
    row = next(r for r in exported if r["annotation_id"] == annotation_id)
    # The raw row still carries it -- this is the store, not the projection.
    assert row["status"] == "reserved"

    from openai4s.server.session_package import package_annotation

    projected = package_annotation(row)
    assert projected["status"] == "open", projected
    assert "reservation_id" not in projected, projected


def test_a_checkpoint_restores_a_reserved_pin_as_open(client):
    """Restoring mid-flight state would recreate a claim whose holder is gone.

    A checkpoint is taken at one instant and applied at another; the request
    that held the reservation does not survive the gap, so the only safe
    restoration is the state a user can act on.
    """
    from openai4s.server.session_package import package_annotation

    annotation_id = _pin(client)
    client.store.reserve_annotations(
        root_frame_id=client.frame_id,
        annotation_ids=[annotation_id],
        reservation_id="resv-checkpoint",
    )
    row = client.store.get_annotation(annotation_id)

    restored = package_annotation(row)
    assert restored["status"] == "open"
    # A pin already consumed stays consumed: it is a fact about a turn that
    # really happened, not about an in-flight request.
    client.store.finalize_annotations_sent(
        "resv-checkpoint", root_frame_id=client.frame_id, expected_ids=[annotation_id]
    )
    assert (
        package_annotation(client.store.get_annotation(annotation_id))["status"]
        == "sent"
    )


# --------------------------------------------------------------------------
# consistency across connections, not within one Store instance
# --------------------------------------------------------------------------
#
# `Store` holds a re-entrant lock per instance and the daemon has more than one
# instance against one file, so "we took the lock" is not a claim about the
# database. Each test below uses two Stores on one path, which is the shape the
# daemon actually has.


def _second_store(client):
    from openai4s.store import Store

    return Store(client.store.db_path)


def _pin_on(store, frame, body="pin"):
    return store.add_annotation(
        root_frame_id=frame,
        artifact_id="a-1",
        artifact_name="p.png",
        rel_x=0.5,
        rel_y=0.5,
        body=body,
    )["annotation_id"]


def test_an_edit_cannot_race_a_reservation_taken_on_another_connection(client):
    """Check-then-write is a TOCTOU window, and a real one.

    The route asked `annotation_is_reserved()` and then updated by
    `annotation_id` alone. A reservation taken in between produced a row with
    `status='open'` and `reservation_id` still set -- a pin the composer offers
    the user while a turn is quoting it.
    """
    other = _second_store(client)
    try:
        annotation_id = _pin(client)
        other.reserve_annotations(
            root_frame_id=client.frame_id,
            annotation_ids=[annotation_id],
            reservation_id="resv-raced",
        )
        # The write the route would have issued after its check passed.
        assert client.store.update_annotation(annotation_id, status="open") is None
        row = other.get_annotation(annotation_id)
        assert row["status"] == "reserved"
        assert row["reservation_id"] == "resv-raced"
    finally:
        other.close()


def test_a_delete_cannot_race_a_reservation_taken_on_another_connection(client):
    other = _second_store(client)
    try:
        annotation_id = _pin(client)
        other.reserve_annotations(
            root_frame_id=client.frame_id,
            annotation_ids=[annotation_id],
            reservation_id="resv-raced-delete",
        )
        assert client.store.delete_unreserved_annotation(annotation_id) is False
        assert other.get_annotation(annotation_id) is not None
    finally:
        other.close()


@pytest.mark.parametrize("status", ["reserved", "banana", "SENT", ""])
def test_a_public_edit_cannot_invent_a_status(client, status):
    """`reserved` is entered only by `reserve`, which sets the id in the same
    statement. A PATCH able to write it would make a held row with no holder --
    invisible in the composer and released by nothing."""
    annotation_id = _pin(client)
    with pytest.raises(ValueError):
        client.store.update_annotation(annotation_id, status=status)
    assert _status(client, annotation_id) == "open"


def test_a_failed_finalize_changes_zero_rows(client):
    """All-or-none means none, including across connections.

    A `SELECT` then an `UPDATE` under a per-instance lock is not a transaction:
    measured on two Stores, a row moved in between and finalize returned False
    having already sent the other one. The invariant is that a mismatch leaves
    the database exactly as it was.
    """
    other = _second_store(client)
    try:
        first = _pin(client, body="one")
        second = _pin(client, body="two")
        client.store.reserve_annotations(
            root_frame_id=client.frame_id,
            annotation_ids=[first, second],
            reservation_id="resv-zero",
        )
        # A second connection frees one of them.
        other.release_annotations("resv-zero", root_frame_id=client.frame_id)
        client.store.reserve_annotations(
            root_frame_id=client.frame_id,
            annotation_ids=[first],
            reservation_id="resv-zero",
        )

        assert (
            client.store.finalize_annotations_sent(
                "resv-zero",
                root_frame_id=client.frame_id,
                expected_ids=[first, second],
            )
            is False
        )
        # Zero rows changed: the one still held is still held, not sent.
        assert other.get_annotation(first)["status"] == "reserved"
        assert other.get_annotation(second)["status"] == "open"
    finally:
        other.close()


def test_one_reservation_id_in_two_frames_stays_isolated(client):
    """The read-back after the claim was not frame-scoped, so a second session
    using the same id read *both* frames' rows and quoted another session's
    pins."""
    other_frame = client.store.new_frame(kind="turn", project_id="p")
    mine = _pin(client, body="mine")
    theirs = _pin_on(client.store, other_frame, "theirs")

    client.store.reserve_annotations(
        root_frame_id=client.frame_id,
        annotation_ids=[mine],
        reservation_id="resv-collide",
    )
    claimed = client.store.reserve_annotations(
        root_frame_id=other_frame,
        annotation_ids=[theirs],
        reservation_id="resv-collide",
    )

    assert [row["annotation_id"] for row in claimed] == [theirs]
    # ...and releasing one frame's claim leaves the other's alone.
    assert (
        client.store.release_annotations("resv-collide", root_frame_id=other_frame) == 1
    )
    assert _status(client, mine) == "reserved"


def test_a_finalize_interleaved_by_another_connection_is_still_all_or_none(client):
    """The transaction, as distinct from the exact-set check.

    A mismatch arranged *before* the call is caught by comparing the held set
    to the expected one -- no transaction required. What needs the transaction
    is an interleave: a second connection writing between the `SELECT` that
    reads the set and the `UPDATE` that consumes it. Measured on the
    lock-only version, that produced `returned=False` with one row already
    `sent`: a failure that changed rows.

    The clock is the hook because `finalize_sent` calls it between those two
    statements, which is exactly the window.
    """
    import threading

    other = _second_store(client)
    try:
        first = _pin(client, body="one")
        second = _pin(client, body="two")
        client.store.reserve_annotations(
            root_frame_id=client.frame_id,
            annotation_ids=[first, second],
            reservation_id="resv-interleave",
        )

        reached = threading.Event()
        proceed = threading.Event()
        real_clock = client.store._annotations._clock_ms

        def hooked():
            if not reached.is_set():
                reached.set()
                proceed.wait(2)
            return real_clock()

        client.store._annotations._clock_ms = hooked

        def interfere():
            if reached.wait(2):
                try:
                    other.release_annotations(
                        "resv-interleave", root_frame_id=client.frame_id
                    )
                except Exception:
                    pass
                proceed.set()

        helper = threading.Thread(target=interfere, daemon=True)
        helper.start()
        try:
            returned = client.store.finalize_annotations_sent(
                "resv-interleave",
                root_frame_id=client.frame_id,
                expected_ids=[first, second],
            )
        finally:
            proceed.set()
            helper.join(timeout=10)
            client.store._annotations._clock_ms = real_clock

        states = [other.get_annotation(x)["status"] for x in (first, second)]
        sent = states.count("sent")
        # All-or-none, and the answer has to match what happened. The weaker
        # form of this ("a False result changed no rows") missed the mutation
        # that mattered: without the transaction the interleave leaves ONE row
        # sent and the call reports success, which is a partial consume
        # announced as a complete one.
        assert sent in (0, 2), f"a partial consume: states={states}"
        assert (returned is True) == (sent == 2), (
            f"the answer disagrees with the database: "
            f"returned={returned} states={states}"
        )
    finally:
        other.close()
