"""Stage 6 Guardian shadow: exact-action hash, no standing allow, no execute."""

from __future__ import annotations

from openai4s.server.guardian_shadow import (
    assess_shadow,
    exact_action_envelope,
    maybe_record_shadow,
)
from openai4s.store import Store


def _real_request(
    store: Store,
    *,
    decision_id: str,
    tool: str = "read_file",
    target: str = "a.txt",
    dangerous: bool = False,
):
    canonical_arguments = [{"path": target}]
    payload = {
        "tool": tool,
        "target": target,
        "side_effect_class": "read_only",
        "resource_keys": [f"workspace:{target}"],
        "dangerous": dangerous,
        "input": canonical_arguments[0],
    }
    request = store.create_permission_request(
        decision_id=decision_id,
        root_frame_id="frame-1",
        frame_id="frame-1",
        project_id="project-1",
        tool=tool,
        target=target,
        side_effect_class="read_only",
        resource_keys=[f"workspace:{target}"],
        payload=payload,
        dangerous=dangerous,
        canonical_arguments=canonical_arguments,
    )
    return request, payload, canonical_arguments


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
    request, payload, canonical_arguments = _real_request(store, decision_id="dec-1")
    assessment = maybe_record_shadow(
        store,
        request,
        payload,
        config=type(
            "Cfg",
            (),
            {"roadmap_features": type("F", (), {"stage6_guardian_shadow": True})()},
        )(),
        canonical_arguments=canonical_arguments,
    )
    assert assessment is not None
    assert assessment["executes"] is False
    assert assessment["outcome"] == "shadow_allow"
    assert assessment["action_digest"] == request["action_digest"]
    raw = store.get_setting("guardian-shadow:dec-1")
    assert raw and "shadow_allow" in raw
    store.close()


def test_shadow_attributes_a_hard_policy_boundary_to_policy(tmp_path):
    store = Store(tmp_path / "hard-policy.db")
    request, payload, canonical_arguments = _real_request(
        store,
        decision_id="dec-policy",
        target="config.json",
    )
    assessment = maybe_record_shadow(
        store,
        request,
        payload,
        config=type(
            "Cfg",
            (),
            {"roadmap_features": type("F", (), {"stage6_guardian_shadow": True})()},
        )(),
        canonical_arguments=canonical_arguments,
        hard_deny_reason="unattended credential policy denied access",
    )

    assert assessment is not None
    assert assessment["outcome"] == "shadow_deny"
    assert assessment["decision_source"] == "deterministic_policy"
    assert "credential policy" in assessment["rationale"]
    store.close()


def test_shadow_fails_closed_when_request_copy_disagrees_with_durable_row(tmp_path):
    store = Store(tmp_path / "tampered.db")
    request, payload, canonical_arguments = _real_request(
        store, decision_id="dec-tampered"
    )
    tampered = {**request, "action_digest": "0" * 64, "tool": "write_file"}

    assessment = maybe_record_shadow(
        store,
        tampered,
        payload,
        config=type(
            "Cfg",
            (),
            {"roadmap_features": type("F", (), {"stage6_guardian_shadow": True})()},
        )(),
        canonical_arguments=canonical_arguments,
    )

    assert assessment is not None
    assert assessment["outcome"] == "failed"
    assert assessment["fail_closed"] is True
    assert assessment["executes"] is False
    assert "mismatch" in assessment["rationale"]
    raw = store.get_setting("guardian-shadow:dec-tampered")
    assert raw and '"outcome": "failed"' in raw
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
