"""Builtin resources and availability are explicit, with toy off by default."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.lab.builtin import register_builtin_devices
from openai4s.lab.devices import DeviceRegistry
from openai4s.lab.models import ErrorCode, LabError


def test_builtin_registration_without_install_never_uses_daemon_python(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("OPENAI4S_LAB_ENABLE_TOY", raising=False)
    monkeypatch.delenv("OPENAI4S_LAB_CHEMGYMRL_PYTHON", raising=False)
    registry = DeviceRegistry()
    register_builtin_devices(registry, SimpleNamespace(data_dir=tmp_path))
    assert [reg.device_id for reg in registry.list()] == ["chemgym.extractor.01"]
    reg = registry.list()[0]
    assert set(reg.profiles) == {"WaterOilExtract-v0", "GenWurtzExtract-v2"}
    assert reg.availability()["available"] is False
    assert reg.port_factory().python is None
    assert registry.describe(reg.device_id, reg.profiles[0]).backend == "chemgymrl"


def test_toy_registration_requires_exact_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_LAB_ENABLE_TOY", "true")
    cfg = SimpleNamespace(data_dir=tmp_path)
    registry = DeviceRegistry()
    register_builtin_devices(registry, cfg)
    assert len(registry.list()) == 1
    monkeypatch.setenv("OPENAI4S_LAB_ENABLE_TOY", "1")
    registry = DeviceRegistry()
    register_builtin_devices(registry, cfg)
    reg = registry.get("toy.extractor.01")
    assert reg.availability()["available"] is True
    assert registry.describe(reg.device_id, reg.profiles[0]).backend == "toy"
    port = reg.port_factory()
    assert port.backend == "toy" and Path(port.python).is_file()


def test_corrupt_bundled_manifest_is_an_explicit_error(tmp_path, monkeypatch):
    import openai4s.lab.builtin as builtin

    manifests = tmp_path / "manifests"
    manifests.mkdir()
    (manifests / "broken.json").write_text('{"corrupted": true}')
    monkeypatch.setattr(builtin, "files", lambda package: tmp_path)
    with pytest.raises(LabError) as caught:
        register_builtin_devices(DeviceRegistry(), SimpleNamespace(data_dir=tmp_path))
    assert caught.value.code == ErrorCode.ADAPTER_MISMATCH
    assert "manifest" in str(caught.value)
