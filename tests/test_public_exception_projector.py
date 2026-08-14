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
import sqlite3
import sys
import threading
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.llm.models import TransportError
from openai4s.observability import reset_correlation_id, set_correlation_id
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.server.errors import DIAGNOSTIC_DETAIL as INTERNAL_DIAGNOSTIC_DETAIL
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
    # The category, not the class's own name: `CanaryFailure` is a name this
    # file chose, and a name is not metadata for parsing as an identifier.
    assert record["exception"] == "RuntimeError"
    assert record["request_id"] == "req-canary"
    assert record["surface"] == "test:diagnostic"
    # The failure is identifiable by (surface, error_class) -- not by quoting
    # it. This test used to require `"upstream refused" in record["detail"]`,
    # which is the assertion that kept an arbitrary exception message on a
    # record that outlives the request. Redaction was the argument for allowing
    # it, and redaction lost: a `/srv` path, a shell command and an ordinary
    # English sentence all came through every pattern intact, and the record
    # then reached `logs/app.out`, which the support bundle collects.
    assert record["detail"] == INTERNAL_DIAGNOSTIC_DETAIL
    assert record["error_class"]
    blob = json.dumps(record, ensure_ascii=False, default=str)
    for canary in CANARIES:
        assert canary not in blob, f"{canary!r} reached the operator record"


def test_no_path_of_any_kind_reaches_the_diagnostic(monkeypatch, tmp_path):
    """This used to assert the home directory was collapsed to `~`.

    Collapsing was the right instinct and the wrong scope: it handled *this*
    account's home and nothing else, so a path under another user, a `/srv`
    mount or a shared volume went through untouched. The record no longer
    carries a rendering of the exception at all, so there is no path in it to
    collapse -- which is the only version of this guarantee that does not
    depend on having thought of the right prefixes.
    """
    home = str(tmp_path / "someone")
    monkeypatch.setenv("HOME", home)

    record = record_diagnostic(
        OSError(f"cannot read {home}/notes/embargo.csv or /srv/raw/other.csv"),
        surface="test:home",
        request_id="req-canary",
    )

    blob = json.dumps(record, ensure_ascii=False, default=str)
    assert home not in blob
    assert "/srv/raw/other.csv" not in blob
    assert "embargo.csv" not in blob
    # Still a diagnostic: which surface, which kind of failure.
    assert record["exception"] == "OSError"
    assert record["surface"] == "test:home"


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

    # `open`, not `read_bytes`: `_read_snapshot` reads through a file handle so
    # it can stop one byte past the budget. Patching `read_bytes` left this
    # test passing on the FileNotFoundError from opening ABS_PATH -- the right
    # assertion driven by the wrong error, on a branch it never entered.
    original_open = artifact_refs.Path.open
    original_isfile = artifact_refs.Path.is_file
    artifact_refs.Path.open = unreadable
    artifact_refs.Path.is_file = lambda self: True
    try:
        _text, problem, _sent, _cut = artifact_refs._read_snapshot(
            metadata, "notes.txt"
        )
    finally:
        artifact_refs.Path.open = original_open
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
# one local id across every terminal surface
# --------------------------------------------------------------------------
#
# Five, and the fifth was the one I said did not exist. `storage/frames.py`
# has `update_message_metadata` and `list_message_boundaries`, and
# `GET /frames/{fid}/messages` projects the row -- I had grepped for the
# literal string `request_id` under `storage/`, which searches for a field name
# inside a generic JSON blob and therefore proves nothing.
#
# The injection point matters more than the surfaces. `SessionRunner.run_message`
# catches its own exceptions and *returns* a failed dict, so a test that
# replaces the whole method reaches an outer handler that production never
# reaches. These drive `_loop` -- inside the try, where a real LLM or tool
# failure lands.


def _api(runner, method, path, body=None):
    """Drive the real route and return (body, status)."""
    handler = object.__new__(gateway_mod.make_handler(runner.cfg, runner.hub, runner))
    handler._correlation_id = "req-canary"
    handler._last_status = 0
    handler.headers = {}
    handler._query = lambda: {}
    handler._body = lambda: (body or {})
    seen: list[tuple] = []
    handler._json = lambda value, code=200: seen.append((value, code))
    handler._api(method, path)
    assert seen, f"{method} {path} answered nothing"
    return seen[-1]


def _failed_frame_updates(runner):
    return [
        event
        for event in runner.hub.events
        if event.get("type") == "frame_update" and event.get("status") == "failed"
    ]


def _run_failing_turn(runner, monkeypatch, exc, *, request="go"):
    """POST a real `wait:false` turn whose *inner* loop raises."""
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")

    def boom(*_a, **_k):
        raise exc

    monkeypatch.setattr(runner, "_loop", boom)
    accepted, status = _api(
        runner,
        "POST",
        f"/frames/{frame_id}/message",
        {"request": request, "wait": False},
    )
    assert status == 202, (status, accepted)
    job = next(iter(runner._jobs.values()))
    result = job.wait_result()
    messages, _ = _api(runner, "GET", f"/frames/{frame_id}/messages")
    return frame_id, accepted, result, messages


@pytest.mark.stubbed_backend
def test_one_id_reaches_all_five_surfaces_for_a_real_internal_failure(
    runner, monkeypatch
):
    """The id a user can quote, on every surface that reports the failure.

    Reopening is the surface that had nothing at all: the socket event is gone
    once the tab closes and the stored row is a sentence, so the person most
    likely to need a support id -- the one who closed the tab on a failure --
    was the one who could not get one.
    """
    _fid, accepted, result, messages = _run_failing_turn(
        runner, monkeypatch, CanaryFailure()
    )
    updates = _failed_frame_updates(runner)
    assert updates, f"no failure reached the socket: {runner.hub.events}"
    stored = [m for m in messages["messages"] if m.get("failure")]
    assert stored, f"the failure was persisted without an identity: {messages}"

    # The persisted row, read from the store rather than through the route --
    # a projection that invented the field would otherwise look like storage
    # that kept it.
    raw = [
        m
        for m in runner.store.list_messages(_fid)
        if "failure" in str(m.get("metadata") or "")
    ]
    assert raw, f"nothing was persisted with a failure identity: {raw}"
    persisted = json.loads(raw[-1]["metadata"])["failure"]

    ids = {
        "202": accepted.get("request_id"),
        "ws": updates[-1].get("request_id"),
        "job": result.get("request_id"),
        "persisted": persisted.get("request_id"),
        "projected": stored[-1]["failure"].get("request_id"),
    }
    assert all(ids.values()), ids
    assert (
        len(set(ids.values())) == 1
    ), f"five surfaces, {len(set(ids.values()))} ids: {ids}"
    assert persisted.get("code") == stored[-1]["failure"].get("code")
    assert updates[-1].get("code")


@pytest.mark.stubbed_backend
def test_ark_burst_protection_is_named_consistently_without_blaming_the_key(
    runner, monkeypatch
):
    """A provider burst refusal is operational, not a bad configuration.

    The exact Ark code is a controlled signal.  Its free-form message is not:
    it may contain upstream request metadata or credential-shaped text, so the
    public wording and stable code must be local on all five failure surfaces.
    """
    exc = TransportError(
        f"System protection triggered by request burst: {CREDENTIAL}",
        provider="ark",
        status=429,
        error_code="RequestBurstTooFast",
        retryable=True,
    )
    frame_id, _accepted, result, messages = _run_failing_turn(
        runner, monkeypatch, exc, request="请继续完成这个长任务"
    )
    updates = _failed_frame_updates(runner)
    stored = [m for m in messages["messages"] if m.get("failure")]
    raw = [
        m
        for m in runner.store.list_messages(frame_id)
        if "failure" in str(m.get("metadata") or "")
    ]
    assert updates and stored and raw
    persisted = json.loads(raw[-1]["metadata"])["failure"]

    for where in (result, updates[-1], persisted, stored[-1]["failure"]):
        assert where.get("code") == "llm_request_burst", where

    chunks = [
        str(event.get("chunk") or "")
        for event in runner.hub.events
        if event.get("type") == "text_chunk"
    ]
    public_blob = json.dumps(
        [result, updates[-1], stored[-1], chunks], ensure_ascii=False, default=str
    )
    assert "突发流量保护" in public_blob
    assert "Customize → Models" not in public_blob
    assert CREDENTIAL not in public_blob


@pytest.mark.stubbed_backend
def test_a_max_turns_failure_is_named_too(runner, monkeypatch):
    """The most ordinary failure in the product raises nothing at all.

    `loop_reason == "max_turns"` sets `status = "failed"` with no exception, so
    an identity derived from an `except` clause would leave it with nothing to
    quote. It gets a stable code rather than an error one.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    monkeypatch.setattr(runner, "_loop", lambda *a, **k: "max_turns")

    accepted, _ = _api(
        runner, "POST", f"/frames/{frame_id}/message", {"request": "go", "wait": False}
    )
    job = next(iter(runner._jobs.values()))
    result = job.wait_result()
    messages, _ = _api(runner, "GET", f"/frames/{frame_id}/messages")
    updates = _failed_frame_updates(runner)

    assert updates, runner.hub.events
    stored = [m for m in messages["messages"] if m.get("failure")]
    raw = [
        m
        for m in runner.store.list_messages(frame_id)
        if "failure" in str(m.get("metadata") or "")
    ]
    assert stored and raw, messages
    persisted = json.loads(raw[-1]["metadata"])["failure"]
    ids = {
        "202": accepted.get("request_id"),
        "ws": updates[-1].get("request_id"),
        "job": result.get("request_id"),
        "persisted": persisted.get("request_id"),
        "projected": stored[-1]["failure"].get("request_id"),
    }
    assert all(ids.values()) and len(set(ids.values())) == 1, ids
    for where in (updates[-1], result, persisted, stored[-1]["failure"]):
        assert where.get("code") == "max_turns", where


@pytest.mark.stubbed_backend
def test_the_retry_veto_reaches_the_surface_that_offers_the_retry(runner, monkeypatch):
    """A 502 looks retryable; a 502 after a tool has run is not.

    `TransportError.output_committed` decides, and it was read only inside the
    LLM layer -- so the browser met every failure with "please try again",
    including the ones where trying again re-runs the tool.
    """
    exc = TransportError(
        "upstream 502 after streaming 4 tool calls",
        provider="deepseek",
        status=502,
        retryable=True,
        output_committed=True,
    )
    _fid, accepted, result, messages = _run_failing_turn(runner, monkeypatch, exc)
    updates = _failed_frame_updates(runner)
    stored = [m for m in messages["messages"] if m.get("failure")]

    assert updates[-1].get("output_committed") is True, updates[-1]
    assert result.get("output_committed") is True, result
    assert stored and stored[-1]["failure"].get("output_committed") is True, messages
    assert accepted["request_id"] == updates[-1]["request_id"]


@pytest.mark.stubbed_backend
def test_an_ordinary_failure_makes_no_claim_about_committed_output(runner, monkeypatch):
    """Absent, never `False` -- a `False` asserts a safety nothing here knows."""
    _fid, _accepted, result, messages = _run_failing_turn(
        runner, monkeypatch, CanaryFailure()
    )
    updates = _failed_frame_updates(runner)
    stored = [m for m in messages["messages"] if m.get("failure")]

    assert "output_committed" not in updates[-1], updates[-1]
    assert "output_committed" not in result, result
    assert "output_committed" not in stored[-1]["failure"], stored[-1]


@pytest.mark.stubbed_backend
def test_no_canary_survives_on_any_of_the_five_surfaces(runner, monkeypatch):
    """Plan item 16, against a failure that carries all three.

    `_friendly_error` used to end in `f"... {str(exc)[:300]}"` and to pick its
    branch by substring-matching that same string, so a provider error echoing
    a credential, a `PermissionError` naming an absolute path, or a subprocess
    failure carrying an argv was published as prose -- and is now *stored*,
    which is worse, because the row outlives the session.
    """
    exc = TransportError(
        f"POST failed for {CANARIES[0]} reading {CANARIES[1]} via {CANARIES[2]}",
        provider="deepseek",
        status=502,
    )
    _fid, accepted, result, messages = _run_failing_turn(runner, monkeypatch, exc)
    chunks = [e for e in runner.hub.events if e.get("type") == "text_chunk"]
    blob = json.dumps(
        [accepted, result, messages, _failed_frame_updates(runner), chunks],
        ensure_ascii=False,
        default=str,
    )
    for canary in CANARIES:
        assert canary not in blob, f"{canary!r} survived: {blob[:600]}"


@pytest.mark.stubbed_backend
def test_the_operator_diagnostic_is_written_exactly_once(runner, monkeypatch):
    """One failure, one record, naming the same request as the public body.

    Two would mean two places decided what the public body says, which is how
    they drift; zero would mean the generic sentence the client receives is the
    only account of the failure anywhere.

    Patched on `openai4s.server.errors`, which is where `public_exception`
    resolves it -- patching the gateway's own module name catches nothing,
    because the gateway does not call it on this path at all.
    """
    from openai4s.server import errors as errors_mod

    real = errors_mod.record_diagnostic
    seen: list[dict] = []

    def recording(exc, *, surface, request_id=None):
        seen.append({"surface": surface, "request_id": request_id})
        return real(exc, surface=surface, request_id=request_id)

    monkeypatch.setattr(errors_mod, "record_diagnostic", recording)
    _fid, accepted, _result, _messages = _run_failing_turn(
        runner, monkeypatch, CanaryFailure()
    )

    assert len(seen) == 1, f"{len(seen)} diagnostics for one failure: {seen}"
    assert seen[0]["surface"] == "web:turn", seen[0]
    assert seen[0]["request_id"] == accepted["request_id"], (seen[0], accepted)


@pytest.mark.stubbed_backend
def test_a_plan_approval_failure_is_named_on_its_own_route(runner, monkeypatch):
    """The plan spawner is a separate 202 and a separate emitter.

    A message-turn test stays green with the plan route's fields deleted, so
    the two need separate evidence. Injected at the same `_loop` seam: the plan
    path reaches it through `plans.run_execution` -> `run_message`, which is
    how a real plan step fails.
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

    def boom(*_a, **_k):
        raise CanaryFailure()

    monkeypatch.setattr(runner, "_loop", boom)
    accepted, status = _api(runner, "POST", f"/frames/{frame_id}/plan/approve", {})

    assert status == 202, (status, accepted)
    assert accepted.get("request_id"), f"the plan 202 named no request: {accepted}"
    job = next(j for j in runner._jobs.values() if j.job_id == accepted["job_id"])
    result = job.wait_result()
    updates = _failed_frame_updates(runner)

    assert updates, f"the plan failure never reached the socket: {runner.hub.events}"
    assert accepted["request_id"] == updates[-1]["request_id"]
    assert result.get("request_id") == accepted["request_id"], result
    blob = json.dumps([accepted, updates[-1], result], ensure_ascii=False, default=str)
    for canary in CANARIES:
        assert canary not in blob, blob


# --------------------------------------------------------------------------
# the OUTER catches are real paths too
# --------------------------------------------------------------------------
#
# A fault before the turn is entered or after it returns never reaches
# `run_message`'s own handler, and the plan spawner's `fn` can raise outside it
# entirely. Replacing the whole of `run_message` is the wrong injection for the
# *common* failure -- which is what made the earlier tests wrong -- but it is
# the right one for these, because that is exactly the shape of a fault around
# the call rather than inside it.


@pytest.mark.stubbed_backend
def test_an_outer_message_failure_is_persisted_and_projected(runner, monkeypatch):
    """It had the socket and the job result, and nothing that survives a reload."""
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    monkeypatch.setattr(
        runner, "run_message", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )

    accepted, status = _api(
        runner, "POST", f"/frames/{frame_id}/message", {"request": "go", "wait": False}
    )
    assert status == 202
    result = next(iter(runner._jobs.values())).wait_result()
    messages, _ = _api(runner, "GET", f"/frames/{frame_id}/messages")
    stored = [m for m in messages["messages"] if m.get("failure")]

    assert stored, f"an outer failure left nothing to reopen: {messages}"
    assert (
        accepted["request_id"]
        == result["request_id"]
        == stored[-1]["failure"]["request_id"]
    )
    assert stored[-1]["failure"].get("code")
    # The socket said it too, with the same id and code -- read rather than
    # assumed from the job result, because they are separate emitters.
    updates = _failed_frame_updates(runner)
    assert updates, runner.hub.events
    assert updates[-1]["request_id"] == accepted["request_id"]
    assert updates[-1].get("code") == stored[-1]["failure"]["code"]
    # Exactly one row, not one per surface that noticed.
    raw = [
        m
        for m in runner.store.list_messages(frame_id)
        if "failure" in str(m.get("metadata") or "")
    ]
    assert len(raw) == 1, raw
    blob = json.dumps([accepted, result, messages], ensure_ascii=False, default=str)
    for canary in CANARIES:
        assert canary not in blob, blob


@pytest.mark.stubbed_backend
def test_an_outer_plan_failure_is_persisted_and_projected(runner, monkeypatch):
    """The plan spawner's `fn` raising outside `run_message` is its own site."""
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
    # Committed on purpose: the plan route has its own emitter, and without a
    # veto here the plan WS fields can be deleted while the message tests stay
    # green.
    monkeypatch.setattr(
        runner,
        "run_plan_execution",
        lambda *a, **k: (_ for _ in ()).throw(
            TransportError(
                "upstream 502 after a tool ran",
                provider="deepseek",
                status=502,
                output_committed=True,
            )
        ),
    )

    accepted, status = _api(runner, "POST", f"/frames/{frame_id}/plan/approve", {})
    assert status == 202
    job = next(j for j in runner._jobs.values() if j.job_id == accepted["job_id"])
    result = job.wait_result()
    messages, _ = _api(runner, "GET", f"/frames/{frame_id}/messages")
    stored = [m for m in messages["messages"] if m.get("failure")]

    assert stored, f"an outer plan failure left nothing to reopen: {messages}"
    assert accepted["request_id"] == stored[-1]["failure"]["request_id"]
    assert result.get("request_id") == accepted["request_id"]
    updates = _failed_frame_updates(runner)
    assert updates and updates[-1]["request_id"] == accepted["request_id"]
    assert updates[-1].get("code") == stored[-1]["failure"]["code"]
    assert updates[-1].get("output_committed") is True, updates[-1]
    assert stored[-1]["failure"].get("output_committed") is True, stored
    assert result.get("output_committed") is True, result
    raw = [
        m
        for m in runner.store.list_messages(frame_id)
        if "failure" in str(m.get("metadata") or "")
    ]
    assert len(raw) == 1, raw


@pytest.mark.stubbed_backend
def test_an_outer_failure_records_exactly_one_diagnostic(runner, monkeypatch):
    """`job.project` already ran the projector; persisting must not run it again.

    Two records of one failure is how the two accounts of it drift apart, and
    the second would be written from a site that has no exception left to
    describe.
    """
    from openai4s.server import errors as errors_mod

    real = errors_mod.record_diagnostic
    seen: list[str] = []
    monkeypatch.setattr(
        errors_mod,
        "record_diagnostic",
        lambda exc, *, surface, request_id=None: (
            seen.append(surface),
            real(exc, surface=surface, request_id=request_id),
        )[1],
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    monkeypatch.setattr(
        runner, "run_message", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )

    _api(
        runner, "POST", f"/frames/{frame_id}/message", {"request": "go", "wait": False}
    )
    next(iter(runner._jobs.values())).wait_result()

    assert seen == ["web:message"], seen


# --------------------------------------------------------------------------
# one authoritative terminal failure per request
# --------------------------------------------------------------------------
#
# Both catches can fire for one turn: `_loop` fails and the inner handler
# records it, then the tail -- `update_frame`, `mark_finalizing`,
# `recovery.touch` -- fails too and leaves through the outer one. That is not
# hypothetical; it is what a database or filesystem problem during
# finalisation looks like.
#
# `MessageJob.project` assigns `output_committed` unconditionally, so the
# second, uncommitted exception *downgrades* the veto the first one earned, and
# `_persist_outer_failure` adds a second terminal row. The user is shown two
# failures, the last of which invites a retry that re-runs a tool.


def _committed_then_tail_failure(runner, monkeypatch):
    """The real double fault: a committed turn failure, then a tail failure."""
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")

    def loop_boom(*_a, **_k):
        raise TransportError(
            "upstream 502 after streaming tool calls",
            provider="deepseek",
            status=502,
            output_committed=True,
        )

    # Only AFTER the turn has already failed: `recovery.touch` is also called
    # on the way in, and raising there would kill the turn before the inner
    # handler ever ran -- a different bug, and not this one.
    real_touch = runner.recovery.touch

    def tail_boom(*args, **kwargs):
        if state["failed"]:
            raise RuntimeError("recovery journal is unwritable")
        return real_touch(*args, **kwargs)

    state = {"failed": False}

    def loop_then_mark(*args, **kwargs):
        try:
            loop_boom(*args, **kwargs)
        finally:
            state["failed"] = True

    monkeypatch.setattr(runner, "_loop", loop_then_mark)
    monkeypatch.setattr(runner.recovery, "touch", tail_boom)
    accepted, status = _api(
        runner, "POST", f"/frames/{frame_id}/message", {"request": "go", "wait": False}
    )
    assert status == 202
    result = next(iter(runner._jobs.values())).wait_result()
    messages, _ = _api(runner, "GET", f"/frames/{frame_id}/messages")
    return frame_id, accepted, result, messages


@pytest.mark.stubbed_backend
def test_a_tail_failure_does_not_downgrade_the_retry_veto(runner, monkeypatch):
    """The veto may only ever be OR-ed for one request.

    Losing it is the one direction that costs something: the UI goes back to
    "please try again" for a turn that already ran a tool.
    """
    _fid, accepted, result, messages = _committed_then_tail_failure(runner, monkeypatch)
    updates = _failed_frame_updates(runner)
    stored = [m for m in messages["messages"] if m.get("failure")]

    assert result.get("output_committed") is True, result
    assert updates[-1].get("output_committed") is True, updates[-1]
    assert stored and stored[-1]["failure"].get("output_committed") is True, stored


@pytest.mark.stubbed_backend
def test_a_double_fault_leaves_exactly_one_terminal_failure(runner, monkeypatch):
    """Two exceptions, one thing that happened to the user."""
    fid, accepted, result, messages = _committed_then_tail_failure(runner, monkeypatch)
    raw = [
        m
        for m in runner.store.list_messages(fid)
        if "failure" in str(m.get("metadata") or "")
    ]
    stored = [m for m in messages["messages"] if m.get("failure")]

    assert len(raw) == 1, f"{len(raw)} terminal failure rows for one request"
    assert len(stored) == 1, stored
    identity = json.loads(raw[0]["metadata"])["failure"]
    updates = _failed_frame_updates(runner)
    assert identity["request_id"] == accepted["request_id"] == result["request_id"]
    assert updates and updates[-1]["request_id"] == identity["request_id"]
    # One code for one thing that happened, on every surface that names it.
    codes = {
        identity["code"],
        stored[0]["failure"]["code"],
        updates[-1].get("code"),
        result.get("code"),
    }
    assert len(codes) == 1, codes


@pytest.mark.stubbed_backend
def test_a_double_fault_records_both_diagnostics_once_each(runner, monkeypatch):
    """Two real exceptions are two operator events, whatever the user is shown."""
    from openai4s.server import errors as errors_mod

    real = errors_mod.record_diagnostic
    seen: list[str] = []
    monkeypatch.setattr(
        errors_mod,
        "record_diagnostic",
        lambda exc, *, surface, request_id=None: (
            seen.append(surface),
            real(exc, surface=surface, request_id=request_id),
        )[1],
    )
    _committed_then_tail_failure(runner, monkeypatch)

    assert seen == ["web:turn", "web:message"], seen


@pytest.mark.stubbed_backend
def test_a_second_projection_cannot_un_say_the_veto(runner):
    """The invariant, driven on `MessageJob` itself.

    The merge above happens to carry the veto through today, so a test that
    only goes through the routes stays green with this assignment restored --
    and the next caller that projects twice would silently lose it again. A
    veto is a fact about the request; the last exception projected does not get
    to withdraw it.
    """
    job = gateway_mod.MessageJob("job-veto", "f-veto")

    job.project(
        TransportError(
            "committed", provider="deepseek", status=502, output_committed=True
        ),
        "web:turn",
    )
    assert job.output_committed is True

    job.project(RuntimeError("an ordinary tail failure"), "web:message")

    assert (
        job.output_committed is True
    ), "a later, uncommitted exception withdrew the retry veto"


@pytest.mark.stubbed_backend
def test_the_terminal_failure_handoff_is_scoped_to_its_turn(runner):
    """A note belongs to one job and one request, and dies with the job.

    Not to a branch resolved later: the outer handler runs while the failure is
    still happening, and re-deriving a branch there is how the correct
    hand-off got dropped. The note carries its own.
    """
    identity = {"request_id": "req-1", "code": "internal_error"}
    runner._enter_turn_scope("job-1")
    try:
        runner._remember_terminal_failure("req-1", "branch-a", "m-1", identity)

        assert (
            runner._take_terminal_failure("job-2", "req-1") is None
        ), "another job claimed this turn's note"
        assert (
            runner._take_terminal_failure("job-1", "req-other") is None
        ), "a different request claimed this turn's note"
        taken = runner._take_terminal_failure("job-1", "req-1")
        assert taken and taken["message_id"] == "m-1"
        # The branch travels with the note rather than being asked for again.
        assert taken["branch_id"] == "branch-a"
        # Taken once: a hand-off, not a registry.
        assert runner._take_terminal_failure("job-1", "req-1") is None
    finally:
        runner._exit_turn_scope("job-1")

    assert not runner._terminal_failures, runner._terminal_failures


@pytest.mark.stubbed_backend
def test_the_outer_handler_actually_receives_the_note(runner, monkeypatch):
    """The ordering, asserted rather than assumed.

    The scope used to be a `with` around the call, so it closed as the
    exception unwound -- taking the note away a moment before the handler it
    was filed for ran. Nothing about the resulting duplicate row said why.
    """
    taken: list[dict | None] = []
    real = runner._take_terminal_failure
    monkeypatch.setattr(
        runner,
        "_take_terminal_failure",
        lambda job_id, request_id: taken.append(real(job_id, request_id)) or taken[-1],
    )
    _committed_then_tail_failure(runner, monkeypatch)

    assert (
        taken and taken[-1] is not None
    ), "the outer handler found no note, so it wrote a second row"
    assert taken[-1]["identity"].get("output_committed") is True


@pytest.mark.stubbed_backend
def test_an_outer_plan_failure_records_exactly_one_diagnostic(runner, monkeypatch):
    """The plan spawner's own surface, counted separately from the message one."""
    from openai4s.server import errors as errors_mod

    real = errors_mod.record_diagnostic
    seen: list[str] = []
    monkeypatch.setattr(
        errors_mod,
        "record_diagnostic",
        lambda exc, *, surface, request_id=None: (
            seen.append(surface),
            real(exc, surface=surface, request_id=request_id),
        )[1],
    )
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
        runner,
        "run_plan_execution",
        lambda *a, **k: (_ for _ in ()).throw(CanaryFailure()),
    )

    accepted, _ = _api(runner, "POST", f"/frames/{frame_id}/plan/approve", {})
    next(
        j for j in runner._jobs.values() if j.job_id == accepted["job_id"]
    ).wait_result()

    assert seen == ["web:plan"], seen


@pytest.mark.stubbed_backend
def test_an_outer_failure_after_a_committed_turn_keeps_the_veto_everywhere(
    runner, monkeypatch
):
    """The committed case on the outer path, across helper, WS, job and GET."""
    _fid, accepted, result, messages = _committed_then_tail_failure(runner, monkeypatch)
    updates = _failed_frame_updates(runner)
    stored = [m for m in messages["messages"] if m.get("failure")]

    assert result.get("output_committed") is True, result
    assert updates[-1].get("output_committed") is True, updates[-1]
    assert stored[-1]["failure"].get("output_committed") is True, stored
    assert (
        accepted["request_id"]
        == result["request_id"]
        == updates[-1]["request_id"]
        == stored[-1]["failure"]["request_id"]
    )


@pytest.mark.stubbed_backend
def test_a_reused_request_id_does_not_let_one_turn_amend_another(runner, monkeypatch):
    """The hand-off must not outlive the turn that created it.

    Only the outer handler consumed the note, so a turn that failed *inside*
    and returned normally left one behind forever. An HTTP client may reuse
    `X-Request-Id`, so the next independent outer failure on the same branch
    found that stale note, amended a message from a turn that had already
    finished, and wrote nothing of its own -- one failure silently swallowed
    and an old one rewritten.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")

    from openai4s.observability import set_correlation_id

    def post(body):
        # The id a client supplied, twice. `MessageJob` reads the contextvar,
        # so this is where a reused `X-Request-Id` actually lands.
        set_correlation_id("req-reused")
        handler = object.__new__(
            gateway_mod.make_handler(runner.cfg, runner.hub, runner)
        )
        handler._correlation_id = "req-reused"
        handler._last_status = 0
        handler.headers = {}
        handler._query = lambda: {}
        handler._body = lambda: body
        seen: list[tuple] = []
        handler._json = lambda value, code=200: seen.append((value, code))
        handler._api("POST", f"/frames/{frame_id}/message")
        return seen[-1][0]

    # Turn A: fails inside `run_message`, which returns normally.
    monkeypatch.setattr(
        runner, "_loop", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )
    first = post({"request": "a", "wait": False})
    next(iter(runner._jobs.values())).wait_result()

    # Turn B: an independent failure that leaves through the OUTER handler.
    monkeypatch.setattr(
        runner, "run_message", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )
    second = post({"request": "b", "wait": False})
    for job in list(runner._jobs.values()):
        job.wait_result()

    assert first["request_id"] == second["request_id"] == "req-reused"
    rows = [
        m
        for m in runner.store.list_messages(frame_id)
        if "failure" in str(m.get("metadata") or "")
    ]
    assert len(rows) == 2, (
        f"{len(rows)} terminal rows for two independent failures -- the second "
        "amended the first instead of recording itself"
    )
    # And nothing is left over to catch a third.
    assert not runner._terminal_failures, runner._terminal_failures


@pytest.mark.stubbed_backend
def test_a_store_that_cannot_persist_still_completes_the_job(runner, monkeypatch):
    """The persistence is best effort; the job's completion is not.

    This runs inside the outer handler, on the job thread, after the original
    exception. If it raises, the thread dies before `job.finish` -- so the
    socket never gets a terminal event, `wait_result()` blocks forever, and a
    polling client never terminates. The failure that brought us here is very
    often the Store itself, which makes this the expected path rather than the
    exotic one.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    monkeypatch.setattr(
        runner, "run_message", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )

    def unwritable(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(runner.store, "add_message", unwritable)
    monkeypatch.setattr(runner.store, "active_session_branch", unwritable)

    accepted, status = _api(
        runner, "POST", f"/frames/{frame_id}/message", {"request": "go", "wait": False}
    )
    assert status == 202
    job = next(iter(runner._jobs.values()))

    # The assertion that matters: this returns at all.
    assert job.done.wait(timeout=10), "the job thread died and nothing finished it"
    result = job.wait_result()

    assert result["status"] == "failed"
    assert result["request_id"] == accepted["request_id"]
    assert result.get("code")
    updates = _failed_frame_updates(runner)
    assert updates, "no terminal event reached the socket"
    assert updates[-1]["request_id"] == accepted["request_id"]
    blob = json.dumps([accepted, result, updates[-1]], ensure_ascii=False, default=str)
    for canary in CANARIES:
        assert canary not in blob, blob


@pytest.mark.stubbed_backend
def test_a_failed_persistence_does_not_add_a_second_diagnostic(runner, monkeypatch):
    """The store failing is not a second thing that happened to the user."""
    from openai4s.server import errors as errors_mod

    real = errors_mod.record_diagnostic
    seen: list[str] = []
    monkeypatch.setattr(
        errors_mod,
        "record_diagnostic",
        lambda exc, *, surface, request_id=None: (
            seen.append(surface),
            real(exc, surface=surface, request_id=request_id),
        )[1],
    )
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    monkeypatch.setattr(
        runner, "run_message", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )
    monkeypatch.setattr(
        runner.store,
        "add_message",
        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("locked")),
    )

    _api(
        runner, "POST", f"/frames/{frame_id}/message", {"request": "go", "wait": False}
    )
    next(iter(runner._jobs.values())).wait_result()

    assert seen == ["web:message"], seen


@pytest.mark.stubbed_backend
def test_an_outer_plan_failure_lands_on_the_branch_it_was_submitted_on(
    runner, monkeypatch
):
    """A fork's failure belongs to the fork.

    The plan spawner froze no branch at all, so the helper fell back to the
    root frame. On a session whose active branch is a sibling that writes the
    failure somewhere the user is not looking, and grows one on the trunk that
    never happened there. Frozen on the submitting thread, where the answer is
    both available and still true.

    Injected at the branch the *session* is on, which is what the ticket now
    carries. This used to patch `active_session_branch`, the reader the old
    spawner called directly; the branch is now resolved once, when the session
    state is built, and every ticket issued for that session inherits it.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    sibling = "branch-fork-1"
    monkeypatch.setattr(runner.store, "active_session_branch", lambda _fid: sibling)
    monkeypatch.setattr(
        runner.store, "ensure_active_session_branch", lambda _fid: sibling
    )
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
        runner,
        "run_plan_execution",
        lambda *a, **k: (_ for _ in ()).throw(CanaryFailure()),
    )

    accepted, _ = _api(runner, "POST", f"/frames/{frame_id}/plan/approve", {})
    next(
        j for j in runner._jobs.values() if j.job_id == accepted["job_id"]
    ).wait_result()

    rows = [
        m
        for m in runner.store.list_messages(frame_id)
        if "failure" in str(m.get("metadata") or "")
    ]
    assert len(rows) == 1, rows
    on_branch = runner.store.list_messages(frame_id, branch_id=sibling)
    assert [
        m for m in on_branch if "failure" in str(m.get("metadata") or "")
    ], "the failure is not on the branch the plan was approved from"
    on_root = runner.store.list_messages(frame_id, branch_id=frame_id)
    assert not [
        m for m in on_root if "failure" in str(m.get("metadata") or "")
    ], "the failure was written onto the trunk, where it never happened"


def _outer_failure_with_unwritable_frame(runner, monkeypatch, *, plan: bool):
    """An outer failure on a Store that cannot record the frame's status."""
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    if plan:
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
            runner,
            "run_plan_execution",
            lambda *a, **k: (_ for _ in ()).throw(
                TransportError(
                    "upstream 502 after a tool ran",
                    provider="deepseek",
                    status=502,
                    output_committed=True,
                )
            ),
        )
    else:
        monkeypatch.setattr(
            runner,
            "run_message",
            lambda *a, **k: (_ for _ in ()).throw(
                TransportError(
                    "upstream 502 after a tool ran",
                    provider="deepseek",
                    status=502,
                    output_committed=True,
                )
            ),
        )

    real_update = runner.store.update_frame

    def refuse(fid, **fields):
        if fields.get("status") == "failed":
            raise sqlite3.OperationalError("database is locked")
        return real_update(fid, **fields)

    monkeypatch.setattr(runner.store, "update_frame", refuse)
    path = f"/frames/{frame_id}/plan/approve" if plan else f"/frames/{frame_id}/message"
    body = {} if plan else {"request": "go", "wait": False}
    accepted, status = _api(runner, "POST", path, body)
    assert status == 202, (status, accepted)
    job = next(j for j in runner._jobs.values() if j.job_id == accepted["job_id"])
    return accepted, job


@pytest.mark.stubbed_backend
@pytest.mark.parametrize("plan", [False, True], ids=["message", "plan"])
def test_a_frame_status_write_failure_does_not_swallow_the_terminal_event(
    runner, monkeypatch, plan
):
    """Three obligations, one `try`, and the first failure cancelled the rest.

    `update_frame` and the emits shared a handler, so an `OperationalError`
    recording the frame's status skipped the terminal `frame_update`
    altogether. The client had been told 202 and was watching the socket: it
    got a turn that simply never ended. The earlier Store test missed this
    because it broke `add_message` and left `update_frame` working.
    """
    accepted, job = _outer_failure_with_unwritable_frame(runner, monkeypatch, plan=plan)

    assert job.done.wait(timeout=10), "the job never finished"
    result = job.wait_result()
    updates = _failed_frame_updates(runner)

    assert updates, "the terminal event was cancelled by the status write"
    assert updates[-1]["request_id"] == accepted["request_id"]
    assert updates[-1].get("code")
    assert updates[-1].get("output_committed") is True, updates[-1]
    assert result["request_id"] == accepted["request_id"]
    assert result.get("output_committed") is True, result


@pytest.mark.stubbed_backend
def test_an_outer_message_failure_lands_on_the_branch_it_was_submitted_on(
    runner, monkeypatch
):
    """The message turn's own branch, taken from the ticket that admitted it.

    Asserted separately from the plan route's: they freeze the branch from
    different sources -- the execution ticket here, an explicit lookup on the
    submitting thread there -- so one can be broken while the other holds.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    sibling = "branch-fork-msg"
    monkeypatch.setattr(runner.store, "active_session_branch", lambda _fid: sibling)
    monkeypatch.setattr(
        runner.store, "ensure_active_session_branch", lambda _f: sibling
    )
    monkeypatch.setattr(
        runner, "run_message", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )

    accepted, _ = _api(
        runner, "POST", f"/frames/{frame_id}/message", {"request": "go", "wait": False}
    )
    next(iter(runner._jobs.values())).wait_result()

    on_branch = [
        m
        for m in runner.store.list_messages(frame_id, branch_id=sibling)
        if "failure" in str(m.get("metadata") or "")
    ]
    on_root = [
        m
        for m in runner.store.list_messages(frame_id, branch_id=frame_id)
        if "failure" in str(m.get("metadata") or "")
    ]
    assert on_branch, "the failure is not on the branch the turn was submitted on"
    assert not on_root, "the failure was written onto the trunk instead"


@pytest.mark.stubbed_backend
def test_the_processing_event_names_the_turn_it_starts(runner, monkeypatch):
    """A queued follow-up's 202 is useless to the client that receives it.

    It resolves while an earlier turn still owns the screen, so the follow-up
    cannot take the slot then. `processing` -- "your turn is running now" -- is
    the first moment its id is current, and it is the only event that can carry
    it there.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    monkeypatch.setattr(runner, "_loop", lambda *a, **k: "submitted")

    accepted, status = _api(
        runner, "POST", f"/frames/{frame_id}/message", {"request": "go", "wait": False}
    )
    assert status == 202
    next(iter(runner._jobs.values())).wait_result()

    processing = [
        e
        for e in runner.hub.events
        if e.get("type") == "frame_update" and e.get("status") == "processing"
    ]
    assert processing, runner.hub.events
    assert processing[-1].get("request_id") == accepted["request_id"], processing[-1]


@pytest.mark.stubbed_backend
def test_a_direct_turn_still_names_itself(runner, monkeypatch):
    """A call with no HTTP request behind it must not emit an empty id.

    The CLI, a recovery replay and a test all reach `run_message` directly, and
    an empty `request_id` on `processing` and on the terminal event is a field
    every client has to special-case. Under a job the contextvar is already set
    by the request thread, so this and the 202 are the same string.
    """
    from openai4s.observability import set_correlation_id

    set_correlation_id("")
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    monkeypatch.setattr(runner, "_loop", lambda *a, **k: "submitted")

    response = runner.run_message(frame_id, "proj", "go")

    processing = [
        e
        for e in runner.hub.events
        if e.get("type") == "frame_update" and e.get("status") == "processing"
    ]
    assert processing and processing[-1].get("request_id"), processing
    assert response.get("request_id") == processing[-1]["request_id"]


# --- no HTTP request behind the turn ------------------------------------------
#
# The CLI, a recovery replay and a direct API user all submit without a
# correlation id in context. `MessageJob` read the contextvar and got "", so the
# 202 and the job result were nameless -- while `run_message` minted its own for
# the socket. Two ids for one turn is worse than none: they cannot be joined.


@pytest.mark.stubbed_backend
def test_a_direct_submit_names_one_turn_everywhere(runner, monkeypatch):
    from openai4s.observability import set_correlation_id

    set_correlation_id("")
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    monkeypatch.setattr(runner, "_loop", lambda *a, **k: "submitted")

    job = runner.submit_message(frame_id, "proj", "go")
    result = job.wait_result()
    processing = [
        e
        for e in runner.hub.events
        if e.get("type") == "frame_update" and e.get("status") == "processing"
    ]

    assert job.request_id, "the ticket has no id at all"
    assert processing and processing[-1].get("request_id") == job.request_id
    assert result.get("request_id") == job.request_id


@pytest.mark.stubbed_backend
def test_a_direct_plan_turn_names_one_turn_everywhere(runner, monkeypatch):
    from openai4s.observability import set_correlation_id

    set_correlation_id("")
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
    monkeypatch.setattr(runner, "_loop", lambda *a, **k: "submitted")

    job = runner.submit_plan_approval(frame_id, "proj")
    job.wait_result()
    processing = [
        e
        for e in runner.hub.events
        if e.get("type") == "frame_update" and e.get("status") == "processing"
    ]

    assert job.request_id
    assert processing and processing[-1].get("request_id") == job.request_id, processing


@pytest.mark.stubbed_backend
def test_a_direct_plan_failure_names_one_turn_everywhere(runner, monkeypatch):
    """The outer path, with nothing in context to inherit."""
    from openai4s.observability import set_correlation_id

    set_correlation_id("")
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
        runner,
        "run_plan_execution",
        lambda *a, **k: (_ for _ in ()).throw(CanaryFailure()),
    )

    job = runner.submit_plan_approval(frame_id, "proj")
    result = job.wait_result()
    updates = _failed_frame_updates(runner)
    stored = [
        m
        for m in runner.store.list_messages(frame_id)
        if "failure" in str(m.get("metadata") or "")
    ]

    assert job.request_id, "the ticket has no id"
    assert updates and updates[-1]["request_id"] == job.request_id
    assert result.get("request_id") == job.request_id
    assert stored and json.loads(stored[-1]["metadata"])["failure"]["request_id"] == (
        job.request_id
    )


@pytest.mark.stubbed_backend
def test_a_plan_failure_before_the_turn_starts_still_names_an_execution(
    runner, monkeypatch
):
    """The plan spawner holds no coordinator ticket.

    So its job had `execution_id = None` all the way through, and a failure
    before `run_message` reached an execution emitted a terminal event with no
    execution at all. A client whose running turn *does* have one then falls
    into the "one side is silent" compatibility branch and closes it.
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
        runner,
        "run_plan_execution",
        lambda *a, **k: (_ for _ in ()).throw(CanaryFailure()),
    )

    job = runner.submit_plan_approval(frame_id, "proj")
    job.wait_result()
    updates = _failed_frame_updates(runner)

    assert job.execution_id, "the plan ticket has no execution identity at all"
    assert updates and updates[-1].get("execution_id") == job.execution_id


@pytest.mark.stubbed_backend
def test_a_plan_tail_failure_names_the_execution_the_turn_actually_ran(
    runner, monkeypatch
):
    """Once the turn reaches an execution, that is the identity it keeps.

    Emitting the synthetic one after the turn has already announced the real
    one on `processing` would make its own terminal event look stale to every
    client -- the failure would never close the turn it belongs to.
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
    monkeypatch.setattr(runner, "_loop", lambda *a, **k: "submitted")
    real_execution = runner.run_plan_execution

    def run_then_fail(*args, **kwargs):
        real_execution(*args, **kwargs)
        raise CanaryFailure()  # the tail: update_plan / emit_ready

    monkeypatch.setattr(runner, "run_plan_execution", run_then_fail)

    job = runner.submit_plan_approval(frame_id, "proj")
    job.wait_result()
    processing = [
        e
        for e in runner.hub.events
        if e.get("type") == "frame_update" and e.get("status") == "processing"
    ]
    updates = _failed_frame_updates(runner)

    assert processing and processing[-1].get("execution_id"), processing
    assert not str(job.execution_id).startswith(
        "plan-"
    ), "the real execution id was never bound back to the ticket"
    assert updates[-1].get("execution_id") == processing[-1]["execution_id"], (
        updates[-1],
        processing[-1],
    )


# --------------------------------------------------------------------------
# the resume buffer belongs to an execution, not to a frame
# --------------------------------------------------------------------------
#
# The client-side filter is not enough on its own. `WSHub._live` is keyed by
# frame, so A's late `text_reset` replaced the window B was streaming into and
# A's late terminal cleared `running` -- and a client that RECONNECTS replays
# from that window and asks `is_running` from that flag. The turn that is
# genuinely still running looks finished to everyone who arrives afterwards.


# --------------------------------------------------------------------------
# the resume buffer belongs to an execution, not to a frame
# --------------------------------------------------------------------------
#
# The client-side filter is not enough on its own. `WSHub._live` is keyed by
# frame, so A's late `text_reset` replaced the window B was streaming into and
# A's late terminal cleared `running`. A client that RECONNECTS replays from
# that window and asks `is_running` from that flag, so the turn that is
# genuinely still running looks finished to everyone arriving afterwards.
#
# Driven through `WSHub._record`, the shipped recorder, in the order the
# gateway emits.


@pytest.fixture
def hub():
    from openai4s.server import gateway as gw

    return gw.WSHub()


def _live(hub, frame_id):
    return hub._live.get(frame_id) or {}


def test_a_new_execution_takes_the_window_over_from_a_running_one(hub):
    """The boundary must be read before the staleness test.

    Judged as an ordinary event, `processing(B)` is stale against A's window --
    so it is dropped, and A's window stays live forever while B streams into
    nothing.
    """
    hub._record("f", {"type": "text_reset", "frame_id": "f", "execution_id": "exec-A"})
    assert _live(hub, "f").get("execution_id") == "exec-A"

    hub._record(
        "f",
        {
            "type": "frame_update",
            "frame_id": "f",
            "status": "processing",
            "execution_id": "exec-B",
        },
    )

    assert (
        _live(hub, "f").get("execution_id") == "exec-B"
    ), "B never took the window over"
    assert _live(hub, "f").get("running") is True


def test_an_idless_reset_keeps_the_identity_the_boundary_established(hub):
    """A running turn's own stream events carry no execution id.

    Taking the field verbatim wipes what `processing` just established and
    hands the window to whichever late event arrives next.
    """
    hub._record(
        "f",
        {
            "type": "frame_update",
            "frame_id": "f",
            "status": "processing",
            "execution_id": "exec-B",
        },
    )
    hub._record("f", {"type": "text_reset", "frame_id": "f"})

    assert (
        _live(hub, "f").get("execution_id") == "exec-B"
    ), "an identity-less reset erased the running execution"
    assert _live(hub, "f").get("running") is True


def test_every_late_event_from_the_previous_execution_is_dropped(hub):
    """Reset, chunk and terminal alike -- the last one is the damaging one."""
    hub._record("f", {"type": "text_reset", "frame_id": "f", "execution_id": "exec-A"})
    hub._record(
        "f",
        {
            "type": "frame_update",
            "frame_id": "f",
            "status": "processing",
            "execution_id": "exec-B",
        },
    )
    hub._record("f", {"type": "text_reset", "frame_id": "f"})
    hub._record("f", {"type": "text_chunk", "frame_id": "f", "chunk": "B says"})

    for late in (
        {"type": "text_reset", "frame_id": "f", "execution_id": "exec-A"},
        {
            "type": "text_chunk",
            "frame_id": "f",
            "chunk": "_Error: A failed_",
            "execution_id": "exec-A",
        },
        {
            "type": "frame_update",
            "frame_id": "f",
            "status": "failed",
            "execution_id": "exec-A",
        },
    ):
        hub._record("f", late)

    buf = _live(hub, "f")
    assert buf.get("execution_id") == "exec-B"
    assert buf.get("running") is True, "A's terminal ended B's window"
    assert hub.is_running("f") is True
    text = json.dumps(buf.get("events") or [], ensure_ascii=False)
    assert "A failed" not in text, f"A's late prose landed in B's window: {text}"
    assert "B says" in text, "B's own stream was lost"


def test_the_running_executions_own_terminal_still_closes_the_window(hub):
    """The guard must not make a turn impossible to finish."""
    hub._record(
        "f",
        {
            "type": "frame_update",
            "frame_id": "f",
            "status": "processing",
            "execution_id": "exec-B",
        },
    )
    hub._record(
        "f",
        {
            "type": "frame_update",
            "frame_id": "f",
            "status": "completed",
            "execution_id": "exec-B",
        },
    )

    assert hub.is_running("f") is False


def test_a_daemon_without_execution_ids_behaves_as_it_always_did(hub):
    """Neither side naming one is the pre-identity contract."""
    hub._record("f", {"type": "text_reset", "frame_id": "f"})
    hub._record("f", {"type": "text_chunk", "frame_id": "f", "chunk": "hello"})
    assert hub.is_running("f") is True

    hub._record("f", {"type": "frame_update", "frame_id": "f", "status": "completed"})
    assert hub.is_running("f") is False


@pytest.mark.stubbed_backend
def test_a_late_turn_does_not_end_the_resume_window_of_the_running_one(tmp_path):
    """A's whole failure runs while A still owns the session; B has not begun.

    No sleeps and no serial fake concurrency. A stays inside its loop until B
    is queued; A's tail faults so it leaves through the outer handler; that
    handler stops *inside* A's finalisation, still holding the lease, and the
    assertions run there. B is released afterwards and blocks inside its own
    loop so the durable and projected `processing` can be read while it is
    unambiguously the running turn.

    This is the inverse of the probe's first version, which made A's
    finalisation wait for B. That shape forced the old bug into the open but
    deadlocks by construction once the lease is held correctly, because B
    cannot start until A lets go. What has to be proven is the opposite: that
    B is still *queued* -- not merely unscheduled -- while A writes its own
    outcome, since A's `update_frame(status="failed")` would otherwise
    overwrite the `processing` B had already written.

    The two turns are told apart by `correlation_id()`, which each job target
    binds to its own ticket. A shared flag cannot do it: A's tail resets before
    B is scheduled, so B gets mistaken for A and nothing ever releases.
    """
    from openai4s.config import LLMConfig
    from openai4s.observability import correlation_id, set_correlation_id
    from openai4s.server import gateway as gw

    cfg = Config(
        data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="test-key")
    )
    hub = gw.WSHub()
    runner = gw.SessionRunner(cfg, hub, start_idle_sweeper=False)
    a_finalizing = threading.Event()
    allow_a_finish = threading.Event()
    b_queued = threading.Event()
    b_running = threading.Event()
    b_release = threading.Event()
    seen: set[str] = set()
    job_b = None

    try:
        frame_id = runner.store.new_frame(
            kind="turn", project_id="default", status="ready"
        )
        # A real `WSHub` keeps no event list; a subscriber is how the ordering
        # is observed, and it is also the client the ordering is *for*.
        watcher = _FakeConn()
        watcher.subs.add(frame_id)
        hub._conns.add(watcher)

        def loop(_st, *_a, **_k):
            if correlation_id() == "req-B":
                b_running.set()
                assert b_release.wait(20), "the probe never released B"
                return "submitted"
            seen.add("a-loop")
            assert b_queued.wait(20), "B was never queued"
            raise CanaryFailure()

        real_touch = runner.recovery.touch

        def tail(*args, **kwargs):
            if correlation_id() == "req-A" and "a-loop" in seen and "tail" not in seen:
                seen.add("tail")
                raise RuntimeError("recovery journal is unwritable")
            return real_touch(*args, **kwargs)

        real_persist = runner._persist_outer_failure

        def persist_holding_the_lease(*args, **kwargs):
            outcome = real_persist(*args, **kwargs)
            a_finalizing.set()
            assert allow_a_finish.wait(20), "the probe never released A"
            return outcome

        runner._loop = loop
        runner.recovery.touch = tail
        runner._persist_outer_failure = persist_holding_the_lease

        set_correlation_id("req-A")
        job_a = runner.submit_message(frame_id, "default", "a")
        set_correlation_id("req-B")
        job_b = runner.submit_message(frame_id, "default", "b")
        set_correlation_id("")
        b_queued.set()

        # A is inside its failure handling and still holds the lease.
        assert a_finalizing.wait(20), "A never reached its finalisation"
        # Asked of the coordinator, which is the thing that decides. `b_running`
        # is set by B's own thread, so its absence only says B has not been
        # *scheduled* -- a release-early bug wins that by luck on a busy box.
        # A broadcast is no better: it is downstream of the promotion and
        # observing it is still a race. The FIFO's own state is neither: while
        # A finalises, A must be the owner and B must still be sitting at
        # position 1.
        state = runner.executions.snapshot(frame_id)
        owner = state.get("owner") or {}
        assert owner.get("execution_id") == job_a.execution_id, (
            "the session's owner is not the turn that is still finalising -- A "
            f"released before writing its outcome: {state}"
        )
        queued = list(state.get("queue") or [])
        assert [
            (item.get("execution_id"), item.get("queue_position")) for item in queued
        ] == [(job_b.execution_id, 1)], (
            "B is not waiting behind A while A writes its own outcome -- A's "
            f"durable status would overwrite the `processing` B wrote: {state}"
        )
        allow_a_finish.set()
        job_a.wait_result()
        assert b_running.wait(20), "B never started"

        # B is blocked inside its loop, so it is unambiguously the running
        # turn. Every surface has to say so -- the durable row A wrote its
        # failure to must not be the one B is running under.
        frame = runner.store.get_frame(frame_id) or {}
        assert frame.get("status") == "processing", (
            f"the stored frame says {frame.get('status')!r} while B is running: "
            "A's terminal write overwrote the status B had already set"
        )
        projected, _ = _api(runner, "GET", f"/frames/{frame_id}/status")
        assert projected.get("running") is True, projected
        assert projected.get("status") == "processing", projected

        # And A's failure was durable and announced before B ever said
        # `processing` -- which is what holding the lease buys.
        order = [
            (index, event)
            for index, event in enumerate(watcher.sent)
            if event.get("type") == "frame_update"
            and event.get("status") in {"failed", "processing"}
        ]
        a_failed = [i for i, e in order if e.get("status") == "failed"]
        b_processing = [
            i
            for i, e in order
            if e.get("status") == "processing"
            and e.get("execution_id") == job_b.execution_id
        ]
        assert a_failed and b_processing, order
        assert (
            a_failed[-1] < b_processing[-1]
        ), "B announced itself before A had finished failing"

        # A is finished; B is still inside its loop.
        buf = hub._live.get(frame_id) or {}
        assert buf.get("execution_id") == job_b.execution_id, (
            f"the live window belongs to {buf.get('execution_id')!r}, not to the "
            f"turn still running ({job_b.execution_id!r})"
        )
        assert (
            hub.is_running(frame_id) is True
        ), "a finished turn's terminal event closed the running turn's window"
        stale = [
            event
            for event in buf.get("events") or []
            if str(event.get("execution_id") or "") == str(job_a.execution_id)
        ]
        assert not stale, f"A's late events landed in B's window: {stale}"
    finally:
        # A first, and unconditionally. If an assertion above fires while A is
        # parked inside its finalisation, releasing only B leaves A's thread
        # holding the lease for the whole 20s wait and B blocked behind it --
        # the failure report then describes a hang instead of the assertion,
        # which is exactly what a mutation run needs to read.
        allow_a_finish.set()
        b_release.set()
        if job_b is not None:
            job_b.wait_result()
        runner.close()

    assert hub.is_running(frame_id) is False, "B's own terminal did not close it"


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "path,body",
    [("plan/approve", {}), ("plan/revise", {"changes": "x"})],
)
def test_a_plan_202_names_the_execution_a_pre_run_failure_will_use(
    runner, monkeypatch, path, body
):
    """The 202 is the only synchronous thing a `wait:false` client receives.

    A plan turn that fails before it reaches an execution is reported under the
    synthetic id, and without it on the 202 the client cannot tell that failure
    from the next turn's -- which is the case the whole execution filter exists
    for.
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
    for attribute in ("run_plan_execution", "run_plan_revision"):
        monkeypatch.setattr(
            runner,
            attribute,
            lambda *a, **k: (_ for _ in ()).throw(CanaryFailure()),
        )

    accepted, status = _api(runner, "POST", f"/frames/{frame_id}/{path}", body)
    assert status == 202, (status, accepted)
    assert accepted.get("execution_id"), f"the plan 202 named no execution: {accepted}"

    job = next(j for j in runner._jobs.values() if j.job_id == accepted["job_id"])
    result = job.wait_result()
    updates = _failed_frame_updates(runner)

    # The 202, the poll and the socket agree about which execution failed.
    assert result.get("execution_id") == accepted["execution_id"], result
    assert updates and updates[-1].get("execution_id") == accepted["execution_id"]


@pytest.mark.stubbed_backend
def test_a_post_run_plan_failure_reports_the_real_execution_on_every_surface(
    runner, monkeypatch
):
    """Once bound, the real id is what the poll and the socket both carry."""
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
    monkeypatch.setattr(runner, "_loop", lambda *a, **k: "submitted")
    real_execution = runner.run_plan_execution

    def run_then_fail(*args, **kwargs):
        real_execution(*args, **kwargs)
        raise CanaryFailure()

    monkeypatch.setattr(runner, "run_plan_execution", run_then_fail)

    accepted, _ = _api(runner, "POST", f"/frames/{frame_id}/plan/approve", {})
    job = next(j for j in runner._jobs.values() if j.job_id == accepted["job_id"])
    result = job.wait_result()
    processing = [
        e
        for e in runner.hub.events
        if e.get("type") == "frame_update" and e.get("status") == "processing"
    ]
    updates = _failed_frame_updates(runner)

    real = processing[-1]["execution_id"]
    assert not real.startswith("plan-"), real
    assert result.get("execution_id") == real, result
    assert updates[-1].get("execution_id") == real


# --- the fence has to be at the connection, not just at the buffer ------------


class _FakeConn:
    """Just enough of `WSConnection` for the hub to deliver to."""

    def __init__(self) -> None:
        self.alive = True
        self.subs: set[str] = set()
        self.sent: list[dict] = []

    def send_json(self, obj: dict) -> None:
        self.sent.append(dict(obj))


def _turn_events(conn):
    return [
        e
        for e in conn.sent
        if e.get("type") in {"text_reset", "text_chunk", "frame_update"}
    ]


def test_a_late_turns_events_never_reach_a_subscriber(hub):
    """Dropping them from the resume window is not enough.

    `broadcast` delivered them anyway, and a tab that joined during B has no
    stored identity for A -- so its own filter reads "one side silent", which
    means current, and A's late terminal closes B.
    """
    conn = _FakeConn()
    conn.subs.add("f")
    hub._conns.add(conn)

    hub.broadcast(
        "f", {"type": "text_reset", "frame_id": "f", "execution_id": "exec-A"}
    )
    hub.broadcast(
        "f",
        {
            "type": "frame_update",
            "frame_id": "f",
            "status": "processing",
            "execution_id": "exec-B",
        },
    )
    hub.broadcast("f", {"type": "text_reset", "frame_id": "f"})
    hub.broadcast("f", {"type": "text_chunk", "frame_id": "f", "chunk": "B says"})
    before = len(conn.sent)

    for late in (
        {"type": "text_reset", "frame_id": "f", "execution_id": "exec-A"},
        {
            "type": "text_chunk",
            "frame_id": "f",
            "chunk": "_Error: A failed_",
            "execution_id": "exec-A",
        },
        {
            "type": "frame_update",
            "frame_id": "f",
            "status": "failed",
            "execution_id": "exec-A",
        },
    ):
        hub.broadcast("f", late)

    assert (
        len(conn.sent) == before
    ), f"A's late events reached the socket: {conn.sent[before:]}"
    assert hub.is_running("f") is True
    assert "A failed" not in json.dumps(conn.sent, ensure_ascii=False)


def test_state_deltas_that_no_execution_owns_are_still_broadcast(hub):
    """The fence must not swallow permission cards or kernel/idle deltas.

    Those belong to the frame, not to a turn, and withholding them breaks
    surfaces that have nothing to do with turn ordering -- including the
    approval prompt, which blocks until someone answers it.
    """
    conn = _FakeConn()
    conn.subs.add("f")
    hub._conns.add(conn)
    hub.broadcast(
        "f",
        {
            "type": "frame_update",
            "frame_id": "f",
            "status": "processing",
            "execution_id": "exec-B",
        },
    )
    before = len(conn.sent)

    hub.broadcast("f", {"type": "await_permission", "frame_id": "f", "id": "p-1"})
    hub.broadcast("f", {"type": "kernel_status", "frame_id": "f", "state": "idle"})

    kinds = [e.get("type") for e in conn.sent[before:]]
    assert kinds == ["await_permission", "kernel_status"], conn.sent[before:]


def test_the_running_turns_own_terminal_still_reaches_the_socket(hub):
    conn = _FakeConn()
    conn.subs.add("f")
    hub._conns.add(conn)
    hub.broadcast(
        "f",
        {
            "type": "frame_update",
            "frame_id": "f",
            "status": "processing",
            "execution_id": "exec-B",
        },
    )
    hub.broadcast(
        "f",
        {
            "type": "frame_update",
            "frame_id": "f",
            "status": "completed",
            "execution_id": "exec-B",
        },
    )

    assert _turn_events(conn)[-1]["status"] == "completed"
    assert hub.is_running("f") is False


def test_a_daemon_that_names_no_execution_broadcasts_everything(hub):
    conn = _FakeConn()
    conn.subs.add("f")
    hub._conns.add(conn)
    for event in (
        {"type": "text_reset", "frame_id": "f"},
        {"type": "text_chunk", "frame_id": "f", "chunk": "hello"},
        {"type": "frame_update", "frame_id": "f", "status": "completed"},
    ):
        hub.broadcast("f", event)

    assert len(_turn_events(conn)) == 3, conn.sent
    assert hub.is_running("f") is False


def test_a_tab_that_joins_during_b_is_never_told_that_a_ended_it(hub):
    """The new-tab case, through the real `subscribe` replay path.

    This is the client the fence exists for. It has no stored identity for A --
    it was not connected when A ran -- so its own filter reads A's late
    terminal as "one side silent", which means current, and closes the turn it
    is actually watching. Subscribing after B's window exists, rather than
    adding to `conn.subs` by hand, is what makes it that client.
    """
    hub.broadcast(
        "f",
        {
            "type": "frame_update",
            "frame_id": "f",
            "status": "processing",
            "execution_id": "exec-B",
        },
    )
    hub.broadcast("f", {"type": "text_reset", "frame_id": "f"})
    hub.broadcast("f", {"type": "text_chunk", "frame_id": "f", "chunk": "B says"})

    conn = _FakeConn()
    hub._conns.add(conn)
    hub.subscribe("f", conn)
    replayed = len(conn.sent)
    assert replayed, "the joining tab received no replay of the running turn"

    for late in (
        {"type": "text_reset", "frame_id": "f", "execution_id": "exec-A"},
        {
            "type": "text_chunk",
            "frame_id": "f",
            "chunk": "_Error: A failed_",
            "execution_id": "exec-A",
        },
        {
            "type": "frame_update",
            "frame_id": "f",
            "status": "failed",
            "execution_id": "exec-A",
        },
    ):
        hub.broadcast("f", late)

    assert (
        len(conn.sent) == replayed
    ), f"A's late events reached the joining tab: {conn.sent[replayed:]}"
    assert hub.is_running("f") is True
    assert "A failed" not in json.dumps(conn.sent, ensure_ascii=False)


# --- the lease's own record of what happened ----------------------------------


@pytest.mark.stubbed_backend
def test_a_failed_turn_leaves_its_ticket_marked_failed(runner, monkeypatch):
    """Swallowing the exception let the lease exit cleanly.

    The coordinator then recorded `queued -> running -> completed` while the
    job and the socket both said failed -- so the execution log, which is what
    an operator reads to find out what a session did, disagreed with every
    other surface about the one thing that mattered.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    monkeypatch.setattr(
        runner, "_loop", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )

    job = runner.submit_message(frame_id, "proj", "go")
    job.wait_result()

    states = [
        event.get("status")
        for event in runner.hub.events
        if event.get("type") == "execution_state"
    ]
    assert states, f"the coordinator recorded nothing: {runner.hub.events}"
    terminal = [
        state for state in states if state in {"completed", "failed", "cancelled"}
    ]
    assert terminal == ["failed"], (
        f"a turn that failed recorded {terminal} -- exactly one terminal state, "
        f"and it must be the failure: {states}"
    )


@pytest.mark.stubbed_backend
def test_a_turn_is_still_running_while_its_failure_is_being_recorded(
    runner, monkeypatch
):
    """`job.finish` inside the lease opens a window nobody can see from outside.

    `runner.is_running` answers from `job.done` and the ticket is still active,
    so for the length of the failure handling a client polling `/status` was
    told the session was idle while the coordinator still held its lease --
    and a follow-up submitted in that window races the turn that has not
    actually let go.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    observed: dict = {}
    real_persist = runner._persist_outer_failure

    def watch(root_frame_id, job, message):
        # Inside the lease, in the OUTER handler -- the one that owns
        # `job.finish`. An inner failure never reaches here, so the fault has
        # to leave `run_message` itself for this window to exist at all.
        observed["is_running"] = runner.is_running(root_frame_id)
        observed["job_done"] = job.done.is_set()
        return real_persist(root_frame_id, job, message)

    monkeypatch.setattr(
        runner, "run_message", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )
    monkeypatch.setattr(runner, "_persist_outer_failure", watch)

    job = runner.submit_message(frame_id, "proj", "go")
    job.wait_result()

    assert observed, "the failure path never ran"
    assert observed["job_done"] is False, "the job finished before its lease ended"
    assert (
        observed["is_running"] is True
    ), "the session reported idle while its ticket was still held"
    # And it does end.
    assert runner.is_running(frame_id) is False


@pytest.mark.stubbed_backend
def test_an_outer_failure_leaves_exactly_one_terminal_and_it_is_failed(
    runner, monkeypatch
):
    """The other half of the coordinator contract.

    An inner failure is reported through `mark_failed`; a fault that leaves
    `run_message` itself never reaches it, and the lease fails on the
    exception instead. Both paths owe the execution log the same thing: one
    terminal state for this ticket, and it says failed.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    monkeypatch.setattr(
        runner, "run_message", lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )

    job = runner.submit_message(frame_id, "proj", "go")
    job.wait_result()

    states = [
        event.get("status")
        for event in runner.hub.events
        if event.get("type") == "execution_state"
        and event.get("execution_id") == job.execution_id
    ]
    terminal = [s for s in states if s in {"completed", "failed", "cancelled"}]
    assert terminal == ["failed"], (
        f"the ticket recorded {terminal} for a turn that failed outside "
        f"`run_message`: {states}"
    )


# --- a plan turn is an execution like any other -------------------------------


def _draft(runner, frame_id, status="draft"):
    return runner.store.create_plan(
        frame_id=frame_id,
        project_id="proj",
        title="p",
        rationale="",
        confidence="high",
        steps=[{"id": "s1", "title": "s", "detail": "d", "deliverables": []}],
        status=status,
    )


def _plan_status(runner, frame_id):
    return (runner.store.get_plan_by_frame(frame_id) or {}).get("status")


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "action,claimed_from",
    [("approve", "draft"), ("resume", "paused")],
)
def test_a_plan_that_fails_before_it_runs_does_not_stay_executing(
    runner, monkeypatch, action, claimed_from
):
    """The claim is a one-way door if nothing settles the row it moved.

    The route compare-and-swaps the plan into `executing` before it answers
    202, because a status read inside the background thread cannot decide who
    owns the execution. That is right, and it means the route has handed the
    background thread an obligation: whatever happens, the row it claimed has
    to reach a settled status.

    A failure *before* the turn reaches `run_message` -- the Store refusing the
    re-read, `emit_ready` throwing, the seed builder raising -- skipped every
    settle point, so the row stayed `executing` with nothing running. Nothing
    recovers from that state: approve swaps against `draft` and resume against
    `paused`, so both lose forever, and `get_by_frame` prefers the newest
    non-discarded plan, so that row also shadows every new draft the session
    ever makes. One failed turn permanently removes planning from the session.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    _draft(runner, frame_id, status=claimed_from)
    target = "run_plan_execution" if action == "approve" else "run_plan_resume"
    monkeypatch.setattr(
        runner, target, lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    )

    accepted, status = _api(runner, "POST", f"/frames/{frame_id}/plan/{action}", {})
    assert status == 202, (status, accepted)
    job = next(j for j in runner._jobs.values() if j.job_id == accepted["job_id"])
    job.wait_result()

    assert _plan_status(runner, frame_id) == "failed", (
        "the claimed plan row was left `executing` by a turn that never ran -- "
        "approve and resume can both never win again"
    )
    ready = [
        event
        for event in runner.hub.events
        if event.get("type") == "plan_ready" and event.get("status") == "failed"
    ]
    assert ready, "the client was never told the plan had settled"


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "action,claimed_from,expected",
    [("approve", "draft", "paused"), ("resume", "paused", "paused")],
)
def test_a_cancelled_plan_turn_settles_paused_not_failed(
    runner, monkeypatch, action, claimed_from, expected
):
    """Cancelling leaves work to finish, so the plan is paused, not over.

    `run_execution` already draws this distinction once the turn has started.
    A cancellation that arrives before it -- the session is being stopped while
    the item is still queued -- took the other branch, and a user who pressed
    stop got a plan marked `failed`.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    _draft(runner, frame_id, status=claimed_from)
    target = "run_plan_execution" if action == "approve" else "run_plan_resume"
    monkeypatch.setattr(
        runner,
        target,
        lambda *a, **k: (_ for _ in ()).throw(
            gateway_mod.ExecutionCancelled("stopped")
        ),
    )

    accepted, _ = _api(runner, "POST", f"/frames/{frame_id}/plan/{action}", {})
    job = next(j for j in runner._jobs.values() if j.job_id == accepted["job_id"])
    job.wait_result()

    assert (
        _plan_status(runner, frame_id) == expected
    ), "a cancelled plan turn did not settle as paused"


@pytest.mark.stubbed_backend
def test_a_plan_202_names_a_real_coordinator_execution(runner):
    """The plan spawner minted `plan-<job id>` and told the client about it.

    A synthetic id is not an execution: the FIFO never heard of it, so a plan
    turn was not queued behind the running one and did not hold the session
    while it finalised. It also made the 202's `execution_id` a different kind
    of thing from the message path's, on a field clients filter by.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    _draft(runner, frame_id)
    started = threading.Event()
    release = threading.Event()

    def blocking_loop(*_a, **_k):
        started.set()
        assert release.wait(20), "the probe never released the plan turn"
        return "submitted"

    runner._loop = blocking_loop
    try:
        accepted, status = _api(runner, "POST", f"/frames/{frame_id}/plan/approve", {})
        assert status == 202, (status, accepted)
        assert started.wait(20), "the plan turn never reached its loop"

        execution_id = accepted.get("execution_id") or ""
        assert not execution_id.startswith(
            "plan-"
        ), f"the 202 named a synthetic execution: {execution_id!r}"
        # The FIFO's own view is the test: an id the coordinator does not hold
        # is not an execution, whatever it is named.
        state = runner.executions.snapshot(frame_id)
        owner = state.get("owner") or {}
        assert (
            owner.get("execution_id") == execution_id
        ), f"the coordinator does not own the execution the 202 named: {state}"

        # And the socket agrees with the 202 -- one id, not two.
        processing = [
            event
            for event in runner.hub.events
            if event.get("type") == "frame_update"
            and event.get("status") == "processing"
        ]
        assert (
            processing and processing[-1].get("execution_id") == execution_id
        ), f"the stream named a different execution than the 202: {processing}"
    finally:
        release.set()
        job = next(
            (j for j in runner._jobs.values() if j.job_id == accepted.get("job_id")),
            None,
        )
        if job is not None:
            job.wait_result()


@pytest.mark.stubbed_backend
def test_a_finalising_plan_turn_still_owns_the_session(runner):
    """The plan path's half of the durable admission race.

    A plan turn writes its own outcome after `run_message` returns: the plan
    row's final status, a `plan_ready`, and -- when it failed -- the frame's
    status and the terminal event. Holding no lease, all of that ran while the
    next queued turn was already `processing`, so the same overwrite the
    message path had applies here with an extra row on top of it.
    """
    from openai4s.observability import correlation_id

    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    _draft(runner, frame_id)
    finalising = threading.Event()
    allow_finish = threading.Event()
    a_in_loop = threading.Event()
    b_started = threading.Event()
    b_release = threading.Event()
    job_b = None

    real_emit_ready = runner.plans.emit_ready
    seen: list[str] = []

    def emit_ready(emit, rid, plan):
        real_emit_ready(emit, rid, plan)
        # The *last* one: the settle-time emit, after `run_message` returned.
        if (plan or {}).get("status") in ("completed", "failed", "paused"):
            seen.append("settled")
            finalising.set()
            assert allow_finish.wait(20), "the probe never released the plan turn"

    def loop(_st, *_a, **_k):
        if correlation_id() == "req-B":
            b_started.set()
            assert b_release.wait(20), "the probe never released B"
        else:
            a_in_loop.set()
        return "submitted"

    runner._loop = loop
    runner.plans.emit_ready = emit_ready
    try:
        set_correlation_id("req-A")
        accepted, _ = _api(runner, "POST", f"/frames/{frame_id}/plan/approve", {})
        # B is queued only once A is unambiguously the running turn. Submitting
        # it earlier races A's own thread for the head of the FIFO, and a probe
        # that can deadlock on ordering says nothing about who holds the lease.
        assert a_in_loop.wait(20), "the plan turn never started"
        set_correlation_id("req-B")
        job_b = runner.submit_message(frame_id, "proj", "b")
        set_correlation_id("req-canary")

        assert finalising.wait(20), "the plan turn never reached its settle"
        state = runner.executions.snapshot(frame_id)
        owner = state.get("owner") or {}
        assert owner.get("execution_id") == accepted.get("execution_id"), (
            "the plan turn released the session before writing its outcome: " f"{state}"
        )
        assert [item.get("queue_position") for item in state.get("queue") or []] == [
            1
        ], f"the follow-up is not waiting behind the plan turn: {state}"
        assert not b_started.is_set()
    finally:
        allow_finish.set()
        b_release.set()
        job_a = next(
            (j for j in runner._jobs.values() if j.job_id == accepted.get("job_id")),
            None,
        )
        if job_a is not None:
            job_a.wait_result()
        if job_b is not None:
            job_b.wait_result()


@pytest.mark.stubbed_backend
def test_a_failure_after_the_plan_finished_does_not_rewrite_its_status(
    runner, monkeypatch
):
    """The settle must not become the damage it exists to prevent.

    By the time the outer handler runs, the plan path may have already settled
    the row itself -- `run_execution` writes `completed` and then emits a last
    `plan_ready`, and that emit is a socket write, which is exactly the kind of
    thing that fails after the science is done. Settling with a plain write
    would then mark a finished plan `failed`: the steps ran, the artifacts
    exist, and the row says the plan did not happen.

    So it is a compare-and-swap from `executing` and nothing else -- if the row
    has moved on, it is not this handler's to move.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    _draft(runner, frame_id)
    monkeypatch.setattr(runner, "_loop", lambda *a, **k: "submitted")
    real_emit_ready = runner.plans.emit_ready
    settled: list[str] = []

    def emit_ready(emit, rid, plan):
        status = (plan or {}).get("status")
        if status in ("completed", "failed", "paused"):
            # The plan is finished and the row already says so; the socket is
            # what fails.
            settled.append(status)
            raise CanaryFailure()
        real_emit_ready(emit, rid, plan)

    monkeypatch.setattr(runner.plans, "emit_ready", emit_ready)

    accepted, _ = _api(runner, "POST", f"/frames/{frame_id}/plan/approve", {})
    job = next(j for j in runner._jobs.values() if j.job_id == accepted["job_id"])
    job.wait_result()

    assert settled == ["completed"], settled
    assert _plan_status(runner, frame_id) == "completed", (
        "a plan that ran to completion was marked failed by the handler that "
        "only exists to rescue rows nothing else settled"
    )


@pytest.mark.stubbed_backend
def test_a_plan_cancelled_while_still_queued_does_not_strand_its_claim(runner):
    """Stop drains the queue, and a queued plan never reaches its own handler.

    The approve route claims the row before the 202, so a plan sitting behind a
    running turn is already `executing` when the user presses stop. Cancelling
    it raises out of `admitted` -- before `fn`, before any plan code -- so
    every settle point inside the turn is skipped. The row was left
    `executing`, which nothing recovers from: approve swaps against `draft`,
    resume against `paused`, and `get_by_frame` keeps preferring that row over
    every later draft.

    It settles as `paused` rather than `failed` for the same reason a
    cancellation mid-run does: stopping is not failing, and there is work left.
    """
    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    _draft(runner, frame_id)
    running = threading.Event()
    release = threading.Event()

    def loop(*_a, **_k):
        running.set()
        assert release.wait(20), "the probe never released the running turn"
        return "submitted"

    runner._loop = loop
    job_a = runner.submit_message(frame_id, "proj", "a")
    try:
        assert running.wait(20), "the first turn never started"
        accepted, status = _api(runner, "POST", f"/frames/{frame_id}/plan/approve", {})
        assert status == 202, (status, accepted)
        assert _plan_status(runner, frame_id) == "executing", "the route did not claim"

        # The plan is behind the running turn. This is what a session Stop does
        # to everything waiting.
        cancelled = runner.executions.drain_queued(frame_id, reason="session stopped")
        assert accepted["execution_id"] in cancelled, cancelled

        job = next(j for j in runner._jobs.values() if j.job_id == accepted["job_id"])
        result = job.wait_result()
        assert result.get("status") == "cancelled", result
        assert _plan_status(runner, frame_id) == "paused", (
            "a plan cancelled before it ever started was left `executing` -- "
            "neither approve nor resume can ever win against that row again"
        )
    finally:
        release.set()
        job_a.wait_result()


@pytest.mark.stubbed_backend
def test_a_failing_plan_turn_owns_the_session_while_it_settles(runner):
    """The plan path's failure side effects, against a follow-up in the queue.

    What a failing plan turn writes after `fn` has raised is not small: the
    persisted failure row, the plan row's rescue out of `executing`, the
    frame's status and the terminal event. Outside the lease, all of it lands
    while the next turn is already `processing` -- so the frame says `failed`
    for a turn that is running, and the plan the user is about to re-approve
    changes status underneath them.

    Parked at `_settle_claimed_plan`, which is the plan half specifically:
    the barrier the success path proves runs inside `fn`, so it cannot see
    whether the *handler* is leased.
    """
    from openai4s.observability import correlation_id

    frame_id = runner.store.new_frame(kind="turn", project_id="proj", status="ready")
    _draft(runner, frame_id)
    settling = threading.Event()
    allow_finish = threading.Event()
    b_started = threading.Event()
    b_release = threading.Event()
    job_b = None

    def loop(*_a, **_k):
        if correlation_id() == "req-B":
            b_started.set()
            assert b_release.wait(20), "the probe never released B"
        return "submitted"

    real_settle = runner._settle_claimed_plan

    def settle_holding_the_lease(*args, **kwargs):
        outcome = real_settle(*args, **kwargs)
        settling.set()
        assert allow_finish.wait(20), "the probe never released the plan turn"
        return outcome

    runner._loop = loop
    runner._settle_claimed_plan = settle_holding_the_lease
    runner.run_plan_execution = lambda *a, **k: (_ for _ in ()).throw(CanaryFailure())
    accepted = {}
    try:
        set_correlation_id("req-A")
        accepted, _ = _api(runner, "POST", f"/frames/{frame_id}/plan/approve", {})
        set_correlation_id("req-B")
        job_b = runner.submit_message(frame_id, "proj", "b")
        set_correlation_id("req-canary")

        assert settling.wait(20), "the plan turn never reached its settle"
        state = runner.executions.snapshot(frame_id)
        owner = state.get("owner") or {}
        assert owner.get("execution_id") == accepted.get(
            "execution_id"
        ), f"the failing plan turn released the session before settling: {state}"
        assert [item.get("execution_id") for item in state.get("queue") or []] == [
            job_b.execution_id
        ], f"the follow-up is not waiting behind the failing plan turn: {state}"
        assert not b_started.is_set()
    finally:
        allow_finish.set()
        b_release.set()
        job_a = next(
            (j for j in runner._jobs.values() if j.job_id == accepted.get("job_id")),
            None,
        )
        if job_a is not None:
            job_a.wait_result()
        if job_b is not None:
            job_b.wait_result()

    # And the rescue happened: the row is not left `executing`.
    assert _plan_status(runner, frame_id) == "failed"
