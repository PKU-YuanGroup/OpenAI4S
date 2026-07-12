"""Verified remote-science capability registration for host RPC calls."""

from __future__ import annotations

import re
import shlex
import subprocess
from typing import Any, Callable

_PROBE_BINARY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_PROBE_FORBIDDEN = (";", "|", "&", "`", "$(", "\r", "\n", "\x00")


def _reject_probe_metacharacters(value: str, field: str) -> None:
    bad = next((token for token in _PROBE_FORBIDDEN if token in value), None)
    if bad is not None:
        label = {"\r": "CR", "\n": "LF", "\x00": "NUL"}.get(bad, bad)
        raise ValueError(f"{field} contains forbidden shell syntax {label!r}")


def normalize_remote_capability_probe(spec: dict) -> tuple[dict, str]:
    """Return a canonical probe and its single safe remote shell command."""
    has_structured = "probe" in spec and spec.get("probe") is not None
    legacy_raw = spec.get("verify_command")
    if legacy_raw is None:
        legacy = ""
    elif isinstance(legacy_raw, str):
        legacy = legacy_raw.strip()
    else:
        raise ValueError("verify_command must be a string")

    if has_structured and legacy:
        raise ValueError("provide probe or verify_command, not both")

    if has_structured:
        raw = spec.get("probe")
        if not isinstance(raw, dict):
            raise ValueError("probe must be an object")
        kind = raw.get("kind")
        if kind == "path_exists":
            expected = {"kind", "path"}
            if set(raw) != expected:
                raise ValueError("path_exists probe accepts exactly kind and path")
            path = raw.get("path")
            if not isinstance(path, str) or not path.strip():
                raise ValueError("path_exists probe requires a non-empty string path")
            _reject_probe_metacharacters(path, "probe.path")
            probe = {"kind": "path_exists", "path": path}
            return probe, f"test -e {shlex.quote(path)}"
        if kind == "executable_exists":
            expected = {"kind", "binary"}
            if set(raw) != expected:
                raise ValueError(
                    "executable_exists probe accepts exactly kind and binary"
                )
            binary = raw.get("binary")
            if not isinstance(binary, str) or not _PROBE_BINARY.fullmatch(binary):
                raise ValueError(
                    "executable_exists binary must be one plain executable name"
                )
            probe = {"kind": "executable_exists", "binary": binary}
            return probe, f"which {binary}"
        raise ValueError(f"unknown probe kind {kind!r}")

    if legacy:
        _reject_probe_metacharacters(legacy, "verify_command")
        try:
            tokens = shlex.split(legacy, posix=True)
        except ValueError as exc:
            raise ValueError(f"invalid verify_command quoting: {exc}") from exc
        if len(tokens) == 3 and tokens[:2] == ["test", "-e"]:
            path = tokens[2]
            if not path.strip():
                raise ValueError("legacy test probe requires a non-empty path")
            _reject_probe_metacharacters(path, "verify_command path")
            if path.startswith("~") or "$" in path:
                raise ValueError(
                    "verify_command path would no longer be shell-expanded; "
                    "use an absolute path"
                )
            probe = {"kind": "path_exists", "path": path}
            return probe, f"test -e {shlex.quote(path)}"
        if len(tokens) == 2 and tokens[0] == "which":
            binary = tokens[1]
            if not _PROBE_BINARY.fullmatch(binary):
                raise ValueError(
                    "legacy which probe requires one plain executable name"
                )
            probe = {"kind": "executable_exists", "binary": binary}
            return probe, f"which {binary}"
        raise ValueError(
            "verify_command must be exactly 'test -e <path>' or 'which <binary>'"
        )

    script_raw = spec.get("script")
    if script_raw is None:
        script = ""
    elif isinstance(script_raw, str):
        script = script_raw.strip()
    else:
        raise ValueError("script must be a string")
    if not script:
        raise ValueError("provide probe, verify_command, or script")
    _reject_probe_metacharacters(script, "script")
    probe = {"kind": "path_exists", "path": script}
    return probe, f"test -e {shlex.quote(script)}"


class RemoteCapabilityService:
    """Inspect and register real services on configured remote GPU hosts."""

    CORE_CAPABILITIES = ("fold", "score_mutations")

    def __init__(
        self,
        *,
        registry_factory: Callable[[], Any] | None = None,
        run_command: Callable[..., Any] | None = None,
        normalize_probe: Callable[[dict], tuple[dict, str]] = (
            normalize_remote_capability_probe
        ),
    ) -> None:
        self._registry_factory = registry_factory
        self._run_command = run_command
        self._normalize_probe = normalize_probe

    def _registry(self) -> Any:
        if self._registry_factory is not None:
            return self._registry_factory()
        from openai4s.compute import registry

        return registry

    def _runner(self) -> Callable[..., Any]:
        return self._run_command or subprocess.run

    def status(self) -> dict:
        """Project configured hosts without fabricating service availability."""
        registry = self._registry()
        registered_hosts = registry.list_hosts()
        hosts = []
        all_capabilities: set[str] = set()
        for alias, host in registered_hosts.items():
            capabilities = host.get("capabilities") or {}
            all_capabilities.update(capabilities.keys())
            hosts.append(
                {
                    "alias": alias,
                    "label": host.get("label") or alias,
                    "provider": f"ssh:{alias}",
                    "gpus": host.get("gpus"),
                    "gpu_count": host.get("gpu_count", 0),
                    "capabilities": [
                        {
                            "name": name,
                            "engine": (metadata or {}).get("engine"),
                            "script": (metadata or {}).get("script"),
                            "verified": bool((metadata or {}).get("verified_at")),
                            "verified_at": (metadata or {}).get("verified_at"),
                        }
                        for name, metadata in capabilities.items()
                    ],
                }
            )
        return {
            "configured": bool(hosts),
            "default_host": registry.default_host(),
            "hosts": hosts,
            "core_capabilities": list(self.CORE_CAPABILITIES),
            "missing_core_capabilities": [
                capability
                for capability in self.CORE_CAPABILITIES
                if capability not in all_capabilities
            ],
        }

    def register(self, spec: dict) -> dict:
        """Verify a remote service over SSH before recording it as available."""
        registry = self._registry()
        alias = str(spec.get("alias") or "").strip()
        capability = str(spec.get("capability") or spec.get("cap") or "").strip()
        script = str(spec.get("script") or "").strip()
        if not alias:
            return {"error": "register_remote_capability: alias is required"}
        if not capability:
            return {"error": "register_remote_capability: capability is required"}
        if not registry.get_host(alias):
            return {
                "error": (
                    "register_remote_capability: unknown remote GPU host " f"{alias!r}"
                )
            }
        try:
            probe, remote_command = self._normalize_probe(spec)
        except ValueError as exc:
            return {"error": f"register_remote_capability: invalid probe: {exc}"}

        try:
            process = self._runner()(
                [
                    "ssh",
                    "-o",
                    "ConnectTimeout=15",
                    "-o",
                    "BatchMode=yes",
                    alias,
                    remote_command,
                ],
                capture_output=True,
                text=True,
                timeout=90,
            )
        except subprocess.TimeoutExpired:
            return {
                "error": (
                    "register_remote_capability: verification timed out on " f"{alias}"
                )
            }
        except OSError as exc:
            return {
                "error": f"register_remote_capability: ssh to {alias} failed: {exc}"
            }
        if process.returncode != 0:
            tail = ((process.stderr or process.stdout or "")[-800:]).strip()
            return {
                "error": (
                    "register_remote_capability: verification failed on "
                    f"{alias} (rc={process.returncode}). tail: {tail}"
                )
            }

        metadata = {
            "script": script,
            "invoke": spec.get("invoke") or "",
            "engine": spec.get("engine") or capability,
            "markers": spec.get("markers") or {},
            "notes": spec.get("notes") or "",
            "probe": probe,
            "verification": remote_command,
        }
        registry.set_capability(alias, capability, metadata)
        return {
            "ok": True,
            "alias": alias,
            "capability": capability,
            "status": self.status(),
        }


__all__ = [
    "RemoteCapabilityService",
    "normalize_remote_capability_probe",
]
