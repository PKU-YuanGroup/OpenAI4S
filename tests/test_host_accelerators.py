"""Local accelerator discovery is separate from backend readiness."""

from __future__ import annotations

import subprocess

from openai4s.host.accelerators import (
    AcceleratorRoutingService,
    LocalAcceleratorService,
)


def test_local_h100_inventory_does_not_require_docker_or_claim_backend_readiness():
    found = {
        "nvidia-smi": "/usr/bin/nvidia-smi",
        "docker": None,
        "podman": None,
    }
    completed = subprocess.CompletedProcess(
        ["nvidia-smi"],
        0,
        "\n".join(
            f"{index}, NVIDIA H100 80GB HBM3, 81559 MiB, 550.54.15"
            for index in range(4)
        ),
        "",
    )
    service = LocalAcceleratorService(
        which=lambda name: found.get(name), run=lambda *_a, **_kw: completed
    )

    status = service.status()

    assert status["available"] is True
    assert status["gpu_count"] == 4
    assert status["devices"][0]["name"] == "NVIDIA H100 80GB HBM3"
    assert status["devices"][0]["memory_total_mib"] == 81559.0
    assert status["container_runtimes"] == []
    assert "backends and checkpoints must be checked separately" in status["note"]


def test_installed_nvidia_smi_with_driver_failure_is_not_reported_as_cpu_only():
    completed = subprocess.CompletedProcess(
        ["nvidia-smi"], 9, "", "Failed to communicate with NVIDIA driver"
    )
    service = LocalAcceleratorService(
        which=lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
        run=lambda *_a, **_kw: completed,
    )

    status = service.status()

    assert status["available"] is False
    assert status["probe_executable"] == "/usr/bin/nvidia-smi"
    assert "driver" in status["probe_error"]
    assert "present" in status["note"]


def test_route_discovery_is_local_first_and_requires_user_choice_when_both_exist():
    calls = []

    def local():
        calls.append("local")
        return {"available": True, "gpu_count": 4}

    def remote():
        calls.append("remote")
        return {
            "configured": True,
            "hosts": [{"alias": "lab", "label": "Lab H100", "gpu_count": 8}],
        }

    status = AcceleratorRoutingService(
        local_status=local, remote_status=remote
    ).status()

    assert calls == ["local", "remote"]
    assert [route["execution_target"] for route in status["candidate_routes"]] == [
        "local",
        "ssh:lab",
    ]
    assert status["selection_required"] is True
    assert status["selected_route"] is None
