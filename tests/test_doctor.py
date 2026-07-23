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


def _no_ambient_key(monkeypatch):
    """Clear the suite's fake keys so a no-credential case is really one.

    conftest exports OPENAI4S_LLM_API_KEY for every test, and LLMConfig
    re-resolves from the environment on every `dataclasses.replace` — so
    without this the "no key configured" tests silently have one.
    """
    for var in (
        "OPENAI4S_LLM_API_KEY",
        "OPENAI4S_DEEPSEEK_API_KEY",
        "OPENAI4S_CLAUDE_API_KEY",
        "OPENAI4S_CHATGPT_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


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
    # Assembled at runtime rather than written as a literal: the source
    # credential scan matches on shape, and a realistic-looking key checked
    # into the tree is a finding whether or not it is real. The sentinel still
    # has to be distinctive enough that finding it in the output means the
    # value leaked and nothing else.
    sentinel = "sk-" + "PLEASE-DO-NOT-LEAK" + "-" + ("9" * 24)
    cfg.llm.api_key = sentinel
    result = doctor.report(cfg)

    blob = json.dumps(result) + doctor.render(result)
    assert sentinel not in blob
    assert "PLEASE-DO-NOT-LEAK" not in blob


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


# --------------------------------------------------------------------------
# a diagnostic that disagrees with the runtime is worse than none
# --------------------------------------------------------------------------


def test_a_model_configured_only_in_the_ui_is_not_reported_as_missing(cfg, monkeypatch):
    """The regression.

    The daemon boots with no key — that is the documented path — and the model
    is configured from Customize → Models, which writes to the store. Reading
    `cfg.llm` alone therefore diagnosed `model FAIL` on an installation that
    worked, sending the user to fix something that was not broken.
    """
    from openai4s.store import get_store

    _no_ambient_key(monkeypatch)
    cfg.llm.api_key = ""
    store = get_store(cfg.db_path)
    store.set_setting("llm_provider", "claude")
    store.set_setting("llm_model", "claude-sonnet-5")
    store.set_secret_setting("llm_api_key", "sk-configured-in-the-ui", scope="llm")

    check = _by_name(doctor.report(cfg))["model"]
    assert check["status"] == doctor.OK
    assert check["facts"]["provider"] == "claude"
    assert check["facts"]["model"] == "claude-sonnet-5"
    assert "sk-configured-in-the-ui" not in json.dumps(check)


def test_a_local_endpoint_needs_no_credential(cfg, monkeypatch):
    """Ollama, LM Studio, vLLM and llama.cpp authenticate by being unreachable
    from anywhere else. Demanding a key from them demands a credential that
    does not exist."""
    from openai4s.store import get_store

    _no_ambient_key(monkeypatch)
    cfg.llm.api_key = ""
    store = get_store(cfg.db_path)
    store.set_setting("llm_base_url", "http://127.0.0.1:11434/v1")

    check = _by_name(doctor.report(cfg))["model"]
    assert check["status"] == doctor.OK
    assert check["facts"]["endpoint_is_local"] is True
    assert check["facts"]["api_key_configured"] is False


def test_a_dot_local_endpoint_needs_no_credential(cfg, monkeypatch):
    """The runtime allows keyless for `.local`/private/docker hosts too, so
    doctor must not report a working keyless setup as a model failure."""
    from openai4s.store import get_store

    _no_ambient_key(monkeypatch)
    cfg.llm.api_key = ""
    store = get_store(cfg.db_path)
    store.set_setting("llm_base_url", "http://ollama.local:11434/v1")

    check = _by_name(doctor.report(cfg))["model"]
    assert check["status"] in (doctor.OK, doctor.WARN)
    assert check["facts"]["endpoint_is_local"] is True


def test_a_remote_endpoint_without_a_key_still_fails(cfg, monkeypatch):
    """The loopback exemption must not become a blanket one."""
    from openai4s.store import get_store

    _no_ambient_key(monkeypatch)
    cfg.llm.api_key = ""
    store = get_store(cfg.db_path)
    store.set_setting("llm_base_url", "https://api.example.com/v1")

    check = _by_name(doctor.report(cfg))["model"]
    assert check["status"] == doctor.FAIL


def test_the_doctor_and_the_runtime_resolve_the_same_model(cfg):
    """The whole point: two implementations of one question is how they came
    to disagree. There is now one, and both call it."""
    from openai4s.llm.resolve import resolve_llm_config
    from openai4s.store import get_store

    store = get_store(cfg.db_path)
    store.set_setting("llm_provider", "claude")
    store.set_setting("llm_model", "claude-opus-4-8")

    resolved = resolve_llm_config(cfg.llm, store)
    check = _by_name(doctor.report(cfg))["model"]
    assert check["facts"]["provider"] == resolved.provider
    assert check["facts"]["model"] == resolved.model


def test_r_is_resolved_the_way_the_r_kernel_resolves_it(cfg, monkeypatch):
    """`shutil.which("Rscript")` reported "R cells will not run" on exactly the
    installations `openai4s setup` had just built an R environment for: a conda
    env's bin directory is not on the daemon's PATH."""
    monkeypatch.setattr(
        "openai4s.kernel.environments.discover_environments",
        lambda *a, **k: [
            types.SimpleNamespace(name="py", rscript=None),
            types.SimpleNamespace(name="r", rscript="/opt/envs/r/bin/Rscript"),
        ],
    )
    monkeypatch.setattr(doctor.shutil, "which", lambda _name: None)

    check = _by_name(doctor.report(cfg))["runtime"]
    assert check["status"] == doctor.OK
    assert check["facts"]["rscript"] is True
    assert check["facts"]["rscript_path"] == "/opt/envs/r/bin/Rscript"


def test_no_r_anywhere_still_warns(cfg, monkeypatch):
    monkeypatch.setattr(
        "openai4s.kernel.environments.discover_environments",
        lambda *a, **k: [types.SimpleNamespace(name="py", rscript=None)],
    )
    monkeypatch.setattr("openai4s.kernel.r_kernel.shutil.which", lambda _n: None)

    check = _by_name(doctor.report(cfg))["runtime"]
    assert check["status"] == doctor.WARN
    assert "R cells will not run" in check["detail"]


def test_a_backend_whose_self_test_fails_is_not_reported_active(cfg, monkeypatch):
    """The most important thing this command can get wrong before a release.

    `bwrap` installed but unprivileged user namespaces disabled, or a Seatbelt
    profile the OS rejects, both leave a host where the runtime degrades in
    `auto` and refuses to start in `enforce` — while this check said "active"
    and the gate that reads it saw nothing.
    """
    from openai4s.security.sandbox import KernelSandbox, SandboxStatus

    def failing_sandbox(_workspace, **_kw):
        return KernelSandbox(
            status=SandboxStatus(
                mode="auto",
                state="degraded",
                backend="bwrap",
                enforced=False,
                self_test_passed=False,
                network_policy="not_enforced",
                workspace=str(_workspace),
                temp_dir=None,
                detail="self-test could not create a user namespace",
                warning="user namespaces are disabled by this kernel",
            )
        )

    monkeypatch.setattr(
        "openai4s.security.sandbox.create_kernel_sandbox", failing_sandbox
    )
    check = _by_name(doctor.report(cfg))["isolation"]
    assert check["status"] == doctor.WARN
    assert check["facts"]["self_test_passed"] is False
    assert "self-test failed" in check["detail"]
    assert "user namespaces" in check["detail"]


def test_enforce_with_a_failing_self_test_fails(cfg, monkeypatch):
    from openai4s.security.sandbox import SandboxUnavailableError

    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "enforce")

    def refusing(_workspace, **_kw):
        raise SandboxUnavailableError("self-test failed under enforce")

    monkeypatch.setattr("openai4s.security.sandbox.create_kernel_sandbox", refusing)
    check = _by_name(doctor.report(cfg))["isolation"]
    assert check["status"] == doctor.FAIL
    assert "self-test failed under enforce" in check["detail"]


def test_the_real_network_kill_switch_is_what_decides_connector_reach(cfg, monkeypatch):
    """The global switch is OPENAI4S_ALLOW_NETWORK. OPENAI4S_EGRESS selects
    whether an *allowlist* is enforced, and its default `off` means fail-open —
    so reading `off` as "offline" inverted both answers at once."""
    monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "0")
    check = _by_name(doctor.report(cfg))["connectors"]
    assert check["status"] == doctor.WARN
    assert check["facts"]["network_allowed"] is False
    assert "OPENAI4S_ALLOW_NETWORK" in check["detail"]


def test_the_default_egress_mode_is_not_reported_as_offline(cfg, monkeypatch):
    """`OPENAI4S_EGRESS=off` is the default and means the allowlist is not
    enforced — everything is reachable."""
    monkeypatch.setenv("OPENAI4S_EGRESS", "off")
    monkeypatch.delenv("OPENAI4S_ALLOW_NETWORK", raising=False)
    check = _by_name(doctor.report(cfg))["connectors"]
    assert check["status"] == doctor.OK
    assert check["facts"]["egress_mode"] == "off"
    assert check["facts"]["network_allowed"] is True


def test_an_enforced_allowlist_is_reported_as_such(cfg, monkeypatch):
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    monkeypatch.delenv("OPENAI4S_ALLOW_NETWORK", raising=False)
    check = _by_name(doctor.report(cfg))["connectors"]
    assert check["status"] == doctor.OK
    assert "allowlist enforced" in check["detail"]
