"""Runtime package-installation control tool."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from openai4s.tools.base import Tool


def install_target(active_env_bin: str | None) -> tuple[str | None, str]:
    """``(interpreter, environment name)`` the session kernel actually runs.

    The kernel runs the daemon's own interpreter ("base") unless the session
    switched to an environment, in which case ``active_env_bin`` is that
    environment's ``bin`` directory. The interpreter is None for an R-only
    environment, which has no Python to install into.
    """
    if not active_env_bin:
        return sys.executable, "base"
    bindir = Path(active_env_bin)
    for name in ("python", "python3"):
        candidate = bindir / name
        if candidate.exists():
            return str(candidate), bindir.parent.name
    return None, bindir.parent.name


class EnvCreateTool(Tool):
    """Install requested packages into the session kernel's environment."""

    name = "env_create"
    host_method = "env_setup"
    description = (
        "Install extra packages into the environment the kernel runs in. Needs "
        "the user's approval; never changes the version of an installed package."
    )
    parameters = {
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "description": "Optional environment label.",
            },
            "packages": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "maxItems": 100,
                "description": "Package names to install.",
            },
        },
        "required": ["packages"],
    }
    read_only = False
    side_effect_class = "runtime_mutation"
    resource_key_prefix = "kernel"
    resource_target_default = "environment"

    def permission_target(self, arguments: Any) -> str:
        if not isinstance(arguments, dict):
            return ""
        packages = arguments.get("packages") or []
        if packages:
            return " ".join(str(package) for package in packages)
        return str(arguments.get("name") or "")

    def execute(self, runtime: Any, arguments: dict) -> dict:
        from openai4s.kernel import preinstall

        arguments = arguments or {}
        packages = [
            package
            for package in (arguments.get("packages") or [])
            if isinstance(package, str)
        ]
        # ``name`` is only the caller's label. It used to be echoed back as if
        # it were the environment, so "Setting up the lab_env
        # environment · installed …" was shown while pip wrote into the
        # daemon's own venv. Report the environment that was really targeted.
        interpreter, environment = install_target(
            getattr(runtime, "active_env_bin", None)
        )
        if not packages:
            return {
                "name": environment,
                "environment": environment,
                "installed": [],
                "ok": True,
                "note": "no packages requested",
            }
        if interpreter is None:
            return {
                "name": environment,
                "environment": environment,
                "ok": False,
                "installed": [],
                "failed": [
                    {
                        "name": ", ".join(packages),
                        "error": (
                            f"the kernel environment '{environment}' has no Python "
                            "interpreter, so Python packages cannot be installed "
                            "into it"
                        ),
                    }
                ],
                "log": "",
            }
        result = preinstall.install(
            packages, python=interpreter, preserve_installed=True
        )
        result["name"] = environment
        result["environment"] = environment
        return result


__all__ = ["EnvCreateTool"]
