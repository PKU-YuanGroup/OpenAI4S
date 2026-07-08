"""Environment tools: inspect the prebuilt kernels, switch to one, or install
extra packages into the current kernel.

Routes to the host `env_list` / `env_use` / `env_setup` methods. `env_use`
only queues a pending switch (applied before the next cell), so it is
read-only; `env_create` installs packages and is not.
"""
from __future__ import annotations

from openai4s.tools.base import Tool

env_list = Tool(
    name="env_list",
    host_method="env_list",
    description="List the prebuilt runtime environments (optionally check package coverage).",
    parameters={
        "properties": {
            "packages": {
                "type": "array",
                "description": "Package names to check per-environment coverage for.",
            },
        },
        "required": [],
    },
    read_only=True,
)

env_use = Tool(
    name="env_use",
    host_method="env_use",
    description="Queue a switch of the kernel to a named prebuilt environment.",
    parameters={
        "properties": {
            "name": {
                "type": "string",
                "description": "Prebuilt environment to switch to.",
            },
        },
        "required": ["name"],
    },
    read_only=True,
)

env_create = Tool(
    name="env_create",
    host_method="env_setup",
    description="Install extra packages into the current kernel (pip).",
    parameters={
        "properties": {
            "name": {
                "type": "string",
                "description": "Optional environment label.",
            },
            "packages": {
                "type": "array",
                "description": "Package names to install.",
            },
        },
        "required": ["packages"],
    },
    read_only=False,
)
