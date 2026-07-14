"""Offline unit contracts for release and source-security gates."""

from __future__ import annotations

import importlib.util
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"openai4s_test_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_source_secret_scan_detects_without_echoing_values(tmp_path):
    scanner = _load_script("source_secret_scan")
    secret = "sk-" + "z" * 32
    (tmp_path / "module.py").write_text(f'API_TOKEN = "{secret}"\n', encoding="utf-8")

    findings = scanner.scan(tmp_path)

    assert [(item.path, item.line, item.detector) for item in findings] == [
        ("module.py", 1, "openai-api-key")
    ]
    assert secret not in repr(findings)


def test_source_secret_scan_allows_explicit_synthetic_fixtures(tmp_path):
    scanner = _load_script("source_secret_scan")
    (tmp_path / "fixture.py").write_text(
        'TOKEN = "sk-SYNTHETIC-DO-NOT-LEAK-123456789"\n',
        encoding="utf-8",
    )
    (tmp_path / "binary.bin").write_bytes(b"\0" + b"sk-" + b"z" * 40)

    assert scanner.scan(tmp_path) == []


def test_source_secret_scan_rejects_credential_files(tmp_path):
    scanner = _load_script("source_secret_scan")
    (tmp_path / ".env").write_text("SAFE_PLACEHOLDER=1\n", encoding="utf-8")
    (tmp_path / ".env.production").write_text("SAFE_PLACEHOLDER=1\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text(
        "OPENAI4S_LLM_API_KEY=your-key-here\n", encoding="utf-8"
    )

    findings = scanner.scan(tmp_path)

    assert [(item.path, item.detector) for item in findings] == [
        (".env", "credential-file"),
        (".env.production", "credential-file"),
    ]


def _metadata(*, dependency: str | None = None, summary: str = "OpenAI4S") -> bytes:
    requires = f"Requires-Dist: {dependency}\n" if dependency else ""
    return (
        "Metadata-Version: 2.4\n"
        "Name: openai4s\n"
        "Version: 0.1.0\n"
        f"Summary: {summary}\n"
        "License-Expression: MIT\n"
        "Project-URL: Homepage, https://github.com/PKU-YuanGroup/OpenAI4S\n"
        "Project-URL: Documentation, https://github.com/PKU-YuanGroup/OpenAI4S/tree/main/docs\n"
        "Project-URL: Issues, https://github.com/PKU-YuanGroup/OpenAI4S/issues\n"
        "Project-URL: Source, https://github.com/PKU-YuanGroup/OpenAI4S\n"
        "Requires-Python: >=3.10\n"
        "Description-Content-Type: text/markdown\n"
        f"{requires}\n"
    ).encode()


def _write_wheel(path: Path, verifier, *, omit: str | None = None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in verifier._WHEEL_REQUIRED:
            if name != omit:
                archive.writestr(name, b"resource")
        dist_info = "openai4s-0.1.0.dist-info"
        archive.writestr(f"{dist_info}/METADATA", _metadata())
        archive.writestr(
            f"{dist_info}/WHEEL", b"Wheel-Version: 1.0\nTag: py3-none-any\n"
        )
        archive.writestr(
            f"{dist_info}/entry_points.txt",
            b"[console_scripts]\nopenai4s = openai4s.cli:main\n",
        )


def _write_sdist(path: Path, verifier) -> None:
    root = "openai4s-0.1.0"
    with tarfile.open(path, "w:gz") as archive:
        for name in verifier._SDIST_REQUIRED:
            payload = b"resource"
            info = tarfile.TarInfo(f"{root}/{name}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def test_release_artifact_verifier_accepts_complete_archives(tmp_path):
    verifier = _load_script("verify_release_artifacts")
    wheel = tmp_path / "openai4s-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "openai4s-0.1.0.tar.gz"
    _write_wheel(wheel, verifier)
    _write_sdist(sdist, verifier)

    assert verifier.verify(tmp_path) == (wheel, sdist)


def test_release_artifact_verifier_rejects_missing_runtime_resource(tmp_path):
    verifier = _load_script("verify_release_artifacts")
    wheel = tmp_path / "openai4s-0.1.0-py3-none-any.whl"
    missing = "openai4s/kernel/r_worker.R"
    _write_wheel(wheel, verifier, omit=missing)

    with pytest.raises(verifier.ReleaseCheckError, match="r_worker.R"):
        verifier.verify_wheel(wheel)


def test_release_artifact_verifier_rejects_core_dependency():
    verifier = _load_script("verify_release_artifacts")

    with pytest.raises(verifier.ReleaseCheckError, match="non-extra dependencies"):
        verifier._verify_metadata(_metadata(dependency="requests>=2"))

    verifier._verify_metadata(_metadata(dependency='numpy>=1.24; extra == "science"'))


def test_release_artifact_verifier_requires_publishable_metadata():
    verifier = _load_script("verify_release_artifacts")

    with pytest.raises(verifier.ReleaseCheckError, match="no Summary"):
        verifier._verify_metadata(_metadata(summary=""))


def _write_versions(root: Path, project: str, package: str) -> None:
    (root / "openai4s").mkdir()
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "openai4s"\nversion = "{project}"\n',
        encoding="utf-8",
    )
    (root / "openai4s" / "__init__.py").write_text(
        f'__version__ = "{package}"\n',
        encoding="utf-8",
    )


def test_release_tag_verifier_requires_exact_semver_and_matching_sources(tmp_path):
    verifier = _load_script("verify_release_tag")
    _write_versions(tmp_path, "1.2.3", "1.2.3")

    assert verifier.verify(tmp_path, "v1.2.3") == "1.2.3"
    with pytest.raises(verifier.ReleaseTagError, match="vMAJOR.MINOR.PATCH"):
        verifier.verify(tmp_path, "release-1.2.3")


def test_release_tag_verifier_rejects_version_drift(tmp_path):
    verifier = _load_script("verify_release_tag")
    _write_versions(tmp_path, "1.2.3", "1.2.4")

    with pytest.raises(verifier.ReleaseTagError, match="openai4s/__init__.py=1.2.4"):
        verifier.verify(tmp_path, "v1.2.3")


def test_release_workflow_keeps_source_build_and_offline_install_gates():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text("utf-8")

    for contract in (
        "python scripts/source_secret_scan.py",
        "uv build --no-sources --out-dir dist --clear",
        "python scripts/verify_release_artifacts.py dist",
        "PIP_NO_INDEX",
        "--no-deps",
        "scripts/release_import_smoke.py",
    ):
        assert contract in workflow


def test_publish_workflow_uses_verified_artifact_and_job_scoped_oidc():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")

    for contract in (
        "types: [published]",
        "scripts/verify_release_tag.py",
        "git cat-file -t",
        "git merge-base --is-ancestor HEAD origin/main",
        "scripts/source_secret_scan.py",
        "scripts/verify_release_artifacts.py",
        "python-package-distributions",
        "needs: build",
        "environment:",
        "name: pypi",
        "id-token: write",
        "pypa/gh-action-pypi-publish@7f25271a4aa483500f742f9492b2ab5648d61011",
    ):
        assert contract in workflow

    assert workflow.index("id-token: write") > workflow.index("publish:")


def test_distribution_manifest_keeps_release_and_runtime_resources():
    manifest = (ROOT / "MANIFEST.in").read_text("utf-8")

    for contract in (
        "include scripts/*.py",
        "recursive-include docs *.md",
        "recursive-include skills",
        "recursive-include openai4s/compute/templates",
        "recursive-include openai4s/kernel *.R",
        "recursive-include openai4s/server/webui",
        "global-exclude *.py[cod]",
    ):
        assert contract in manifest
