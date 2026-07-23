#!/usr/bin/env python3
"""Draft-first release: build, prove, stage, verify, and only then publish.

    uv run python scripts/release_pipeline.py --dry-run --version 0.2.0
    uv run python scripts/release_pipeline.py --mode local  --version 0.2.0
    uv run python scripts/release_pipeline.py --mode release --version 0.2.0 \
        --from-artifacts --assets-dir assets --stop-after reverify
    uv run python scripts/release_pipeline.py --mode release --version 0.2.0 \
        --only publish

The pipeline is here rather than in the workflow YAML on purpose. A release
step embedded in an event trigger can only ever be exercised by cutting a real
release, which means it is tested by the thing it is supposed to protect. As a
script it runs on a laptop, in `--dry-run`, and under pytest.

## The state machine, and why it is that order

    existing draft
      → build exact artifacts → test → smoke the exact wheel
      → sbom → provenance → checksums over everything
      → verify → stage unchanged bytes → upload → remote digest verification
      → PyPI publish
      → GitHub publish

Everything irreversible is last, and the *last* thing is the GitHub flip. That
ordering is not cosmetic. The flip used to happen inside the staging job while
the PyPI upload ran in a separate job afterwards, so an OIDC failure, a denied
environment approval or a rejected upload left a public GitHub release with no
matching package — recreating the half-published state this pipeline exists to
prevent. `publish` now runs on its own, after PyPI, and refuses to run until it
has evidence the version is actually on the index.

**If PyPI succeeds and the GitHub finalize fails**, the release stays a draft
and nothing needs rebuilding. Re-run:

    scripts/release_pipeline.py --version <v> --mode release --only publish

Do not bump the version and do not rebuild: the artifacts on the draft are the
ones PyPI already has, and rebuilding would publish different bytes under the
same version.

## Modes

* `--dry-run` performs no external call and prints what it would do. It is not
  a weaker `local`: it is how the *ordering* is tested.
* `--mode local` really builds, really hashes, really writes the SBOM and
  provenance, and stops before anything is published.
* `--mode release` additionally requires a real Developer ID signature on any
  disk image. Missing it is a hard failure, because a release that silently
  ships unsigned is exactly the outcome signing exists to prevent.
* `--from-artifacts` is the staging-only mode. `build` and `test` do not run —
  their inputs are artifacts an earlier job already produced and verified — and
  the distributions are fingerprinted on entry and re-checked before upload, so
  this job cannot replace the bytes GitHub and PyPI are both meant to receive.

Notarization is never reported as verified here. It needs Apple's service and a
paid identity; a pipeline that printed "notarized: ok" without one would be the
kind of confident wrong answer this codebase spends its time removing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]

#: The ordered pipeline. Named here so the order itself is testable.
STEPS = (
    "build",
    "test",
    "assets",
    "smoke",
    "sbom",
    "provenance",
    "checksums",
    "verify",
    "draft",
    "upload",
    "reverify",
    "publish",
)

#: Steps that change something outside this machine. `publish` is the only
#: irreversible one, and it is last for that reason.
EXTERNAL = frozenset({"draft", "upload", "publish"})

#: Steps a staging-only run must not perform. Their outputs are its inputs.
STAGING_SKIPPED = ("build", "test")

SIGNING_IDENTITY_VAR = "OPENAI4S_MACOS_SIGNING_IDENTITY"

#: What a real Apple distribution signature says. An ad-hoc signature ("-")
#: verifies happily and says nothing about who produced the image.
DEVELOPER_ID_AUTHORITY = "Developer ID Application"

#: Written beside the DMG by the macOS job, which is the only place a
#: `codesign` inspection can happen. The ubuntu job that stages the release
#: cannot inspect a signature, and inferring one from an environment variable
#: is what let an ad-hoc image pass the gate as Developer-ID-signed.
SIGNATURE_RECEIPT_SUFFIX = ".codesign.json"

#: Written beside the DMG by the macOS job: the package inventory of the
#: runtime actually embedded in the image. Freezing the runner's interpreter
#: instead described neither the wheel nor the image.
COMPONENTS_SIDECAR_SUFFIX = ".components.json"

DISTRIBUTION_SUFFIXES = (".whl", ".gz", ".dmg", ".zip")


class ReleaseError(RuntimeError):
    """The pipeline stopped. Nothing after the failing step ran."""


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""
    facts: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "step": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "facts": self.facts,
        }


def _run(argv: Sequence[str], cwd: Path | None = None):
    return subprocess.run(
        [str(part) for part in argv],
        cwd=str(cwd or ROOT),
        capture_output=True,
        timeout=1800,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# what is actually in the release
# --------------------------------------------------------------------------


def wheel_components(wheel: Path) -> list[dict[str, str]]:
    """The shipped package and the dependencies its own metadata declares.

    Read out of the wheel, because the wheel is what is published. Freezing the
    interpreter that happened to run the build described the *runner* — on a
    staging job that is an Ubuntu image with none of this installed, so the
    document listed unrelated packages and omitted every shipped component.
    """
    components: list[dict[str, str]] = []
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
            if not names:
                return []
            text = archive.read(sorted(names)[0]).decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile):
        return []
    name = version = ""
    for line in text.splitlines():
        if line.startswith("Name: ") and not name:
            name = line[6:].strip()
        elif line.startswith("Version: ") and not version:
            version = line[9:].strip()
        elif line.startswith("Requires-Dist: "):
            requirement = line[15:].split(";", 1)[0].strip()
            dependency = requirement.split("[", 1)[0]
            for separator in ("==", ">=", "<=", "~=", "!=", ">", "<", " "):
                dependency = dependency.split(separator, 1)[0]
            dependency = dependency.strip()
            if dependency:
                components.append(
                    {"name": dependency, "version": "", "scope": "declared-dependency"}
                )
    if name:
        components.insert(
            0, {"name": name, "version": version or "unknown", "scope": "shipped"}
        )
    return components


def sidecar_components(
    assets: Sequence[Path],
) -> tuple[list[dict[str, str]], list[str]]:
    """Components the macOS job read out of the image it built.

    Returns ``(components, missing)``. A DMG whose sidecar is absent is
    reported as unread rather than described by whatever happens to be
    installed on the machine assembling the release.
    """
    components: list[dict[str, str]] = []
    missing: list[str] = []
    for asset in assets:
        if asset.suffix != ".dmg":
            continue
        sidecar = asset.with_name(asset.name + COMPONENTS_SIDECAR_SUFFIX)
        if not sidecar.is_file():
            missing.append(asset.name)
            continue
        try:
            payload = json.loads(sidecar.read_text("utf-8"))
        except (OSError, ValueError):
            missing.append(asset.name)
            continue
        for item in payload.get("packages") or []:
            components.append(
                {
                    "name": str(item.get("name") or ""),
                    "version": str(item.get("version") or "unknown"),
                    "scope": f"embedded-in:{asset.name}",
                }
            )
    return [c for c in components if c["name"]], missing


def canonical_source_uri(runner: Callable[..., Any] = _run) -> str:
    """Where a consumer following the attestation actually finds this source.

    Every statement used to name ``github.com/openai4s/openai4s`` while the
    package metadata, the documentation and the configured origin all named
    ``PKU-YuanGroup/OpenAI4S`` — so the attestation pointed at the wrong
    repository, which is worse than pointing nowhere.
    """
    server = (os.environ.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    repository = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if repository:
        return f"git+{server}/{repository}"
    completed = runner(["git", "config", "--get", "remote.origin.url"])
    if getattr(completed, "returncode", 1) == 0:
        origin = (getattr(completed, "stdout", b"") or b"").decode().strip()
        if origin:
            if origin.startswith("git@"):
                host, _, path = origin.partition(":")
                origin = f"https://{host[4:]}/{path}"
            if origin.endswith(".git"):
                origin = origin[:-4]
            return f"git+{origin}"
    for line in (ROOT / "pyproject.toml").read_text("utf-8").splitlines():
        if "github.com" in line and "=" in line:
            candidate = line.split("=", 1)[1].strip().strip('"').strip("'")
            if candidate.startswith("http"):
                return f"git+{candidate.rstrip('/')}"
    raise ReleaseError(
        "the canonical source repository could not be determined; refusing to "
        "sign a provenance statement pointing at a guess"
    )


def build_sbom(
    assets: list[Path],
    *,
    version: str,
    packages: list[dict],
    unread: Sequence[str] = (),
) -> dict:
    """A CycloneDX document naming what is in the release and what it is made of.

    Written by hand rather than by a third-party generator because the core is
    stdlib-only and a supply-chain document produced by an unpinned tool is a
    supply-chain question of its own. The shape is CycloneDX 1.5's, so ordinary
    scanners read it.
    """
    document: dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "openai4s",
                "version": version,
            },
            "tools": [{"name": "openai4s release_pipeline", "version": version}],
        },
        "components": [
            {
                "type": "library",
                "name": item["name"],
                "version": item.get("version") or "unknown",
                **(
                    {"properties": [{"name": "openai4s:scope", "value": item["scope"]}]}
                    if item.get("scope")
                    else {}
                ),
            }
            for item in sorted(packages, key=lambda p: str(p.get("name", "")).lower())
        ],
        "externalReferences": [
            {
                "type": "distribution",
                "url": asset.name,
                "hashes": [{"alg": "SHA-256", "content": sha256_file(asset)}],
            }
            for asset in sorted(assets)
        ],
    }
    if unread:
        # Named, not omitted. An SBOM that silently leaves out a shipped
        # component reads as "there is nothing there".
        document["metadata"]["properties"] = [
            {
                "name": "openai4s:components-unread",
                "value": (
                    f"no component inventory was produced for: "
                    f"{', '.join(sorted(unread))}"
                ),
            }
        ]
    return document


def build_provenance(assets: list[Path], *, version: str, source: dict) -> dict:
    """An in-toto SLSA provenance statement over the release's own assets.

    The subjects are the artifacts and their digests, so a consumer can check
    that the file they downloaded is the file this statement is about. What it
    does *not* claim is who built it: that needs a signature, and this document
    carries none.
    """
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": "https://slsa.dev/provenance/v1",
        "subject": [
            {"name": asset.name, "digest": {"sha256": sha256_file(asset)}}
            for asset in sorted(assets)
        ],
        "predicate": {
            "buildDefinition": {
                "buildType": "https://openai4s.org/release/v1",
                "externalParameters": {"version": version},
                "resolvedDependencies": [source],
            },
            "runDetails": {
                "builder": {
                    "id": f"openai4s-release-pipeline@{platform.node() or 'local'}"
                },
                "metadata": {"invocationId": f"{version}-{int(time.time())}"},
            },
            "unsigned": True,
            "note": (
                "This statement is not signed. It binds the listed digests to "
                "this build's parameters; it does not establish who produced "
                "them."
            ),
        },
    }


def read_signature(dmg: Path, runner: Callable[..., Any] = _run) -> dict[str, Any]:
    """What actually signed this image, from evidence rather than intent.

    A receipt written by the macOS job wins, because the job that stages the
    release runs on Linux and has no `codesign`. Where `codesign` *is*
    available the image is inspected directly. Neither path consults
    ``OPENAI4S_MACOS_SIGNING_IDENTITY``: reading a non-empty environment
    variable as "this is signed" is what let an ad-hoc image pass the release
    gate as Developer-ID-signed, since the build script only ever ad-hoc signs.
    """
    receipt = dmg.with_name(dmg.name + SIGNATURE_RECEIPT_SUFFIX)
    if receipt.is_file():
        try:
            payload = json.loads(receipt.read_text("utf-8"))
        except (OSError, ValueError) as e:
            return {"source": "receipt", "error": f"unreadable receipt: {e}"}
        authorities = [str(a) for a in (payload.get("authorities") or [])]
        return {
            "source": "receipt",
            "authorities": authorities,
            "developer_id": any(
                DEVELOPER_ID_AUTHORITY in authority for authority in authorities
            ),
            "adhoc": bool(payload.get("adhoc")),
        }
    if not shutil.which("codesign"):
        return {
            "source": "unavailable",
            "error": (
                "no codesign on this host and no signature receipt beside the "
                "image; the signature cannot be established here"
            ),
        }
    completed = runner(["codesign", "--display", "--verbose=4", str(dmg)])
    text = (getattr(completed, "stderr", b"") or b"").decode("utf-8", "replace")
    authorities = [
        line.split("=", 1)[1].strip()
        for line in text.splitlines()
        if line.startswith("Authority=")
    ]
    return {
        "source": "codesign",
        "authorities": authorities,
        "developer_id": any(
            DEVELOPER_ID_AUTHORITY in authority for authority in authorities
        ),
        "adhoc": "Signature=adhoc" in text,
        "returncode": getattr(completed, "returncode", None),
    }


class Pipeline:
    """The ordered release. Nothing irreversible until everything else holds."""

    def __init__(
        self,
        version: str,
        *,
        mode: str = "local",
        dry_run: bool = False,
        assets_dir: Path | None = None,
        runner: Callable[..., Any] | None = None,
        gh: Callable[[Sequence[str]], Any] | None = None,
        from_artifacts: bool = False,
        stop_after: str | None = None,
        only: str | None = None,
        pypi_check: Callable[[str, str], bool] | None = None,
        smoke: Callable[[Path], str] | None = None,
    ) -> None:
        self.version = version
        self.mode = mode
        self.dry_run = dry_run
        # Absolute at construction: `_run` executes subprocesses from ROOT
        # (the checkout), while the staging job passes `--assets-dir assets`
        # as a *sibling* of the checkout. A relative path would make pip in
        # `step_smoke`, and the gh upload/download, look for the wheel under
        # ROOT/assets, where it does not exist.
        self.assets_dir = Path(assets_dir or ROOT / "dist").resolve()
        self._run = runner or _run
        self._gh = gh or (lambda argv: _run(["gh", *argv]))
        self._pypi_check = pypi_check or _pypi_has_version
        #: Injected only so the ordering tests do not have to build a venv per
        #: case. The real implementation is what every non-test run uses, and
        #: it is exercised by `--mode local`.
        self._smoke = smoke or self._install_and_exercise
        self.from_artifacts = from_artifacts
        self.stop_after = stop_after
        self.only = only
        self.results: list[StepResult] = []
        self.assets: list[Path] = []
        self.performed: list[str] = []
        #: Digests of the distributions as they arrived, so a staging run can
        #: prove it published the bytes it was given.
        self.incoming: dict[str, str] = {}

    # --- steps ------------------------------------------------------------
    def step_build(self) -> StepResult:
        if self.from_artifacts:
            # Not "skipped because it is slow". This job's inputs *are* the
            # outputs of an earlier, verified build, and rebuilding here would
            # write different bytes into the same directory — so GitHub and
            # PyPI could receive two different distributions for one version.
            return StepResult(
                "build",
                True,
                "not run: staging consumes the verified artifacts unchanged",
                {"from_artifacts": True},
            )
        if self.dry_run:
            return StepResult("build", True, "would build sdist and wheel")
        # Clear the output directory first. It is reused across runs, and
        # `step_assets` collects *every* wheel/sdist/dmg/zip it finds — so a
        # previous build's artifacts would be smoke-tested, hashed into the
        # SBOM and checksums, and uploaded alongside (or instead of) this
        # version's. `--clear` here mirrors what the CI `uv build --clear`
        # does, so the two build paths agree.
        if self.assets_dir.exists():
            for stale in self.assets_dir.glob("*"):
                if stale.is_file() and stale.suffix in (
                    *DISTRIBUTION_SUFFIXES,
                    ".json",
                ):
                    stale.unlink()
                elif stale.name == "SHA256SUMS":
                    stale.unlink()
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        # `uv build`, not `python -m build`: the documented invocation is
        # `uv run python scripts/release_pipeline.py`, which runs in the locked
        # project environment — and `build` is not a locked dependency, so the
        # module import failed before any release check ran. `uv build` is the
        # frontend that is always available there.
        completed = self._build_frontend()
        if completed.returncode != 0:
            raise ReleaseError(
                f"build failed ({completed.returncode}): "
                f"{(completed.stderr or b'').decode('utf-8', 'replace')[-2000:]}"
            )
        return StepResult("build", True, f"built into {self.assets_dir}")

    def _build_frontend(self):
        """Build with uv when available, falling back to `python -m build`."""
        if shutil.which("uv"):
            return self._run(
                ["uv", "build", "--no-sources", "--out-dir", str(self.assets_dir)]
            )
        return self._run(
            [sys.executable, "-m", "build", "--outdir", str(self.assets_dir)]
        )

    def step_test(self) -> StepResult:
        if self.from_artifacts:
            return StepResult(
                "test",
                True,
                "not run: the suite gated the build that produced these artifacts",
                {"from_artifacts": True},
            )
        if self.dry_run:
            return StepResult("test", True, "would run the offline suite")
        completed = self._run([sys.executable, "-m", "pytest", "-q", "-x"])
        if completed.returncode != 0:
            raise ReleaseError(f"the offline suite failed ({completed.returncode})")
        return StepResult("test", True, "offline suite passed")

    def step_assets(self) -> StepResult:
        if self.dry_run:
            self.assets = [self.assets_dir / f"openai4s-{self.version}.whl"]
            return StepResult("assets", True, "would collect built assets")
        candidates = sorted(
            path
            for path in self.assets_dir.glob("*")
            if path.is_file() and path.suffix in DISTRIBUTION_SUFFIXES
        )
        # A distribution whose filename does not carry this version is a
        # leftover from another build — belt to the `step_build` clear's
        # braces, and the only guard on the staging path, where build does not
        # run and the directory is populated by an earlier job. Publishing a
        # stale version under this release is exactly the mismatch the whole
        # pipeline exists to prevent.
        self.assets = [p for p in candidates if self.version in p.name]
        wrong_version = [p.name for p in candidates if p not in self.assets]
        if wrong_version:
            raise ReleaseError(
                f"the asset directory holds distributions for another version: "
                f"{wrong_version}; refusing to stage a mixed release"
            )
        if not self.assets:
            raise ReleaseError(f"no release assets were produced in {self.assets_dir}")
        self.incoming = {a.name: sha256_file(a) for a in self.assets}
        return StepResult(
            "assets",
            True,
            f"{len(self.assets)} asset(s)",
            {"assets": [a.name for a in self.assets], "digests": dict(self.incoming)},
        )

    def step_smoke(self) -> StepResult:
        """Install the exact wheel in a clean environment and use it.

        The offline suite runs against the source checkout, so packaging,
        entry-point, import and packaged-resource failures survived it entirely
        — the release gate proved the code worked, never that the artifact did.
        """
        if self.dry_run:
            return StepResult("smoke", True, "would install the wheel and exercise it")
        wheels = [a for a in self.assets if a.suffix == ".whl"]
        if not wheels:
            raise ReleaseError("no wheel to smoke-test; refusing to stage a release")
        wheel = wheels[0]
        daemon = self._smoke(wheel)
        return StepResult(
            "smoke",
            True,
            f"{wheel.name} installs and runs from a clean environment",
            {"wheel": wheel.name, "daemon": daemon},
        )

    def _install_and_exercise(self, wheel: Path) -> str:
        with tempfile.TemporaryDirectory(prefix="openai4s-release-smoke-") as temp:
            root = Path(temp)
            venv = root / "venv"
            created = self._run([sys.executable, "-m", "venv", str(venv)])
            if created.returncode != 0:
                raise ReleaseError("could not create an isolated environment")
            python = venv / "bin" / "python"
            if not python.exists():  # pragma: no cover - Windows layout
                python = venv / "Scripts" / "python.exe"
            installed = self._run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--disable-pip-version-check",
                    str(wheel),
                ]
            )
            if installed.returncode != 0:
                raise ReleaseError(
                    f"the wheel does not install in a clean environment: "
                    f"{(installed.stderr or b'').decode('utf-8', 'replace')[-1500:]}"
                )
            # Run from outside the checkout with no PYTHONPATH, so nothing can
            # resolve back to the source tree and hide a packaging fault.
            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            env["OPENAI4S_DATA_DIR"] = str(root / "data")
            smoke = subprocess.run(
                [str(python), str(ROOT / "scripts" / "release_import_smoke.py")],
                cwd=str(root),
                env=env,
                capture_output=True,
                timeout=600,
            )
            if smoke.returncode != 0:
                raise ReleaseError(
                    f"the installed wheel failed its smoke test: "
                    f"{(smoke.stdout or b'').decode('utf-8', 'replace')[-1500:]}"
                    f"{(smoke.stderr or b'').decode('utf-8', 'replace')[-1500:]}"
                )
            return self._smoke_daemon(python, root, env)

    def _smoke_daemon(self, python: Path, root: Path, env: dict[str, str]) -> str:
        """Start the installed daemon, ask it for its URL, and stop it."""
        import socket
        import urllib.error
        import urllib.request

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = {**env, "OPENAI4S_PORT": str(port), "OPENAI4S_HOST": "127.0.0.1"}
        # `serve` is foreground by design, so it is started as a child and
        # stopped through the CLI's own pidfile — the same path a user takes.
        daemon = subprocess.Popen(
            [str(python), "-I", "-m", "openai4s", "serve", "--no-open"],
            cwd=str(root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 90
            last = ""
            while time.monotonic() < deadline:
                if daemon.poll() is not None:
                    output = (daemon.stdout.read() or b"") if daemon.stdout else b""
                    raise ReleaseError(
                        "the installed daemon exited before serving: "
                        + output.decode("utf-8", "replace")[-1200:]
                    )
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/", timeout=5
                    ) as response:
                        if 200 <= getattr(response, "status", 0) < 400:
                            return f"served on 127.0.0.1:{port}"
                except (urllib.error.URLError, OSError) as e:
                    last = str(e)
                time.sleep(1)
            raise ReleaseError(f"the installed daemon never served a page: {last}")
        finally:
            subprocess.run(
                [str(python), "-I", "-m", "openai4s", "stop"],
                cwd=str(root),
                env=env,
                capture_output=True,
                timeout=120,
            )
            if daemon.poll() is None:
                daemon.terminate()
            try:
                daemon.wait(timeout=30)
            except subprocess.TimeoutExpired:  # pragma: no cover
                daemon.kill()

    def step_sbom(self) -> StepResult:
        if self.dry_run:
            return StepResult("sbom", True, "would write sbom.cdx.json")
        packages: list[dict[str, str]] = []
        for wheel in [a for a in self.assets if a.suffix == ".whl"]:
            packages.extend(wheel_components(wheel))
        embedded, unread = sidecar_components(self.assets)
        packages.extend(embedded)
        document = build_sbom(
            self.assets, version=self.version, packages=packages, unread=unread
        )
        target = self.assets_dir / "sbom.cdx.json"
        target.write_text(json.dumps(document, indent=2, sort_keys=True), "utf-8")
        self.assets.append(target)
        return StepResult(
            "sbom",
            True,
            str(target.name),
            {"components": len(document["components"]), "unread": list(unread)},
        )

    def step_provenance(self) -> StepResult:
        if self.dry_run:
            return StepResult("provenance", True, "would write provenance.intoto.json")
        commit = ""
        completed = self._run(["git", "rev-parse", "HEAD"])
        if completed.returncode == 0:
            commit = (completed.stdout or b"").decode().strip()
        uri = canonical_source_uri(self._run)
        document = build_provenance(
            [a for a in self.assets if a.suffix != ".json"],
            version=self.version,
            source={"uri": uri, "digest": {"sha1": commit}},
        )
        target = self.assets_dir / "provenance.intoto.json"
        target.write_text(json.dumps(document, indent=2, sort_keys=True), "utf-8")
        self.assets.append(target)
        return StepResult(
            "provenance",
            True,
            str(target.name),
            {"subjects": len(document["subject"]), "source_uri": uri},
        )

    def step_checksums(self) -> StepResult:
        """One manifest, written after every other asset exists, and uploaded.

        It used to be written inside `assets` — before the SBOM and provenance
        were generated, so those shipped unhashed — and it was never added to
        the upload set, so it did not ship at all.
        """
        if self.dry_run:
            return StepResult("checksums", True, "would write SHA256SUMS")
        target = self.assets_dir / "SHA256SUMS"
        covered = sorted(a for a in self.assets if a.name != target.name)
        target.write_text(
            "".join(f"{sha256_file(a)}  {a.name}\n" for a in covered), encoding="utf-8"
        )
        self.assets.append(target)
        return StepResult(
            "checksums",
            True,
            f"{len(covered)} asset(s) hashed",
            {"covered": [a.name for a in covered]},
        )

    def step_verify(self) -> StepResult:
        """Every asset present, unchanged, and — in release mode — really signed."""
        if self.dry_run:
            return StepResult("verify", True, "would verify assets and signing")
        missing = [a.name for a in self.assets if not a.is_file()]
        if missing:
            raise ReleaseError(f"declared assets are not on disk: {missing}")
        drifted = [
            name
            for name, digest in self.incoming.items()
            if sha256_file(self.assets_dir / name) != digest
        ]
        if drifted:
            raise ReleaseError(
                f"distributions changed after they were collected: {drifted}; "
                f"GitHub and PyPI would receive different bytes for one version"
            )
        dmgs = [a for a in self.assets if a.suffix == ".dmg"]
        signatures = {dmg.name: read_signature(dmg, self._run) for dmg in dmgs}
        unsigned = [
            name for name, info in signatures.items() if not info.get("developer_id")
        ]
        if self.mode == "release" and unsigned:
            # Fail closed on evidence, not on configuration. Reading a
            # non-empty OPENAI4S_MACOS_SIGNING_IDENTITY as "signed" meant that
            # setting the secret made an ad-hoc image — the only kind the build
            # script produces — pass this gate as Developer-ID-signed.
            raise ReleaseError(
                f"no {DEVELOPER_ID_AUTHORITY} signature could be established "
                f"for {unsigned}; refusing to publish. "
                + json.dumps(signatures, sort_keys=True)
            )
        return StepResult(
            "verify",
            True,
            f"{len(self.assets)} asset(s) verified",
            {
                "signatures": signatures,
                "identity_configured": bool(
                    os.environ.get(SIGNING_IDENTITY_VAR, "").strip()
                ),
                # Never claimed. Notarization needs Apple's service and a paid
                # identity; asserting it here without one would be a confident
                # wrong answer about the thing users check.
                "notarized": None,
                "notarization_note": (
                    "not attempted by this pipeline; requires an Apple "
                    "Developer identity and the notary service"
                ),
            },
        )

    def step_draft(self) -> StepResult:
        if self.dry_run or self.mode != "release":
            return StepResult(
                "draft", True, f"would use the existing draft v{self.version}"
            )
        completed = self._gh(
            ["release", "view", f"v{self.version}", "--json", "isDraft"]
        )
        if completed.returncode != 0:
            raise ReleaseError(
                f"there is no release v{self.version} to stage into; create the "
                f"draft first — this pipeline never creates a public release"
            )
        try:
            payload = json.loads((completed.stdout or b"{}").decode("utf-8"))
        except ValueError as e:
            raise ReleaseError(f"the release listing was not JSON: {e}") from e
        if not payload.get("isDraft"):
            raise ReleaseError(
                f"v{self.version} is already public; staging assets onto it is "
                f"the half-published state this pipeline exists to prevent"
            )
        return StepResult("draft", True, f"draft v{self.version} confirmed")

    def step_upload(self) -> StepResult:
        if self.dry_run or self.mode != "release":
            return StepResult(
                "upload", True, f"would upload {len(self.assets)} asset(s)"
            )
        completed = self._gh(
            [
                "release",
                "upload",
                f"v{self.version}",
                *[str(a) for a in self.assets],
                "--clobber",
            ]
        )
        if completed.returncode != 0:
            raise ReleaseError(
                f"asset upload failed: "
                f"{(completed.stderr or b'').decode('utf-8', 'replace')}"
            )
        return StepResult("upload", True, f"{len(self.assets)} asset(s) uploaded")

    def step_reverify(self) -> StepResult:
        """Hash what was uploaded, not what was built.

        Comparing *names* satisfied this check while the bytes behind a name
        could be truncated or replaced. Every staged asset is downloaded back
        and re-hashed, which is the only form of this check a lost or swapped
        transfer cannot pass.
        """
        if self.dry_run or self.mode != "release":
            return StepResult("reverify", True, "would re-hash the uploaded assets")
        completed = self._gh(
            ["release", "view", f"v{self.version}", "--json", "assets"]
        )
        if completed.returncode != 0:
            raise ReleaseError("could not read back the staged assets")
        try:
            remote = json.loads((completed.stdout or b"{}").decode("utf-8"))
        except ValueError as e:
            raise ReleaseError(f"the release listing was not JSON: {e}") from e
        listing = {str(item.get("name")): item for item in (remote.get("assets") or [])}
        missing = sorted({a.name for a in self.assets} - set(listing))
        if missing:
            raise ReleaseError(f"assets did not survive the upload: {missing}")

        mismatched: list[str] = []
        checked: dict[str, str] = {}
        with tempfile.TemporaryDirectory(prefix="openai4s-reverify-") as temp:
            for asset in self.assets:
                local = sha256_file(asset)
                size = listing[asset.name].get("size")
                if isinstance(size, int) and size != asset.stat().st_size:
                    mismatched.append(
                        f"{asset.name}: size {size} != {asset.stat().st_size}"
                    )
                    continue
                pulled = self._gh(
                    [
                        "release",
                        "download",
                        f"v{self.version}",
                        "--pattern",
                        asset.name,
                        "--dir",
                        temp,
                        "--clobber",
                    ]
                )
                if pulled.returncode != 0:
                    raise ReleaseError(
                        f"could not download {asset.name} back for verification: "
                        f"{(pulled.stderr or b'').decode('utf-8', 'replace')}"
                    )
                downloaded = Path(temp) / asset.name
                if not downloaded.is_file():
                    raise ReleaseError(
                        f"{asset.name} did not come back from the release"
                    )
                remote_digest = sha256_file(downloaded)
                checked[asset.name] = remote_digest
                if remote_digest != local:
                    mismatched.append(
                        f"{asset.name}: {remote_digest[:12]} != {local[:12]}"
                    )
        if mismatched:
            raise ReleaseError(
                f"uploaded bytes do not match what was verified: {mismatched}"
            )
        return StepResult(
            "reverify",
            True,
            f"{len(checked)} asset(s) re-hashed from the release",
            {"digests": checked},
        )

    def step_publish(self) -> StepResult:
        """The last cross-channel step: flip the draft public.

        It runs only after the package is on PyPI, and it checks that rather
        than assuming it. Flipping first — as this used to, inside the staging
        job, with the PyPI upload in a separate job afterwards — meant an OIDC
        failure, a denied environment approval or a rejected upload left a
        public GitHub release with no matching package version.
        """
        if self.dry_run or self.mode != "release":
            return StepResult(
                "publish", True, "would publish (not performed in this mode)"
            )
        if not self._pypi_check("openai4s", self.version):
            raise ReleaseError(
                f"openai4s {self.version} is not on PyPI; refusing to make the "
                f"GitHub release public. Publish to PyPI first, then re-run "
                f"with --only publish. The draft is untouched."
            )
        completed = self._gh(["release", "edit", f"v{self.version}", "--draft=false"])
        if completed.returncode != 0:
            raise ReleaseError(
                f"could not publish: "
                f"{(completed.stderr or b'').decode('utf-8', 'replace')}. "
                f"PyPI already has this version; the release is still a draft. "
                f"Re-run `--only publish` — do not rebuild and do not bump the "
                f"version, or the two channels would carry different bytes."
            )
        return StepResult("publish", True, f"v{self.version} is public")

    # --- the run ----------------------------------------------------------
    def planned_steps(self) -> tuple[str, ...]:
        """Which steps this invocation runs, in order.

        `--only` is how the finalize job flips the draft after PyPI without
        re-entering anything before it; `--stop-after` is how the staging job
        goes right up to the edge and no further.
        """
        if self.only:
            if self.only not in STEPS:
                raise ReleaseError(f"unknown step {self.only!r}")
            return (self.only,)
        if self.stop_after:
            if self.stop_after not in STEPS:
                raise ReleaseError(f"unknown step {self.stop_after!r}")
            return STEPS[: STEPS.index(self.stop_after) + 1]
        return STEPS

    def run(self) -> dict[str, Any]:
        try:
            planned = self.planned_steps()
        except ReleaseError as error:
            self.results.append(StepResult("plan", False, str(error)))
            return self.report(stopped_at="plan")
        for name in planned:
            step = getattr(self, f"step_{name}")
            try:
                result = step()
            except ReleaseError as error:
                self.results.append(StepResult(name, False, str(error)))
                return self.report(stopped_at=name, planned=planned)
            self.performed.append(name)
            self.results.append(result)
        return self.report(planned=planned)

    def report(
        self, stopped_at: str | None = None, planned: Sequence[str] = STEPS
    ) -> dict[str, Any]:
        return {
            "version": self.version,
            "mode": "dry-run" if self.dry_run else self.mode,
            "ok": stopped_at is None,
            "stopped_at": stopped_at,
            "planned": list(planned),
            "from_artifacts": self.from_artifacts,
            "published": stopped_at is None
            and self.mode == "release"
            and not self.dry_run
            and "publish" in planned,
            "steps": [result.public() for result in self.results],
        }


def _pypi_has_version(project: str, version: str) -> bool:
    """Is this exact version actually on the index?

    Evidence for the cross-channel ordering: the GitHub flip must not happen
    on the strength of a job having been scheduled.
    """
    import urllib.error
    import urllib.request

    url = f"https://pypi.org/pypi/{project}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return 200 <= getattr(response, "status", 0) < 300
    except urllib.error.HTTPError:
        return False
    except Exception:  # noqa: BLE001 - unreachable index is not a yes
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--mode", choices=("local", "release"), default="local")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--assets-dir", type=Path)
    parser.add_argument(
        "--from-artifacts",
        action="store_true",
        help="staging only: consume already-built, already-verified assets",
    )
    parser.add_argument("--stop-after", choices=STEPS)
    parser.add_argument("--only", choices=STEPS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    pipeline = Pipeline(
        args.version,
        mode=args.mode,
        dry_run=args.dry_run,
        assets_dir=args.assets_dir,
        from_artifacts=args.from_artifacts,
        stop_after=args.stop_after,
        only=args.only,
    )
    report = pipeline.run()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for step in report["steps"]:
            mark = "ok  " if step["ok"] else "FAIL"
            print(f"  [{mark}] {step['step']:<11} {step['detail']}")
        if report["ok"]:
            print(f"\nv{report['version']}: every step passed ({report['mode']})")
        else:
            print(f"\nv{report['version']}: stopped at {report['stopped_at']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
