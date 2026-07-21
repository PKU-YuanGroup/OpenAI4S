"""Offline contracts for the bundled Python/R setup manifests."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _setup_args(
    *,
    only: str | None = None,
    profile: str | None = None,
    dry_run: bool = False,
    update: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        only=only,
        profile=profile,
        dry_run=dry_run,
        update=update,
    )


def _write_specs(root: Path, *names: str) -> None:
    for name in names:
        (root / f"{name}.yml").write_text(
            f"name: {name}\ndependencies:\n  - python=3.11\n",
            encoding="utf-8",
        )


def test_python_manifest_covers_claude_science_portable_baseline():
    lines = set(
        (_REPO / "envs" / "python.yml").read_text(encoding="utf-8").splitlines()
    )
    required = {
        "  - python=3.11",
        "  - numpy",
        "  - pandas<3",
        "  - scipy",
        "  - matplotlib",
        "  - seaborn",
        "  - pillow",
        "  - pysocks",
        "  - socksio",
        "  - rdkit",
        "  - pip",
        "      - pypdfium2==5.9.0",
    }
    assert required <= lines


def test_python_manifest_does_not_pip_install_the_numpy1_abi_rdkit():
    """RDKit must come from conda-forge, never the pip `rdkit-pypi` wheel.

    `rdkit-pypi` is frozen at 2022.9.5 and is built against the NumPy 1.x C ABI.
    This manifest leaves numpy unpinned, so conda resolves NumPy 2.x — and any
    RDKit call that crosses into NumPy then hard-crashes the kernel worker.
    """
    spec = (_REPO / "envs" / "python.yml").read_text(encoding="utf-8")
    # comments may name it (to explain why it is banned); dependency lines may not.
    deps = [
        line.split("#", 1)[0].strip()
        for line in spec.splitlines()
        if line.split("#", 1)[0].strip().startswith("- ")
    ]
    assert not [d for d in deps if d.startswith("- rdkit-pypi")]
    assert "- rdkit" in deps


def test_r_manifest_covers_claude_science_and_reporting_baseline():
    lines = set((_REPO / "envs" / "r.yml").read_text(encoding="utf-8").splitlines())
    required = {
        "  - r-base=4.5.3",
        "  - r-tidyverse=2.0.0",
        "  - r-ggplot2",
        "  - r-jsonlite",
        "  - r-data.table",
        "  - r-rmarkdown",
        "  - r-knitr",
        "  - pandoc=3.10",
    }
    assert required <= lines


@pytest.mark.parametrize("profile", ["standard", "full"])
def test_setup_profiles_are_exposed_by_the_cli(profile):
    from openai4s.cli.main import build_parser

    args = build_parser().parse_args(["setup", "--profile", profile])
    assert args.profile == profile


def test_setup_only_and_profile_are_mutually_exclusive(capsys):
    from openai4s.cli.main import build_parser

    with pytest.raises(SystemExit) as stopped:
        build_parser().parse_args(
            ["setup", "--only", "python", "--profile", "standard"]
        )
    assert stopped.value.code == 2
    capsys.readouterr()


def test_standard_profile_dry_run_targets_only_python_and_r(
    monkeypatch, tmp_path, capsys
):
    cli = importlib.import_module("openai4s.cli.main")

    _write_specs(tmp_path, "python", "r")
    monkeypatch.setattr(cli, "_find_conda_tool", lambda: "micromamba")
    monkeypatch.setattr(cli, "_envs_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_existing_envs", lambda: {})

    assert cli.cmd_setup(_setup_args(profile="standard", dry_run=True)) == 0
    output = capsys.readouterr().out
    assert "python.yml" in output
    assert "r.yml" in output
    assert "phylo.yml" not in output
    assert "struct.yml" not in output


def test_missing_environment_is_created_from_the_spec_name(
    monkeypatch, tmp_path, capsys
):
    cli = importlib.import_module("openai4s.cli.main")

    _write_specs(tmp_path, "python")
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli, "_find_conda_tool", lambda: "micromamba")
    monkeypatch.setattr(cli, "_envs_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_existing_envs", lambda: {})
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    # --update on an env that does not exist yet still creates it.
    assert cli.cmd_setup(_setup_args(only="python", update=True)) == 0
    assert calls == [
        ["micromamba", "env", "create", "-f", str(tmp_path / "python.yml")]
    ]
    assert "1 created" in capsys.readouterr().out


def test_update_targets_the_discovered_prefix_and_does_not_prune(
    monkeypatch, tmp_path, capsys
):
    cli = importlib.import_module("openai4s.cli.main")

    _write_specs(tmp_path, "python")
    # The env lives somewhere the conda tool's own root would not resolve
    # `name: python` to — so the argv has to carry the prefix explicitly.
    prefix = tmp_path / "elsewhere" / "envs" / "python"
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli, "_find_conda_tool", lambda: "micromamba")
    monkeypatch.setattr(cli, "_envs_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_existing_envs", lambda: {"python": prefix})
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    assert cli.cmd_setup(_setup_args(only="python", update=True)) == 0
    assert calls == [
        [
            "micromamba",
            "env",
            "update",
            "-p",
            str(prefix),
            "-f",
            str(tmp_path / "python.yml"),
        ]
    ]
    assert "--prune" not in calls[0]
    assert "1 updated" in capsys.readouterr().out


def test_existing_environment_is_not_mutated_without_update(
    monkeypatch, tmp_path, capsys
):
    cli = importlib.import_module("openai4s.cli.main")

    _write_specs(tmp_path, "r")
    monkeypatch.setattr(cli, "_find_conda_tool", lambda: "micromamba")
    monkeypatch.setattr(cli, "_envs_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_existing_envs", lambda: {"r": tmp_path / "envs" / "r"})

    def unexpected_run(*args, **kwargs):
        raise AssertionError("setup must not mutate an existing env without --update")

    monkeypatch.setattr(cli.subprocess, "run", unexpected_run)
    assert cli.cmd_setup(_setup_args(only="r")) == 0
    assert "use --update to sync" in capsys.readouterr().out


def test_root_setup_help_exposes_kernel_environment_modes():
    proc = subprocess.run(
        ["bash", str(_REPO / "setup.sh"), "--help"],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--with-kernel-envs" in proc.stdout
    assert "--update-kernel-envs" in proc.stdout
