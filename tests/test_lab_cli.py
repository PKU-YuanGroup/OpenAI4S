"""Operator Lab commands avoid the Store and never publish simulator truth."""

import json
from pathlib import Path
from types import SimpleNamespace

from openai4s.cli.main import main


def test_status_and_doctor_report_uninstalled_as_optional(
    tmp_path, monkeypatch, capsys
):
    from openai4s import doctor

    data = tmp_path / "new-data"
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(data))
    monkeypatch.delenv("OPENAI4S_LAB_CHEMGYMRL_PYTHON", raising=False)
    monkeypatch.delenv("OPENAI4S_LAB_ENABLE_TOY", raising=False)
    assert main(["lab", "status"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["provider_environment"]["available"] is False
    assert result["toy_enabled"] is False
    assert result["sandbox"]["state"] == "not_probed"
    assert result["devices"][0]["device_id"] == "chemgym.extractor.01"
    check = doctor._lab(SimpleNamespace(data_dir=data))
    assert check.status == doctor.OK
    assert "setup chemgymrl" in check.detail
    assert "lab" in doctor.SIDE_EFFECT_CHECKS
    assert any(name == "lab" for name, _ in doctor._CHECKS)
    assert not data.exists()


def test_cli_toy_smoke_has_no_database_or_truth(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI4S_LAB_ENABLE_TOY", "1")
    assert main(["lab", "smoke", "--profile", "toy-extract-v0"]) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["status"] == "ok" and result["seed"] == 42
    assert result["receipt"]["applied"] is True
    for hidden in (
        "evaluation",
        "ground_truth",
        "reward",
        "provider_action",
        "toy_component",
    ):
        assert hidden not in output
    assert not list(tmp_path.rglob("*.db"))
    assert not list((tmp_path / "lab/runs").iterdir())


def test_setup_dry_run_does_not_write_or_run_python(tmp_path, monkeypatch, capsys):
    data = tmp_path / "absent"
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(data))
    assert main(["lab", "setup", "chemgymrl", "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "dry_run"
    assert not data.exists()


def test_chemgym_smoke_without_install_is_actionable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAI4S_LAB_CHEMGYMRL_PYTHON", raising=False)
    assert main(["lab", "smoke"]) == 2
    result = json.loads(capsys.readouterr().err)
    assert result["error"]["code"] == "provider_unavailable"
    assert "setup chemgymrl" in result["error"]["message"]


def test_doctor_installed_probe_is_side_effect_only(tmp_path, monkeypatch):
    import openai4s.lab.builtin as builtin
    from openai4s import doctor

    monkeypatch.setattr(
        builtin, "provider_environment_status", lambda data: {"available": True}
    )

    class Port:
        sandbox_status = {"enforced": False, "state": "degraded"}

        def describe(self, profile):
            assert profile in {"WaterOilExtract-v0", "GenWurtzExtract-v2"}

    monkeypatch.setattr(
        builtin, "ProviderProcessDevice", lambda *args, **kwargs: Port()
    )
    check = doctor._lab(SimpleNamespace(data_dir=tmp_path))
    assert check.status == doctor.WARN
    assert "degraded" in check.detail
    assert check.facts["sandbox"]["enforced"] is False


def test_doctor_remedy_matches_a_sandbox_failure(tmp_path, monkeypatch):
    # A required sandbox that is missing is not fixed by reinstalling.
    import openai4s.lab.builtin as builtin
    from openai4s import doctor
    from openai4s.lab.models import ErrorCode, LabError

    monkeypatch.setattr(
        builtin, "provider_environment_status", lambda data: {"available": True}
    )

    class Port:
        sandbox_status = {"enforced": False, "state": "unavailable"}

        def describe(self, profile):
            raise LabError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Required provider sandbox unavailable",
                {"reason": "sandbox"},
            )

    monkeypatch.setattr(
        builtin, "ProviderProcessDevice", lambda *args, **kwargs: Port()
    )
    check = doctor._lab(SimpleNamespace(data_dir=tmp_path))
    assert check.status == doctor.WARN
    assert "OPENAI4S_KERNEL_SANDBOX" in check.remedy
    assert "lab setup" not in check.remedy


import pytest


@pytest.mark.external
def test_external_setup_and_cli_smoke(tmp_path, monkeypatch, capsys):
    import os
    import time

    from openai4s.lab.provider_env import resolve_provider_python

    destination = os.environ.get("OPENAI4S_LAB_SETUP_TEST_DIR")
    if not destination:
        pytest.skip(
            "set OPENAI4S_LAB_SETUP_TEST_DIR to explicitly build a fresh provider environment"
        )
    data = Path(destination).absolute()
    assert not (data / "lab/providers/chemgymrl/current").exists()
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(data))
    monkeypatch.delenv("OPENAI4S_LAB_CHEMGYMRL_PYTHON", raising=False)
    start = time.monotonic()
    assert main(["lab", "setup", "chemgymrl"]) == 0
    installed = json.loads(capsys.readouterr().out)
    assert installed["status"] == "installed"
    assert resolve_provider_python(data) == installed["python"]
    for profile in ("WaterOilExtract-v0", "GenWurtzExtract-v2"):
        assert main(["lab", "smoke", "--profile", profile]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["receipt"]["applied"] is True
        assert result["sandbox"]["state"] != "not_started"
    assert not list(data.rglob("*.db"))
    print(
        json.dumps(
            {
                "setup_and_smoke_seconds": round(time.monotonic() - start, 3),
                "generation": installed["generation"],
            }
        )
    )
