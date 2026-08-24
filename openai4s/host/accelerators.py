"""Host-local accelerator discovery with no optional runtime dependencies."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, Callable

_MEMORY_MIB = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*MiB\s*$", re.I)


class LocalAcceleratorService:
    """Probe hardware at the daemon's local execution boundary.

    This deliberately says nothing about model readiness. A visible GPU, an
    installed container runtime, and an admitted model backend are independent
    facts and are projected independently by callers.
    """

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        run: Callable[..., Any] = subprocess.run,
    ) -> None:
        self._which = which
        self._run = run

    def status(self) -> dict[str, Any]:
        binary = self._which("nvidia-smi")
        runtimes = [
            name for name in ("docker", "podman") if self._which(name) is not None
        ]
        base: dict[str, Any] = {
            "available": False,
            "vendor": "nvidia",
            "source": "nvidia-smi",
            "probe_executable": binary,
            "gpu_count": 0,
            "devices": [],
            "container_runtimes": runtimes,
            "probe_error": None,
            "note": "No local NVIDIA GPU was detected.",
        }
        if binary is None:
            base["probe_error"] = "nvidia-smi is not installed or not on PATH"
            return base
        try:
            process = self._run(
                [
                    binary,
                    "--query-gpu=index,name,memory.total,driver_version",
                    "--format=csv,noheader",
                ],
                capture_output=True,
                text=True,
                timeout=6,
            )
        except Exception as error:  # noqa: BLE001
            # `GET /compute/gpu` and `host.capabilities()` both read this, and
            # neither wraps it. Narrowing to OSError/TimeoutExpired turned a
            # `text=True` UnicodeDecodeError on a localized driver banner into
            # a 500 and an empty capabilities reply, where the route it
            # replaced could not fail at all. A probe that cannot answer
            # reports that it could not answer.
            base["probe_error"] = f"{type(error).__name__}: {error}"
            base["note"] = "The local GPU probe could not be completed."
            return base
        if process.returncode != 0:
            detail = (process.stderr or process.stdout or "").strip()[-1000:]
            base["probe_error"] = (
                f"nvidia-smi exited with status {process.returncode}"
                + (f": {detail}" if detail else "")
            )
            base["note"] = "nvidia-smi is present but could not query the driver."
            return base

        devices = []
        for line in process.stdout.splitlines():
            fields = [field.strip() for field in line.split(",", 3)]
            if len(fields) != 4:
                continue
            raw_memory = fields[2]
            match = _MEMORY_MIB.fullmatch(raw_memory)
            devices.append(
                {
                    # Always a string. The route this replaced was rewritten
                    # precisely because a field whose *shape* depended on the
                    # host is not a contract; an int here would have been the
                    # same defect one level down, since a MIG instance reports
                    # `0:1` and some vGPU drivers report `[N/A]`.
                    "index": fields[0],
                    "name": fields[1],
                    "memory_total_mib": float(match.group(1)) if match else None,
                    "driver_version": fields[3],
                }
            )
        if not devices:
            base["probe_error"] = "nvidia-smi returned no parseable GPU rows"
            base["note"] = "nvidia-smi ran but returned no local GPU inventory."
            return base
        base.update(
            {
                "available": True,
                "gpu_count": len(devices),
                "devices": devices,
                "note": (
                    "Local GPU hardware is visible. Model backends and checkpoints "
                    "must be checked separately."
                ),
            }
        )
        return base

    def legacy_web_status(self) -> dict[str, Any]:
        """Keep the existing ``GET /compute/gpu`` response contract stable."""
        status = self.status()
        devices = status["devices"]
        first = devices[0] if devices else {}
        return {
            "available": status["available"],
            "gpu_name": first.get("name"),
            "gpu_count": status["gpu_count"],
            # Historical field name retained for API compatibility. The old
            # route also returned nvidia-smi's driver_version in this field.
            "cuda_version": first.get("driver_version"),
            "note": (
                "GPU detected via nvidia-smi."
                if status["available"]
                else "No usable local NVIDIA GPU was detected."
            ),
        }


class AcceleratorRoutingService:
    """Present local first, then configured remote routes, without choosing."""

    def __init__(
        self,
        *,
        local_status: Callable[[], dict[str, Any]],
        remote_status: Callable[[], dict[str, Any]],
    ) -> None:
        self._local_status = local_status
        self._remote_status = remote_status

    def status(self) -> dict[str, Any]:
        # Probe order is part of the contract: local hardware first, then the
        # SSH registry. Neither result is used to silently override the other.
        local = self._local_status()
        remote = self._remote_status()
        candidates = []
        if local.get("available"):
            candidates.append(
                {
                    "execution_target": "local",
                    "kind": "local",
                    "label": "Local GPU",
                    "gpu_count": local.get("gpu_count", 0),
                    "currently_reachable": True,
                }
            )
        for host in remote.get("hosts") or []:
            alias = str(host.get("alias") or "")
            if not alias:
                continue
            candidates.append(
                {
                    "execution_target": f"ssh:{alias}",
                    "kind": "ssh_remote",
                    "label": host.get("label") or alias,
                    "gpu_count": host.get("gpu_count", 0),
                    # Registration is durable configuration, not a live SSH
                    # probe. The selected route's preflight must establish
                    # current reachability before installation or execution.
                    "currently_reachable": None,
                }
            )
        return {
            "schema_version": "openai4s.accelerator-status.v1",
            "probe_order": ["local", "ssh_remote"],
            "local": local,
            "ssh_remote": remote,
            "candidate_routes": candidates,
            "selection_required": len(candidates) > 1,
            "selected_route": None,
            "selection_note": (
                "When more than one route is available, ask the user to choose "
                "an execution_target; do not select local or remote automatically. "
                "A selected SSH route still requires a live reachability preflight."
            ),
            "readiness_note": (
                "GPU visibility, route selection, provider registration, and model "
                "backend readiness are independent states."
            ),
        }


__all__ = ["AcceleratorRoutingService", "LocalAcceleratorService"]
