"""Correlation IDs and redaction in structured logs.

The daemon logged with bare print() and carried no request identity, so a user
reporting "my run failed" could not be tied to the request, session, or remote
job it became — support meant guessing from timestamps.

The security-critical half is redaction. The proposal requires that logs,
diagnostics, and exports carry no secret material, and says explicitly that a
denylist is not evidence of that. So the tests below assert on *values* leaving
the process, not on field names: a secret under an unremarkable key is exactly
the one a name rule misses.
"""
import json

import pytest

from openai4s.observability import (
    correlation_id,
    fingerprint,
    log_event,
    new_correlation_id,
    redact,
    reset_correlation_id,
    set_correlation_id,
)

_KEY = "sk-live-9f3a1c7e4b2d8e6f0a1b2c3d4e5f6071"


# --------------------------------------------------------------------------
# correlation id
# --------------------------------------------------------------------------


def test_ids_are_unique_and_short_enough_to_read():
    a, b = new_correlation_id(), new_correlation_id()
    assert a != b
    assert 8 <= len(a) <= 32


def test_the_id_is_readable_from_anywhere_in_the_call():
    token = set_correlation_id("abc123")
    try:
        assert correlation_id() == "abc123"
    finally:
        reset_correlation_id(token)


def test_resetting_restores_the_previous_value():
    outer = set_correlation_id("outer")
    inner = set_correlation_id("inner")
    reset_correlation_id(inner)
    assert correlation_id() == "outer"
    reset_correlation_id(outer)


def test_a_foreign_token_does_not_raise():
    """A background thread resetting a token from another context must not turn
    a logging detail into a request failure."""
    import threading

    token = set_correlation_id("main")
    error = []

    def other():
        try:
            reset_correlation_id(token)
        except Exception as e:  # noqa: BLE001
            error.append(e)

    thread = threading.Thread(target=other)
    thread.start()
    thread.join()
    assert error == []
    reset_correlation_id(token)


def test_events_carry_the_current_id():
    token = set_correlation_id("req-1")
    try:
        assert log_event("thing_happened")["correlation_id"] == "req-1"
    finally:
        reset_correlation_id(token)


# --------------------------------------------------------------------------
# redaction — by value shape, not by field name
# --------------------------------------------------------------------------


def test_a_secret_under_a_sensitive_key_is_redacted():
    assert _KEY not in json.dumps(redact({"api_key": _KEY}))


def test_a_secret_under_an_innocent_key_is_still_redacted():
    """The case a denylist misses. This is why redaction is shape-based."""
    out = json.dumps(redact({"note": _KEY, "widget": _KEY}))
    assert _KEY not in out


def test_nested_secrets_are_redacted():
    payload = {"outer": {"inner": [{"authorization": _KEY}]}}
    assert _KEY not in json.dumps(redact(payload))


def test_connector_env_values_are_redacted():
    assert _KEY not in json.dumps(redact({"env": {"LAB_TOKEN": _KEY}}))


def test_ordinary_text_survives():
    """Redaction that eats the useful fields makes the log worthless, so the
    thing it exists for stops being done."""
    out = redact(
        {
            "method": "POST",
            "path": "/api/frames/abc/kernel",
            "status": 200,
            "message": "kernel restarted after a failed cell",
        }
    )
    assert out["method"] == "POST"
    assert out["path"] == "/api/frames/abc/kernel"
    assert out["status"] == 200
    assert "kernel restarted" in out["message"]


def test_paths_and_urls_are_not_mistaken_for_secrets():
    """Long and opaque-looking, but redacting them would remove exactly what a
    log is read for."""
    for value in (
        "/Users/someone/.openai4s/artifacts/abcdef0123456789.parquet",
        "https://api.example.com/v1/chat/completions",
    ):
        assert redact({"where": value})["where"] == value


def test_a_broker_reference_survives():
    """It names a secret rather than being one, and being safe to record is its
    entire purpose."""
    ref = "secret://v1/llm/llm_api_key"
    assert redact({"api_key": ref})["api_key"] == ref


def test_short_identifiers_survive():
    for value in ("job-abc123", "mp-8f2a", "frame_42"):
        assert redact({"id": value})["id"] == value


def test_a_redacted_value_is_still_correlatable():
    """Two lines about the same secret must be tie-able without either showing
    it — otherwise redaction destroys the debuggability it is meant to preserve.
    """
    first = redact({"api_key": _KEY})["api_key"]
    second = redact({"other": _KEY})["other"]
    assert first == second
    assert _KEY not in first


def test_fingerprints_are_not_reversible_and_are_stable():
    fp = fingerprint(_KEY)
    assert _KEY not in fp
    assert fp == fingerprint(_KEY)
    assert fp != fingerprint(_KEY + "x")


def test_redaction_terminates_on_deep_structures():
    deep: dict = {}
    node = deep
    for _ in range(40):
        node["next"] = {}
        node = node["next"]
    node["api_key"] = _KEY
    assert _KEY not in json.dumps(redact(deep))


# --------------------------------------------------------------------------
# emission
# --------------------------------------------------------------------------


def test_logging_is_off_unless_asked_for(monkeypatch, capsys):
    """Turning this on by default would change what every existing deployment
    writes to disk."""
    monkeypatch.delenv("OPENAI4S_STRUCTURED_LOGS", raising=False)
    log_event("quiet_event", note="hello")
    assert capsys.readouterr().err == ""


def test_enabled_logging_writes_one_json_object_per_line(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI4S_STRUCTURED_LOGS", "1")
    log_event("http_request", method="GET", path="/api/status", status=200)
    line = capsys.readouterr().err.strip()
    record = json.loads(line)
    assert record["event"] == "http_request"
    assert record["path"] == "/api/status"
    assert "ts" in record


def test_an_emitted_line_never_carries_a_secret(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI4S_STRUCTURED_LOGS", "1")
    log_event("configured", api_key=_KEY, env={"TOKEN": _KEY})
    assert _KEY not in capsys.readouterr().err


def test_a_broken_field_cannot_fail_the_request(monkeypatch, capsys):
    """Logging is not allowed to be the reason a request dies."""
    monkeypatch.setenv("OPENAI4S_STRUCTURED_LOGS", "1")

    class _Hostile:
        def __repr__(self):
            raise RuntimeError("boom")

    log_event("weird", value=_Hostile())  # must not raise


def test_there_is_no_prompt_logging_helper():
    """Prompts and kernel data are the likeliest carriers of a user's
    unpublished work. The safe default is that they have no path out through
    this module at all — if one is ever added, this should be the argument."""
    import openai4s.observability as module

    names = [n.lower() for n in dir(module)]
    for banned in ("prompt", "message", "transcript", "conversation"):
        assert not any(banned in n for n in names), banned
