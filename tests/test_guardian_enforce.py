"""Stage 7 Guardian enforcement: allow_once only, never standing, fail closed.

Every test here builds the payload the way `PermissionBroker.gate` builds it --
a durable `action_digest`, a persisted audit, a real `side_effect_class` -- so
that a branch which production cannot reach also cannot be asserted green.
"""

from __future__ import annotations

import pytest

from openai4s.server.guardian_enforce import (
    ALLOWED_SIDE_EFFECTS,
    ALLOWED_TOOLS,
    circuit,
    decide_unattended,
)

_DIGEST = "a" * 64


class _Budgets:
    guardian_consecutive_denial_limit = 3
    guardian_window_size = 50
    guardian_window_denial_limit = 10


class _Flags:
    stage7_guardian_enforcement = True


class _Auto:
    approvals_reviewer = "auto_review"
    budgets = _Budgets()


class _Cfg:
    roadmap_features = _Flags()
    auto_mode = _Auto()


def _payload(**over):
    base = {
        "tool": "read_file",
        "target": "a.txt",
        "dangerous": False,
        "side_effect_class": "read",
        "canonical_arguments": {"path": "a.txt"},
        "resource_keys": [],
    }
    base.update(over)
    return base


def _decide(payload=None, **kw):
    kw.setdefault("config", _Cfg())
    kw.setdefault("expected_digest", _DIGEST)
    kw.setdefault("audit_persisted", True)
    kw.setdefault("circuit_key", None)
    return decide_unattended(payload or _payload(), **kw)


@pytest.fixture(autouse=True)
def _clean_circuit():
    circuit().reset("k")
    yield
    circuit().reset("k")


def test_allowlisted_read_is_allow_once():
    allowed, message = _decide()
    assert allowed is True
    assert "allow_once" in message


def test_dangerous_action_is_denied():
    allowed, _ = _decide(_payload(tool="bash", target="rm -rf /", dangerous=True))
    assert allowed is False


def test_tool_off_the_allowlist_is_denied():
    for tool in ("write_file", "edit_file", "bash", "exec_background", "web_fetch"):
        allowed, message = _decide(
            _payload(tool=tool, side_effect_class="read"), circuit_key=None
        )
        assert allowed is False, tool
        assert "allowlist" in message


def test_write_side_effect_is_denied_even_for_an_allowlisted_tool():
    allowed, message = _decide(_payload(side_effect_class="write"))
    assert allowed is False
    assert "allowlist" in message


def test_unknown_side_effect_class_is_denied():
    # An action whose effect we cannot name is not one we can bound.
    allowed, _ = _decide(_payload(side_effect_class=""))
    assert allowed is False


def test_hard_deny_outranks_the_guardian():
    allowed, message = _decide(hard_deny=True)
    assert allowed is False
    assert "hard policy" in message


def test_missing_action_digest_denies_rather_than_bypassing():
    # Without a digest there is nothing binding the approval to an action.
    allowed, message = _decide(expected_digest=None)
    assert allowed is False
    assert "digest" in message


def test_unpersisted_audit_denies():
    allowed, message = _decide(audit_persisted=False)
    assert allowed is False
    assert "audit" in message


def test_durable_selection_of_user_beats_the_environment(monkeypatch):
    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "auto_review")

    class _UserAuto:
        approvals_reviewer = "user"
        budgets = _Budgets()

    class _UserCfg:
        roadmap_features = _Flags()
        auto_mode = _UserAuto()

    # None hands the decision back to the legacy fail-closed path.
    assert _decide(config=_UserCfg()) is None


def test_flag_off_returns_none_so_legacy_path_remains():
    class _Off:
        roadmap_features = type("F", (), {"stage7_guardian_enforcement": False})()
        auto_mode = _Auto()

    assert _decide(config=_Off()) is None


def test_consecutive_denials_open_the_circuit():
    denied = _payload(tool="bash", dangerous=True)
    for _ in range(3):
        allowed, _ = _decide(denied, circuit_key="k")
        assert allowed is False
    # The circuit is now open: even an allowlisted read is refused.
    allowed, message = _decide(circuit_key="k")
    assert allowed is False
    assert "blocked_by_guardian" in message


def test_a_non_denial_resets_the_consecutive_count():
    denied = _payload(tool="bash", dangerous=True)
    for _ in range(2):
        assert _decide(denied, circuit_key="k")[0] is False
    assert _decide(circuit_key="k")[0] is True
    assert _decide(denied, circuit_key="k")[0] is False
    # Two more denials would have opened the circuit without the reset.
    assert _decide(circuit_key="k")[0] is True


def test_allowlists_are_read_only():
    assert "write_file" not in ALLOWED_TOOLS
    assert "bash" not in ALLOWED_TOOLS
    assert "web_fetch" not in ALLOWED_TOOLS
    assert "write" not in ALLOWED_SIDE_EFFECTS


def test_a_recorded_selection_of_user_beats_the_environment(monkeypatch):
    """The durable per-conversation control must actually control approvals.

    Session-import quarantine and the legacy `review:auto:*` migration both
    pin `approvals_reviewer` to "user". If the gate could only see the process
    environment, loading a quarantined session on a daemon started with
    OPENAI4S_UNATTENDED_APPROVAL=auto_review would auto-approve it anyway.
    """

    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "auto_review")
    assert _decide(approvals_reviewer="user") is None
    assert _decide(approvals_reviewer="auto_review")[0] is True


def test_no_recorded_selection_falls_back_to_the_environment(monkeypatch):
    """An empty selection means nobody recorded one -- not that someone said no.

    The CLI has no durable Auto Mode state at all, so the operator's
    environment is the only expressed intent there is.
    """

    class _NoAuto:
        roadmap_features = _Flags()

    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "auto_review")
    assert _decide(config=_NoAuto(), approvals_reviewer="")[0] is True
    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "deny")
    assert _decide(config=_NoAuto(), approvals_reviewer="") is None


def test_broker_exposes_the_resolver_port_and_defaults_closed():
    from openai4s import permissions

    original = permissions._SELECTION_RESOLVER
    try:
        permissions.set_approvals_reviewer_resolver(None)
        # No resolver: unknown, so the environment decides (see above).
        assert permissions._resolved_approvals_reviewer(None, "r", "p") == ""

        permissions.set_approvals_reviewer_resolver(lambda *_: "auto_review")
        assert permissions._resolved_approvals_reviewer(None, "r", "p") == "auto_review"

        def _boom(*_args):
            raise RuntimeError("store unreadable")

        # A resolver that RAISES is different: we were supposed to know and
        # could not, which is not consent.
        permissions.set_approvals_reviewer_resolver(_boom)
        assert permissions._resolved_approvals_reviewer(None, "r", "p") == "user"
    finally:
        permissions.set_approvals_reviewer_resolver(original)
