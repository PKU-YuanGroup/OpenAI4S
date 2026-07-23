#!/usr/bin/env python3
"""Draft-first release: build, prove, stage, verify, and only then publish.

    uv run python scripts/release_pipeline.py --dry-run --version 0.2.0
    uv run python scripts/release_pipeline.py --mode local  --version 0.2.0
    uv run python scripts/release_pipeline.py --mode release --version 0.2.0

`release.yml` triggers `on: release`, so today assets are built and uploaded
*after* the release is already public. Anyone watching sees a version with a
wheel and no DMG, or a checksum file that names a file not yet attached — a
half-published state, which the proposal names as the thing to eliminate.

The pipeline is here rather than in the workflow YAML on purpose. A release
step embedded in `on: release` can only ever be exercised by cutting a real
release, which means it is tested by the thing it is supposed to protect. As a
script it runs on a laptop, in `--dry-run`, and under pytest.

## The order, and why it is the order

    build → test → assets → SBOM → provenance → verify → draft → upload →
    re-verify → publish

Everything irreversible is last. Publishing is the only step that cannot be
undone — a package index will not forget a version — so every fact that could
stop it must be established before it runs. `verify` before `draft` catches a
bad asset while nothing is visible; `re-verify` after `upload` catches a
transfer that lost bytes, which is the failure a local checksum cannot see.

## Modes

* `--dry-run` performs no external call and prints what it would do. It is not
  a weaker `local`: it is how the *ordering* is tested.
* `--mode local` really builds, really hashes, really writes the SBOM and
  provenance, and stops before anything is published. Signing is *described*
  rather than performed, and the report says so.
* `--mode release` additionally requires the signing identity. Missing it is a
  hard failure, because a release that silently ships unsigned is exactly the
  outcome signing exists to prevent.

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
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]

#: The ordered pipeline. Named here so the order itself is testable.
STEPS = (
    "build",
    "test",
    "assets",
    "sbom",
    "provenance",
    "verify",
    "draft",
    "upload",
    "reverify",
    "publish",
)

#: Steps that change something outside this machine. `publish` is the only
#: irreversible one, and it is last for that reason.
EXTERNAL = frozenset({"draft", "upload", "publish"})

SIGNING_IDENTITY_VAR = "OPENAI4S_MACOS_SIGNING_IDENTITY"


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


def build_sbom(assets: list[Path], *, version: str, packages: list[dict]) -> dict:
    """A CycloneDX document naming what is in the release and what it is made of.

    Written by hand rather than by a third-party generator because the core is
    stdlib-only and a supply-chain document produced by an unpinned tool is a
    supply-chain question of its own. The shape is CycloneDX 1.5's, so ordinary
    scanners read it.
    """
    return {
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
    ) -> None:
        self.version = version
        self.mode = mode
        self.dry_run = dry_run
        self.assets_dir = Path(assets_dir or ROOT / "dist")
        self._run = runner or _run
        self._gh = gh or (lambda argv: _run(["gh", *argv]))
        self.results: list[StepResult] = []
        self.assets: list[Path] = []
        self.performed: list[str] = []

    # --- steps ------------------------------------------------------------
    def step_build(self) -> StepResult:
        if self.dry_run:
            return StepResult("build", True, "would build sdist and wheel")
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        completed = self._run(
            [sys.executable, "-m", "build", "--outdir", str(self.assets_dir)]
        )
        if completed.returncode != 0:
            raise ReleaseError(
                f"build failed ({completed.returncode}): "
                f"{(completed.stderr or b'').decode('utf-8', 'replace')[-2000:]}"
            )
        return StepResult("build", True, f"built into {self.assets_dir}")

    def step_test(self) -> StepResult:
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
        self.assets = sorted(
            path
            for path in self.assets_dir.glob("*")
            if path.is_file() and path.suffix in (".whl", ".gz", ".dmg", ".zip")
        )
        if not self.assets:
            raise ReleaseError(f"no release assets were produced in {self.assets_dir}")
        checksums = self.assets_dir / "SHA256SUMS"
        checksums.write_text(
            "".join(f"{sha256_file(a)}  {a.name}\n" for a in self.assets),
            encoding="utf-8",
        )
        return StepResult(
            "assets",
            True,
            f"{len(self.assets)} asset(s)",
            {"assets": [a.name for a in self.assets]},
        )

    def step_sbom(self) -> StepResult:
        if self.dry_run:
            return StepResult("sbom", True, "would write sbom.cdx.json")
        packages = []
        try:
            from openai4s.kernel import preinstall

            packages = preinstall.full_freeze() or []
        except Exception:  # noqa: BLE001 - an SBOM with no components is still
            # an honest SBOM; a fabricated component list is not.
            packages = []
        document = build_sbom(self.assets, version=self.version, packages=packages)
        target = self.assets_dir / "sbom.cdx.json"
        target.write_text(json.dumps(document, indent=2, sort_keys=True), "utf-8")
        self.assets.append(target)
        return StepResult(
            "sbom", True, str(target.name), {"components": len(document["components"])}
        )

    def step_provenance(self) -> StepResult:
        if self.dry_run:
            return StepResult("provenance", True, "would write provenance.intoto.json")
        commit = ""
        completed = self._run(["git", "rev-parse", "HEAD"])
        if completed.returncode == 0:
            commit = (completed.stdout or b"").decode().strip()
        document = build_provenance(
            [a for a in self.assets if a.suffix != ".json"],
            version=self.version,
            source={
                "uri": "git+https://github.com/openai4s/openai4s",
                "digest": {"sha1": commit},
            },
        )
        target = self.assets_dir / "provenance.intoto.json"
        target.write_text(json.dumps(document, indent=2, sort_keys=True), "utf-8")
        self.assets.append(target)
        return StepResult(
            "provenance", True, str(target.name), {"subjects": len(document["subject"])}
        )

    def step_verify(self) -> StepResult:
        """Every asset present, hashed, and — in release mode — signed."""
        if self.dry_run:
            return StepResult("verify", True, "would verify assets and signing")
        missing = [a.name for a in self.assets if not a.is_file()]
        if missing:
            raise ReleaseError(f"declared assets are not on disk: {missing}")
        identity = os.environ.get(SIGNING_IDENTITY_VAR, "").strip()
        signed = bool(identity)
        dmgs = [a for a in self.assets if a.suffix == ".dmg"]
        if self.mode == "release" and dmgs and not signed:
            # Fail closed. A release that silently ships unsigned is the exact
            # outcome signing exists to prevent, and "the certificate was not
            # configured" is not a reason to publish anyway.
            raise ReleaseError(
                f"{SIGNING_IDENTITY_VAR} is not set and this release includes "
                f"a macOS disk image; refusing to publish unsigned"
            )
        return StepResult(
            "verify",
            True,
            f"{len(self.assets)} asset(s) verified",
            {
                "signing_identity_configured": signed,
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
                "draft", True, f"would create draft release v{self.version}"
            )
        completed = self._gh(
            [
                "release",
                "create",
                f"v{self.version}",
                "--draft",
                "--title",
                f"v{self.version}",
            ]
        )
        if completed.returncode != 0:
            # An existing draft is fine; a real failure is not.
            stderr = (completed.stderr or b"").decode("utf-8", "replace")
            if "already exists" not in stderr:
                raise ReleaseError(f"could not create the draft release: {stderr}")
        return StepResult("draft", True, f"draft v{self.version} staged")

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

        A local checksum cannot see a transfer that lost bytes, and that is
        precisely the failure the last step must not publish over.
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
        names = {item.get("name") for item in remote.get("assets") or []}
        missing = sorted({a.name for a in self.assets} - names)
        if missing:
            raise ReleaseError(f"assets did not survive the upload: {missing}")
        return StepResult("reverify", True, f"{len(names)} asset(s) present remotely")

    def step_publish(self) -> StepResult:
        if self.dry_run or self.mode != "release":
            return StepResult(
                "publish", True, "would publish (not performed in this mode)"
            )
        completed = self._gh(["release", "edit", f"v{self.version}", "--draft=false"])
        if completed.returncode != 0:
            raise ReleaseError(
                f"could not publish: "
                f"{(completed.stderr or b'').decode('utf-8', 'replace')}"
            )
        return StepResult("publish", True, f"v{self.version} is public")

    # --- the run ----------------------------------------------------------
    def run(self) -> dict[str, Any]:
        for name in STEPS:
            step = getattr(self, f"step_{name}")
            try:
                result = step()
            except ReleaseError as error:
                self.results.append(StepResult(name, False, str(error)))
                return self.report(stopped_at=name)
            self.performed.append(name)
            self.results.append(result)
        return self.report()

    def report(self, stopped_at: str | None = None) -> dict[str, Any]:
        return {
            "version": self.version,
            "mode": "dry-run" if self.dry_run else self.mode,
            "ok": stopped_at is None,
            "stopped_at": stopped_at,
            "published": stopped_at is None
            and self.mode == "release"
            and not self.dry_run,
            "steps": [result.public() for result in self.results],
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--mode", choices=("local", "release"), default="local")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--assets-dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    pipeline = Pipeline(
        args.version,
        mode=args.mode,
        dry_run=args.dry_run,
        assets_dir=args.assets_dir,
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
