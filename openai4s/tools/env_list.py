"""Runtime-environment discovery control tool."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from openai4s.tools.base import Tool
from openai4s.tools.contexts import EnvironmentToolContext

if TYPE_CHECKING:
    from openai4s.kernel.environments import Environment


class EnvListTool(Tool):
    """List prebuilt kernels and compare their package coverage."""

    name = "env_list"
    host_method = "env_list"
    description = (
        "List the prebuilt runtime environments (optionally check package coverage)."
    )
    parameters = {
        "properties": {
            "packages": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "maxItems": 200,
                "description": "Package names to check per-environment coverage for.",
            },
        },
        "required": [],
    }
    requires_approval = False
    resource_key_prefix = "environment"
    resource_target_default = "catalog"

    @staticmethod
    def is_runnable(environment: Environment) -> bool:
        """Whether cells can run in ``environment`` on the channel it serves.

        ``Environment.interpreter`` is the Python that hosts the notebook
        kernel, so it is None for every R-only environment -- yet ``env_use``
        accepts one and R cells run on its ``Rscript``. Judged per language
        here, for the model; ``Environment.to_dict`` keeps the Python-only
        meaning the Notebook's kernel picker needs. Only an interpreter's
        presence is checked, as for Python: the R worker still needs
        ``jsonlite`` and says so when it is missing.
        """
        if environment.language == "r":
            return bool(environment.rscript)
        return environment.interpreter is not None

    @staticmethod
    def current_environment_name(runtime: EnvironmentToolContext) -> str:
        if runtime.active_env_bin:
            return Path(runtime.active_env_bin).parent.name
        return "base"

    def execute(self, runtime: EnvironmentToolContext, arguments: dict) -> dict:
        from openai4s.kernel import environments as envmod

        arguments = arguments or {}
        packages = [
            package
            for package in (arguments.get("packages") or [])
            if isinstance(package, str)
        ]
        current = self.current_environment_name(runtime)
        environments: list[dict] = []
        best: str | None = None
        best_score = -1
        for environment in envmod.discover_environments():
            has = [package for package in packages if environment.has_package(package)]
            missing = [
                package for package in packages if not environment.has_package(package)
            ]
            runnable = self.is_runnable(environment)
            environments.append(
                {
                    "name": environment.name,
                    "language": environment.language,
                    "python_version": environment.python_version(),
                    "runnable": runnable,
                    "current": environment.name == current,
                    "description": environment.description(),
                    "notable": environment.notable(),
                    "has": has,
                    "missing": missing,
                }
            )
            if runnable and packages:
                score = len(has)
                if score > best_score or (
                    score == best_score and environment.name == current
                ):
                    best_score, best = score, environment.name
        truly_missing = [
            package
            for package in packages
            if not any(package in environment["has"] for environment in environments)
        ]
        return {
            "environments": environments,
            "requested": packages,
            "missing": truly_missing,
            "current": current,
            "recommend": best if (packages and best_score > 0) else None,
        }


__all__ = ["EnvListTool"]
