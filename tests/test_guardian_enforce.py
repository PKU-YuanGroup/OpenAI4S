"""Stage 7 Guardian enforcement: allow_once only, never standing, fail closed."""

from __future__ import annotations

from openai4s.server.guardian_enforce import decide_unattended


class _Flags:
    stage7_guardian_enforcement = True


class _Auto:
    approvals_reviewer = "auto_review"


class _Cfg:
    roadmap_features = _Flags()
    auto_mode = _Auto()


def test_low_risk_read_is_allow_once():
    allowed, message = decide_unattended(
        {"tool": "read_file", "target": "a.txt", "dangerous": False},
        config=_Cfg(),
    )
    assert allowed is True
    assert "allow_once" in message


def test_dangerous_action_is_denied():
    allowed, message = decide_unattended(
        {"tool": "bash", "target": "rm -rf /", "dangerous": True},
        config=_Cfg(),
    )
    assert allowed is False
    assert allowed is not True


def test_flag_off_returns_none_so_legacy_path_remains():
    class Off:
        roadmap_features = type("F", (), {"stage7_guardian_enforcement": False})()
        auto_mode = _Auto()

    assert decide_unattended({"tool": "read_file"}, config=Off()) is None
