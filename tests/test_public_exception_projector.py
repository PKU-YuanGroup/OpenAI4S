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
