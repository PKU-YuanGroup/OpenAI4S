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
            "reason": {
                "type": "string",
                "maxLength": 300,
                "description": (
                    "Optional: one sentence on why this step needs that "
                    "environment. Shown to the user when the switch asks "
                    "for approval."
                ),
            },
        },
        "required": ["name"],
    }
    # Gated so the default rules can tell environments apart: switching into
    # an OpenAI4S environment is allowed (``builtin:*``), switching into one of
    # the user's own conda environments asks (``local:*``).
    requires_approval = True
    read_only = False
    side_effect_class = "runtime_mutation"
    resource_key_prefix = "kernel"
    resource_target_default = "environment"

    @staticmethod
    def is_local(name: str) -> bool:
        """Whether ``name`` is one of the user's own environments."""
        from openai4s.kernel import environments as envmod

        try:
            environment = envmod.get_environment(name)
        except Exception:  # noqa: BLE001 — an unreadable env is not "local"
            return False
        return environment is not None and not environment.builtin

    def permission_target(self, arguments: object) -> str:
        if isinstance(arguments, str):
            name = arguments
        elif isinstance(arguments, dict):
            name = str(arguments.get("name") or arguments.get("env") or "")
        else:
            name = ""
        return f"{'local' if self.is_local(name) else 'builtin'}:{name}"

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
                except Exception as exc:  # noqa: BLE001
                    return {"error": f"env switch refused: {exc}"}
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
        if runtime.on_env_switch is None:
            # No switch mechanism exists in this runtime. Returning ok with a
            # note here made the next cell silently run on the old interpreter
            # while the caller had every reason to believe it switched.
            from openai4s.tools.env_list import EnvListTool

            current = EnvListTool.current_environment_name(runtime)
            return {
                "error": (
                    "environment switching is not available in this runtime; "
                    f"the kernel stays on '{current}'"
                )
            }
        try:
            runtime.on_env_switch(name)
            note = (
                f"switching to '{name}' before the next cell — put your "
                "imports in a new cell"
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": f"env switch refused: {exc}"}
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
