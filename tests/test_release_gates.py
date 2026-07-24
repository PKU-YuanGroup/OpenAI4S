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
        # The entry point is an explicit dispatch against an existing draft.
        "workflow_dispatch:",
        "inputs.publish",
        "inputs.tag",
        "scripts/release_pipeline.py",
        "scripts/verify_release_tag.py",
        "git cat-file -t",
        "git merge-base --is-ancestor HEAD origin/main",
        "scripts/source_secret_scan.py",
        "scripts/verify_release_artifacts.py",
        "python-package-distributions",
        "environment:",
        "name: pypi",
        "id-token: write",
        # A Docker-container action PyPA only publishes tagged by release ref,
        # so this one is intentionally on `release/v1` rather than a SHA pin
        # (see the justification comment in release.yml and test_governance.py).
        "pypa/gh-action-pypi-publish@release/v1",
    ):
        assert contract in workflow

    assert workflow.index("id-token: write") > workflow.index("pypi:")


def test_the_workflow_has_no_trigger_that_cannot_fire_for_a_draft():
    """The hole review found: `release: [created]` is not emitted for a draft.

    The whole draft-first design hung off that trigger, so the intended entry
    point could never run; a *non-draft* creation does emit it, and the draft
    conditions on the jobs then skipped attachment and publication. A pipeline
    that cannot be reached is not a pipeline.
    """
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    trigger = workflow[workflow.index("\non:") : workflow.index("permissions:")]
    assert "release:" not in trigger, (
        "GitHub does not emit release events for draft releases; a draft-first "
        "pipeline cannot be triggered by one"
    )
    assert "workflow_dispatch:" in trigger


def test_publishing_requires_an_existing_draft_before_anything_runs():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    guard = workflow[workflow.index("  guard:") : workflow.index("  build:")]
    assert "gh release view" in guard
    assert "isDraft" in guard
    assert "already public" in guard
    # ...and every outward-facing job waits for that proof.
    for job in ("  attach:", "  pypi:"):
        block = workflow[workflow.index(job) : workflow.index(job) + 900]
        assert "guard" in block, f"{job.strip()} may run without the draft check"


def test_the_staging_job_consumes_artifacts_and_never_publishes():
    """Running the whole pipeline in the attach job re-ran `build` and
    `pytest` — which the job installs neither of — and, if they happened to
    exist, rebuilt into the very directory holding the verified downloads."""
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    attach = workflow[workflow.index("  attach:") : workflow.index("  pypi:")]
    assert "--from-artifacts" in attach
    assert "--stop-after reverify" in attach
    assert "--draft=false" not in attach
    assert "--only publish" not in attach


def test_the_github_flip_is_the_last_cross_channel_step():
    """It used to happen inside `attach`, with PyPI running afterwards — so an
    OIDC failure, a denied environment approval or a rejected upload left a
    public release with no matching package version."""
    import re

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    finalize = workflow[workflow.index("  finalize:") :]
    assert "--only publish" in finalize
    needs = re.search(r"^    needs: (.+)$", finalize, re.MULTILINE)
    assert needs, "finalize must declare what it waits for"
    for required in ("attach", "pypi"):
        assert required in needs.group(
            1
        ), f"the GitHub flip must not run before {required!r}"
    assert workflow.index("  finalize:") > workflow.index("  pypi:")


def test_the_irreversible_pypi_upload_waits_for_every_other_required_job():
    """A PyPI version number, once taken, is taken forever.

    With `needs: build` alone, a macOS image that failed to build or failed
    `verify_macos_bundle.py` only skipped the staging job — the upload went
    ahead, and the result was a version live on PyPI whose GitHub Release
    carried no assets. Yanking is not the same as never having published.
    """
    import re

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    publish = workflow[workflow.index("  pypi:") : workflow.index("  finalize:")]
    needs = re.search(r"^    needs: (.+)$", publish, re.MULTILINE)
    assert needs, "the PyPI job must declare what it waits for"
    for required in ("guard", "build", "macos-app", "attach"):
        assert required in needs.group(1), (
            f"the PyPI upload must not run before {required!r}; it is the "
            f"irreversible step on that channel"
        )


def test_the_recovery_path_for_a_failed_flip_is_written_down():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    pipeline = (ROOT / "scripts" / "release_pipeline.py").read_text("utf-8")
    for text in (workflow, pipeline):
        assert "--only publish" in text
        assert "do not rebuild" in text.lower()


def test_the_signing_identity_reaches_the_build_that_can_use_it():
    """Passing it only to the staging job meant configuring the secret changed
    nothing about the image and everything about what the gate believed."""
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    macos = workflow[workflow.index("  macos-app:") : workflow.index("  attach:")]
    assert "OPENAI4S_MACOS_SIGNING_IDENTITY" in macos
    assert "scripts/build_macos_dmg.sh" in macos
    assert "describe_macos_image.py" in macos

    build = (ROOT / "scripts" / "build_macos_dmg.sh").read_text("utf-8")
    assert '--sign "$SIGNING_IDENTITY"' in build

    # A signing *identity name* is not a signing *identity*: codesign looks it
    # up in a keychain a fresh runner does not have, so release mode could
    # never succeed without importing the certificate first.
    assert "security create-keychain" in macos
    assert "security import" in macos
    assert "MACOS_SIGNING_CERTIFICATE" in macos
    # `secrets` is not available in a step-level `if`; the certificate's
    # presence is surfaced at job level and the import conditions on that env
    # value, or the step is silently unreachable in a real signed run.
    assert "HAS_SIGNING_CERT" in macos
    assert "if: ${{ env.HAS_SIGNING_CERT == 'true' }}" in macos

    attach = workflow[workflow.index("  attach:") : workflow.index("  pypi:")]
    assert "OPENAI4S_MACOS_SIGNING_IDENTITY" not in attach, (
        "the staging job cannot sign anything, so an identity there can only "
        "be used to infer a signature it never inspected"
    )


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
