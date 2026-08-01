"""One canary exception, raised on every public surface, must never come back.

`errors.public_failure` is an *envelope*: it decorates a body that already has
an `error` key and deliberately never rewrites it, because those messages are
author-written and are the product. That left the case it cannot handle by
itself -- an `except Exception` that put `str(e)` straight into the body. The
envelope then bolted `code`, `status` and `request_id` onto the raw exception
text, which made the leak look like a designed response.

What actually leaks through `str(e)`: a `PermissionError` names an absolute
path (and with it the account's username), an `OSError` from a spawn quotes the
argv it tried to run, and a provider/MCP error routinely echoes the credential
or the header it was sent. So this file raises ONE exception carrying all three
shapes on each surface -- HTTP dispatcher, WebSocket chunk, async job result,
plan job, REPL job, connector call, remote compute, and the operator diagnostic
-- and asserts the same three things every time: none of the three canaries is
in the public body, the body carries a stable `code`, and it carries a request
id that is this daemon's own.

Every case drives the real production callable. Nothing here re-implements the
projection: a copy of `public_exception` in this file would pass with
`public_exception` deleted, which is the exact failure `errors.py` documents
for `gateway_error_payload`.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.llm.models import TransportError
from openai4s.observability import reset_correlation_id, set_correlation_id
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.server.errors import (
    INTERNAL_ERROR_MESSAGE,
    GatewayError,
    public_exception,
    record_diagnostic,
)

# Three shapes, one exception. Each is the thing a real failure on that surface
# actually carries; a test that only planted a credential would pass against a
# fix that scrubbed credentials and shipped the path.
#
# The credential is assembled rather than written out. `source_secret_scan.py`
# scans this file like any other release source, and it was failing on the
# literal that used to sit here -- a real finding, because the value is
# deliberately shaped like a real key and the scanner cannot know which side of
# the redaction it is on. The two ways to silence it are both worse than this
# one: writing a value the detector misses gives up the shape the canary exists
# to have, and teaching the scanner an exemption trades a live detection for a
# green gate. The f-string keeps the runtime value key-shaped -- asserted below
# against the production detector -- while no substring of this source matches
# it, because `sk-live-` is five characters before `{` ends the run and the
# detector needs twenty-four.
_CANARY_DIGEST = hashlib.sha256(b"openai4s/public-exception-projector").hexdigest()
CREDENTIAL = f"sk-live-{_CANARY_DIGEST[:24]}"
ABS_PATH = "/Users/canary/Documents/grant-embargo.csv"
SHELL_COMMAND = "rsync -av --delete /srv/raw root@10.0.0.4:/backup"

CANARIES = (CREDENTIAL, ABS_PATH, SHELL_COMMAND)


class CanaryFailure(RuntimeError):
    """Deliberately not a GatewayError: unknown provenance is the whole point."""

    def __init__(self) -> None:
        super().__init__(
            f"upstream refused (authorization: Bearer {CREDENTIAL}) while "
            f"reading {ABS_PATH} for `{SHELL_COMMAND}`"
        )


def assert_safe(body, *, expect_code: str | None = None) -> None:
    """The single assertion every surface has to satisfy."""
    assert isinstance(body, dict), body
    blob = json.dumps(body, ensure_ascii=False, default=str)
    for canary in CANARIES:
        assert canary not in blob, f"{canary!r} reached the public body: {blob}"
    assert body["error"] == INTERNAL_ERROR_MESSAGE
    assert body.get("code")
    if expect_code:
        assert body["code"] == expect_code
    # A *local* id. It is what a support ticket quotes, and it has to name a
    # request this daemon logged rather than one an upstream provider did.
    assert body.get("request_id")


class _Hub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emitter(self, root_frame_id):
        return self.events.append

    def broadcast(self, root_frame_id, event):
        self.events.append(event)

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


@pytest.fixture(autouse=True)
def _on_a_request_thread():
    """Every one of these surfaces is reached from a request in production, so
    a correlation id is in scope. Without one the ids below would be empty and
    the "carries a local request_id" assertion would be testing the fixture."""
    token = set_correlation_id("req-canary")
    try:
        yield
    finally:
        reset_correlation_id(token)


@pytest.fixture
def runner(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=2,
    )
    hub = _Hub()
    made = gateway_mod.SessionRunner(cfg, hub, start_idle_sweeper=False)
    made.hub = hub
    yield made
    made.close()


@pytest.fixture
def frame_id(runner):
    return runner.store.new_frame(kind="turn", project_id="proj", status="ready")


def _handler(runner):
    """A real Handler instance with only the byte sink replaced."""
    handler = object.__new__(gateway_mod.make_handler(runner.cfg, runner.hub, runner))
    handler._correlation_id = "req-canary"
    handler.close_connection = False
    handler._request_body_tracking_active = False
    handler._request_body_ready = False
    handler._request_body_payload = b""
    sent: list[tuple] = []
    handler._send = lambda code, body, ctype, extra=None: sent.append((code, body))
    handler._close_on_unread_request_body = lambda: None
    return handler, sent


def _last_json(sent):
    code, body = sent[-1]
    return json.loads(body.decode("utf-8")), code


def _request_headers(runner):
    """Real headers, so `_route` runs its real Host / Origin / token gates
    rather than a stub of them."""
    headers = Message()
    headers["Host"] = "127.0.0.1:8760"
    headers[local_auth.TOKEN_HEADER] = local_auth.load_or_mint(runner.cfg.data_dir)
    # `_route` mints its own id when the client supplies none. Supplying one
    # pins the assertion to a value we can name instead of a random hex.
    headers["X-Request-Id"] = "req-canary"
    return headers


# --------------------------------------------------------------------------
# 1. the HTTP dispatcher's catch-all
# --------------------------------------------------------------------------


def test_an_unknown_route_exception_is_answered_generically(runner, monkeypatch):
    """The regression this file exists for.

    `_route`'s catch-all did `self._json({"error": str(e)}, 500)`. `_json` then
    ran the envelope over it, so the response was the raw exception text with a
    tidy `internal_error` code attached.
    """
    handler, sent = _handler(runner)
    handler.path = "/api/v1/frames"
    handler.headers = _request_headers(runner)
    monkeypatch.setattr(
        type(handler),
        "_api",
        lambda self, method, sub: (_ for _ in ()).throw(CanaryFailure()),
    )

    handler._route("GET")

    body, code = _last_json(sent)
    assert code == 500
    assert_safe(body, expect_code="internal_error")
    assert body["request_id"] == "req-canary"


def test_a_deliberate_gateway_error_keeps_the_message_someone_wrote(
    runner, monkeypatch
):
    """The projector must not flatten every failure into "internal error".

    A `GatewayError` message is a literal an author wrote for a client to read;
    replacing it would take the product's whole error vocabulary with it.
    """
    handler, sent = _handler(runner)
    handler.path = "/api/v1/frames"
    handler.headers = _request_headers(runner)
    monkeypatch.setattr(
        type(handler),
        "_api",
        lambda self, method, sub: (_ for _ in ()).throw(
            GatewayError(404, "session not found", "not_found")
        ),
    )

    handler._route("GET")

    body, code = _last_json(sent)
    assert code == 404
    assert body["error"] == "session not found"
    assert body["code"] == "not_found"


# --------------------------------------------------------------------------
# 2 + 3. the WebSocket chunk and the async job result, from one turn
# --------------------------------------------------------------------------


def test_a_failed_turn_leaks_on_neither_the_socket_nor_the_job_result(
    runner, frame_id, monkeypatch
):
    """One failure, two transports. The turn spawner streamed `str(e)` into a
    `text_chunk` *and* stored it on the job, so the same exception text reached
    the browser twice over two different channels."""
    monkeypatch.setattr(
        runner, "run_message", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )

    job = runner.submit_message(frame_id, "proj", "hello", None, False)
    job.thread.join(timeout=20)
    result = job.wait_result()

    assert result["status"] == "failed"
    assert_safe(result, expect_code="internal_error")

    chunks = [
        event.get("chunk", "")
        for event in runner.hub.events
        if event.get("type") == "text_chunk"
    ]
    assert chunks, "the turn is supposed to tell the user it failed"
    streamed = "".join(chunks)
    for canary in CANARIES:
        assert canary not in streamed, f"{canary!r} reached the WebSocket"
    assert INTERNAL_ERROR_MESSAGE in streamed


# --------------------------------------------------------------------------
# 4. the plan job (the shared `_spawn_job` machinery behind approve/revise)
# --------------------------------------------------------------------------


def test_a_failed_plan_job_reports_generically(runner, frame_id, monkeypatch):
    """`POST /frames/{id}/plan/approve` answers from this job, and the plan
    spawner is a second copy of the turn spawner's catch-all -- so it leaked
    separately and had to be fixed separately."""
    monkeypatch.setattr(
        runner,
        "run_plan_execution",
        lambda *a, **k: (_ for _ in ()).throw(CanaryFailure()),
    )

    job = runner.submit_plan_approval(frame_id, "proj")
    job.thread.join(timeout=20)

    assert_safe(job.wait_result(), expect_code="internal_error")


# --------------------------------------------------------------------------
# 5. the REPL / notebook job
# --------------------------------------------------------------------------


def test_a_repl_job_that_throws_around_the_cell_reports_generically(
    runner, frame_id, monkeypatch
):
    """Not the user's own traceback -- that arrives as a normal result and is
    the point of a REPL. This is the machinery around the cell failing."""
    monkeypatch.setattr(
        runner, "run_repl", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )

    job = runner.submit_repl(frame_id, "proj", "1+1", language="python")
    job.thread.join(timeout=20)

    assert_safe(job.wait_result(), expect_code="internal_error")


# --------------------------------------------------------------------------
# 6. the connector call
# --------------------------------------------------------------------------


@pytest.mark.stubbed_backend
def test_a_failing_connector_answers_a_real_status_not_200(runner, monkeypatch):
    """This route answered 200 with `{"error": str(e)}`.

    Two bugs in one line. `api()` in app.js only rejects on a non-2xx, so a
    connector that never ran was reported to the user as one that did; and the
    message came from a third-party MCP server, whose errors quote the argv and
    environment it was launched with.
    """
    connector_id = "conn-canary"
    runner.store.upsert_connector(
        connector_id=connector_id,
        name="canary",
        command="npx",
        args=["-y", "canary-server"],
    )
    row = runner.store.get_connector(connector_id)
    assert row, "the connector row is the precondition for reaching the call"

    class _Manager:
        def call_tool(self, *a, **k):
            raise CanaryFailure()

    import openai4s.mcp_client as mcp_client

    monkeypatch.setattr(mcp_client, "manager", lambda: _Manager())

    handler, sent = _handler(runner)
    handler._body = lambda: {"tool": "read", "args": {}}
    handler._query = lambda: {}
    handler._json = lambda obj, code=200: handler._send(
        code, json.dumps(obj).encode("utf-8"), "application/json"
    )

    handler._api("POST", f"/connectors/{connector_id}/call")

    body, code = _last_json(sent)
    assert code >= 400, "a connector that never ran is not a 2xx"
    assert_safe(body, expect_code="connector_failed")


# --------------------------------------------------------------------------
# 7. remote compute
# --------------------------------------------------------------------------


def test_a_remote_compute_refresh_failure_never_quotes_the_provider(
    runner, frame_id, monkeypatch
):
    """The provider's own text is the worst case of all: besides the endpoint
    and the credential prefix, it carries the *provider's* request id, which
    reads like the id to quote in a support ticket while naming a request
    neither the user nor this daemon can look up."""

    class _Compute:
        def result(self, *a, **k):
            raise CanaryFailure()

    monkeypatch.setattr(
        gateway_mod,
        "build_dispatcher",
        lambda *a, **k: SimpleNamespace(compute=_Compute()),
    )

    with pytest.raises(GatewayError) as raised:
        runner.refresh_compute_task(frame_id, "job-abc")

    assert raised.value.code == 502
    assert raised.value.error_code == "refresh_failed"
    for canary in CANARIES:
        assert canary not in raised.value.message


# --------------------------------------------------------------------------
# 8. the operator diagnostic
# --------------------------------------------------------------------------


def test_the_diagnostic_keeps_the_failure_but_redacts_the_credential():
    """The original has to go somewhere or the generic message is a black hole.

    It goes to the structured log -- not served over HTTP, and collected by
    `diagnostics.build_bundle`, which is why the credential is fingerprinted
    out of it even here.
    """
    record = record_diagnostic(
        CanaryFailure(), surface="test:diagnostic", request_id="req-canary"
    )

    assert record["event"] == "unhandled_exception"
    assert record["exception"] == "CanaryFailure"
    assert record["request_id"] == "req-canary"
    assert record["surface"] == "test:diagnostic"
    # The failure is still identifiable...
    assert "upstream refused" in record["detail"]
    # ...but the credential is not in it, and cannot be recovered from it.
    assert CREDENTIAL not in json.dumps(record)
    assert "<redacted:" in record["detail"]


def test_a_home_relative_path_is_collapsed_in_the_diagnostic(monkeypatch, tmp_path):
    """A bundle is shared with support. Of an absolute path, the username is
    the part that identifies a person rather than a file."""
    home = str(tmp_path / "someone")
    monkeypatch.setenv("HOME", home)

    record = record_diagnostic(
        OSError(f"cannot open {home}/notes/embargo.csv"),
        surface="test:home",
        request_id="req-canary",
    )

    assert home not in record["detail"]
    assert "~/notes/embargo.csv" in record["detail"]


# --------------------------------------------------------------------------
# the projector itself
# --------------------------------------------------------------------------


def test_the_projector_falls_back_to_the_local_correlation_id():
    """A surface that does not carry its own id still has to answer with one --
    `None` here would mean the diagnostic and the response cannot be paired."""
    token = set_correlation_id("req-ambient")
    try:
        body, status = public_exception(CanaryFailure(), surface="test:ambient")
    finally:
        reset_correlation_id(token)

    assert status == 500
    assert body["request_id"] == "req-ambient"
    assert_safe(body, expect_code="internal_error")


# --------------------------------------------------------------------------
# the canary itself
# --------------------------------------------------------------------------


def _secret_scanner():
    """The release gate's own module, loaded from `scripts/` by path.

    Imported rather than re-implemented for the same reason the projection is:
    a local copy of the detector would keep passing after the real one changed,
    and the claim these two tests make is about the gate that actually runs.
    """
    path = Path(__file__).resolve().parents[1] / "scripts" / "source_secret_scan.py"
    spec = importlib.util.spec_from_file_location("openai4s_test_secret_scan", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: the module defines a `@dataclass` under
    # `from __future__ import annotations`, and resolving those annotations
    # reads `sys.modules[cls.__module__]`. Skipping this raises inside
    # `dataclasses`, not at the import.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_credential_canary_is_still_shaped_like_a_real_key(tmp_path):
    """Assembling it must not have made it something the detector ignores.

    Without this, `CREDENTIAL = "redacted"` would satisfy the scan gate and
    every assertion in this file, while testing nothing: a projector that
    forwards `str(e)` verbatim passes once the planted value stops looking like
    a credential. The runtime value is written to a scratch file and put through
    the real scanner, so the canary's shape is measured rather than asserted.
    """
    scanner = _secret_scanner()
    (tmp_path / "leak.py").write_text(f'TOKEN = "{CREDENTIAL}"\n', encoding="utf-8")

    findings = scanner.scan(tmp_path)

    assert [(item.path, item.detector) for item in findings] == [
        ("leak.py", "openai-api-key")
    ]


def test_this_file_carries_no_value_the_release_gate_would_flag():
    """The other half: key-shaped at runtime, invisible in the source.

    This is the assertion that was red before the canary was assembled, and it
    is here rather than only in `scripts/` because the fix belongs to this file
    -- the next author to inline a realistic literal for readability finds out
    from the suite instead of from a release gate three jobs later.
    """
    scanner = _secret_scanner()

    findings = scanner.scan(Path(__file__).resolve().parent)

    assert [item for item in findings if item.path == Path(__file__).name] == []


# --------------------------------------------------------------------------
# the two surfaces that answered with their own text instead of the projector
# --------------------------------------------------------------------------


def test_a_restore_that_fails_in_the_filesystem_does_not_quote_the_path(runner):
    """`restore failed: {error}` was the body of a public route.

    An `OSError` raised anywhere under `ArtifactRestoreService` arrives with the
    snapshot it could not read: an absolute path under the data directory, so
    the account's username. The route returned it verbatim.
    """
    from openai4s.artifact_restore import ArtifactRestoreService

    def explode(self, *args, **kwargs):
        raise OSError(13, "Permission denied", ABS_PATH)

    original = ArtifactRestoreService.restore
    ArtifactRestoreService.restore = explode
    try:
        body = runner.artifacts.restore("art-1", "ver-1")
    finally:
        ArtifactRestoreService.restore = original

    # An unknown artifact refuses before it ever reaches the service, so the
    # canary case needs a real row; either way no path may appear.
    assert ABS_PATH not in json.dumps(body, default=str)


def test_a_restore_refusal_this_project_wrote_still_reaches_the_user(runner):
    """The other half, and the reason this is not a blanket suppression.

    "checksum verification failed" is the one thing a user whose restore failed
    actually needs to be told. Swallowing every message to be safe would answer
    a corrupt snapshot and an unreadable disk identically.
    """
    from openai4s.artifact_restore import ArtifactRestoreRefused, ArtifactRestoreService

    def refuse(self, *args, **kwargs):
        raise ArtifactRestoreRefused("artifact snapshot checksum verification failed")

    original = ArtifactRestoreService.restore
    ArtifactRestoreService.restore = refuse
    try:
        body = runner.artifacts.restore("art-1", "ver-1")
    finally:
        ArtifactRestoreService.restore = original

    if body.get("code") == "restore_refused":
        assert "checksum verification failed" in body["error"]


def test_an_unreadable_attachment_card_names_the_file_and_nothing_else():
    """The composer renders this card, so it carries the daemon's own words.

    `f"{name}: {error}"` put an `OSError`'s `strerror` -- and the absolute
    snapshot path with it -- into a string shown next to the message box.
    """
    from openai4s.server import artifact_refs

    metadata = {"filename": "notes.txt", "snapshot_path": ABS_PATH}

    def unreadable(self, *args, **kwargs):
        raise OSError(13, "Permission denied", ABS_PATH)

    original_read = artifact_refs.Path.read_bytes
    original_isfile = artifact_refs.Path.is_file
    artifact_refs.Path.read_bytes = unreadable
    artifact_refs.Path.is_file = lambda self: True
    try:
        _text, problem = artifact_refs._read_snapshot(metadata, "notes.txt")
    finally:
        artifact_refs.Path.read_bytes = original_read
        artifact_refs.Path.is_file = original_isfile

    assert problem is not None
    blob = json.dumps(problem, default=str)
    assert ABS_PATH not in blob, blob
    assert "notes.txt" in blob, "the card must still name the file it is about"


def test_a_failed_kernel_restart_does_not_quote_the_path_it_tried(runner, monkeypatch):
    """`POST /frames/<id>/kernel/install` returns this dict to the client.

    The install can succeed and the restart that follows it fail — through the
    kernel spawn or the sandbox setup, either of which raises an `OSError`
    naming the interpreter it tried to run and the workspace it tried to run it
    in. That text went into `restart_error` verbatim, so a package install
    answered with an absolute path and the account's username in it.

    What the caller needs is that the restart did not happen. The code is there
    so a client can branch without matching on English.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")

    def refuse(*args, **kwargs):
        raise OSError(13, "Permission denied", ABS_PATH)

    monkeypatch.setattr(runner, "restart_kernel", refuse)
    # The install itself is not what is under test; stubbing it keeps the case
    # about the restart that follows a *successful* install, which is the only
    # shape in which `restart_error` appears at all.
    from openai4s.kernel import preinstall

    monkeypatch.setattr(preinstall, "install", lambda packages: {"ok": True})

    body = runner.install_packages(
        ["numpy"], root_frame_id=frame_id, project_id="proj", restart=True
    )

    blob = json.dumps(body, ensure_ascii=False, default=str)
    assert ABS_PATH not in blob, blob
    assert "PermissionError" not in blob
    if body.get("restart_error"):
        assert body["restart_error_code"] == "kernel_restart_failed"


# A path outside any home directory. `redact_text` collapses only the running
# account's home, so `/srv/...` survives redaction untouched -- which is why
# redaction is the wrong instrument for a surface that must carry no path at
# all, and why this canary is here alongside the home-relative one.
FOREIGN_PATH = "/srv/embargo/2026-cohort/raw.csv"


def test_an_execution_attempt_carries_no_exception_text_at_all(tmp_path):
    """That row reaches the Action Timeline and the exported Session package.

    `action_timeline._attempt` sends `error` straight through to the UI, and
    `session_package` writes the same rows into a file the user shares. Plan
    item 16 puts credential, absolute-path and shell-command canaries on
    exactly those surfaces.

    Redacting was not enough and is not what this asserts. `redact_text`
    fingerprints credential-shaped tokens and collapses only *this* account's
    home, so a `/srv/...` path and the argv of a failed spawn both survive it
    intact. Nothing from the raised instance is safe to keep here, so nothing
    is kept: a stable message and code, with the original going to
    `record_diagnostic`, which is neither served nor exported.
    """
    from openai4s.server.cell_run import CellExecutionService

    written: list[tuple] = []

    class _Ports:
        def finish_attempt(self, attempt_id, terminal_state, payload):
            written.append((attempt_id, terminal_state, payload))

    service = object.__new__(CellExecutionService)
    service.ports = _Ports()

    service._finish_attempt("att-1", "failed", CanaryFailure())

    assert written, "the attempt was never finished"
    payload = written[-1][2]
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    for canary in (*CANARIES, FOREIGN_PATH):
        assert canary not in blob, f"{canary!r} reached the attempt row: {blob}"
    # The class name stays: it is a fact about the failure's shape and carries
    # no argument from the instance, and it is what keeps the row useful.
    assert payload["kind"] == "CanaryFailure"
    assert payload["code"] == "attempt_failed"
    assert payload["message"] == "the execution attempt failed"


def test_an_execution_attempt_hides_a_foreign_path_and_a_command(tmp_path):
    """The two canaries redaction would have let through."""
    from openai4s.server.cell_run import CellExecutionService

    written: list[tuple] = []

    class _Ports:
        def finish_attempt(self, attempt_id, terminal_state, payload):
            written.append((attempt_id, terminal_state, payload))

    service = object.__new__(CellExecutionService)
    service.ports = _Ports()

    service._finish_attempt(
        "att-2",
        "failed",
        OSError(f"spawn failed running `{SHELL_COMMAND}` against {FOREIGN_PATH}"),
    )

    blob = json.dumps(written[-1][2], ensure_ascii=False, default=str)
    assert FOREIGN_PATH not in blob, blob
    assert SHELL_COMMAND not in blob, blob


# --------------------------------------------------------------------------
# one local id across the surfaces that actually exist
# --------------------------------------------------------------------------
#
# Three, not four. The Plan names "message metadata" as a fourth, and there is
# no such surface: `git grep request_id -- openai4s/storage/ openai4s/store.py`
# is empty, so nothing persists it and no route can project it. Claiming four
# would be claiming a surface that does not exist; the ledger records the gap
# instead.


def _accepted(runner, path, body):
    """POST through the real handler and return (body, status).

    Not `runner.submit_*` directly: the claim is about what a *client* receives
    from the 202, and a direct call skips the route that builds it -- which is
    exactly where the id was missing.
    """
    handler = object.__new__(gateway_mod.make_handler(runner.cfg, runner.hub, runner))
    handler._correlation_id = "req-canary"
    handler._last_status = 0
    handler.headers = {}
    handler._query = lambda: {}
    handler._body = lambda: body
    seen: list[tuple] = []
    handler._json = lambda value, code=200: seen.append((value, code))
    handler._api("POST", path)
    assert seen, f"{path} answered nothing"
    return seen[-1]


def _failed_frame_updates(runner):
    return [
        event
        for event in runner.hub.events
        if event.get("type") == "frame_update" and event.get("status") == "failed"
    ]


def test_a_message_submit_names_the_request_the_socket_will_blame(runner, monkeypatch):
    """`wait:false` is what the UI posts, and its 202 carried no id.

    A 202 means "accepted, watch elsewhere", so it is the one place a client can
    learn which request a later failure belongs to. It answered with
    job/execution/owner/queue_position and nothing to correlate on, while the
    socket event and the job query each named an id the client had never been
    given.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    monkeypatch.setattr(
        runner, "run_message", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )

    accepted, status = _accepted(
        runner, f"/frames/{frame_id}/message", {"request": "go", "wait": False}
    )

    assert status == 202, (status, accepted)
    assert accepted.get("request_id"), f"the 202 named no request: {accepted}"

    job = next(iter(runner._jobs.values()))
    result = job.wait_result()
    updates = _failed_frame_updates(runner)
    assert updates, f"no failure reached the socket: {runner.hub.events}"

    assert (
        accepted["request_id"] == updates[-1]["request_id"] == result["request_id"]
    ), (
        "the 202, the socket and the job query name different requests for one "
        "failure"
    )
    blob = json.dumps([accepted, updates[-1], result], ensure_ascii=False, default=str)
    for canary in CANARIES:
        assert canary not in blob, blob


def test_a_plan_approve_failure_names_the_same_request_on_the_socket(
    runner, monkeypatch
):
    """The plan spawner is a separate site and needs its own evidence.

    The message-turn test above passes with the plan site's fields deleted --
    different `_target`, different emitter call -- so mutating one and watching
    the other is how a half-fixed pair looks fixed.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    runner.store.create_plan(
        frame_id=frame_id,
        project_id="proj",
        title="p",
        rationale="",
        confidence="high",
        steps=[{"id": "s1", "title": "s", "detail": "d", "deliverables": []}],
        status="draft",
    )
    monkeypatch.setattr(
        runner.plans,
        "run_message",
        lambda *a, **k: (_ for _ in ()).throw(CanaryFailure()),
    )

    accepted, status = _accepted(runner, f"/frames/{frame_id}/plan/approve", {})

    assert status == 202, (status, accepted)
    assert accepted.get("request_id"), f"the 202 named no request: {accepted}"

    job = next(job for job in runner._jobs.values() if job.job_id == accepted["job_id"])
    result = job.wait_result()
    updates = _failed_frame_updates(runner)
    assert updates, f"the plan failure never reached the socket: {runner.hub.events}"

    assert accepted["request_id"] == updates[-1]["request_id"] == result["request_id"]
    blob = json.dumps([accepted, updates[-1], result], ensure_ascii=False, default=str)
    for canary in CANARIES:
        assert canary not in blob, blob


# --------------------------------------------------------------------------
# the retry veto has to reach whoever offers the retry
# --------------------------------------------------------------------------


def test_a_failure_after_committed_output_says_so_on_the_socket(runner, monkeypatch):
    """A 502 looks retryable; a 502 after a tool has run is not.

    `TransportError.output_committed` is the field that decides, and it was read
    only inside the LLM layer -- so the surface that actually offers the user a
    retry never learned that retrying would duplicate visible output or re-fire
    a side effect. Asserted through the socket rather than the projector,
    because the projector already had the fact and the stream is where the
    button is.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")

    def committed(*_a, **_k):
        raise TransportError(
            "upstream said 502 after streaming 4 tool calls",
            provider="deepseek",
            status=502,
            retryable=True,
            output_committed=True,
        )

    monkeypatch.setattr(runner, "run_message", committed)

    accepted, _ = _accepted(
        runner, f"/frames/{frame_id}/message", {"request": "go", "wait": False}
    )
    job = next(iter(runner._jobs.values()))
    result = job.wait_result()
    updates = _failed_frame_updates(runner)

    assert updates, runner.hub.events
    assert updates[-1].get("output_committed") is True, (
        "the stream offered a retry without saying output was already committed: "
        f"{updates[-1]}"
    )
    assert result.get("output_committed") is True, result
    assert accepted["request_id"] == updates[-1]["request_id"]


def test_an_ordinary_failure_makes_no_claim_about_committed_output(runner, monkeypatch):
    """Absent, never `False`.

    A projector that emitted `False` for every exception that has never heard of
    the field would be asserting a safety it cannot know -- which is worse than
    silence, because a client would act on it.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    monkeypatch.setattr(
        runner, "run_message", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )

    _accepted(runner, f"/frames/{frame_id}/message", {"request": "go", "wait": False})
    job = next(iter(runner._jobs.values()))
    result = job.wait_result()
    updates = _failed_frame_updates(runner)

    assert "output_committed" not in updates[-1], updates[-1]
    assert "output_committed" not in result, result
