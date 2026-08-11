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


def test_release_quality_installs_every_collection_dependency():
    """The release-SHA gates must collect the same suite as the 3.12 matrix.

    ``tests/test_admet_genetic.py`` imports pandas at module scope.  Installing
    only the dev group therefore makes the quality job fail during collection,
    before it can produce the receipt that staging requires.
    """
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    quality = workflow[
        workflow.index("  quality:") : workflow.index("  platform-checks:")
    ]
    install = "uv sync --locked --extra science --extra chemistry"

    assert install in quality
    assert quality.index(install) < quality.index("scripts/run_quality_gates.py")


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
        # The ancestor check moved into the `freeze` job and now names the peeled
        # SHA rather than `HEAD`. It lived in `build`, whose `HEAD` was that job's
        # own independent checkout of a mutable tag -- so it asserted something
        # about whatever the tag pointed at when that one job ran.
        'git merge-base --is-ancestor "$SHA" origin/main',
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


def test_publishing_refuses_a_prerelease_draft():
    """A stable tag must not publish a GitHub Release still marked prerelease.

    The version/tag gate accepts only ``vMAJOR.MINOR.PATCH``, so leaving the
    draft's prerelease flag unchecked could publish stable files to PyPI while
    presenting the matching GitHub Release as a prerelease.
    """
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")
    guard = workflow[workflow.index("  guard:") : workflow.index("  quality:")]

    assert "--json isDraft,isPrerelease" in guard
    assert "jq -r .isPrerelease" in guard
    assert "stable publication requires a non-prerelease draft" in guard


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


# ---------------------------------------------------------------------------
# Linux and Windows desktop packaging
# ---------------------------------------------------------------------------


def test_every_desktop_bundle_pre_bakes_the_same_science_stack():
    """One manifest, or the two platforms quietly ship different stacks.

    The macOS image used to own this list. The moment a second platform grew a
    bundle, "what we install" and "what we check" became four things instead of
    two, and the one that stopped matching would be the one nobody ran.
    """
    contract = _load_script("bundle_contract")
    packages = contract.manifest_packages()
    assert len(packages) >= 30
    assert ("rdkit", "rdkit") in packages
    assert ("scikit-learn", "sklearn") in packages

    for builder in ("build_macos_dmg.sh", "build_linux_bundle.sh"):
        text = (ROOT / "scripts" / builder).read_text("utf-8")
        assert "scripts/bundled_packages.txt" in text, f"{builder} bundles its own list"

    for verifier in ("verify_macos_bundle", "verify_linux_bundle"):
        source = (ROOT / "scripts" / f"{verifier}.py").read_text("utf-8")
        assert "from bundle_contract import bundled_imports" in source, (
            f"{verifier} does not read the shared manifest, so the set it "
            "enforces can drift from the set that was installed"
        )


def test_the_linux_bundle_ships_the_resources_only_a_runtime_check_would_miss():
    build = (ROOT / "scripts" / "build_linux_bundle.sh").read_text("utf-8")
    # Same omissions that have bitten the DMG: the benchmark manifests, the
    # Skill catalog, and the environment specs are all resolved by path at
    # runtime, so leaving one out fails long after the build looked green.
    for tree in ("/workflows", "/skills", "/envs", "/openai4s_worker_runtime"):
        assert tree in build, f"the Linux bundle does not copy {tree}"
    # Hash-based bytecode, or the app rewrites its own tree on first import and
    # recompiles the whole stack on every launch from a read-only unpack.
    assert "--invalidation-mode unchecked-hash" in build
    # A cross-build produces a real image but an unexecuted one. It has to say
    # so: a skipped smoke that reads like a passed one is how an untested image
    # gets released.
    assert "cross-build" in build.lower()


def test_the_windows_package_has_no_native_windows_execution_path():
    """Both halves, because either alone is satisfiable by a broken package."""
    launcher = (ROOT / "scripts" / "windows" / "openai4s.ps1").read_text("utf-8")
    assert "wsl.exe" in launcher
    assert "platform_support.py" in launcher, (
        "the launcher must say why it goes through WSL2, at the place someone "
        "would otherwise 'simplify' it into starting Python directly"
    )
    # WSL 1 has no user namespaces, so bubblewrap cannot start and cells would
    # run unisolated — the silent degradation the platform tiers exist to rule
    # out. Refusing it is not optional.
    assert "wsl --set-version" in launcher
    assert "wsl --install" in launcher

    verifier = (ROOT / "scripts" / "verify_windows_zip.py").read_text("utf-8")
    for suffix in ('".exe"', '".dll"', '".pyd"'):
        assert suffix in verifier


def test_the_windows_launcher_opens_the_authenticated_url_and_requires_sandbox():
    launcher = (ROOT / "scripts" / "windows" / "openai4s.ps1").read_text("utf-8")
    bootstrap = (ROOT / "scripts" / "windows" / "bootstrap.sh").read_text("utf-8")

    assert "Get-AppUrl" in launcher
    assert "Start-Process $appUrl" in launcher
    assert "Start-Process $Url" not in launcher
    assert "OPENAI4S_WSL_PYPI_INDEX" in launcher
    assert "Test-LocalhostForwardingDisabled" in launcher
    assert "Get-WslIpv4" in launcher
    # A distro that already holds ~/.openai4s data must keep winning selection,
    # and the default mirrors must be disablable without deleting the env var.
    assert "Test-DistroHasInstall" in launcher
    assert "if ($PypiIndex -eq 'off') { $PypiIndex = '' }" in launcher
    assert "if ($CondaMirror -eq 'off') { $CondaMirror = '' }" in launcher
    assert "'dev', 'eth0', 'scope', 'global'" in launcher
    assert "'route', 'get', '192.0.2.1'" in launcher
    assert '$proxyBypass = "127.0.0.1,localhost,$AppHost"' in launcher
    assert '"NO_PROXY=$proxyBypass"' in launcher
    assert '"no_proxy=$proxyBypass"' in launcher
    assert "if (-not (Test-SandboxIndependentCli $Arguments))" in launcher
    for command in ("status", "url", "stop", "doctor", "verify-package"):
        assert f"'{command}'" in launcher
    assert 'MIN_BWRAP_VERSION="0.8.0"' in bootstrap
    for flag in (
        "--die-with-parent",
        "--new-session",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-net",
    ):
        assert flag in bootstrap
    assert "--unshare-user" not in bootstrap
    assert "--uid 0" not in bootstrap
    assert "--gid 0" not in bootstrap
    assert "OPENAI4S_KERNEL_SANDBOX" in bootstrap
    assert "--no-browser" in bootstrap
    assert "--detached" in bootstrap
    # User-edited mirror files survive relaunch: only marker-carrying files are
    # rewritten, and a foreign pip.conf that names an index-url is preserved.
    assert 'MANAGED_MARK="managed-by-openai4s-windows-launcher"' in bootstrap
    assert "grep -q '^index-url'" in bootstrap


def test_the_windows_launcher_does_not_leak_native_stdout_into_its_return_value():
    """A native command's stdout IS the function's return value in PowerShell.

    `& wsl.exe ...` writes to the success stream, so a bare call followed by
    `return $LASTEXITCODE` returns @('installed /home/.../OpenAI4S-...', 0),
    not 0. bootstrap.sh prints on stdout in every *success* path
    ("already-installed", "installed", "serving http://...") and sends
    failures to stderr, so the defect fires precisely when the install
    worked: `$code -ne 0` filters the array to its one non-zero element,
    `if` reads a non-empty array as true, and the launcher reports "the
    Linux bundle could not be installed" after installing it. `exit $code`
    then cannot convert Object[] to Int32.

    Pinned statically because nothing executes this: no CI runner has WSL,
    so `Invoke-Bootstrap` is unreachable even on the windows-latest job,
    which parses the file and stops.
    """
    launcher = (ROOT / "scripts" / "windows" / "openai4s.ps1").read_text("utf-8")
    calls = [
        line.strip()
        for line in launcher.splitlines()
        if line.lstrip().startswith("& wsl.exe")
    ]

    assert calls, "the launcher must still go through wsl.exe"
    for call in calls:
        consumes_output = call.endswith("| Out-Host") or (
            "| ForEach-Object" in call and "Write-Host" in call
        )
        assert consumes_output, (
            "a wsl.exe invocation whose value is not captured must pipe to "
            f"a host-only sink, or its stdout becomes part of the return value: {call}"
        )


def test_the_windows_launcher_tolerates_successful_wsl_stderr_diagnostics():
    """PowerShell 5.1 turns native stderr into errors under the global Stop.

    Current WSL emits a localized NAT/proxy warning on stderr before successful
    ``--exec`` commands. The launcher must judge native calls by LASTEXITCODE,
    or an unrelated Windows proxy setting makes first launch fail before the
    package path is translated.
    """

    launcher = (ROOT / "scripts" / "windows" / "openai4s.ps1").read_text("utf-8")

    assert "function Invoke-WslCaptureNative" in launcher
    assert "$ErrorActionPreference = 'Continue'" in launcher
    assert "$code = $LASTEXITCODE" in launcher
    assert "$previousPreference" in launcher
    assert "$paths = @(" in launcher
    assert "$selectedPath = $paths[0]" in launcher
    assert "return [string]$selectedPath" in launcher


def test_the_windows_launcher_sources_stay_pure_ascii():
    """Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI, not UTF-8.

    A UTF-8 em dash decodes under cp1252 to three characters ending in 0x94 =
    U+201D, which PowerShell accepts as a *closing double quote* -- so a dash
    inside a string literal ends the string and the parse collapses far below
    it. Found on a real windows-latest runner: pwsh 7 parsed the file happily
    while `powershell.exe`, the one `OpenAI4S.cmd` actually invokes, failed 200
    lines away from the cause. Asserted here as well as in the packaged
    verifier so it is caught before anything is built.
    """
    for name in ("openai4s.ps1", "OpenAI4S.cmd", "bootstrap.sh"):
        body = (ROOT / "scripts" / "windows" / name).read_bytes()
        try:
            body.decode("ascii")
        except UnicodeDecodeError as error:
            line = body[: error.start].count(b"\n") + 1
            pytest.fail(
                f"scripts/windows/{name} line {line} is not ASCII: "
                f"{body[error.start:error.start + 1]!r}"
            )


def test_the_wsl_bootstrap_never_acquires_carriage_returns():
    """A CRLF shell script fails inside WSL, on the user's machine, not here."""
    assert b"\r" not in (ROOT / "scripts" / "windows" / "bootstrap.sh").read_bytes()
    build = (ROOT / "scripts" / "build_windows_zip.sh").read_text("utf-8")
    assert 'to_lf "$SOURCES/bootstrap.sh"' in build
    for windows_side in ("OpenAI4S.cmd", "openai4s.ps1"):
        assert windows_side in build


def _write_fake_linux_payload(path: Path, version: str, arch: str) -> str:
    """A tarball with the shape the Windows launcher depends on, and nothing else."""
    top = f"OpenAI4S-{version}-linux-{arch}"
    executable = (
        "OpenAI4S",
        "bin/openai4s",
        "install.sh",
        "uninstall.sh",
        "runtime/bin/python3",
    )
    plain = (
        "VERSION",
        "LICENSE",
        "runtime/pip.conf",
        "share/applications/openai4s.desktop.in",
        "src/openai4s/__init__.py",
    )
    with tarfile.open(path, "w:gz") as archive:
        for relative in executable + plain:
            payload = b"placeholder\n"
            info = tarfile.TarInfo(f"{top}/{relative}")
            info.size = len(payload)
            info.mode = 0o755 if relative in executable else 0o644
            archive.addfile(info, io.BytesIO(payload))
    return top


def _stage_windows_package(root: Path, version: str = "9.9.9") -> Path:
    """Stage a package from the real launcher sources.

    Using the committed launcher rather than a stub is the point: this doubles
    as proof that what we ship still satisfies what we check.
    """
    package = root / f"OpenAI4S-{version}-windows-x86_64"
    (package / "wsl").mkdir(parents=True)
    (package / "payload").mkdir()

    def crlf(text: str) -> bytes:
        return "\r\n".join(text.splitlines()).encode("utf-8") + b"\r\n"

    sources = ROOT / "scripts" / "windows"
    for name in ("OpenAI4S.cmd", "openai4s.ps1"):
        (package / name).write_bytes(crlf((sources / name).read_text("utf-8")))
    (package / "wsl" / "bootstrap.sh").write_bytes(
        (sources / "bootstrap.sh").read_text("utf-8").encode("utf-8")
    )
    (package / "VERSION").write_bytes(crlf(version))
    (package / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (package / "READ ME FIRST.txt").write_bytes(crlf("Double-click OpenAI4S.cmd."))

    tarball = package / "payload" / f"OpenAI4S-{version}-linux-x86_64.tar.gz"
    _write_fake_linux_payload(tarball, version, "x86_64")
    import hashlib

    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    (package / "payload" / f"{tarball.name}.sha256").write_text(
        f"{digest}  {tarball.name}\n", encoding="utf-8"
    )
    return package


def test_the_windows_verifier_accepts_a_correctly_staged_package(tmp_path):
    verifier = _load_script("verify_windows_zip")
    verifier.verify(_stage_windows_package(tmp_path))


def test_the_windows_verifier_refuses_a_bare_unauthenticated_browser_url(tmp_path):
    verifier = _load_script("verify_windows_zip")
    package = _stage_windows_package(tmp_path)
    launcher = package / "openai4s.ps1"
    body = launcher.read_bytes()
    assert b"Start-Process $appUrl" in body
    launcher.write_bytes(body.replace(b"Start-Process $appUrl", b"Start-Process $Url"))

    with pytest.raises(verifier.BundleCheckError, match="unauthenticated bare URL"):
        verifier.verify(package)


def test_the_windows_verifier_refuses_a_weakened_bubblewrap_baseline(tmp_path):
    verifier = _load_script("verify_windows_zip")
    package = _stage_windows_package(tmp_path)
    bootstrap = package / "wsl" / "bootstrap.sh"
    body = bootstrap.read_bytes()
    assert b'MIN_BWRAP_VERSION="0.8.0"' in body
    bootstrap.write_bytes(
        body.replace(b'MIN_BWRAP_VERSION="0.8.0"', b'MIN_BWRAP_VERSION="0.7.0"')
    )

    with pytest.raises(verifier.BundleCheckError, match="sandbox contract"):
        verifier.verify(package)


def test_the_windows_verifier_refuses_preflight_only_namespace_flags(tmp_path):
    verifier = _load_script("verify_windows_zip")
    package = _stage_windows_package(tmp_path)
    bootstrap = package / "wsl" / "bootstrap.sh"
    body = bootstrap.read_bytes()
    marker = b"--unshare-ipc --unshare-uts --unshare-net"
    assert marker in body
    bootstrap.write_bytes(body.replace(marker, b"--unshare-user " + marker))

    with pytest.raises(verifier.BundleCheckError, match="absent from the runtime"):
        verifier.verify(package)


def test_the_windows_verifier_refuses_a_crlf_wsl_bootstrap(tmp_path):
    verifier = _load_script("verify_windows_zip")
    package = _stage_windows_package(tmp_path)
    bootstrap = package / "wsl" / "bootstrap.sh"
    bootstrap.write_bytes(bootstrap.read_bytes().replace(b"\n", b"\r\n"))

    with pytest.raises(verifier.BundleCheckError, match="carriage return"):
        verifier.verify(package)


def test_the_windows_verifier_refuses_a_payload_its_sidecar_does_not_match(tmp_path):
    verifier = _load_script("verify_windows_zip")
    package = _stage_windows_package(tmp_path)
    sidecar = next((package / "payload").glob("*.sha256"))
    sidecar.write_text("0" * 64 + "  payload.tar.gz\n", encoding="utf-8")

    with pytest.raises(verifier.BundleCheckError, match="checksum sidecar"):
        verifier.verify(package)


def test_the_windows_verifier_refuses_a_shipped_windows_binary(tmp_path):
    """The package has no supported way to run one, so its presence means the
    launcher grew a second, native start that platform_support.py refuses."""
    verifier = _load_script("verify_windows_zip")
    package = _stage_windows_package(tmp_path)
    (package / "python.exe").write_bytes(b"MZ")

    with pytest.raises(verifier.BundleCheckError, match="native Windows binaries"):
        verifier.verify(package)


def test_the_linux_verifier_refuses_a_launcher_that_lost_its_executable_bit(tmp_path):
    """An archive can carry every file and still unpack into a bundle nobody
    can start, which no content check would notice."""
    verifier = _load_script("verify_linux_bundle")
    tarball = tmp_path / "bundle.tar.gz"
    top = _write_fake_linux_payload(tarball, "9.9.9", "x86_64")
    with tarfile.open(tarball) as archive:
        members = {member.name: member for member in archive.getmembers()}

    assert verifier.check_tar_members(dict(members)) == top

    members[f"{top}/OpenAI4S"].mode = 0o644
    with pytest.raises(verifier.BundleCheckError, match="not executable"):
        verifier.check_tar_members(members)


def test_the_desktop_packages_are_built_and_verified_before_anything_publishes():
    import re

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text("utf-8")

    linux = workflow[
        workflow.index("  linux-app:") : workflow.index("  windows-package:")
    ]
    assert "scripts/build_linux_bundle.sh" in linux
    assert "verify_linux_bundle.py" in linux
    assert "runs-on: ubuntu-latest" in linux, (
        "only a Linux runner can execute the bundle, and the import probe is "
        "the check that proves the science stack imports rather than merely "
        "being present on disk"
    )

    windows = workflow[
        workflow.index("  windows-package:") : workflow.index("  windows-launcher:")
    ]
    assert "scripts/build_windows_zip.sh" in windows
    assert "verify_windows_zip.py" in windows
    assert "name: linux-app-bundle" in windows
    assert "build_linux_bundle.sh" not in windows, (
        "the Windows package must wrap the artifact this release publishes, "
        "not a second build that merely ought to match it"
    )

    launcher = workflow[
        workflow.index("  windows-launcher:") : workflow.index("  attach:")
    ]
    assert (
        "runs-on: windows-latest" in launcher
    ), "a syntax error in the .ps1 is invisible to every other job here"
    assert "Parser]::ParseFile" in launcher

    for job, following in (("attach", "pypi"), ("pypi", "finalize")):
        section = workflow[
            workflow.index(f"  {job}:") : workflow.index(f"  {following}:")
        ]
        needs = re.search(r"^    needs: (.+)$", section, re.MULTILINE)
        assert needs, f"{job} must declare what it waits for"
        for required in ("linux-app", "windows-package", "windows-launcher"):
            assert required in needs.group(1), (
                f"{job} must not run before {required!r}: a package that failed "
                "to build or failed verification would only skip its own job"
            )
