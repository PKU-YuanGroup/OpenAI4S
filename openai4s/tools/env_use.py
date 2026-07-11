"""Runtime-environment switching control tool."""

from __future__ import annotations

from openai4s.tools.base import Tool
from openai4s.tools.contexts import EnvironmentToolContext


class EnvUseTool(Tool):
    """Queue a Python or R kernel switch for the next scientific cell."""

    name = "env_use"
    host_method = "env_use"
    description = "Queue a switch of the kernel to a named prebuilt environment."
    parameters = {
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "description": "Prebuilt environment to switch to.",
            },
        },
        "required": ["name"],
    }
    requires_approval = False
    read_only = False
    side_effect_class = "runtime_mutation"
    resource_key_prefix = "kernel"
    resource_target_default = "environment"

    def execute(self, runtime: EnvironmentToolContext, arguments: dict | str) -> dict:
        from openai4s.kernel import environments as envmod

        if isinstance(arguments, str):
            arguments = {"name": arguments}
        arguments = arguments or {}
        name = arguments.get("name") or arguments.get("env") or ""
        environment = envmod.get_environment(name)
        if environment is None:
            available = [item.name for item in envmod.discover_environments()]
            return {
                "error": f"unknown environment {name!r}; available: "
                + ", ".join(available)
            }
        if environment.interpreter is None:
            if not environment.rscript:
                return {
                    "error": f"'{name}' has neither a Python nor an R "
                    "interpreter — pick another environment (host.env.list())."
                }
            runtime.active_r_env = name
            note = f"subsequent ```r cells run in '{name}'"
            if runtime.on_env_switch is not None:
                try:
                    runtime.on_env_switch(name)
                except Exception:  # noqa: BLE001
                    note = "R env switch failed to register"
            return {
                "ok": True,
                "env": {
                    "name": environment.name,
                    "language": environment.language,
                    "description": environment.description(),
                    "notable": environment.notable(),
                },
                "note": note,
            }
        if runtime.on_env_switch is not None:
            try:
                runtime.on_env_switch(name)
                note = (
                    f"switching to '{name}' before the next cell — put your "
                    "imports in a new cell"
                )
            except Exception:  # noqa: BLE001
                note = "env switch failed to register"
        else:
            note = "env switching is only available in the web session kernel"
        return {
            "ok": True,
            "env": {
                "name": environment.name,
                "language": environment.language,
                "python_version": environment.python_version(),
                "description": environment.description(),
                "notable": environment.notable(),
            },
            "note": note,
        }


__all__ = ["EnvUseTool"]
