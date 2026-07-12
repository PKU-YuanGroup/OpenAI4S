#!/usr/bin/env python3
"""Smoke-test an installed, dependency-free OpenAI4S wheel.

Run this script with the isolated environment's interpreter from outside the
checkout.  It rejects accidental imports from the source tree and checks the
runtime resources that ordinary module-import tests miss.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _require(path: Path, label: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"installed wheel is missing {label}: {path}")


def main() -> int:
    modules = (
        "openai4s",
        "openai4s.agent.engine",
        "openai4s.cli.main",
        "openai4s.compute.manager",
        "openai4s.host_dispatch",
        "openai4s.kernel.r_kernel",
        "openai4s.llm",
        "openai4s.server.gateway",
        "openai4s.storage.actions",
        "openai4s.tools.registry",
        "openai4s.adapters.jupyter",
        "openai4s_compute_provider",
        "openai4s_worker_runtime",
    )
    imported = [importlib.import_module(name) for name in modules]
    package_root = Path(imported[0].__file__).resolve().parent
    if package_root == PROJECT_ROOT / "openai4s" or PROJECT_ROOT in package_root.parents:
        raise RuntimeError(f"import smoke resolved the source checkout: {package_root}")

    _require(package_root / "kernel" / "r_worker.R", "R worker")
    _require(package_root / "compute" / "templates" / "run.sh.tmpl", "compute template")
    _require(package_root / "server" / "webui" / "index.html", "Web UI")

    from openai4s.config import Config

    with tempfile.TemporaryDirectory(prefix="openai4s-release-smoke-") as temp:
        cfg = Config(data_dir=Path(temp))
        skills = sorted(cfg.skills_dir.glob("*/SKILL.md"))
        if len(skills) < 20:
            raise RuntimeError(
                f"installed skill catalog is incomplete: {len(skills)} skill(s) at {cfg.skills_dir}"
            )

        env_dir = package_root.parent / "envs"
        for name in ("python", "phylo", "r", "struct"):
            _require(env_dir / f"{name}.yml", f"{name} environment spec")

        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [sys.executable, "-I", "-m", "openai4s", "--help"],
            cwd=temp,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0 or "serve" not in completed.stdout:
            raise RuntimeError("installed `python -m openai4s --help` smoke failed")

    requirements = importlib.metadata.requires("openai4s") or []
    core = [
        requirement
        for requirement in requirements
        if "extra==" not in requirement.partition(";")[2].replace(" ", "").casefold()
    ]
    if core:
        raise RuntimeError(f"installed core unexpectedly requires dependencies: {core}")
    print(
        f"installed release smoke passed: {package_root} ({len(modules)} modules, {len(skills)} skills)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
