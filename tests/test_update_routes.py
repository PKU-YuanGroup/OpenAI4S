"""Explicit update checks: admin-only, no-store, and honest when unsure.

The read side must not reach the network or write the cache — opening the
settings page is not an update check. The write side is the operator pressing
the button, and even then a failure is ``unknown`` with a reason; it is never
"up to date", because a user who stops looking after a DNS failure is the
failure mode this feature exists to avoid.

Everything here is offline: the release source is `discovery._SOURCE`, and the
suite-wide `OPENAI4S_UPDATE_SOURCE=offline` pin is lifted only for the tests that
drive that seam. Those tests carry `stubbed_backend`, so a fabricated response
shape cannot be published as the route's captured contract.
"""

from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth, team_policy, update_routes
from openai4s.server.errors import GatewayError
from openai4s.update import discovery

VERSION = "0.4.0"
RUNNING = "0.3.0"
WHEEL = f"openai4s-{VERSION}-py3-none-any.whl"
SDIST = f"openai4s-{VERSION}.tar.gz"
BUNDLE = f"OpenAI4S-{VERSION}-linux-x86_64.tar.gz"
A, B, C = "a" * 64, "b" * 64, "c" * 64

CHECK_KEYS = {
    "status",
    "reason",
    "running",
    "latest",
    "checked_at_ms",
    "cached",
    "age_s",
    "retry_after_at",
    "previous",
    "trust",
}


#: `test_the_real_update_module_is_the_only_implementation`: the route must not
#: grow a second version oracle. The fake source below is *the* real seam the
#: discovery tests use.
def _project_doc(version=VERSION, yanked=False):
    return {"info": {"name": "openai4s", "version": version, "yanked": yanked}}


def _release_doc(*, wheel_digest=A, sdist_digest=B, yanked=False):
    return {
        "urls": [
            {
                "filename": WHEEL,
                "size": 4_000_000,
                "digests": {"sha256": wheel_digest, "md5": "x" * 32},
                "packagetype": "bdist_wheel",
                "yanked": yanked,
                "url": f"https://files.pythonhosted.org/packages/ab/{WHEEL}",
            },
            {
                "filename": SDIST,
                "size": 3_000_000,
                "digests": {"sha256": sdist_digest},
                "packagetype": "sdist",
                "yanked": False,
                "url": f"https://files.pythonhosted.org/packages/cd/{SDIST}",
            },
        ]
    }


def _sums_text(wheel_digest=A, sdist_digest=B, bundle_digest=C):
    return (
        f"{wheel_digest}  {WHEEL}\n"
        f"{sdist_digest}  {SDIST}\n"
        f"{bundle_digest}  {BUNDLE}\n"
    )


class _Source(discovery.ReleaseSource):
    def __init__(self, *, project=None, release=None, sums=None):
        self._project = project if project is not None else _project_doc()
        self._release = release if release is not None else _release_doc()
        self._sums = sums if sums is not None else _sums_text()
        self.calls: list[str] = []

    @staticmethod
    def _answer(value):
        if isinstance(value, BaseException):
            raise value
        return value

    def project(self, index, *, timeout):
        self.calls.append("project")
        return self._answer(self._project)

    def release(self, index, version, *, timeout):
        self.calls.append("release")
        return self._answer(self._release)

    def sums(self, version, *, timeout):
        self.calls.append("sums")
        return self._answer(self._sums)


class _Exploding(discovery.ReleaseSource):
    """Any read at all fails the test."""

    def project(self, index, *, timeout):
        raise AssertionError("the update route dialled the release index")

    def release(self, index, version, *, timeout):
        raise AssertionError("the update route dialled the release index")

    def sums(self, version, *, timeout):
        raise AssertionError("the update route dialled the release index")


@pytest.fixture(autouse=True)
def _fresh_gate():
    update_routes.reset_check_gate_for_tests()
    yield
    update_routes.reset_check_gate_for_tests()


@pytest.fixture
def cfg(tmp_path):
    return SimpleNamespace(data_dir=tmp_path / "data")


@pytest.fixture
def online(monkeypatch):
    """Lift the suite's offline pin and replace the release source seam."""

    monkeypatch.delenv("OPENAI4S_UPDATE_SOURCE", raising=False)
    monkeypatch.setattr(discovery, "running_version", lambda: RUNNING)

    def install(source):
        monkeypatch.setattr(discovery, "_SOURCE", source)
        return source

    return install


class _Handler:
    """The two response writers the route uses, with no socket."""

    def __init__(
        self,
        *,
        path="/updates/status",
        body=None,
        identity=None,
        request_id="req-update",
    ):
        self.path = path
        self.command = "GET"
        self.headers = {}
        self._correlation_id = request_id
        if identity is not None:
            self._team_identity = identity
        self._body_value = body
        self.sent = None

    def _body(self):
        if isinstance(self._body_value, BaseException):
            raise self._body_value
        return {} if self._body_value is None else self._body_value

    def _json(self, payload, code=200):
        self.sent = (code, payload, {})

    def _send_static_bytes(self, code, body, ctype, extra, security):
        self.sent = (code, json.loads(body.decode("utf-8")), dict(extra or {}))


def _call(handler, method, path, cfg):
    owned = update_routes.handle(handler, method, path, cfg=cfg)
    return owned, handler.sent


def _document(**overrides):
    now = int(time.time() * 1000)
    params = {
        "status": "up_to_date",
        "reason": "ok",
        "running": RUNNING,
        "checked_at_ms": now,
        "retry_after_at": now + discovery.SUCCESS_TTL_MS,
        "latest": RUNNING,
        "index": discovery.DEFAULT_INDEX,
    }
    params.update(overrides)
    return discovery._document(**params)


def _write_cache(cfg, document):
    assert discovery.write_cache(discovery.cache_path(cfg), document)


def _no_check(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("the read-only status route ran a check")

    monkeypatch.setattr(discovery, "check", boom)


# --------------------------------------------------------------------------
# the surface itself
# --------------------------------------------------------------------------


def test_both_routes_are_declared_and_admin_only_in_team_mode():
    from openai4s.server import contract

    routes = contract.http_routes()
    assert "/updates/status" in routes
    assert "/updates/check" in routes
    names = {spec.name for spec in update_routes.ROUTES}
    assert names == {"updates.status", "updates.check"}
    assert team_policy.is_daemon_operation("GET", "/updates/status")
    assert team_policy.is_daemon_operation("POST", "/updates/check")
    assert team_policy.is_admin_only_surface("GET", "/updates/status")
    assert team_policy.is_admin_only_surface("POST", "/updates/check")
    assert not team_policy.is_instance_mutation("GET", "/updates/status")


@pytest.mark.stubbed_backend
def test_a_member_is_refused_before_the_cache_or_the_network(cfg, monkeypatch):
    """The refusal is a policy answer, not a failed check: nothing is read,
    written or dialled on the member's behalf."""
    member = SimpleNamespace(is_admin=False, user_id="bob")
    monkeypatch.setattr(
        discovery,
        "read_cache",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("read the cache")),
    )
    _no_check(monkeypatch)
    monkeypatch.setattr(discovery, "_SOURCE", _Exploding())

    for method, path, body in (
        ("GET", "/updates/status", None),
        ("POST", "/updates/check", {"refresh": True}),
    ):
        handler = _Handler(path=path, body=body, identity=member)
        owned, sent = _call(handler, method, path, cfg)
        assert owned is True
        assert sent[0] == 403
        assert sent[1]["code"] == "admin_only"
        assert sent[2]["Cache-Control"] == "no-store"


def test_an_unrelated_path_is_not_owned(cfg):
    handler = _Handler()
    assert _call(handler, "GET", "/updates/other", cfg) == (False, None)


def test_the_wrong_verb_does_not_answer_a_route(cfg):
    for method, path in (
        ("POST", "/updates/status"),
        ("GET", "/updates/check"),
        ("DELETE", "/updates/check"),
    ):
        handler = _Handler(path=path)
        assert _call(handler, method, path, cfg) == (False, None)


# --------------------------------------------------------------------------
# GET /updates/status: a read, and only a read
# --------------------------------------------------------------------------


@pytest.mark.stubbed_backend
def test_no_record_says_not_checked_and_touches_nothing(cfg, monkeypatch):
    _no_check(monkeypatch)
    monkeypatch.setattr(discovery, "_SOURCE", _Exploding())
    handler = _Handler(path="/updates/status")

    owned, sent = _call(handler, "GET", "/updates/status", cfg)

    assert owned is True
    code, payload, headers = sent
    assert code == 200
    assert headers["Cache-Control"] == "no-store"
    assert set(payload) == {"request_id", "channel", "check", "apply_available"}
    assert set(payload["channel"]) == {"id"}
    assert payload["apply_available"] is False
    assert payload["request_id"] == "req-update"
    check = payload["check"]
    assert set(check) == CHECK_KEYS
    assert check["status"] == "unknown"
    assert check["reason"] == update_routes.REASON_NOT_CHECKED
    assert check["cached"] is False
    assert check["age_s"] is None, "an unread age must not read as zero seconds old"
    assert check["checked_at_ms"] == 0
    assert check["retry_after_at"] == 0
    assert check["latest"] == ""
    assert check["previous"] == {}
    assert check["trust"] == {}
    assert not (Path(cfg.data_dir) / "updates").exists()


@pytest.mark.stubbed_backend
def test_a_cached_answer_is_read_without_checking_or_writing(cfg, monkeypatch):
    checked_at = int(time.time() * 1000) - 120_000
    _write_cache(
        cfg,
        _document(
            status="update_available",
            checked_at_ms=checked_at,
            latest=VERSION,
            previous={},
        ),
    )
    monkeypatch.setattr(discovery, "running_version", lambda: RUNNING)
    _no_check(monkeypatch)
    monkeypatch.setattr(discovery, "_SOURCE", _Exploding())
    cache_file = discovery.cache_path(cfg)
    before = cache_file.read_bytes()

    handler = _Handler(path="/updates/status")
    _owned, sent = _call(handler, "GET", "/updates/status", cfg)

    code, payload, _headers = sent
    assert code == 200
    check = payload["check"]
    assert check["status"] == "update_available"
    assert check["latest"] == VERSION
    assert check["cached"] is True
    assert check["checked_at_ms"] == checked_at
    assert isinstance(check["age_s"], int) and check["age_s"] >= 120
    assert cache_file.read_bytes() == before, "a read rewrote the check cache"


@pytest.mark.stubbed_backend
def test_a_cache_from_another_install_is_not_this_installs_answer(cfg, monkeypatch):
    _write_cache(cfg, _document(running="0.2.0"))
    monkeypatch.setattr(discovery, "running_version", lambda: RUNNING)
    _no_check(monkeypatch)

    handler = _Handler(path="/updates/status")
    _owned, sent = _call(handler, "GET", "/updates/status", cfg)
    check = sent[1]["check"]
    assert check["status"] == "unknown"
    assert check["reason"] == update_routes.REASON_NOT_CHECKED
    assert check["cached"] is False


@pytest.mark.stubbed_backend
def test_a_cache_from_another_index_is_not_this_installs_answer(cfg, monkeypatch):
    _write_cache(cfg, _document(index="https://mirror.example.org/simple"))
    monkeypatch.setattr(discovery, "running_version", lambda: RUNNING)
    _no_check(monkeypatch)

    handler = _Handler(path="/updates/status")
    _owned, sent = _call(handler, "GET", "/updates/status", cfg)
    assert sent[1]["check"]["reason"] == update_routes.REASON_NOT_CHECKED


@pytest.mark.stubbed_backend
def test_a_cached_failure_carries_the_previous_known_answer(cfg, monkeypatch):
    """The failure is this check; the previous answer is not this check."""
    now = int(time.time() * 1000)
    previous_at = now - 3 * 60 * 60 * 1000
    _write_cache(
        cfg,
        _document(
            status="unknown",
            reason="unreachable",
            checked_at_ms=now - 60_000,
            latest="",
            retry_after_at=now + discovery.FAILURE_RETRY_MS,
            previous={
                "status": "update_available",
                "latest": VERSION,
                "checked_at_ms": previous_at,
            },
        ),
    )
    monkeypatch.setattr(discovery, "running_version", lambda: RUNNING)
    _no_check(monkeypatch)

    handler = _Handler(path="/updates/status")
    _owned, sent = _call(handler, "GET", "/updates/status", cfg)
    check = sent[1]["check"]
    assert check["status"] == "unknown"
    assert check["reason"] == "unreachable"
    assert check["previous"] == {
        "status": "update_available",
        "latest": VERSION,
        "checked_at_ms": previous_at,
    }
    assert check["checked_at_ms"] != check["previous"]["checked_at_ms"]


@pytest.mark.stubbed_backend
def test_the_cache_trust_projection_is_a_whitelist(cfg, monkeypatch):
    secret = "Zx9Qw3Er7Ty1Ui5Op2As6Df4Gh8Jk0Lm"
    _write_cache(
        cfg,
        _document(
            status="update_available",
            latest=VERSION,
            witnesses={
                "version": VERSION,
                "pypi": {"wheel": A},
                "trust": {
                    "integrity": "integrity: verified against two witnesses",
                    "authorship": "not established",
                    "authorship_detail": "authorship: not established",
                    "debug_headers": {"Authorization": secret},
                },
            },
        ),
    )
    monkeypatch.setattr(discovery, "running_version", lambda: RUNNING)
    _no_check(monkeypatch)

    handler = _Handler(path="/updates/status")
    _owned, sent = _call(handler, "GET", "/updates/status", cfg)
    trust = sent[1]["check"]["trust"]
    assert set(trust) == {"integrity", "authorship", "authorship_detail"}
    assert secret not in json.dumps(sent[1])


@pytest.mark.stubbed_backend
def test_a_cache_with_an_unreadable_shape_reads_as_not_checked(cfg, monkeypatch):
    cache_file = discovery.cache_path(cfg)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('{"status": "not-a-status"}', encoding="utf-8")
    monkeypatch.setattr(discovery, "running_version", lambda: RUNNING)
    _no_check(monkeypatch)

    handler = _Handler(path="/updates/status")
    _owned, sent = _call(handler, "GET", "/updates/status", cfg)
    assert sent[0] == 200
    assert sent[1]["check"]["reason"] == update_routes.REASON_NOT_CHECKED


# --------------------------------------------------------------------------
# POST /updates/check: the explicit action
# --------------------------------------------------------------------------


@pytest.mark.stubbed_backend
def test_the_body_is_exactly_a_boolean_refresh(cfg, monkeypatch):
    _no_check(monkeypatch)

    for body in (
        {},
        {"refresh": "true"},
        {"refresh": 1},
        {"refresh": True, "index": "https://evil.example.org/simple"},
        {"refresh": True, "host": "evil.example.org"},
    ):
        handler = _Handler(path="/updates/check", body=body)
        owned, sent = _call(handler, "POST", "/updates/check", cfg)
        assert owned is True
        assert sent[0] == 400, body
        assert sent[1]["code"] == "invalid_request"
        assert sent[2]["Cache-Control"] == "no-store"


@pytest.mark.stubbed_backend
def test_a_malformed_json_body_is_the_same_refusal(cfg, monkeypatch):
    _no_check(monkeypatch)
    handler = _Handler(
        path="/updates/check",
        body=GatewayError(400, "request body is not valid JSON", "malformed_json"),
    )
    _owned, sent = _call(handler, "POST", "/updates/check", cfg)
    assert sent[0] == 400
    assert sent[1]["code"] == "malformed_json"
    assert sent[2]["Cache-Control"] == "no-store"


@pytest.mark.stubbed_backend
def test_the_offline_pin_short_circuits_before_the_source(cfg, monkeypatch):
    source = _Exploding()
    monkeypatch.setattr(discovery, "_SOURCE", source)
    handler = _Handler(path="/updates/check", body={"refresh": True})

    _owned, sent = _call(handler, "POST", "/updates/check", cfg)

    code, payload, headers = sent
    assert code == 200
    assert headers["Cache-Control"] == "no-store"
    assert payload["check"]["status"] == "unknown"
    assert payload["check"]["reason"] == "offline_pin"
    assert payload["check"]["cached"] is False
    assert payload["check"]["age_s"] == 0
    assert payload["apply_available"] is False


@pytest.mark.stubbed_backend
def test_a_forced_check_reports_update_available_from_the_real_module(cfg, online):
    source = online(_Source())
    handler = _Handler(path="/updates/check", body={"refresh": True})

    _owned, sent = _call(handler, "POST", "/updates/check", cfg)

    code, payload, headers = sent
    assert code == 200
    assert headers["Cache-Control"] == "no-store"
    assert set(payload) == {"request_id", "channel", "check", "apply_available"}
    assert payload["apply_available"] is False
    check = payload["check"]
    assert set(check) == CHECK_KEYS
    assert check["status"] == "update_available"
    assert check["reason"] == "ok"
    assert check["latest"] == VERSION
    assert check["running"] == RUNNING
    assert check["cached"] is False
    assert check["age_s"] == 0
    assert check["previous"] == {}
    assert check["trust"]["integrity"]
    assert check["trust"]["authorship"] == "not established"
    assert source.calls == ["project", "release", "sums"]
    rendered = json.dumps(payload)
    assert "files.pythonhosted.org" not in rendered
    assert "download" not in rendered
    assert str(Path(cfg.data_dir)) not in rendered


@pytest.mark.stubbed_backend
def test_a_cached_check_is_served_without_dialling_again(cfg, online):
    source = online(_Source())
    first = _Handler(path="/updates/check", body={"refresh": False})
    _owned, sent = _call(first, "POST", "/updates/check", cfg)
    assert sent[1]["check"]["cached"] is False
    calls = list(source.calls)
    assert calls

    second = _Handler(path="/updates/check", body={"refresh": False})
    _owned, sent = _call(second, "POST", "/updates/check", cfg)
    assert sent[0] == 200
    assert sent[1]["check"]["cached"] is True
    assert source.calls == calls, "a fresh cache was re-dialled"


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "failure,reason",
    [
        (urllib.error.URLError("name resolution failed"), "unreachable"),
        (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
        (ValueError("not a release document"), "malformed"),
    ],
)
def test_a_failure_is_unknown_and_keeps_the_previous_answer(
    cfg, online, failure, reason
):
    """A failure never becomes "latest". The previous answer is carried, with
    its own timestamp, so the UI can show it as history rather than this
    check's result."""
    now = int(time.time() * 1000)
    previous_at = now - 2 * 60 * 60 * 1000
    _write_cache(
        cfg,
        _document(
            status="update_available",
            latest="0.3.9",
            checked_at_ms=previous_at,
        ),
    )
    online(_Source(project=failure))
    handler = _Handler(path="/updates/check", body={"refresh": True})

    _owned, sent = _call(handler, "POST", "/updates/check", cfg)

    code, payload, _headers = sent
    assert code == 200
    check = payload["check"]
    assert check["status"] == "unknown"
    assert check["status"] != "up_to_date"
    assert check["reason"] == reason
    assert check["previous"] == {
        "status": "update_available",
        "latest": "0.3.9",
        "checked_at_ms": previous_at,
    }
    assert check["checked_at_ms"] != previous_at


@pytest.mark.stubbed_backend
def test_witness_disagreement_is_never_up_to_date(cfg, online):
    source = _Source(sums=_sums_text(wheel_digest="f" * 64))
    online(source)
    handler = _Handler(path="/updates/check", body={"refresh": True})
    _owned, sent = _call(handler, "POST", "/updates/check", cfg)
    check = sent[1]["check"]
    assert check["status"] == "unknown"
    assert check["reason"] == "witness_disagreement"
    assert check["latest"] == ""


@pytest.mark.stubbed_backend
def test_a_malformed_release_document_is_never_up_to_date(cfg, online):
    online(_Source(project={"info": {"version": "not-a-version"}}))
    handler = _Handler(path="/updates/check", body={"refresh": True})
    _owned, sent = _call(handler, "POST", "/updates/check", cfg)
    assert sent[1]["check"]["status"] == "unknown"
    assert sent[1]["check"]["reason"] == "malformed"


@pytest.mark.stubbed_backend
def test_an_invalid_index_setting_is_unknown_without_a_dial(cfg, monkeypatch):
    monkeypatch.setenv("OPENAI4S_UPDATE_INDEX", "http://evil.example.org/simple")
    source = _Exploding()
    monkeypatch.setattr(discovery, "_SOURCE", source)
    handler = _Handler(path="/updates/check", body={"refresh": True})
    _owned, sent = _call(handler, "POST", "/updates/check", cfg)
    assert sent[0] == 200
    assert sent[1]["check"]["status"] == "unknown"
    assert sent[1]["check"]["reason"] == "bad_index"


@pytest.mark.stubbed_backend
def test_a_second_forced_check_is_rate_limited(cfg, online):
    source = online(_Source())
    first = _Handler(path="/updates/check", body={"refresh": True})
    _owned, sent = _call(first, "POST", "/updates/check", cfg)
    assert sent[0] == 200
    calls = list(source.calls)

    second = _Handler(
        path="/updates/check", body={"refresh": True}, request_id="req-second"
    )
    _owned, sent = _call(second, "POST", "/updates/check", cfg)
    code, payload, headers = sent
    assert code == 429
    assert payload["code"] == "rate_limited"
    assert payload["reason"] == "refresh_cooldown"
    assert 1 <= payload["retry_after_s"] <= 60
    assert headers["Retry-After"] == str(payload["retry_after_s"])
    assert headers["Cache-Control"] == "no-store"
    assert source.calls == calls, "a cooled-down request still dialled"

    # Only the *forced* refresh is cooled down; a normal read is not.
    third = _Handler(path="/updates/check", body={"refresh": False})
    _owned, sent = _call(third, "POST", "/updates/check", cfg)
    assert sent[0] == 200
    assert sent[1]["check"]["cached"] is True


@pytest.mark.stubbed_backend
def test_only_one_check_runs_at_a_time(cfg, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def held(_cfg, *, refresh=False):
        calls.append(1)
        started.set()
        assert release.wait(10)
        return _document(status="unknown", reason="unreachable", latest="")

    monkeypatch.setattr(discovery, "check", held)
    first = _Handler(path="/updates/check", body={"refresh": True})
    result: dict = {}

    def run_first():
        result["sent"] = _call(first, "POST", "/updates/check", cfg)[1]

    worker = threading.Thread(target=run_first)
    worker.start()
    try:
        assert started.wait(5)
        second = _Handler(path="/updates/check", body={"refresh": True})
        _owned, sent = _call(second, "POST", "/updates/check", cfg)
        assert sent[0] == 429
        assert sent[1]["reason"] == "check_in_progress"
        release.set()
        worker.join(10)
        assert result["sent"][0] == 200
        assert len(calls) == 1, "the second click executed a second check"
    finally:
        release.set()
        worker.join(10)


@pytest.mark.stubbed_backend
def test_apply_available_is_always_false(cfg, online):
    """This version reports and points at a manual upgrade; it never installs."""
    online(_Source())
    _write_cache(cfg, _document())
    for method, path, body in (
        ("GET", "/updates/status", None),
        ("POST", "/updates/check", {"refresh": False}),
    ):
        handler = _Handler(path=path, body=body)
        _owned, sent = _call(handler, method, path, cfg)
        assert sent[1]["apply_available"] is False


# --------------------------------------------------------------------------
# through the gateway: dispatch, auth, and the team guard
# --------------------------------------------------------------------------


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


def _api(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)
    token = local_auth.read_token(tmp_path) or ""

    def call(method, path, body=None):
        handler = object.__new__(handler_class)
        handler._correlation_id = "req-gateway"
        sent: dict = {}
        handler._send = (
            lambda code, payload, ctype, extra=None, security=None: sent.update(
                code=code, headers=dict(extra or {})
            )
        )
        handler._send_static_bytes = (
            lambda code, payload, ctype, extra, security: sent.update(
                code=code,
                body=json.loads(payload.decode("utf-8")),
                headers=dict(extra or {}),
            )
        )
        handler.command = method
        handler.path = f"/api/v1{path}"
        raw = json.dumps(body or {}).encode("utf-8")
        handler.headers = {
            "Content-Length": str(len(raw)) if body is not None else "0",
            "Content-Type": "application/json",
            "X-Request-Id": "req-gateway",
            local_auth.TOKEN_HEADER: token,
        }
        handler.rfile = io.BytesIO(raw if body is not None else b"")
        handler._route(method)
        return sent

    def close():
        runner.close()

    return call, close, cfg


def test_the_gateway_dispatches_both_routes_with_no_store(tmp_path):
    call, close, cfg = _api(tmp_path)
    try:
        sent = call("GET", "/updates/status")
        assert sent["code"] == 200
        assert sent["headers"]["Cache-Control"] == "no-store"
        assert sent["body"]["request_id"] == "req-gateway"
        assert sent["body"]["check"]["reason"] == update_routes.REASON_NOT_CHECKED

        sent = call("POST", "/updates/check", {"refresh": True})
        assert sent["code"] == 200
        assert sent["headers"]["Cache-Control"] == "no-store"
        # The suite pins OPENAI4S_UPDATE_SOURCE=offline, so this is the real
        # short-circuit rather than a stubbed success.
        assert sent["body"]["check"]["reason"] == "offline_pin"

        sent = call("POST", "/updates/check", {})
        assert sent["code"] == 400
        assert sent["body"]["code"] == "invalid_request"

        # A second forced check is the cooldown refusal; it is driven through
        # the real handler so its no-store + Retry-After shape is captured too.
        sent = call("POST", "/updates/check", {"refresh": True})
        assert sent["code"] == 429
        assert sent["body"]["code"] == "rate_limited"
        assert sent["body"]["reason"] == "refresh_cooldown"
        assert sent["headers"]["Cache-Control"] == "no-store"
        assert sent["headers"]["Retry-After"] == str(sent["body"]["retry_after_s"])

        # A cached answer reaches the client through the gateway too: an age in
        # seconds, the previous known answer, and the whitelisted trust lines.
        now = int(time.time() * 1000)
        previous_at = now - 4 * 60 * 60 * 1000
        from openai4s.update import verify

        witnesses = verify.agree(
            "9.9.9",
            {WHEEL: A, SDIST: B},
            {WHEEL: A, SDIST: B, BUNDLE: C},
            wheel=WHEEL,
            allow_single_witness=True,
        )
        _write_cache(
            cfg,
            _document(
                status="update_available",
                latest="9.9.9",
                running=discovery.running_version(),
                checked_at_ms=now - 120_000,
                previous={
                    "status": "up_to_date",
                    "latest": discovery.running_version(),
                    "checked_at_ms": previous_at,
                },
                witnesses=witnesses.as_dict(),
            ),
        )
        sent = call("GET", "/updates/status")
        assert sent["code"] == 200
        check = sent["body"]["check"]
        assert check["cached"] is True
        assert isinstance(check["age_s"], int) and check["age_s"] >= 120
        assert check["previous"]["checked_at_ms"] == previous_at
        assert set(check["trust"]) == {"integrity", "authorship", "authorship_detail"}

        sent = call("GET", "/updates/check")
        assert sent["code"] in (404, 405)
    finally:
        close()
