"""Read-only readiness for the bundled ``standard`` kernel profile.

The standard profile is the pair built by ``openai4s setup --profile
standard``: the ``python`` and ``r`` environments described by the shipped
``envs/python.yml`` and ``envs/r.yml`` manifests.  This module deliberately
does not import packages from either environment or start their interpreters.
It compares the manifests' direct dependency intent with the local package
metadata already used by environment discovery.

The public projection is path-free.  Environment prefixes and raw exception
messages are host details that should not be sent to the browser; failures are
reported with stable reason codes instead of being converted into an empty
package set (which would incorrectly claim that every package is missing).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from openai4s import pkgscan
from openai4s.kernel import environments

READY = "ready"
NEEDS_SETUP = "needs_setup"
NEEDS_REPAIR = "needs_repair"
UNAVAILABLE = "unavailable"

STANDARD_PROFILE = "standard"
STANDARD_ENVIRONMENTS = ("python", "r")

_PLAN_REPAIR_COMMAND = (
    "openai4s",
    "env",
    "plan",
    *STANDARD_ENVIRONMENTS,
    "--repair",
)
_APPLY_REPAIR_COMMAND = (
    "openai4s",
    "env",
    "apply",
    *STANDARD_ENVIRONMENTS,
    "--repair",
)
_DEPENDENCY_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\s*(?:[<>=!~].*)?)$")


class _ManifestError(ValueError):
    """A shipped environment manifest cannot be interpreted safely."""


@dataclass(frozen=True)
class _EnvironmentResult:
    name: str
    state: str
    present: bool
    required: tuple[str, ...]
    missing: tuple[str, ...]
    issue: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "state": self.state,
            "present": self.present,
            "required_package_count": len(self.required),
            "installed_required_package_count": (
                None
                if self.state == UNAVAILABLE
                else len(self.required) - len(self.missing)
            ),
            "missing_packages": list(self.missing),
            "issue": self.issue,
        }


def _specs_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "envs"


def _dependency_name(value: str) -> str:
    """Return a normalized name from a simple conda/pip requirement.

    The bundled manifests use direct names with optional conda or PEP 440
    constraints.  Unsupported requirement forms fail closed instead of being
    guessed.  Channel qualification is harmless and is accepted even though
    the current standard manifests do not use it.
    """

    token = value.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        token = token[1:-1].strip()
    if "::" in token:
        token = token.rsplit("::", 1)[1].strip()
    match = _DEPENDENCY_NAME.fullmatch(token)
    if match is None:
        raise _ManifestError("unsupported dependency declaration")
    normalized = pkgscan.normalize_pkg(match.group(1))
    if not normalized:
        raise _ManifestError("empty dependency name")
    return normalized


def _parse_direct_dependencies(path: Path, expected_name: str) -> tuple[str, ...]:
    """Parse the direct dependencies from one shipped, line-oriented spec.

    A YAML dependency parser would add a hard dependency to the control plane.
    The shipped specs intentionally use a narrow structure: a top-level
    ``dependencies:`` list plus an optional nested ``pip:`` list.  This parser
    recognizes exactly that structure and rejects ambiguous nested mappings.
    """

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise _ManifestError("manifest unreadable") from exc

    declared_name: str | None = None
    in_dependencies = False
    in_pip = False
    saw_dependencies = False
    dependencies: list[str] = []

    for raw_line in lines:
        # The bundled files do not carry quoted values containing '#'.  Strip
        # comments before measuring content while retaining indentation.
        content = raw_line.split("#", 1)[0].rstrip()
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        stripped = content.strip()

        if indent == 0:
            if stripped.startswith("name:"):
                declared_name = stripped.partition(":")[2].strip().strip("\"'")
                continue
            if stripped == "dependencies:":
                in_dependencies = True
                in_pip = False
                saw_dependencies = True
                continue
            if in_dependencies:
                # A later top-level key ends the dependency list.
                break
            continue

        if not in_dependencies:
            continue
        if indent == 2 and stripped.startswith("- "):
            item = stripped[2:].strip()
            if item == "pip:":
                in_pip = True
                continue
            in_pip = False
            dependencies.append(_dependency_name(item))
            continue
        if indent > 2 and in_pip and stripped.startswith("- "):
            dependencies.append(_dependency_name(stripped[2:].strip()))
            continue
        raise _ManifestError("unsupported dependencies structure")

    if declared_name != expected_name:
        raise _ManifestError("manifest name mismatch")
    if not saw_dependencies or not dependencies:
        raise _ManifestError("manifest dependencies missing")
    if len(set(dependencies)) != len(dependencies):
        raise _ManifestError("duplicate normalized dependency")
    return tuple(dependencies)


def load_standard_profile_requirements(
    specs_dir: Path | None = None,
) -> dict[str, tuple[str, ...]]:
    """Load the authoritative direct package lists for ``standard``.

    Constraints are removed and names use :func:`pkgscan.normalize_pkg`, the
    same rule used for installed package metadata.
    """

    root = specs_dir if specs_dir is not None else _specs_dir()
    return {
        name: _parse_direct_dependencies(root / f"{name}.yml", name)
        for name in STANDARD_ENVIRONMENTS
    }


def _managed_remediation(state: str) -> dict[str, object] | None:
    if state not in (NEEDS_SETUP, NEEDS_REPAIR):
        return None

    plan_argv = list(_PLAN_REPAIR_COMMAND)
    apply_argv = list(_APPLY_REPAIR_COMMAND)
    return {
        "kind": "managed_generation_repair",
        "plan_argv": plan_argv,
        "apply_argv": apply_argv,
        "commands": [
            {
                "label": "plan",
                "argv": plan_argv,
                "command": " ".join(_PLAN_REPAIR_COMMAND),
            },
            {
                "label": "apply",
                "argv": apply_argv,
                "command": " ".join(_APPLY_REPAIR_COMMAND),
            },
        ],
        "requires_explicit_action": True,
    }


def _requirements_digest(requirements: dict[str, tuple[str, ...]]) -> str:
    canonical = json.dumps(
        {
            "profile": STANDARD_PROFILE,
            "requirements": {
                name: list(requirements[name]) for name in STANDARD_ENVIRONMENTS
            },
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _projection(
    *,
    enabled: bool,
    state: str,
    reason: str | None,
    requirements_digest: str | None = None,
    results: Iterable[_EnvironmentResult] = (),
) -> dict[str, object]:
    environment_results = list(results)
    rows = [result.to_dict() for result in environment_results]
    missing_environments = [
        result.name for result in environment_results if not result.present
    ]
    missing_packages = {
        result.name: list(result.missing)
        for result in environment_results
        if result.missing
    }
    return {
        "schema_version": 1,
        "enabled": enabled,
        "profile": STANDARD_PROFILE,
        "state": state,
        "ready": state == READY,
        "reason": reason,
        "checked_locally": enabled,
        "network_contacted": False,
        "mutation_performed": False,
        "requirements_digest": requirements_digest,
        "required_environments": list(STANDARD_ENVIRONMENTS),
        "missing_environments": missing_environments,
        "missing_packages": missing_packages,
        "environments": rows,
        "remediation": _managed_remediation(state),
    }


def standard_profile_readiness(
    *,
    enabled: bool,
    specs_dir: Path | None = None,
    discover: Callable[[], list[environments.Environment]] | None = None,
    scan_packages: Callable[..., set[str]] | None = None,
) -> dict[str, object]:
    """Return the local-only readiness projection for ``standard``.

    When the rollout flag is off this returns immediately: it does not read the
    manifests or discover environments.  Callers may inject discovery and
    scanning for deterministic tests, but production uses the existing local
    environment discovery and package metadata scanner.
    """

    if enabled is not True:
        return _projection(
            enabled=False,
            state=UNAVAILABLE,
            reason="feature_disabled",
        )

    try:
        requirements = load_standard_profile_requirements(specs_dir)
    except Exception:  # noqa: BLE001 - project a stable, path-free failure
        return _projection(
            enabled=True,
            state=UNAVAILABLE,
            reason="manifest_unavailable",
        )
    requirements_digest = _requirements_digest(requirements)

    if discover is None:
        try:
            managed_layout_safe = environments.standard_generation_layout_is_safe()
        except Exception:  # noqa: BLE001 - path-free fail-closed projection
            managed_layout_safe = False
        if not managed_layout_safe:
            return _projection(
                enabled=True,
                state=UNAVAILABLE,
                reason="managed_environment_layout_invalid",
                requirements_digest=requirements_digest,
            )

    discover_fn = discover or environments.discover_environments
    scan_fn = scan_packages or pkgscan.collect_packages
    try:
        discovered = list(discover_fn())
    except Exception:  # noqa: BLE001 - discovery failure is not "no envs"
        return _projection(
            enabled=True,
            state=UNAVAILABLE,
            reason="environment_discovery_unavailable",
            requirements_digest=requirements_digest,
        )

    selected: dict[str, environments.Environment] = {}
    for environment in discovered:
        name = getattr(environment, "name", None)
        if name not in STANDARD_ENVIRONMENTS:
            continue
        if name in selected:
            return _projection(
                enabled=True,
                state=UNAVAILABLE,
                reason="duplicate_environment",
                requirements_digest=requirements_digest,
            )
        selected[name] = environment

    results: list[_EnvironmentResult] = []
    scan_failed = False
    for name in STANDARD_ENVIRONMENTS:
        required = requirements[name]
        environment = selected.get(name)
        if environment is None:
            results.append(
                _EnvironmentResult(
                    name=name,
                    state=NEEDS_SETUP,
                    present=False,
                    required=required,
                    missing=required,
                    issue="environment_missing",
                )
            )
            continue

        expected_language = "r" if name == "r" else "python"
        runtime = (
            getattr(environment, "rscript", None)
            if expected_language == "r"
            else getattr(environment, "python", None)
        )
        if getattr(environment, "language", None) != expected_language or not runtime:
            results.append(
                _EnvironmentResult(
                    name=name,
                    state=NEEDS_REPAIR,
                    present=True,
                    required=required,
                    missing=required,
                    issue="runtime_mismatch",
                )
            )
            continue

        root = getattr(environment, "root", None)
        try:
            if not isinstance(root, Path) or not root.is_dir():
                raise OSError("environment prefix is not readable")
            installed = {
                pkgscan.normalize_pkg(str(package))
                for package in scan_fn(root, language=expected_language)
            }
        except Exception:  # noqa: BLE001 - never recast an unreadable scan as missing
            scan_failed = True
            results.append(
                _EnvironmentResult(
                    name=name,
                    state=UNAVAILABLE,
                    present=True,
                    required=required,
                    missing=(),
                    issue="package_inventory_unavailable",
                )
            )
            continue

        missing = tuple(package for package in required if package not in installed)
        results.append(
            _EnvironmentResult(
                name=name,
                state=NEEDS_REPAIR if missing else READY,
                present=True,
                required=required,
                missing=missing,
                issue="packages_missing" if missing else None,
            )
        )

    if scan_failed:
        state, reason = UNAVAILABLE, "package_inventory_unavailable"
    elif any(result.state == NEEDS_SETUP for result in results):
        state, reason = NEEDS_SETUP, "environment_missing"
    elif any(result.state == NEEDS_REPAIR for result in results):
        state, reason = NEEDS_REPAIR, "environment_incomplete"
    else:
        state, reason = READY, None
    return _projection(
        enabled=True,
        state=state,
        reason=reason,
        requirements_digest=requirements_digest,
        results=results,
    )


def readiness_failure_message(readiness: dict[str, object]) -> str:
    """Render the path-free, complete remediation shown on every surface."""

    details: list[str] = []
    missing = readiness.get("missing_packages")
    if isinstance(missing, dict):
        for environment in STANDARD_ENVIRONMENTS:
            packages = missing.get(environment)
            if isinstance(packages, list) and packages:
                details.append(
                    f"{environment}: {', '.join(str(item) for item in packages)}"
                )
    missing_envs = readiness.get("missing_environments")
    if isinstance(missing_envs, list) and missing_envs:
        details.insert(
            0,
            "missing environments: " + ", ".join(str(item) for item in missing_envs),
        )

    commands: list[str] = []
    remediation = readiness.get("remediation")
    if isinstance(remediation, dict):
        for key in ("plan_argv", "apply_argv", "argv"):
            argv = remediation.get(key)
            if isinstance(argv, list) and argv:
                command = " ".join(str(item) for item in argv)
                if command not in commands:
                    commands.append(command)
        command = remediation.get("command")
        if isinstance(command, str) and command and command not in commands:
            commands.append(command)

    message = "The standard scientific environment is not ready"
    if details:
        message += ": " + "; ".join(details)
    if commands:
        message += ". Run the managed repair path explicitly: " + " then ".join(
            f"`{command}`" for command in commands
        )
    return message + "."


class EnvironmentReadinessError(RuntimeError):
    """Typed, path-free refusal raised immediately before a local Code Cell.

    The projection is retained so CLI and other local adapters can render the
    same stable error contract as the Web gateway without scraping exception
    text.  Constructing this error performs no discovery or mutation itself.
    """

    def __init__(self, readiness: dict[str, object]) -> None:
        self.readiness = dict(readiness)
        self.error_code = (
            "environment_readiness_unavailable"
            if readiness.get("state") == UNAVAILABLE
            else "environment_not_ready"
        )
        super().__init__(readiness_failure_message(readiness))


__all__ = [
    "EnvironmentReadinessError",
    "NEEDS_REPAIR",
    "NEEDS_SETUP",
    "READY",
    "STANDARD_ENVIRONMENTS",
    "STANDARD_PROFILE",
    "UNAVAILABLE",
    "load_standard_profile_requirements",
    "readiness_failure_message",
    "standard_profile_readiness",
]
