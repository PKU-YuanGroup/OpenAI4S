#!/usr/bin/env python3
"""Smoke-test an installed, dependency-free OpenAI4S wheel.

Run this script with the isolated environment's interpreter from outside the
checkout.  It rejects accidental imports from the source tree and checks the
runtime resources that ordinary module-import tests miss.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIN_CURATED_SKILLS = 20
MIN_COLLECTION_SKILLS = 561


def _require(path: Path, label: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"installed wheel is missing {label}: {path}")


def _check_skill_catalog(skills_dir: Path) -> int:
    _require(skills_dir / "bioskills" / "COLLECTION.json", "bioSkills marker")
    _require(skills_dir / "bioskills" / "LICENSE", "bioSkills license")
    _require(skills_dir / "bioskills" / "MANIFEST.json", "bioSkills manifest")
    curated = sorted(skills_dir.glob("*/SKILL.md"))
    collection = sorted((skills_dir / "bioskills").glob("*/SKILL.md"))
    if len(curated) < MIN_CURATED_SKILLS:
        raise RuntimeError(
            f"installed skill catalog is incomplete: {len(curated)} curated "
            f"skill(s) at {skills_dir}"
        )
    if len(collection) < MIN_COLLECTION_SKILLS:
        raise RuntimeError(
            f"installed bioSkills collection is incomplete: {len(collection)} "
            f"recipe(s) at {skills_dir / 'bioskills'}"
        )
    return len(curated) + len(collection)


def _check_discoverable_catalog(cfg: object, expected_count: int) -> None:
    """Exercise the installed loader, not just filesystem glob counts."""

    from openai4s.skills_loader import SkillLoader

    try:
        discovered = SkillLoader(cfg=cfg).discover()
    except (OSError, ValueError) as error:
        raise RuntimeError(
            f"installed Skill catalog is not discoverable: {error}"
        ) from error
    if len(discovered) != expected_count:
        raise RuntimeError(
            f"installed Skill loader found {len(discovered)} of "
            f"{expected_count} catalog entries"
        )


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
    if (
        package_root == PROJECT_ROOT / "openai4s"
        or PROJECT_ROOT in package_root.parents
    ):
        raise RuntimeError(f"import smoke resolved the source checkout: {package_root}")

    _require(package_root / "kernel" / "r_worker.R", "R worker")
    _require(package_root / "compute" / "templates" / "run.sh.tmpl", "compute template")
    _require(package_root / "server" / "webui" / "index.html", "Web UI")

    from openai4s.config import Config

    with tempfile.TemporaryDirectory(prefix="openai4s-release-smoke-") as temp:
        cfg = Config(data_dir=Path(temp))
        # Counted separately: a single total lets the 561-recipe collection
        # satisfy the floor on its own, so a wheel that dropped every curated
        # Skill would still report a healthy catalog. The collection is a
        # required runtime resource, not an optional add-on whose absence turns
        # its own completeness check off.
        skill_count = _check_skill_catalog(cfg.skills_dir)
        _check_discoverable_catalog(cfg, skill_count)

        env_dir = package_root.parent / "envs"
        for name in ("python", "phylo", "r", "struct"):
            _require(env_dir / f"{name}.yml", f"{name} environment spec")

        # The benchmark manifests must ship, or an installed `openai4s
        # benchmark` finds nothing and reports a green run over zero workflows.
        from openai4s.benchmark import load_workflows

        workflows = load_workflows()
        if len(workflows) < 10:
            raise RuntimeError(
                f"installed benchmark manifests are incomplete: "
                f"{len(workflows)} workflow(s) found"
            )

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

        deployment = subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "skills.retrosynthesis_planning.model_deployment",
                "list",
            ],
            cwd=temp,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
        )
        try:
            checkpoints = json.loads(deployment.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "installed checkpoint registry did not return JSON"
            ) from exc
        if not isinstance(checkpoints, list):
            raise RuntimeError("installed checkpoint registry did not return a list")
        variants = {item.get("name") for item in checkpoints if isinstance(item, dict)}
        if deployment.returncode != 0 or variants != {
            "pistachio",
            "uspto50k",
            "uspto-full",
        }:
            raise RuntimeError(
                "installed `python -m skills.retrosynthesis_planning."
                "model_deployment list` smoke failed"
            )

    requirements = importlib.metadata.requires("openai4s") or []
    core = [
        requirement
        for requirement in requirements
        if "extra==" not in requirement.partition(";")[2].replace(" ", "").casefold()
    ]
    if core:
        raise RuntimeError(f"installed core unexpectedly requires dependencies: {core}")
    print(
        f"installed release smoke passed: {package_root} "
        f"({len(modules)} modules, {skill_count} skills)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
