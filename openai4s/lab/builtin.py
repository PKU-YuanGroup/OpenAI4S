"""Explicit builtin simulation registration and database-free operator commands."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from openai4s.lab.devices import DeviceRegistration, DeviceRegistry
from openai4s.lab.manifest import load_descriptor, project_receipt
from openai4s.lab.models import (
    Dispatch,
    ErrorCode,
    LabError,
    NormalizedCommand,
    Quantity,
    RunMode,
    SessionOpenRequest,
    new_id,
)
from openai4s.lab.provider_env import (
    provider_environment_status,
    resolve_provider_python,
    setup_provider,
)
from openai4s.lab.provider_process import ProviderProcessDevice


@dataclass(frozen=True)
class BuiltinDeviceRegistration(DeviceRegistration):
    # W1's frozen registry has no availability slot. Consumers may call this
    # optional callback without changing the shared DeviceRegistration ABI.
    availability: Callable[[], dict[str, Any]]


def _manifests():
    root = files("openai4s_lab_provider.chemgymrl").joinpath("manifests")
    descriptors = {}
    try:
        for entry in sorted(root.iterdir(), key=lambda path: path.name):
            if entry.name.endswith(".json"):
                descriptor = load_descriptor(
                    json.loads(entry.read_text(encoding="utf-8"))
                )
                if (
                    descriptor.device_id != "chemgym.extractor.01"
                    or descriptor.backend != "chemgymrl"
                    or entry.name != descriptor.profile + ".json"
                ):
                    raise ValueError("manifest identity")
                descriptors[descriptor.profile] = descriptor
        if not descriptors:
            raise ValueError("no manifests")
    except Exception:
        raise LabError(
            ErrorCode.ADAPTER_MISMATCH, "Invalid bundled ChemGymRL manifest"
        ) from None
    return descriptors


def register_builtin_devices(registry: DeviceRegistry, cfg) -> None:
    data_dir = Path(cfg.data_dir)
    package_dir = Path(str(files("openai4s_lab_provider")))

    def factory(device_id, backend):
        return ProviderProcessDevice(
            device_id,
            backend,
            python=(
                sys.executable
                if backend == "toy"
                else resolve_provider_python(data_dir)
            ),
            package_dir=package_dir,
            runs_root=data_dir / "lab/runs",
            sandbox_mode=os.environ.get("OPENAI4S_KERNEL_SANDBOX", "auto"),
        )

    descriptors = _manifests()
    registry.register(
        BuiltinDeviceRegistration(
            device_id="chemgym.extractor.01",
            backend="chemgymrl",
            mode=RunMode.SIMULATION,
            title="ChemGymRL extractor (simulation)",
            profiles=tuple(descriptors),
            descriptor_loader=descriptors.__getitem__,
            port_factory=lambda: factory("chemgym.extractor.01", "chemgymrl"),
            availability=lambda: provider_environment_status(data_dir),
        )
    )
    if os.environ.get("OPENAI4S_LAB_ENABLE_TOY") == "1":
        from openai4s_lab_provider.toy import PROFILE, Backend

        descriptor = load_descriptor(Backend().describe(PROFILE))
        registry.register(
            BuiltinDeviceRegistration(
                device_id="toy.extractor.01",
                backend="toy",
                mode=RunMode.SIMULATION,
                title="Toy extractor (simulation)",
                profiles=(PROFILE,),
                descriptor_loader=lambda profile: descriptor,
                port_factory=lambda: factory("toy.extractor.01", "toy"),
                availability=lambda: {
                    "available": True,
                    "source": "daemon_python",
                    "generation": None,
                    "detail": "Explicitly enabled stdlib toy",
                },
            )
        )


def lab_status(cfg):
    registry = DeviceRegistry()
    register_builtin_devices(registry, cfg)
    return {
        "devices": [
            {
                "device_id": reg.device_id,
                "profiles": list(reg.profiles),
                "backend": reg.backend,
                "availability": reg.availability(),
            }
            for reg in registry.list()
        ],
        "provider_environment": provider_environment_status(cfg.data_dir),
        "sandbox": {
            "mode": os.environ.get("OPENAI4S_KERNEL_SANDBOX", "auto"),
            "state": "not_probed",
            "detail": "Actual boundary is reported by smoke / doctor when a provider starts",
        },
        "toy_enabled": os.environ.get("OPENAI4S_LAB_ENABLE_TOY") == "1",
    }


def lab_smoke(cfg, profile="WaterOilExtract-v0"):
    registry = DeviceRegistry()
    register_builtin_devices(registry, cfg)
    reg = next((reg for reg in registry.list() if profile in reg.profiles), None)
    if reg is None:
        raise LabError(
            ErrorCode.DEVICE_NOT_FOUND,
            "Profile not registered; toy requires OPENAI4S_LAB_ENABLE_TOY=1",
        )
    descriptor = registry.describe(reg.device_id, profile)
    port = reg.port_factory()
    opened = port.open(
        SessionOpenRequest(profile, 42, {}, descriptor.capability_revision)
    )
    try:
        # A settling step works on a fresh ChemGymRL bench; the toy starts with
        # a full source beaker. No hidden simulator state is consulted.
        capability = next(
            (cap for cap in descriptor.capabilities if cap.operation == "settle_model"),
            None,
        )
        if capability is None:
            capability = next(
                cap
                for cap in descriptor.capabilities
                if cap.operation == "transfer_liquid"
            )
        command = NormalizedCommand(
            new_id("labrun"),
            capability.operation,
            capability.source,
            capability.target,
            {
                name: Quantity(spec.allowed[0], spec.unit)
                for name, spec in capability.parameters.items()
            },
            0,
            "smoke",
            capability.capability_id,
        )
        receipt = port.execute(
            opened.session_id,
            Dispatch(new_id("labcmd"), command, {r: 1 for r in capability.resources}),
        )
        if not receipt.applied:
            raise LabError(
                ErrorCode.PRECONDITION_FAILED, "Provider smoke step was refused"
            )
        return {
            "status": "ok",
            "device_id": reg.device_id,
            "profile": profile,
            "seed": 42,
            "receipt": project_receipt(receipt),
            "sandbox": port.sandbox_status,
        }
    finally:
        port.close(opened.session_id)


def doctor_check(cfg):
    from openai4s.doctor import OK, WARN, Check

    environment = provider_environment_status(cfg.data_dir)
    if not environment["available"]:
        return Check(
            "lab",
            OK,
            environment["detail"],
            facts={
                "provider_environment": environment,
                "toy_enabled": os.environ.get("OPENAI4S_LAB_ENABLE_TOY") == "1",
            },
        )
    try:
        registry = DeviceRegistry()
        register_builtin_devices(registry, cfg)
        registration = registry.get("chemgym.extractor.01")
        port = registration.port_factory()
        port.describe(registration.profiles[0])
        return Check(
            "lab",
            OK if port.sandbox_status["enforced"] else WARN,
            "ChemGymRL provider ready; sandbox " + port.sandbox_status["state"],
            facts={"provider_environment": environment, "sandbox": port.sandbox_status},
        )
    except LabError as exc:
        return Check(
            "lab",
            WARN,
            str(exc),
            "Run openai4s lab setup chemgymrl",
            {"provider_environment": environment},
        )


def run_cli(args):
    from openai4s.config import Config

    cfg = Config()
    try:
        if args.lab_action == "setup":
            result = setup_provider(
                cfg.data_dir,
                python=args.python,
                dry_run=args.dry_run,
                rollback=args.rollback,
            )
        elif args.lab_action == "status":
            result = lab_status(cfg)
        else:
            result = lab_smoke(cfg, args.profile)
    except LabError as exc:
        print(json.dumps({"error": exc.to_dict()}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
