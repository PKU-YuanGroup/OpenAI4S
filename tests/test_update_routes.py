"""Explicit update checks: read-only status, and a check the operator pressed.

Appendix B.2 freezes one shape for both routes: the running version, a channel
projection of three static fields, `check` (or `null`), and `apply_available:
false`. The assertions that matter are the negative ones:

* opening the settings page must not reach the network or write the check cache
  (the GET only reads it), and `OPENAI4S_UPDATE_SOURCE=offline` must not even
  read it;
* a failure must never be reported as "up to date", and `previous` must keep its
  own timestamp instead of passing as this check's answer;
* the response must carry none of discovery's internals, `Channel.evidence`, an
  install path, a credential or a download URL;
* a member is refused before the cache or a socket, and one forced refresh
  cannot dial the index twice.

The two default-suite (offline) tests replace no service: their real shapes are
what the response-schema capture freezes. Everything that replaces the release
source carries `stubbed_backend`, so a fabricated shape cannot be published as
the captured contract.
"""

from __future__ import annotations

import ast
import io
import json
import threading
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s import egress, webtools
from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth, team_policy, update_routes
from openai4s.update import discovery, verify

VERSION = "0.4.0"
RUNNING = "0.3.0"
WHEEL = f"openai4s-{VERSION}-py3-none-any.whl"
SDIST = f"openai4s-{VERSION}.tar.gz"
BUNDLE = f"OpenAI4S-{VERSION}-linux-x86_64.tar.gz"
A, B, C = "a" * 64, "b" * 64, "c" * 64

#: Appendix B.2's 200 shape, exactly.
CHANNEL_KEYS = {"id", "refusal_text_key", "refusal_command"}
CHECK_KEYS = {
    "status",
    "reason",
    "latest",
    "source",
    "checked_at_ms",
    "retry_after_at",
    "cached",
    "age_s",
    "previous",
    "trust",
}
TRUST_KEYS = {"integrity", "authorship"}
#: Never published: discovery internals, Channel.evidence, install paths,
#: download URLs, and the route-level request id the DTO does not carry.
FORBIDDEN_KEYS = {
    "detail",
    "assets",
    "index",
    "witnesses",
    "evidence",
    "install_root",
    "interpreter",
    "request_id",
}


def _project_doc(version=VERSION, yanked=False):
    return {"info": {"name": "openai4s", "version": version, "yanked": yanked}}


def _release_doc(*, wheel_digest=A, sdist_digest=B):
    return {
        "urls": [
            {
                "filename": WHEEL,
                "size": 4_000_000,
                "digests": {"sha256": wheel_digest, "md5": "x" * 32},
                "packagetype": "bdist_wheel",
                "yanked": False,
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


def _release_doc_without_wheel():
    return {
        "urls": [
            item
            for item in _release_doc()["urls"]
            if item["packagetype"] != "bdist_wheel"
        ]
    }


def _sums_text(wheel_digest=A, sdist_digest=B, bundle_digest=C):
    return (
        f"{wheel_digest}  {WHEEL}\n"
        f"{sdist_digest}  {SDIST}\n"
        f"{bundle_digest}  {BUNDLE}\n"
    )


class _Source(discovery.ReleaseSource):
    """A scripted release source. Any member may be an exception to raise."""

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
        "status": "update_available",
        "reason": "ok",
        "running": RUNNING,
        "checked_at_ms": now,
        "retry_after_at": now + discovery.SUCCESS_TTL_MS,
        "latest": VERSION,
        "index": discovery.DEFAULT_INDEX,
    }
    params.update(overrides)
    return discovery._document(**params)


def _witnesses(*, single=False):
    return verify.agree(
        VERSION,
        {WHEEL: A, SDIST: B},
        {} if single else {WHEEL: A, SDIST: B, BUNDLE: C},
        wheel=WHEEL,
        allow_single_witness=single,
    ).as_dict()


def _write_cache(cfg, document):
    assert discovery.write_cache(discovery.cache_path(cfg), document)


def _cache_file(cfg):
    return discovery.cache_path(cfg)


def _no_check(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("the read-only status route ran a check")

    monkeypatch.setattr(discovery, "check", boom)


def _assert_whitelisted(payload):
    blob = json.dumps(payload)
    for key in FORBIDDEN_KEYS:
        assert f'"{key}"' not in blob, (key, payload)
    assert "http://" not in blob and "https://" not in blob
    assert "<redacted" not in blob


# --------------------------------------------------------------------------
# the surface itself
# --------------------------------------------------------------------------


def test_both_routes_are_declared_and_admin_only_in_team_mode():
    from openai4s.server import contract

    routes = contract.http_routes()
    assert "/updates/status" in routes
    assert "/updates/check" in routes
    assert {spec.name for spec in update_routes.ROUTES} == {
        "updates.status",
        "updates.check",
    }
    # F14 registers the surface as a daemon-operation prefix, which is what
    # makes the gateway guard cover every verb (including the GET).
    assert "/updates/" in team_policy.DAEMON_OPERATION_PREFIXES
    assert team_policy.is_daemon_operation("GET", "/updates/status")
    assert team_policy.is_daemon_operation("POST", "/updates/check")
    assert team_policy.is_admin_only_surface("GET", "/updates/status")
    assert team_policy.is_admin_only_surface("POST", "/updates/check")


def test_unrelated_paths_and_wrong_verbs_are_not_owned(cfg):
    assert _call(_Handler(), "GET", "/updates/other", cfg) == (False, None)
    for method, path in (
        ("POST", "/updates/status"),
        ("GET", "/updates/check"),
        ("DELETE", "/updates/check"),
    ):
        assert _call(_Handler(path=path), method, path, cfg) == (False, None)


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
    assert not _cache_file(cfg).exists()


# --------------------------------------------------------------------------
# GET /updates/status: the read
# --------------------------------------------------------------------------


def test_offline_pin_returns_the_offline_state_without_reading_the_cache(cfg):
    """The suite pins `OPENAI4S_UPDATE_SOURCE=offline`. The GET answers the
    offline state and must not even read the cache file: reporting a recorded
    answer as this install's current state is the claim the pin forbids."""
    from openai4s.update.channel import CHANNEL_IDS

    _write_cache(cfg, _document(status="up_to_date", latest=RUNNING))
    before = _cache_file(cfg).read_bytes()

    handler = _Handler(path="/updates/status")
    owned, sent = _call(handler, "GET", "/updates/status", cfg)

    assert owned is True
    code, payload, headers = sent
    assert code == 200
    assert headers["Cache-Control"] == "no-store"
    assert set(payload) == {"running", "channel", "check", "apply_available"}
    assert payload["running"]
    assert set(payload["channel"]) == CHANNEL_KEYS
    assert payload["channel"]["id"] in CHANNEL_IDS
    assert isinstance(payload["channel"]["refusal_command"], list)
    assert payload["apply_available"] is False
    check = payload["check"]
    assert check is not None
    assert set(check) == CHECK_KEYS
    assert check["status"] == "unknown"
    assert check["reason"] == "offline_pin"
    assert check["source"] == "offline"
    assert check["latest"] == ""
    assert check["cached"] is False
    assert check["age_s"] == 0
    assert check["checked_at_ms"] == 0
    assert check["retry_after_at"] == 0
    assert check["previous"] == {}
    assert set(check["trust"]) == TRUST_KEYS
    assert check["trust"] == {"integrity": "none", "authorship": "not_established"}
    assert _cache_file(cfg).read_bytes() == before, "the read rewrote the cache"


def test_offline_get_creates_no_cache_file(tmp_path):
    fresh = SimpleNamespace(data_dir=tmp_path / "fresh")
    handler = _Handler(path="/updates/status")
    _owned, sent = _call(handler, "GET", "/updates/status", fresh)
    assert sent[0] == 200
    assert not (Path(fresh.data_dir) / "updates").exists()


def test_get_without_a_record_returns_a_null_check(tmp_path, monkeypatch):
    """No record is `"check": null`, not an invented "not checked" object, and
    reading it writes nothing."""
    monkeypatch.delenv("OPENAI4S_UPDATE_SOURCE", raising=False)
    cfg = SimpleNamespace(data_dir=tmp_path / "empty")
    handler = _Handler(path="/updates/status")
    _owned, sent = _call(handler, "GET", "/updates/status", cfg)
    code, payload, headers = sent
    assert code == 200
    assert headers["Cache-Control"] == "no-store"
    assert payload["check"] is None
    assert not (Path(cfg.data_dir) / "updates").exists()


@pytest.mark.stubbed_backend
def test_get_never_runs_a_check(cfg, monkeypatch):
    _no_check(monkeypatch)
    monkeypatch.delenv("OPENAI4S_UPDATE_SOURCE", raising=False)
    handler = _Handler(path="/updates/status")
    _owned, sent = _call(handler, "GET", "/updates/status", cfg)
    assert sent[0] == 200
    assert sent[1]["check"] is None


def test_get_projects_the_cached_answer_without_writing(cfg, monkeypatch):
    monkeypatch.delenv("OPENAI4S_UPDATE_SOURCE", raising=False)
    checked_at = int(time.time() * 1000) - 120_000
    _write_cache(
        cfg,
        _document(
            status="update_available",
            checked_at_ms=checked_at,
            witnesses=_witnesses(),
        ),
    )
    _no_check(monkeypatch)
    before = _cache_file(cfg).read_bytes()

    handler = _Handler(path="/updates/status")
    _owned, sent = _call(handler, "GET", "/updates/status", cfg)

    code, payload, _headers = sent
    assert code == 200
    check = payload["check"]
    assert check["status"] == "update_available"
    assert check["reason"] == "ok"
    assert check["latest"] == VERSION
    assert check["source"] == "pypi"
    assert check["cached"] is True
    assert check["checked_at_ms"] == checked_at
    assert isinstance(check["age_s"], int) and check["age_s"] >= 120
    assert check["trust"] == {
        "integrity": "two_witnesses",
        "authorship": "not_established",
    }
    assert _cache_file(cfg).read_bytes() == before


def test_get_ignores_an_invalid_cache_record(cfg, monkeypatch):
    monkeypatch.delenv("OPENAI4S_UPDATE_SOURCE", raising=False)
    cache_file = _cache_file(cfg)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('{"status": "not-a-status"}', encoding="utf-8")

    handler = _Handler(path="/updates/status")
    _owned, sent = _call(handler, "GET", "/updates/status", cfg)
    assert sent[0] == 200
    assert sent[1]["check"] is None


@pytest.mark.stubbed_backend
def test_the_channel_projection_is_static_and_path_free(cfg, monkeypatch):
    """`Channel.evidence`, `install_root` and `interpreter` name absolute paths;
    the response carries only the three static B.2 fields."""
    from openai4s.update import channel as update_channel

    channel = update_channel.Channel(
        id="source",
        self_update=False,
        restart_supported=False,
        install_root=Path("/Users/alice/secret-checkout"),
        interpreter=Path("/Users/alice/secret-venv/bin/python"),
        refusal="this install runs from a git worktree",
        refusal_command=("git pull", "uv sync --locked"),
        refusal_text_key="update.refusal.source",
        evidence=("/Users/alice/secret-checkout/pyproject.toml",),
    )
    monkeypatch.setattr(update_channel, "detect", lambda _cfg: channel)

    handler = _Handler(path="/updates/status")
    _owned, sent = _call(handler, "GET", "/updates/status", cfg)

    payload = sent[1]
    assert payload["channel"] == {
        "id": "source",
        "refusal_text_key": "update.refusal.source",
        "refusal_command": ["git pull", "uv sync --locked"],
    }
    rendered = json.dumps(payload)
    assert "/Users/alice" not in rendered
    assert "secret-checkout" not in rendered
    assert "evidence" not in rendered
    assert "install_root" not in rendered


# --------------------------------------------------------------------------
# POST /updates/check: the explicit action
# --------------------------------------------------------------------------


def test_the_body_is_an_object_with_at_most_a_boolean_refresh(cfg, monkeypatch):
    # The accepted bodies run the real (offline-pinned) check; the refused ones
    # never reach it, so the check seam is made to fail loudly for those.
    for body in ({}, {"refresh": True}, {"refresh": False}):
        handler = _Handler(path="/updates/check", body=body)
        owned, sent = _call(handler, "POST", "/updates/check", cfg)
        assert owned is True
        assert sent[0] == 200, body
        assert sent[2]["Cache-Control"] == "no-store"

    update_routes.reset_check_gate_for_tests()
    _no_check(monkeypatch)
    for body in (
        {"refresh": "true"},
        {"refresh": 1},
        {"refresh": None},
        {"refresh": True, "index": "https://evil.example.org/simple"},
        {"refresh": True, "host": "evil.example.org"},
    ):
        handler = _Handler(path="/updates/check", body=body)
        _owned, sent = _call(handler, "POST", "/updates/check", cfg)
        assert sent[0] == 400, body
        assert sent[1]["code"] == "bad_request"
        assert sent[2]["Cache-Control"] == "no-store"


def test_a_malformed_json_or_non_object_body_is_bad_request(cfg, monkeypatch):
    from openai4s.server.errors import GatewayError

    _no_check(monkeypatch)
    for error in (
        # What `Handler._body` raises for a truncated payload...
        GatewayError(400, "request body is not valid JSON", "malformed_json"),
        # ...and for `[1,2]`, which parses but is not an object.
        GatewayError(400, "request body must be a JSON object", "invalid_body_type"),
    ):
        handler = _Handler(path="/updates/check", body=error)
        _owned, sent = _call(handler, "POST", "/updates/check", cfg)
        assert sent[0] == 400
        # B.2 names one code for every bad request on this route.
        assert sent[1]["code"] == "bad_request"
        assert sent[2]["Cache-Control"] == "no-store"


def test_the_offline_post_short_circuits_and_writes_no_cache(tmp_path):
    """The default suite's pin: the check answers from the pin, before the cache
    or the network, so no cache file appears and `previous` claims nothing."""
    cfg = SimpleNamespace(data_dir=tmp_path / "offline")
    handler = _Handler(path="/updates/check", body={"refresh": True})
    _owned, sent = _call(handler, "POST", "/updates/check", cfg)

    code, payload, headers = sent
    assert code == 200
    assert headers["Cache-Control"] == "no-store"
    assert set(payload) == {"running", "channel", "check", "apply_available"}
    assert "request_id" not in payload
    check = payload["check"]
    assert check is not None
    assert check["status"] == "unknown"
    assert check["reason"] == "offline_pin"
    assert check["source"] == "offline"
    assert check["cached"] is False
    assert set(check["trust"]) == TRUST_KEYS
    assert not (Path(cfg.data_dir) / "updates").exists()


@pytest.mark.stubbed_backend
def test_a_forced_check_reports_update_available(cfg, online):
    source = online(_Source())
    handler = _Handler(path="/updates/check", body={"refresh": True})

    _owned, sent = _call(handler, "POST", "/updates/check", cfg)

    code, payload, headers = sent
    assert code == 200
    assert headers["Cache-Control"] == "no-store"
    assert set(payload) == {"running", "channel", "check", "apply_available"}
    assert payload["running"] == RUNNING
    assert payload["apply_available"] is False
    check = payload["check"]
    assert set(check) == CHECK_KEYS
    assert check["status"] == "update_available"
    assert check["reason"] == "ok"
    assert check["latest"] == VERSION
    assert check["source"] == "pypi"
    assert check["cached"] is False
    assert check["age_s"] == 0
    assert check["previous"] == {}
    assert check["trust"] == {
        "integrity": "two_witnesses",
        "authorship": "not_established",
    }
    assert source.calls == ["project", "release", "sums"]
    _assert_whitelisted(payload)


@pytest.mark.stubbed_backend
def test_a_cached_check_is_served_without_dialling_again(cfg, online):
    source = online(_Source())
    first = _Handler(path="/updates/check", body={})
    _owned, sent = _call(first, "POST", "/updates/check", cfg)
    assert sent[1]["check"]["cached"] is False
    calls = list(source.calls)
    assert calls

    second = _Handler(path="/updates/check", body={"refresh": False})
    _owned, sent = _call(second, "POST", "/updates/check", cfg)
    assert sent[0] == 200
    assert sent[1]["check"]["cached"] is True
    assert sent[1]["check"]["age_s"] == 0
    assert source.calls == calls, "a fresh cache was re-dialled"


@pytest.mark.stubbed_backend
def test_up_to_date_is_reported_without_claiming_trust(cfg, online):
    online(_Source(project=_project_doc(RUNNING)))
    handler = _Handler(path="/updates/check", body={})
    _owned, sent = _call(handler, "POST", "/updates/check", cfg)
    check = sent[1]["check"]
    assert check["status"] == "up_to_date"
    assert check["reason"] == "ok"
    assert check["latest"] == RUNNING
    assert check["trust"] == {
        "integrity": "none",
        "authorship": "not_established",
    }


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "failure,reason",
    [
        (urllib.error.URLError("name resolution failed"), "unreachable"),
        (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
        (webtools.NetworkDisabled("networking is disabled"), "network_disabled"),
        (egress.EgressBlocked("pypi.org is not allowlisted"), "egress_blocked"),
        (ValueError("not a release document"), "malformed"),
    ],
)
def test_a_failure_is_unknown_and_keeps_the_previous_answer(
    cfg, online, failure, reason
):
    """A failure never becomes "latest". `previous` is carried with its own
    timestamp, so the UI can show it as history rather than this result."""
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
def test_witness_disagreement_and_missing_witness_are_never_up_to_date(cfg, online):
    online(_Source(sums=_sums_text(wheel_digest="f" * 64)))
    handler = _Handler(path="/updates/check", body={"refresh": True})
    _owned, sent = _call(handler, "POST", "/updates/check", cfg)
    assert sent[1]["check"]["status"] == "unknown"
    assert sent[1]["check"]["reason"] == "witness_disagreement"
    assert sent[1]["check"]["latest"] == ""

    update_routes.reset_check_gate_for_tests()
    online(_Source(release=_release_doc_without_wheel()))
    handler = _Handler(path="/updates/check", body={"refresh": True})
    _owned, sent = _call(handler, "POST", "/updates/check", cfg)
    assert sent[1]["check"]["status"] == "unknown"
    assert sent[1]["check"]["reason"] == "witness_missing"


@pytest.mark.stubbed_backend
def test_a_single_readable_witness_says_so(cfg, online):
    online(_Source(sums=urllib.error.URLError("github.com is unreachable")))
    handler = _Handler(path="/updates/check", body={"refresh": True})
    _owned, sent = _call(handler, "POST", "/updates/check", cfg)
    check = sent[1]["check"]
    assert check["status"] == "update_available"
    assert check["trust"] == {
        "integrity": "single_witness",
        "authorship": "not_established",
    }


@pytest.mark.stubbed_backend
def test_a_malicious_index_response_never_reaches_the_client(cfg, online):
    online(_Source(project={"info": {"version": "9.9.9<script>alert(1)</script>"}}))
    handler = _Handler(path="/updates/check", body={"refresh": True})
    _owned, sent = _call(handler, "POST", "/updates/check", cfg)
    check = sent[1]["check"]
    assert check["status"] == "unknown"
    assert check["reason"] == "malformed"
    assert check["latest"] == ""
    assert "<script>" not in json.dumps(sent[1])


@pytest.mark.stubbed_backend
def test_an_invalid_index_setting_is_unknown_without_a_dial(cfg, monkeypatch):
    monkeypatch.setenv("OPENAI4S_UPDATE_INDEX", "http://evil.example.org/simple")
    monkeypatch.setattr(discovery, "_SOURCE", _Exploding())
    handler = _Handler(path="/updates/check", body={"refresh": True})
    _owned, sent = _call(handler, "POST", "/updates/check", cfg)
    assert sent[0] == 200
    assert sent[1]["check"]["status"] == "unknown"
    assert sent[1]["check"]["reason"] == "bad_index"


@pytest.mark.stubbed_backend
def test_the_response_never_carries_the_discovery_internals_or_paths(cfg, online):
    online(_Source())
    handler = _Handler(path="/updates/check", body={"refresh": True})
    _owned, sent = _call(handler, "POST", "/updates/check", cfg)
    _assert_whitelisted(sent[1])
    assert str(Path(cfg.data_dir)) not in json.dumps(sent[1])


def test_apply_available_is_always_false(cfg):
    """This version reports and points at a manual upgrade; it never installs."""
    for method, path, body in (
        ("GET", "/updates/status", None),
        ("POST", "/updates/check", {}),
    ):
        _owned, sent = _call(_Handler(path=path, body=body), method, path, cfg)
        assert sent[0] == 200
        assert sent[1]["apply_available"] is False


@pytest.mark.stubbed_backend
def test_a_second_forced_check_is_rate_limited(cfg, online):
    source = online(_Source())
    first = _Handler(path="/updates/check", body={"refresh": True})
    _owned, sent = _call(first, "POST", "/updates/check", cfg)
    assert sent[0] == 200
    calls = list(source.calls)

    second = _Handler(path="/updates/check", body={"refresh": True})
    _owned, sent = _call(second, "POST", "/updates/check", cfg)
    code, payload, headers = sent
    assert code == 429
    assert payload["code"] == "rate_limited"
    assert 1 <= payload["retry_after_s"] <= 60
    assert headers["Retry-After"] == str(payload["retry_after_s"])
    assert headers["Cache-Control"] == "no-store"
    assert source.calls == calls, "a cooled-down request still dialled"

    # Only the *forced* refresh is cooled down; a normal check is not.
    third = _Handler(path="/updates/check", body={})
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
        assert sent[1]["code"] == "rate_limited"
        release.set()
        worker.join(10)
        assert result["sent"][0] == 200
        assert len(calls) == 1, "the second click executed a second check"
    finally:
        release.set()
        worker.join(10)


# --------------------------------------------------------------------------
# isolation: no turn-reachable surface can import the updater (INV-9)
# --------------------------------------------------------------------------


def test_no_tool_host_skill_source_imports_the_update_package():
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for base in ("openai4s/tools", "openai4s/host", "openai4s/sdk", "skills"):
        for path in sorted((root / base).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):  # pragma: no cover - not Python source
                continue
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                if any(
                    module == "openai4s.update" or module.startswith("openai4s.update.")
                    for module in modules
                ):
                    offenders.append(str(path.relative_to(root)))
    assert offenders == [], offenders


# --------------------------------------------------------------------------
# through the gateway: dispatch, auth, no-store
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

    return call, close


def test_the_gateway_dispatches_both_routes_with_no_store(tmp_path, monkeypatch):
    call, close = _api(tmp_path)
    try:
        sent = call("GET", "/updates/status")
        assert sent["code"] == 200
        assert sent["headers"]["Cache-Control"] == "no-store"
        assert set(sent["body"]) == {"running", "channel", "check", "apply_available"}
        assert sent["body"]["check"]["reason"] == "offline_pin"

        sent = call("POST", "/updates/check", {})
        assert sent["code"] == 200
        assert sent["headers"]["Cache-Control"] == "no-store"
        assert sent["body"]["check"]["reason"] == "offline_pin"

        # A JSON array parses but is not an object; `Handler._body` refuses it and
        # the route reports B.2's single bad-request code.
        sent = call("POST", "/updates/check", [1, 2])
        assert sent["code"] == 400
        assert sent["body"]["code"] == "bad_request"

        sent = call("POST", "/updates/check", {"refresh": True})
        assert sent["code"] == 200
        sent = call("POST", "/updates/check", {"refresh": True})
        assert sent["code"] == 429
        assert sent["body"]["code"] == "rate_limited"
        assert sent["body"]["retry_after_s"] >= 1
        assert sent["headers"]["Cache-Control"] == "no-store"

        # Without the offline pin and without a record, the same route answers
        # `"check": null` (B.2) — the shape the settings card reads on a fresh
        # install.
        monkeypatch.delenv("OPENAI4S_UPDATE_SOURCE", raising=False)
        sent = call("GET", "/updates/status")
        assert sent["code"] == 200
        assert sent["body"]["check"] is None

        sent = call("GET", "/updates/check")
        assert sent["code"] in (404, 405)
    finally:
        close()


def test_the_forced_check_cooldown_map_keeps_only_live_stamps():
    """A stamp older than the cooldown decides nothing, so it is not retained:
    a long-running team server must not accumulate one entry per principal that
    ever forced a check."""
    gate = update_routes._CheckGate(cooldown_s=60.0)
    now = [1000.0]
    gate.clock = lambda: now[0]
    for index in range(5):
        assert gate.try_begin(f"user:{index}", force=True) == ("ok", 0)
        gate.end()
    now[0] += 61.0
    assert gate.try_begin("user:late", force=True) == ("ok", 0)
    gate.end()
    assert set(gate._last_forced) == {"user:late"}
    # A live stamp still rate-limits its own principal.
    assert gate.try_begin("user:late", force=True)[0] == "rate"
