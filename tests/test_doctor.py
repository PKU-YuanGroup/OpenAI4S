"""One command that says whether this installation can do the work.

Every probe already existed behind a separate route or import, so the person
best placed to need them — someone whose daemon will not start — had nothing
single to run and nothing coherent to paste into a report.

The properties that make it worth having, each pinned below: it works without
the daemon, it separates "degraded" from "cannot proceed", and it never reports
a secret value.
"""
from __future__ import annotations

import importlib
import json
import types

import pytest

from openai4s import doctor
from openai4s.config import Config, LLMConfig


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "skills").mkdir()
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="chatgpt", api_key="test-key", model="test-model"),
    )


def _by_name(result):
    return {c["name"]: c for c in result["checks"]}


# --------------------------------------------------------------------------
# it runs at all, without a daemon
# --------------------------------------------------------------------------


def test_every_probe_reports_without_a_running_daemon(cfg):
    """The situation that motivates running it is usually one where the daemon
    will not start, so a check that needs the server is unavailable exactly
    when it is wanted."""
    result = doctor.report(cfg)
    assert set(_by_name(result)) == {
        "model",
        "runtime",
        "isolation",
        "disk",
        "connectors",
        "remote",
    }
    for check in result["checks"]:
        assert check["status"] in (doctor.OK, doctor.WARN, doctor.FAIL)
        assert check["detail"], f"{check['name']} said nothing"


def test_a_probe_that_raises_becomes_a_finding_not_a_traceback(cfg, monkeypatch):
    """A crash here would deny the report to whoever most needs it."""

    def boom(_cfg):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(doctor, "_CHECKS", (("disk", boom),))
    result = doctor.report(cfg)

    assert result["status"] == doctor.FAIL
    assert "probe exploded" in result["checks"][0]["detail"]


# --------------------------------------------------------------------------
# degraded is not the same as broken
# --------------------------------------------------------------------------


def test_a_missing_credential_fails_rather_than_warns(cfg):
    """No key means no work can happen at all — that is not a degradation."""
    cfg.llm.api_key = ""
    assert _by_name(doctor.report(cfg))["model"]["status"] == doctor.FAIL


def test_a_configured_model_passes(cfg):
    check = _by_name(doctor.report(cfg))["model"]
    assert check["status"] == doctor.OK
    assert check["facts"]["api_key_configured"] is True


def test_low_disk_fails_because_a_run_would_die_partway(cfg, monkeypatch):
    """Refusing up front beats failing after the expensive part."""
    monkeypatch.setattr(
        doctor.shutil,
        "disk_usage",
        lambda _p: types.SimpleNamespace(total=10**9, used=10**9, free=10**8),
    )
    assert _by_name(doctor.report(cfg))["disk"]["status"] == doctor.FAIL


def test_a_disabled_sandbox_warns_rather_than_fails(cfg, monkeypatch):
    """Usable, and deliberately chosen — but never silent."""
    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "off")
    check = _by_name(doctor.report(cfg))["isolation"]
    assert check["status"] == doctor.WARN
    assert "off" in check["detail"]


def test_enforce_without_a_backend_fails(cfg, monkeypatch):
    """`enforce` means refuse, so the doctor must not call it a degradation."""
    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "enforce")
    monkeypatch.setattr(
        "openai4s.security.sandbox._detect_backend",
        lambda **_kw: (None, None, "no backend here"),
    )
    check = _by_name(doctor.report(cfg))["isolation"]
    assert check["status"] == doctor.FAIL
    assert "no backend here" in check["detail"]


def test_the_overall_status_is_the_worst_check(cfg, monkeypatch):
    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "off")  # one warn
    assert doctor.report(cfg)["status"] in (doctor.WARN, doctor.FAIL)

    cfg.llm.api_key = ""  # now also a fail
    assert doctor.report(cfg)["status"] == doctor.FAIL


# --------------------------------------------------------------------------
# it must not leak what it is checking
# --------------------------------------------------------------------------


def test_no_credential_value_appears_anywhere_in_the_report(cfg):
    """Whether a credential is configured is a diagnostic; the credential is
    not. This output is written to be pasted into bug reports."""
    cfg.llm.api_key = "sk-canary-do-not-leak-1234567890"
    result = doctor.report(cfg)

    blob = json.dumps(result) + doctor.render(result)
    assert cfg.llm.api_key not in blob
    assert "canary" not in blob


def test_the_rendered_report_shows_a_remedy_only_where_there_is_a_problem(cfg):
    cfg.llm.api_key = ""
    text = doctor.render(doctor.report(cfg))
    assert "->" in text, "a failing check must say what to do"
    ok_lines = [ln for ln in text.splitlines() if ln.startswith("[ok  ]")]
    assert ok_lines, "this fixture should pass something"
    for line in ok_lines:
        assert "->" not in line


# --------------------------------------------------------------------------
# the CLI contract
# --------------------------------------------------------------------------


def test_the_exit_code_is_the_verdict(monkeypatch, capsys):
    """So a setup script can branch on it instead of grepping prose."""
    cli = importlib.import_module("openai4s.cli.main")

    for status, expected in (
        (doctor.OK, 0),
        (doctor.WARN, 1),
        (doctor.FAIL, 2),
    ):
        monkeypatch.setattr(
            doctor, "report", lambda _cfg, s=status: {"status": s, "checks": []}
        )
        assert cli.main(["doctor"]) == expected
    capsys.readouterr()


def test_the_json_form_is_machine_readable(monkeypatch, capsys):
    cli = importlib.import_module("openai4s.cli.main")

    monkeypatch.setattr(
        doctor,
        "report",
        lambda _cfg: {
            "status": doctor.OK,
            "checks": [
                {
                    "name": "model",
                    "status": doctor.OK,
                    "detail": "d",
                    "remedy": "",
                    "facts": {},
                }
            ],
        },
    )
    assert cli.main(["doctor", "--json"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] == doctor.OK
    assert parsed["checks"][0]["name"] == "model"
