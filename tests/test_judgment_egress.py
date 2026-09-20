"""Egress reporting for experimental judgment: authorized vs blocked.

Does not add an EGRESS_GROUPS entry. ``enabled`` on those groups is display
only; ``builtin_domains()`` never reads it. Adding ``api.typesafe.ai`` to the
catalog would widen allowlist mode for every kernel cell even while this
experimental feature is off.
"""

from __future__ import annotations

import pytest

from openai4s import egress
from openai4s.config import Config, LLMConfig
from openai4s.judgment.settings import TYPESAFE_HOST, egress_report, status


@pytest.fixture(autouse=True)
def _reset_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI4S_EGRESS", raising=False)
    egress.reset_grants()
    yield
    egress.reset_grants()
    monkeypatch.delenv("OPENAI4S_EGRESS", raising=False)


def test_off_mode_reports_no_remediation(monkeypatch):
    monkeypatch.setenv("OPENAI4S_EGRESS", "off")
    report = egress_report()
    assert report["mode"] == "off"
    assert report["domain_allowed"] is False
    assert report["remediation"] is None
    egress.check_url("https://api.typesafe.ai/v1/systemone")


def test_allowlist_without_grant_reports_blocked(monkeypatch):
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    report = egress_report()
    assert report["mode"] == "allowlist"
    assert report["domain_allowed"] is False
    assert report["remediation"] == egress.blocked_message(TYPESAFE_HOST)
    assert "host.request_network_access" in report["remediation"]
    assert TYPESAFE_HOST in report["remediation"]
    with pytest.raises(egress.EgressBlocked) as raised:
        egress.check_url("https://api.typesafe.ai/v1/systemone")
    assert str(raised.value) == egress.blocked_message(TYPESAFE_HOST)


def test_allowlist_after_grant_is_authorized(monkeypatch):
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    egress.grant_domain(TYPESAFE_HOST)
    report = egress_report()
    assert report["mode"] == "allowlist"
    assert report["domain_allowed"] is True
    assert report["remediation"] is None
    egress.check_url("https://api.typesafe.ai/v1/systemone")


def test_status_carries_the_same_egress_report(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    body = status(cfg, None)
    assert body["egress"] == egress_report()
    assert body["egress"]["remediation"] == egress.blocked_message(TYPESAFE_HOST)


def test_enabled_field_is_not_what_authorizes_the_host():
    """A group ``enabled: False`` would still flatten into builtin_domains."""

    assert TYPESAFE_HOST not in egress.builtin_domains()
    for group in egress.EGRESS_GROUPS:
        assert TYPESAFE_HOST not in group.get("domains", [])
        assert TYPESAFE_HOST not in group.get("exact_domains", [])
