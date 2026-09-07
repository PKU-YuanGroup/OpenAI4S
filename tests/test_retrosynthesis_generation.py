"""Generation provenance and real public-only verifier confinement contracts."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = (
    ROOT / "skills" / "retrosynthesis_planning" / "scenarios" / "openai4s_codebases"
)


@pytest.fixture
def generator():
    spec = importlib.util.spec_from_file_location(
        "retrosynthesis_generator", DIRECTORY / "generate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verification_environment_does_not_inherit_operator_values(
    generator, monkeypatch, tmp_path
):
    for name in (
        "OPENAI4S_LLM_API_KEY",
        "AWS_SESSION_TOKEN",
        "UNRECOGNIZED_SECRET",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "LD_PRELOAD",
    ):
        monkeypatch.setenv(name, "review-canary-not-a-credential")
    environment = generator._environment(tmp_path, tmp_path / "temporary")
    assert "review-canary-not-a-credential" not in environment.values()
    assert environment["HOME"] == str(tmp_path / "temporary")
    assert "PYTHONPATH" not in environment


def test_failed_child_diagnostics_never_serialize_output(generator, tmp_path, capfd):
    sentinel = "review-output-canary"
    with pytest.raises(generator.VerificationError) as failure:
        generator._checked(
            [sys.executable, "-c", f"print({sentinel!r}); raise SystemExit(1)"],
            root=tmp_path,
            environment=generator._environment(tmp_path, tmp_path),
        )
    assert sentinel not in str(failure.value)
    assert sentinel not in str(capfd.readouterr())
    assert generator._diagnostic(ValueError(sentinel)) == "ValueError"


@pytest.mark.stubbed_backend
def test_verification_fails_closed_without_a_backend(generator, monkeypatch, tmp_path):
    monkeypatch.setattr(generator.sys, "platform", "unsupported-review-platform")
    with pytest.raises(
        generator.VerificationError, match="requires an available OS sandbox"
    ):
        generator._sandbox_command(
            [sys.executable, "-c", "raise AssertionError('must not run')"],
            readonly=tmp_path,
            results=tmp_path,
            temporary=tmp_path,
        )


@pytest.mark.stubbed_backend
def test_failed_boundary_probe_never_runs_candidate(generator, monkeypatch, tmp_path):
    candidate = tmp_path / "candidate.py"
    marker = tmp_path / "ran"
    candidate.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")

    def unavailable(**kwargs):
        raise generator.VerificationError(
            "verification OS sandbox boundary probe failed"
        )

    monkeypatch.setattr(generator, "_probe_boundary", unavailable)
    name = generator.NAMES[0]
    with pytest.raises(generator.VerificationError, match="boundary probe failed"):
        generator._verify_case(
            ROOT, name, DIRECTORY.parent / "gt_codebases" / f"{name}.py", candidate
        )
    assert not marker.exists()


@pytest.mark.stubbed_backend
def test_linux_sandbox_uses_empty_root_and_private_namespaces(
    generator, monkeypatch, tmp_path
):
    readonly, results, temporary = (
        tmp_path / name for name in ("readonly", "results", "temporary")
    )
    for path in (readonly, results, temporary):
        path.mkdir()
    monkeypatch.setattr(generator.sys, "platform", "linux")
    monkeypatch.setattr(generator.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        generator,
        "_runtime_roots",
        lambda: (Path("/usr/lib"), Path("/lib"), Path("/lib64")),
    )
    monkeypatch.setattr(
        generator, "_runtime_denials", lambda: (Path("/usr/lib/python3/site-packages"),)
    )
    command = generator._sandbox_command(
        ["/usr/bin/python3", "-I", "-S"],
        readonly=readonly,
        results=results,
        temporary=temporary,
    )
    assert "--unshare-all" in command
    triples = list(zip(command, command[1:], command[2:]))
    assert ("--ro-bind", "/", "/") not in triples
    assert ("--ro-bind", "/lib", "/lib") in triples
    assert ("--ro-bind", "/lib64", "/lib64") in triples
    assert ("--ro-bind", str(readonly), str(readonly)) in triples
    assert ("--bind", str(results), str(results)) in triples
    assert ("--tmpfs", "/usr/lib/python3/site-packages", "--remount-ro") in triples


@pytest.mark.stubbed_backend
def test_runtime_mounts_preserve_merged_usr_aliases(generator, monkeypatch):
    monkeypatch.setattr(generator.Path, "is_dir", lambda path: True)
    roots = generator._runtime_roots()
    assert Path("/lib") in roots
    assert Path("/lib64") in roots


@pytest.mark.stubbed_backend
def test_installed_packages_are_masked_at_each_runtime_alias(
    generator, monkeypatch, tmp_path
):
    library = tmp_path / "usr" / "lib"
    installed = library / "python3" / "site-packages"
    installed.mkdir(parents=True)
    alias = tmp_path / "lib"
    alias.symlink_to(library, target_is_directory=True)
    monkeypatch.setattr(generator, "_runtime_roots", lambda: (library, alias))
    denials = generator._runtime_denials()
    assert installed in denials
    assert alias / "python3" / "site-packages" in denials


@pytest.mark.stubbed_backend
def test_seatbelt_denies_host_root_and_installed_packages(
    generator, monkeypatch, tmp_path
):
    readonly, results, temporary = (
        tmp_path / name for name in ("readonly", "results", "temporary")
    )
    for path in (readonly, results, temporary):
        path.mkdir()
    monkeypatch.setattr(generator.sys, "platform", "darwin")
    monkeypatch.setattr(generator.shutil, "which", lambda name: "/usr/bin/sandbox-exec")
    monkeypatch.setattr(generator, "_runtime_roots", lambda: ())
    monkeypatch.setattr(
        generator, "_runtime_denials", lambda: (Path("/runtime/site-packages"),)
    )
    command = generator._sandbox_command(
        [sys.executable, "-I", "-S"],
        readonly=readonly,
        results=results,
        temporary=temporary,
    )
    profile = command[2]
    assert '(deny file-read* (literal "/") (subpath "/"))' in profile
    assert '(deny file-read* (subpath "/runtime/site-packages"))' in profile
    assert "(deny network*)" in profile
    assert "deny process-fork process-info* mach-lookup appleevent-send" in profile
    assert '(deny sysctl-read (sysctl-name-prefix "kern.proc"))' in profile


def _copy_generation_tree(tmp_path):
    target = (
        tmp_path
        / "skills"
        / "retrosynthesis_planning"
        / "scenarios"
        / "openai4s_codebases"
    )
    shutil.copytree(DIRECTORY, target)
    return target


def test_refused_overwrite_preserves_manifest_bytes(tmp_path):
    target = _copy_generation_tree(tmp_path)
    manifest = target / "generation_manifest.json"
    before = manifest.read_bytes()
    completed = subprocess.run(
        [
            sys.executable,
            str(target / "generate.py"),
            "--scenario",
            "01_single_step_retrosynthesis",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 1
    assert manifest.read_bytes() == before
    assert json.loads(completed.stdout)["manifest_updated"] is False


@pytest.mark.stubbed_backend
def test_per_scenario_regeneration_preserves_other_entries(
    generator, monkeypatch, tmp_path
):
    target = _copy_generation_tree(tmp_path)
    manifest = target / "generation_manifest.json"
    original = json.loads(manifest.read_text())
    name = generator.NAMES[0]
    updated = {**original["entries"][0], "generated_sha256": "regenerated-source-hash"}
    monkeypatch.setattr(generator, "__file__", str(target / "generate.py"))
    monkeypatch.setattr(generator, "_entry", lambda *args, **kwargs: updated)
    monkeypatch.setattr(
        generator.sys, "argv", ["generate.py", "--scenario", name, "--overwrite"]
    )
    assert generator.main() == 0
    entries = json.loads(manifest.read_text())["entries"]
    assert entries[0] == updated
    assert entries[1:] == original["entries"][1:]


@pytest.mark.stubbed_backend
def test_a_failed_regeneration_preserves_the_verified_source_and_manifest(
    generator, monkeypatch, tmp_path
):
    root = tmp_path / "root"
    shutil.copytree(
        ROOT / "skills",
        root / "skills",
        ignore=shutil.ignore_patterns("bioskills", "__pycache__"),
    )
    target = root / "skills" / "retrosynthesis_planning" / "scenarios"
    target = target / "openai4s_codebases"
    name = generator.NAMES[0]
    source = target / f"{name}.py"
    manifest = target / "generation_manifest.json"
    source_before = source.read_bytes()
    manifest_before = manifest.read_bytes()
    monkeypatch.setattr(generator, "__file__", str(target / "generate.py"))
    monkeypatch.setattr(
        generator,
        "_command",
        lambda r, q: [sys.executable, "-c", "raise SystemExit(3)"],
    )
    monkeypatch.setattr(
        generator.sys, "argv", ["generate.py", "--scenario", name, "--overwrite"]
    )

    assert generator.main() == 1

    # A generation that produced nothing is an attempt, not a replacement.
    assert source.read_bytes() == source_before
    assert manifest.read_bytes() == manifest_before


def test_real_verification_blocks_private_reads_credentials_and_public_writes(
    generator, monkeypatch, tmp_path
):
    sentinel = "review-environment-canary"
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", sentinel)
    secret = tmp_path / "operator-private-data"
    secret.write_text("review-filesystem-canary")
    name = generator.NAMES[0]
    gt = DIRECTORY.parent / "gt_codebases" / f"{name}.py"
    source = (DIRECTORY / f"{name}.py").read_text()
    checks = (
        "\nimport os as _probe_os\nfrom pathlib import Path as _ProbePath\n"
        f"assert {sentinel!r} not in _probe_os.environ.values()\n"
        f"for _private_path in ({str(secret)!r}, {str(gt)!r}):\n"
        "    try: _ProbePath(_private_path).read_bytes()\n"
        "    except OSError: pass\n"
        "    else: raise RuntimeError('private data readable')\n"
    )
    candidate = tmp_path / "candidate.py"
    candidate.write_text(
        source.replace(
            "from __future__ import annotations",
            "from __future__ import annotations\n" + checks,
            1,
        )
    )
    try:
        artifact_hash = generator._verify_case(ROOT, name, gt, candidate)
    except generator.VerificationError as error:
        # Only an absent or unusable sandbox may be skipped. "boundary was not
        # honoured" means the probe caught a real escape and must fail here.
        unavailable = "requires an available OS sandbox" in str(
            error
        ) or "boundary probe could not run" in str(error)
        if unavailable and os.environ.get("OPENAI4S_REQUIRE_GENERATION_SANDBOX") != "1":
            pytest.skip(
                "OS sandbox unavailable inside this test runner; verifier failed closed"
            )
        raise
    assert len(artifact_hash) == 64


@pytest.mark.stubbed_backend
def test_a_detected_boundary_breach_is_never_reported_as_a_missing_sandbox(
    generator, monkeypatch, tmp_path
):
    def breached(command, *, root, environment):
        failure = generator.VerificationError("verification command failed (exit 22)")
        failure.exit_code = 22
        raise failure

    monkeypatch.setattr(generator, "_checked", breached)
    monkeypatch.setattr(generator.sys, "platform", "darwin")
    monkeypatch.setattr(generator.shutil, "which", lambda name: "/usr/bin/sandbox-exec")
    monkeypatch.setattr(generator, "_runtime_roots", lambda: ())
    monkeypatch.setattr(generator, "_runtime_denials", lambda: ())
    readonly, results, temporary = (
        tmp_path / name for name in ("readonly", "results", "temporary")
    )
    for path in (readonly, results, temporary):
        path.mkdir()
    with pytest.raises(generator.VerificationError, match="was not honoured"):
        generator._probe_boundary(
            readonly=readonly, results=results, temporary=temporary, denied=()
        )
