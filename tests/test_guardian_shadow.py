"""Stage 6 Guardian shadow: exact-action hash, no standing allow, no execute."""

from __future__ import annotations

from openai4s.server.guardian_shadow import (
    assess_shadow,
    envelope_digest,
    exact_action_envelope,
    maybe_record_shadow,
)
from openai4s.store import Store


def test_hash_mismatch_fails_closed_and_does_not_execute():
    envelope = exact_action_envelope(
        tool="write_file", target="out.txt", dangerous=False
    )
    result = assess_shadow(envelope, expected_digest="0" * 64)
    assert result["fail_closed"] is True
    assert result["executes"] is False
    assert result["outcome"] == "failed"
    assert result["standing_allow"] is False


def test_guardian_cannot_create_standing_allow():
    envelope = exact_action_envelope(tool="bash", target="rm -rf /", dangerous=True)
    result = assess_shadow(envelope, requested_scope="global")
    assert result["outcome"] == "deny"
    assert result["standing_allow"] is False
    assert result["executes"] is False


def test_shadow_allow_does_not_execute(tmp_path):
    store = Store(tmp_path / "guardian.db")
    envelope = exact_action_envelope(tool="read_file", target="a.txt")
    assessment = maybe_record_shadow(
        store,
        {"decision_id": "dec-1", "expected_envelope_digest": envelope_digest(envelope)},
        {"tool": "read_file", "target": "a.txt"},
        config=type(
            "Cfg",
            (),
            {"roadmap_features": type("F", (), {"stage6_guardian_shadow": True})()},
        )(),
    )
    assert assessment is not None
    assert assessment["executes"] is False
    assert assessment["outcome"] == "shadow_allow"
    raw = store.get_setting("guardian-shadow:dec-1")
    assert raw and "shadow_allow" in raw
    store.close()


def test_flag_off_records_nothing(tmp_path):
    store = Store(tmp_path / "off.db")
    assert (
        maybe_record_shadow(
            store,
            {"decision_id": "dec-off"},
            {"tool": "read_file"},
            config=type(
                "Cfg",
                (),
                {
                    "roadmap_features": type(
                        "F", (), {"stage6_guardian_shadow": False}
                    )()
                },
            )(),
        )
        is None
    )
    assert store.get_setting("guardian-shadow:dec-off") is None
    store.close()
