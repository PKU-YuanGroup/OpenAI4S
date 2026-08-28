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
      → sbom → provenance → verify → seal evidence
      → checksums over everything → stage unchanged bytes
      → upload → remote digest verification
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
* `--mode release` additionally requires a real Developer ID signature and a
  stapled notarization ticket on any disk image. Missing either is a hard
  failure, because a release that silently ships an image Gatekeeper refuses is
  exactly the outcome these checks exist to prevent.
* `--from-artifacts` is the staging-only mode. `build` and `test` do not run —
  their inputs are artifacts an earlier job already produced and verified — and
  the distributions are fingerprinted on entry and re-checked before upload, so
  this job cannot replace the bytes GitHub and PyPI are both meant to receive.

This pipeline does not submit an image to Apple's notary service or staple a
ticket. ``scripts/notarize_macos_dmg.sh`` does that in the macOS job when the
workflow input ``macos_asset=notarized``. The pipeline reports notarization as
verified only when image-bound evidence from ``xcrun stapler validate`` plus
the post-staple digest establishes that a ticket is already attached to *these*
bytes. The default workflow input is ``macos_asset=omit``: no preview DMG is
uploaded.
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
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    # `python scripts/release_pipeline.py` puts `scripts/` on the path, not the
    # checkout root, so the sibling import below needs the root added. Tests and
    # `run_quality_gates.py` already import through the `scripts` package.
    sys.path.insert(0, str(ROOT))

from scripts import release_gates, release_receipts  # noqa: E402
from scripts.release_gates import GateManifestError  # noqa: E402


def _sandbox_posture() -> dict[str, Any]:
    """The sandbox posture this build was produced under, as a fact not a claim.

    Read from the environment rather than probed: the release runner is not the
    machine the product will run on, so "enforce was requested here" is the only
    honest statement available. Recorded because the evidence bundle is supposed
    to carry it and carried nothing.
    """
    return {
        "requested": os.environ.get("OPENAI4S_KERNEL_SANDBOX", "auto"),
        "note": (
            "the posture requested on the build runner; not a measurement of the "
            "sandbox on an end user's machine"
        ),
        # The platform boundaries this release did not prove, and why. Carried
        # because a bundle that simply omits them is indistinguishable from one
        # where every platform passed, and that is the reading a reader defaults
        # to. Named here so "we could not check Linux" survives into the
        # evidence rather than living only in a workflow comment.
        "unproven": dict(release_gates.PLATFORM_CHECKS_UNAVAILABLE),
    }


#: One name, because the writer and the collector disagreeing about it is
#: exactly how the SBOM came to be built on every release and carried on none.
SBOM_NAME = "sbom.cdx.json"


def dmg_present(assets: Sequence[Path]) -> bool:
    """Whether this release carries a macOS image at all.

    An omitted DMG is a supported release shape, so the `macos` build receipt is
    required only when there is an image to bind to a commit.
    """
    return any(Path(a).suffix == ".dmg" for a in assets)


#: What names an asset as belonging to a receipt kind. Derived from the file, so
#: adding a platform to the release means adding a row here and a receipt step
#: to its build job -- rather than the previous arrangement, where a new
#: platform simply had no receipt and nothing said so.
_RECEIPT_KIND_SUFFIXES = (
    ("macos", (".dmg",)),
    ("windows", ("-windows-x86_64.zip", "-windows-arm64.zip")),
    ("linux", ("-linux-x86_64.tar.gz", "-linux-aarch64.tar.gz")),
)


def required_receipt_kinds(assets: Sequence[Path]) -> tuple[str, ...]:
    """The receipt kinds this particular set of assets must carry.

    `("dist", "macos")` was the whole list, so the Linux tarball and the Windows
    zip were staged with no document binding their bytes to the frozen commit --
    covered only by the in-run `incoming` digests, which attest that `attach`
    downloaded what it downloaded, not that it was built from these sources.

    Computed from the assets rather than fixed, because an omitted platform is a
    supported release shape and demanding a receipt for an artifact that is not
    there would refuse every partial release.
    """
    kinds = ["dist"]
    names = [Path(a).name for a in assets]
    for kind, suffixes in _RECEIPT_KIND_SUFFIXES:
        if any(name.endswith(suffix) for name in names for suffix in suffixes):
            kinds.append(kind)
    return tuple(kinds)


#: The ordered pipeline. Named here so the order itself is testable.
#:
#: `verify` and `evidence` sit *before* `checksums` on purpose. The evidence
#: bundle has to record the signing and notarization facts `verify` establishes,
#: and `SHA256SUMS` has to cover the evidence bundle -- a bundle outside the
#: checksum manifest is an asset the release publishes without hashing. Sealing
#: the bundle after `upload`, as this used to, produced a record of a release
#: that had already left.
STEPS = (
    "build",
    "test",
    "assets",
    "smoke",
    "sbom",
    "provenance",
    "verify",
    "evidence",
    "checksums",
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

#: The four states a macOS image's signing can honestly be in. Named because
#: the evidence was previously four scattered fields -- `developer_id`,
#: `adhoc`, `identity_configured`, `notarized: None` -- from which a reader had
#: to infer the answer, and a reader who infers it wrongly is exactly who this
#: is for.
#:
#: `verified` requires a Developer ID signature *and* a stapled notarization
#: ticket, both read from the macOS job's receipt. It is **reachable** — that is
#: the change: it used to be described as unreachable, but the reason given was
#: that `notarized` was hardcoded `None`, which is a statement about this file
#: rather than about the image. Meanwhile `step_verify` gated on the signature
#: alone, so once the signing-certificate secret exists (the workflow already
#: imports it into a keychain) a correctly signed, un-notarized image published.
#: "Unreachable" was documenting the absence of the check as if it were the
#: absence of the capability.
#:
#: With no notary credentials configured the state of a built image is `preview`
#: (ad-hoc), and the supported release shape is to omit the DMG. That is a real
#: outcome rather than a label on a published artifact.
SIGNING_STATES = {
    # Developer ID signature, notarization confirmed. The only publishable one.
    "verified",
    # Developer ID signature, no stapled notarization ticket. Gatekeeper refuses
    # this on a user's machine, so a public release must not carry it.
    "not_notarized",
    # Ad-hoc signature: verifies happily, says nothing about who produced it.
    # What the build script produces, and what a local or CI build is.
    "preview",
    # No signature evidence at all, or none that could be read.
    "not_configured",
}


def signing_state(signature: Mapping[str, Any] | None) -> str:
    """Name what the signature evidence actually establishes.

    Reads evidence only. In particular it does not consult
    ``OPENAI4S_MACOS_SIGNING_IDENTITY``: treating a configured secret as proof
    of a signature is the specific mistake that once let an ad-hoc image pass
    the release gate as Developer-ID-signed.
    """
    if not isinstance(signature, Mapping) or signature.get("error"):
        return "not_configured"
    if signature.get("developer_id"):
        # `notarized` is derived in `read_signature` from a stapler result bound
        # to this image's digest, not from a configured secret or an intent.
        return "verified" if signature.get("notarized") else "not_notarized"
    if signature.get("adhoc"):
        return "preview"
    return "not_configured"


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

#: Hosts the pyproject fallback will accept as the canonical source. Named
#: rather than pattern-matched: this value goes into a signed provenance
#: statement, so "looks like GitHub" is not a good enough test.
_SOURCE_HOSTS = frozenset({"github.com", "www.github.com"})


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


def parse_checksums(text: str) -> dict[str, str]:
    """Parse a ``SHA256SUMS`` body (``<digest>  <name>`` per line) to a map.

    This is the persisted digest manifest the finalize-only publish re-validates
    the draft against, so parsing must be strict: a malformed line is dropped
    rather than guessed at.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].strip(), parts[1].strip()
        if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest) and name:
            out[name] = digest
    return out


def _asset_version(filename: str) -> str | None:
    """The exact version encoded in a distribution filename, or None.

    Wheels are ``<name>-<version>-<pytag>-...whl`` and sdists
    ``<name>-<version>.tar.gz``; the DMG and zip use ``<Name>-<version>-...``.
    A substring test (`"0.2.0" in name`) matched `0.2.0rc1` and `10.2.0`, so
    the version is taken as the field after the first ``<name>-`` and compared
    whole.
    """
    stem = filename
    for suffix in (".tar.gz", ".whl", ".dmg", ".zip"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    parts = stem.split("-")
    if len(parts) < 2:
        return None
    # The version is the second dash-separated field (after the project name).
    return parts[1] or None


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
        # Bind the inventory to the exact image. A components sidecar left by an
        # earlier rebuild with the same filename would otherwise contribute its
        # package list to an SBOM describing a *different* image. Require the
        # recorded digest to match the bytes on disk; a missing or mismatched
        # binding is reported as unread, not trusted.
        recorded = str(payload.get("image_sha256") or "")
        if not recorded or recorded != sha256_file(asset):
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
        if "=" not in line:
            continue
        candidate = line.split("=", 1)[1].strip().strip('"').strip("'")
        # Host equality, not `"github.com" in line`. A substring test also
        # accepts `github.com.example.net` and `evil-github.com`, and the value
        # it selects is written into a *signed* provenance statement as the
        # place a consumer should go to find this source — the one field in the
        # document whose whole job is to be trustworthy.
        #
        # Both the split and the authority normalisation are guarded: `urlsplit`
        # raises on a malformed IPv6 authority, and `.hostname` normalises the
        # netloc and can itself raise. A line we cannot parse is skipped, never
        # signed on a guess — the same fail-closed answer as the substring miss.
        try:
            parts = urllib.parse.urlsplit(candidate)
            host_matches = (
                parts.scheme in ("http", "https") and parts.hostname in _SOURCE_HOSTS
            )
        except ValueError:
            continue
        if host_matches:
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


#: Name of the receipt the quality job writes and staging verifies.
QUALITY_RECEIPT_NAME = release_gates.RECEIPT_NAME


def build_quality_receipt(
    source_sha: str,
    gates: list[dict],
    checks: list[dict] | None = None,
    *,
    platform_checks: list[dict] | None = None,
) -> dict:
    """The document a quality run leaves behind.

    Thin on purpose: the shape and every rule about it live in
    `scripts/release_gates.py`, so the producer and the consumer cannot describe
    two different gate lists. They previously did — the producer owned a `GATES`
    tuple and the consumer compared nothing but exit codes.
    """
    return release_gates.build_receipt(
        source_sha,
        gates,
        checks or [],
        platform_checks=platform_checks or [],
    )


def verify_quality_receipt(path: Path, *, expected_sha: str) -> dict:
    """Read a receipt and refuse everything about it that is not proof.

    The binding is the whole value, and it has two halves. The SHA half: a
    receipt that records *a* SHA proves nothing unless the consumer re-derives
    the SHA it is actually releasing and compares. The manifest half: a receipt
    that records *some* gates proves nothing unless the consumer requires
    exactly the canonical ones, with the argv they were declared with — the
    check this used to be missing entirely, which let a two-row document with
    the argv `["pytest"]` stage a release.

    Raises ``ReleaseError`` rather than returning a verdict, because every
    caller here treats "cannot prove it" as "do not release".
    """
    if not path.is_file():
        raise ReleaseError(
            f"no quality receipt at {path}; staging cannot claim the suite ran. "
            "The quality job must run at this SHA and upload its receipt."
        )
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseError(f"quality receipt is unreadable: {error}") from error
    try:
        return release_gates.verify_receipt_document(
            document, expected_sha=expected_sha
        )
    except GateManifestError as error:
        raise ReleaseError(str(error)) from error


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
        # A receipt must describe *this* image. It records the DMG's digest;
        # without checking it, a stale or copied receipt from any signed image
        # could be paired with a different, unsigned DMG on a staging host and
        # pass the gate. Re-hash and require a match.
        recorded_digest = str(payload.get("image_sha256") or "")
        actual_digest = sha256_file(dmg)
        digest_matches = bool(recorded_digest) and recorded_digest == actual_digest
        # A Developer ID *authority string* is not a valid signature: the
        # receipt also records whether `codesign --verify --deep --strict`
        # succeeded, and a nonzero result means the signature does not verify
        # (tampered, broken, or revoked). Requiring a successful verification
        # stops release mode from accepting an image whose deep check failed.
        verify_rc = payload.get("verify_returncode")
        names_developer_id = any(
            DEVELOPER_ID_AUTHORITY in authority for authority in authorities
        )
        # Notarization, from evidence. `xcrun stapler validate` on the image is
        # the only local check that a notarization ticket is actually attached,
        # and it is what Gatekeeper consults offline. It has to be recorded where
        # the image is built -- the staging host is Linux and has no `xcrun` --
        # and it is required to agree with the digest just like the signature is,
        # so a stapler result copied from another image proves nothing here.
        stapler_rc = payload.get("stapler_returncode")
        spctl_rc = payload.get("spctl_returncode")
        post_staple = str(payload.get("post_staple_sha256") or "")
        post_staple_matches = bool(post_staple) and post_staple == actual_digest
        # A stapled ticket is bound to the post-staple digest of *this* image.
        # stapler_returncode==0 copied from another build is a stale ticket:
        # without a matching post_staple_sha256 it does not notarize these bytes.
        notarized = (
            bool(payload.get("notarized"))
            and stapler_rc == 0
            and names_developer_id
            and verify_rc == 0
            and digest_matches
            and post_staple_matches
        )
        return {
            "source": "receipt",
            "authorities": authorities,
            "developer_id": names_developer_id and verify_rc == 0 and digest_matches,
            "verify_returncode": verify_rc,
            "image_digest_matches": digest_matches,
            "adhoc": bool(payload.get("adhoc")),
            "notarized": notarized,
            "stapler_returncode": stapler_rc,
            "spctl_returncode": spctl_rc,
            "post_staple_sha256": post_staple,
            "post_staple_digest_matches": post_staple_matches,
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
    # `--display` only reads the signature; it does not check that it verifies.
    # Run the strict deep verification separately, and only treat the image as
    # Developer-ID-signed when it both names that authority and verifies.
    verified = runner(["codesign", "--verify", "--deep", "--strict", str(dmg)])
    verify_rc = getattr(verified, "returncode", None)
    names_developer_id = any(
        DEVELOPER_ID_AUTHORITY in authority for authority in authorities
    )
    return {
        "source": "codesign",
        "authorities": authorities,
        "developer_id": names_developer_id and verify_rc == 0,
        "verify_returncode": verify_rc,
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
        pypi_digests: Callable[[str, str], dict[str, str]] | None = None,
        smoke: Callable[[Path], str] | None = None,
        source_sha: str = "",
        attestation: Path | None = None,
    ) -> None:
        self.version = version
        self.mode = mode
        self.dry_run = dry_run
        #: The SHA the workflow froze once, before any job checked anything out.
        #: When supplied it is not trusted as a label: `_frozen_sha` requires the
        #: working tree to actually *be* that commit. Every job used to check out
        #: the mutable `inputs.tag` independently, so the gates, the wheel and the
        #: DMG could each be a different commit with nothing comparing them.
        self.source_sha = str(source_sha or "")
        #: Where the finalize job finds the staging job's attestation. Outside the
        #: draft, because a document stored in the draft can be replaced by
        #: whatever replaced the asset it vouches for.
        self.attestation = Path(attestation) if attestation else None
        # Absolute at construction: `_run` executes subprocesses from ROOT
        # (the checkout), while the staging job passes `--assets-dir assets`
        # as a *sibling* of the checkout. A relative path would make pip in
        # `step_smoke`, and the gh upload/download, look for the wheel under
        # ROOT/assets, where it does not exist.
        self.assets_dir = Path(assets_dir or ROOT / "dist").resolve()
        self._run = runner or _run
        self._gh = gh or (lambda argv: _run(["gh", *argv]))
        self._pypi_check = pypi_check or _pypi_has_version
        self._pypi_digests = pypi_digests or _pypi_file_digests
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
                # The quality receipt is an *input* to this run, not an output
                # of it: it is written by the job that ran the eight gates at
                # the frozen SHA, and this directory is where that job hands it
                # over. Sweeping it with the stale artifacts made a release run
                # destroy the only document proving its own gates -- and then
                # `step_test` could only fall back to a local pytest, which is
                # how that path came to have no source quality proof at all.
                if stale.name == QUALITY_RECEIPT_NAME:
                    continue
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
            # This used to answer "not run: the suite gated the build that
            # produced these artifacts" and pass. The build job runs no suite
            # at all -- it checks out the tag, scans for secrets, builds, and
            # verifies the wheel's metadata. The sentence was not a shortcut,
            # it was false, and it was the only thing standing between a
            # release and "tests gated this".
            head = self._frozen_sha()
            receipt = verify_quality_receipt(
                self.assets_dir / QUALITY_RECEIPT_NAME, expected_sha=head
            )
            return StepResult(
                "test",
                True,
                f"quality receipt verified for {head[:12]}",
                {
                    "from_artifacts": True,
                    "source_sha": head,
                    "manifest_digest": receipt.get("manifest_digest", ""),
                    "builder": receipt.get("builder", {}),
                    "gates": [gate["name"] for gate in receipt["gates"]],
                    # The check-run and workflow-run ids travel into the
                    # evidence bundle: an attestation nobody can go and look at
                    # is not evidence, so the pointers have to survive the hop.
                    "checks": [
                        {
                            "name": row["name"],
                            "check_run_id": row["check_run_id"],
                            "run_id": row["run_id"],
                            "url": row["url"],
                        }
                        for row in receipt.get("checks", [])
                    ],
                    "platform_checks": receipt.get("platform_checks", []),
                    "linux_sandbox_full_check_run_id": next(
                        (
                            str(row.get("check_run_id") or "")
                            for row in receipt.get("checks", [])
                            if row.get("name") == "ci-linux-sandbox-full"
                        ),
                        "",
                    ),
                },
            )
        if self.mode == "release":
            # The third path, and the one nothing held to the source quality
            # proof. `--from-artifacts` cannot bypass it and `--dry-run` reaches
            # nothing that publishes, but a plain `--mode release` ran `pytest`
            # alone -- no pre-commit, no mypy, no README check, no harness tier,
            # no response schema or contract, no secret scan, no browser or
            # Python-matrix attestation -- and then went on to stage assets onto
            # the draft. Only the final flip was blocked, and only because
            # `step_publish` requires PyPI to already hold matching digests.
            #
            # A local suite is not eight gates run at a frozen SHA on a machine
            # nobody can quietly reconfigure, and the difference is the whole
            # point of the receipt.
            head = self._frozen_sha()
            receipt = verify_quality_receipt(
                self.assets_dir / QUALITY_RECEIPT_NAME, expected_sha=head
            )
            return StepResult(
                "test",
                True,
                f"quality receipt verified for {head[:12]}",
                {
                    "from_artifacts": False,
                    "source_sha": head,
                    "manifest_digest": receipt.get("manifest_digest", ""),
                    "builder": receipt.get("builder", {}),
                    "gates": [gate["name"] for gate in receipt["gates"]],
                    "checks": [
                        {
                            "name": row["name"],
                            "check_run_id": row["check_run_id"],
                            "run_id": row["run_id"],
                            "url": row["url"],
                        }
                        for row in receipt.get("checks", [])
                    ],
                    "platform_checks": receipt.get("platform_checks", []),
                },
            )
        if self.dry_run:
            return StepResult("test", True, "would run the offline suite")
        completed = self._run([sys.executable, "-m", "pytest", "-q", "-x"])
        if completed.returncode != 0:
            raise ReleaseError(f"the offline suite failed ({completed.returncode})")
        return StepResult("test", True, "offline suite passed")

    def _head_sha(self) -> str:
        completed = self._run(["git", "rev-parse", "HEAD"])
        if completed.returncode != 0:
            return ""
        return (completed.stdout or b"").decode().strip()

    def _frozen_sha(self) -> str:
        """The one commit this release is, refusing to guess between two answers.

        `--source-sha` is the workflow's frozen value, resolved once from the
        annotated tag before any job ran. It is checked against the checkout
        rather than believed: a job that checked out a tag which has since moved
        would otherwise carry the frozen SHA as a label while building different
        sources. When the flag is absent this falls back to the checkout, which
        is what a local `--mode local` run has.
        """
        head = self._head_sha()
        if not self.source_sha:
            return head
        if not head:
            raise ReleaseError(
                "a frozen source SHA was supplied but this is not a work tree, so "
                "it cannot be checked against the sources being released"
            )
        if head != self.source_sha:
            raise ReleaseError(
                f"this checkout is {head[:12]} but the release was frozen at "
                f"{self.source_sha[:12]}; the tag moved between jobs and the "
                f"artifacts would not all be the same commit"
            )
        return head

    def step_assets(self) -> StepResult:
        if self.dry_run:
            self.assets = [self.assets_dir / f"openai4s-{self.version}.whl"]
            return StepResult("assets", True, "would collect built assets")
        candidates = sorted(
            path
            for path in self.assets_dir.glob("*")
            if path.is_file() and path.suffix in DISTRIBUTION_SUFFIXES
        )
        # A distribution whose version is not exactly this release's is a
        # leftover from another build — belt to the `step_build` clear's
        # braces, and the only guard on the staging path, where build does not
        # run and the directory is populated by an earlier job. A *substring*
        # test let `0.2.0` match `0.2.0rc1` and `10.2.0`, so a stale prerelease
        # or a wrong wheel could be staged for the final tag; the version is
        # parsed out of the filename and compared exactly instead.
        self.assets = [p for p in candidates if _asset_version(p.name) == self.version]
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
        """Start the installed daemon, authenticate, load its UI, and stop it."""
        import socket

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
                    self._probe_installed_daemon(
                        python,
                        root,
                        env,
                        f"http://127.0.0.1:{port}/",
                    )
                    return f"served authenticated Web UI on 127.0.0.1:{port}"
                except ReleaseError as error:
                    # Deliberately contains no token or authenticated URL. The
                    # CLI bootstrap URL is a credential and must not leak into
                    # a release log just because readiness took another tick.
                    last = str(error)
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

    @staticmethod
    def _probe_installed_daemon(
        python: Path,
        root: Path,
        env: dict[str, str],
        base_url: str,
    ) -> None:
        """Prove liveness and load the authenticated installed Web UI.

        ``/`` is protected by the default token gate. Probing it without a
        credential therefore turns a healthy installed daemon into a permanent
        401 and made the local release pipeline time out. ``/health`` is the
        intentionally public liveness endpoint, but accepting it alone would
        weaken the packaging smoke to "a process answers JSON" and stop proving
        that the wheel actually ships a usable workbench.

        The installed CLI owns the token-file contract, so ask its ``url``
        command for the browser bootstrap URL rather than duplicating the
        filename here. A cookie-aware stdlib opener follows the 303 hand-off,
        then loads both the installed HTML shell and its JavaScript entrypoint.
        """
        import http.cookiejar
        import urllib.error
        import urllib.parse
        import urllib.request

        try:
            with urllib.request.urlopen(
                urllib.parse.urljoin(base_url, "health"), timeout=5
            ) as response:
                status = getattr(response, "status", 0)
                health = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError):
            raise ReleaseError(
                "installed daemon health endpoint is not ready"
            ) from None
        if not (200 <= status < 300) or health.get("status") != "ok":
            raise ReleaseError("installed daemon health endpoint is not ready")

        try:
            completed = subprocess.run(
                [str(python), "-I", "-m", "openai4s", "url"],
                cwd=str(root),
                env=env,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ReleaseError(
                "installed CLI could not report its Web UI URL"
            ) from None
        lines = [
            line.strip()
            for line in (completed.stdout or b"")
            .decode("utf-8", "replace")
            .splitlines()
            if line.strip()
        ]
        if completed.returncode != 0 or len(lines) != 1:
            raise ReleaseError("installed CLI could not report its Web UI URL")
        authenticated_url = lines[0]

        try:
            expected = urllib.parse.urlsplit(base_url)
            supplied = urllib.parse.urlsplit(authenticated_url)
            same_origin = (
                supplied.scheme == expected.scheme
                and supplied.hostname == expected.hostname
                and supplied.port == expected.port
            )
            query = urllib.parse.parse_qs(supplied.query)
        except ValueError:
            supplied = None
            same_origin = False
            query = {}
        if (
            not same_origin
            or supplied is None
            or supplied.path not in ("", "/")
            or not any(query.get("token", []))
        ):
            raise ReleaseError(
                "installed CLI did not return an authenticated URL for this daemon"
            )

        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        try:
            # The first GET exchanges ?token= for an HttpOnly cookie and follows
            # the 303 to the credential-free root page.
            with opener.open(authenticated_url, timeout=5) as response:
                html_status = getattr(response, "status", 0)
                html_type = str(response.headers.get("Content-Type", "")).lower()
                final_url = response.geturl()
                html = response.read()
            with opener.open(
                urllib.parse.urljoin(base_url, "static/app.js"), timeout=5
            ) as response:
                script_status = getattr(response, "status", 0)
                script_type = str(response.headers.get("Content-Type", "")).lower()
                script = response.read()
        except (OSError, urllib.error.URLError):
            # Never include the exception: HTTPError renders its URL, and the
            # bootstrap URL contains the daemon credential.
            raise ReleaseError("authenticated installed Web UI is not ready") from None

        if (
            not (200 <= html_status < 300)
            or "text/html" not in html_type
            or "token=" in final_url
            or b"<title>OpenAI4S</title>" not in html
            or b'id="dashboard"' not in html
            or b"static/app.js" not in html
        ):
            raise ReleaseError("installed daemon did not serve the Web UI shell")
        # The entrypoint is judged by serving facts: status, a JavaScript
        # content type, a non-empty body, and the shell above actually
        # referencing it. Never by source literals — asserting fragments like
        # `"use strict";` here turned an app.js style choice into a release
        # failure whose message points at packaging.
        if (
            not (200 <= script_status < 300)
            or "javascript" not in script_type
            or not script.strip()
        ):
            raise ReleaseError("installed daemon did not serve the Web UI application")

    def step_sbom(self) -> StepResult:
        if self.dry_run:
            return StepResult("sbom", True, f"would write {SBOM_NAME}")
        packages: list[dict[str, str]] = []
        for wheel in [a for a in self.assets if a.suffix == ".whl"]:
            packages.extend(wheel_components(wheel))
        embedded, unread = sidecar_components(self.assets)
        packages.extend(embedded)
        document = build_sbom(
            self.assets, version=self.version, packages=packages, unread=unread
        )
        target = self.assets_dir / SBOM_NAME
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

    def step_evidence(self) -> StepResult:
        """Seal the release's own claims, before anything leaves.

        This used to run after `publish`, wrapped in `except Exception: print(...)`
        and documented as best-effort so it "cannot fail a good release". Three
        things followed from that. A bundle sealed after the upload describes a
        release that has already gone out, so nothing could have been prevented
        by it. A seal that cannot fail is a seal that can be absent from a
        release nobody notices is missing one. And it was called with no `files=`
        argument at all, so the archive contained a single `release-report.json`
        and none of the artifacts, receipts or checksums it claimed to be
        evidence for.

        So it is a step: it runs before `checksums` (which then covers it) and
        before `upload`, and a failure stops the release. The bundle carries the
        frozen source SHA, the complete gate receipt including the attested
        check-run ids, every artifact digest, the builder platform/interpreter,
        the sandbox posture and the signing/notarization facts.
        """
        if self.dry_run:
            return StepResult("evidence", True, "would seal the evidence bundle")

        payload = self.report(planned=STEPS, sealing=True)
        carried = [
            path
            for path in (
                self.assets_dir / QUALITY_RECEIPT_NAME,
                # `step_sbom` writes `sbom.cdx.json`; this asked for
                # `sbom.spdx.json`, a file nothing in this repository has ever
                # produced. The `if path.is_file()` filter below then dropped it
                # without a word, so every evidence bundle shipped without the
                # SBOM while the step that built it reported success -- an
                # absence that reads, to anyone opening the zip, as "this
                # release has no SBOM" rather than "the collector asked for the
                # wrong name".
                self.assets_dir / SBOM_NAME,
                self.assets_dir / "provenance.intoto.json",
                *sorted(
                    self.assets_dir.glob(
                        f"{release_receipts.BUILD_RECEIPT_PREFIX}*.json"
                    )
                ),
                *sorted(self.assets_dir.glob(f"*{SIGNATURE_RECEIPT_SUFFIX}")),
            )
            if path.is_file()
        ]
        destination = self.assets_dir / f"openai4s-{self.version}-evidence.zip"
        try:
            manifest = seal_evidence_bundle(destination, payload, files=carried)
        except Exception as error:  # noqa: BLE001
            raise ReleaseError(
                f"could not seal the evidence bundle: {error}; refusing to publish "
                f"a release whose claims are not recorded"
            ) from error
        # Read it straight back with the product's own verifier. Sealing and
        # then never opening it is how a corrupt archive ships looking sealed.
        try:
            from openai4s import evidence as evidence_module

            verdict = evidence_module.verify_package(destination)
        except Exception as error:  # noqa: BLE001
            raise ReleaseError(
                f"the evidence bundle could not be verified after sealing: {error}"
            ) from error
        if not verdict.get("ok"):
            raise ReleaseError(
                "the evidence bundle failed its own verifier: "
                + "; ".join(verdict.get("problems") or ["no reason given"])
            )
        self.assets.append(destination)
        return StepResult(
            "evidence",
            True,
            destination.name,
            {
                "files": [entry["path"] for entry in manifest["files"]],
                "manifest_sha256": manifest["manifest_sha256"],
                "verified_by": "openai4s.evidence.verify_package",
            },
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
        receipts = sorted(
            self.assets_dir.glob(f"{release_receipts.BUILD_RECEIPT_PREFIX}*.json")
        )
        build_receipts: dict[str, Any] = {}
        if self.from_artifacts or self.mode == "release":
            # Staging is the only place every artifact is together, so it is the
            # only place "the wheel and the DMG are the same commit" can be
            # checked. Nothing checked it: each build job checked out the mutable
            # tag on its own.
            #
            # `or self.mode == "release"` because the binding is a property of
            # what is being published, not of how the bytes arrived. Guarded on
            # `from_artifacts` alone, a plain `--mode release` staged every asset
            # with no document tying any of them to the frozen commit -- the same
            # hole one layer down from the quality receipt above.
            try:
                build_receipts = release_receipts.verify_build_receipts(
                    receipts,
                    expected_sha=self._frozen_sha(),
                    assets_dir=self.assets_dir,
                    required_kinds=required_receipt_kinds(self.assets),
                )
            except release_receipts.ReceiptError as error:
                raise ReleaseError(str(error)) from error

        dmgs = [a for a in self.assets if a.suffix == ".dmg"]
        signatures = {dmg.name: read_signature(dmg, self._run) for dmg in dmgs}
        states = {name: signing_state(info) for name, info in signatures.items()}
        if self.mode == "release":
            # Two separate refusals, because they have different remedies.
            #
            # `unsigned` (ad-hoc or unreadable) is D11's original gate, unchanged.
            unsigned = sorted(
                name
                for name, info in signatures.items()
                if not info.get("developer_id")
            )
            if unsigned:
                raise ReleaseError(
                    f"no {DEVELOPER_ID_AUTHORITY} signature could be established "
                    f"for {unsigned}; refusing to publish. "
                    + json.dumps(signatures, sort_keys=True)
                )
            # `not_notarized` is the gap D11 left open. A Developer-ID signature
            # satisfied this gate, and `notarized` was hardcoded `None` and read
            # by nothing -- so once the signing certificate secret exists (the
            # workflow already imports it), a correctly signed but un-notarized
            # image publishes. Gatekeeper rejects that image on a user's machine,
            # which is the outcome a release gate is for.
            #
            # There is no downgrade path here on purpose: the remedy is to
            # notarize the image, or to not ship a DMG at all. An omitted macOS
            # asset is recorded below as `macos_asset: omitted`.
            not_notarized = sorted(
                name for name, state in states.items() if state != "verified"
            )
            if not_notarized:
                raise ReleaseError(
                    f"{not_notarized} carry a {DEVELOPER_ID_AUTHORITY} signature "
                    f"but no completed notarization, so Gatekeeper will refuse "
                    f"them on a user's machine. Notarize and staple the image, or "
                    f"omit the macOS asset from this release; a public release "
                    f"must not carry an un-notarized image. "
                    + json.dumps(states, sort_keys=True)
                )
        return StepResult(
            "verify",
            True,
            f"{len(self.assets)} asset(s) verified",
            {
                "signatures": signatures,
                # One named state per image, so a reader does not have to infer
                # it from four fields and get it wrong.
                "signing_states": states,
                "identity_configured": bool(
                    os.environ.get(SIGNING_IDENTITY_VAR, "").strip()
                ),
                # Read from evidence -- `xcrun stapler validate` on the image, via
                # the macOS job's receipt -- rather than asserted. It was
                # hardcoded `None` and gated nothing, which is how a
                # Developer-ID-signed, un-notarized image could publish.
                "notarized": {
                    name: bool(info.get("notarized"))
                    for name, info in signatures.items()
                },
                "post_staple_sha256": {
                    name: str(info.get("post_staple_sha256") or "")
                    for name, info in signatures.items()
                },
                "stapler_returncode": {
                    name: info.get("stapler_returncode")
                    for name, info in signatures.items()
                },
                "spctl_returncode": {
                    name: info.get("spctl_returncode")
                    for name, info in signatures.items()
                },
                # An absent DMG is a release with no macOS asset, which is a
                # supported outcome and the honest one while notarization
                # credentials do not exist. Named so a reader learns it here
                # rather than by noticing an absence. The workflow input
                # `macos_asset=omit` (default) is how that absence is produced
                # without uploading a preview image.
                "macos_asset": "present" if dmgs else "omitted",
                "macos_publishable": bool(dmgs)
                and all(state == "verified" for state in states.values()),
                "macos_publishable_note": (
                    "a public release requires every macOS image to be Developer-ID "
                    "signed AND notarized (stapled). Without notary credentials the "
                    "supported path is to omit the DMG, not to publish it labelled."
                ),
                "build_receipts": {
                    kind: {
                        "source_sha": document.get("source_sha", ""),
                        "builder": document.get("builder", {}),
                        "artifacts": [
                            row.get("name") for row in document.get("artifacts", [])
                        ],
                    }
                    for kind, document in build_receipts.items()
                },
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
        # Exact set, not just a superset. `gh release upload --clobber` overwrites
        # matching names but leaves anything extra in place, so an asset from an
        # earlier staging attempt would ride along — published without appearing
        # in checksums, provenance, or this read-back.
        unexpected = sorted(set(listing) - {a.name for a in self.assets})
        if unexpected:
            raise ReleaseError(
                f"the draft carries assets this release did not produce: "
                f"{unexpected}; they would be published unverified. Remove them "
                f"(likely a leftover from an earlier staging attempt) and re-run."
            )

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
        # The record the finalize job will compare the draft against. It has to
        # leave through a channel the draft does not control: `step_publish` used
        # to re-hash the draft against the draft's own `SHA256SUMS`, and anything
        # able to replace an asset can replace that manifest in the same motion.
        attestation = release_receipts.build_stage_attestation(
            version=self.version,
            source_sha=self._frozen_sha(),
            assets=[a for a in self.assets if a.is_file()],
        )
        target = self.assets_dir / release_receipts.STAGE_ATTESTATION_NAME
        target.write_text(json.dumps(attestation, indent=2, sort_keys=True), "utf-8")
        return StepResult(
            "reverify",
            True,
            f"{len(checked)} asset(s) re-hashed from the release",
            {
                "digests": checked,
                "attestation": target.name,
                "attested_assets": [row["name"] for row in attestation["assets"]],
            },
        )

    def _download_asset(self, name: str, dest_dir: Path) -> Path:
        """Pull one named asset from the draft, or fail loudly."""
        pulled = self._gh(
            [
                "release",
                "download",
                f"v{self.version}",
                "--pattern",
                name,
                "--dir",
                str(dest_dir),
                "--clobber",
            ]
        )
        if pulled.returncode != 0:
            raise ReleaseError(
                f"could not download {name} from the draft: "
                f"{(pulled.stderr or b'').decode('utf-8', 'replace')}"
            )
        path = dest_dir / name
        if not path.is_file():
            raise ReleaseError(f"{name} did not come back from the draft")
        return path

    def _revalidate_draft_from_checksums(self) -> dict[str, str]:
        """Re-hash the draft's current assets against an out-of-band attestation.

        ``step_publish`` runs standalone in the finalize job (``--only publish``),
        so it cannot trust in-process state from staging. It used to compare the
        draft against the draft's own ``SHA256SUMS`` -- a document that is itself
        one of the assets, so anything able to replace an asset could replace the
        manifest in the same motion and the check would pass. It was a document
        vouching for itself.

        The staging job's attestation travels through the workflow's artifact
        store instead, which the draft cannot reach. When it is present it is
        authoritative: the draft's asset set and every digest must match it
        exactly. ``SHA256SUMS`` is still cross-checked, so a disagreement between
        the two is itself a refusal rather than a silent preference.

        Without an attestation (a hand-run ``--only publish``) this falls back to
        the old self-referential check and says so, because refusing outright
        would remove the documented manual recovery path -- but it is a weaker
        claim and is reported as one.
        """
        completed = self._gh(
            ["release", "view", f"v{self.version}", "--json", "assets"]
        )
        if completed.returncode != 0:
            raise ReleaseError("could not read back the draft before publishing")
        try:
            remote = json.loads((completed.stdout or b"{}").decode("utf-8"))
        except ValueError as e:
            raise ReleaseError(f"the draft listing was not JSON: {e}") from e
        present = {str(item.get("name")) for item in (remote.get("assets") or [])}
        if "SHA256SUMS" not in present:
            raise ReleaseError(
                "the draft has no SHA256SUMS manifest; refusing to publish an "
                "unverifiable release"
            )

        attested: dict[str, str] | None = None
        if self.attestation is not None:
            if not self.attestation.is_file():
                raise ReleaseError(
                    f"no stage attestation at {self.attestation}; the finalize job "
                    f"cannot verify the draft against anything the draft does not "
                    f"itself contain"
                )
            try:
                attested = release_receipts.verify_stage_attestation(
                    self.attestation, version=self.version
                )
            except release_receipts.ReceiptError as error:
                raise ReleaseError(str(error)) from error
            unexpected = sorted(present - set(attested))
            missing_attested = sorted(set(attested) - present)
            if missing_attested or unexpected:
                raise ReleaseError(
                    f"the draft is not the release that was staged: missing "
                    f"{missing_attested}, unexpected {unexpected}; refusing to "
                    f"publish"
                )

        checked: dict[str, str] = {}
        with tempfile.TemporaryDirectory(prefix="openai4s-publish-verify-") as temp:
            sums = self._download_asset("SHA256SUMS", Path(temp))
            expected = parse_checksums(sums.read_text("utf-8"))
            if not expected:
                raise ReleaseError("SHA256SUMS was empty; nothing to verify")
            if attested is not None:
                # A replaced asset *and* a replaced manifest is the attack the
                # attestation exists for: the two would agree with each other and
                # disagree with this.
                drifted = sorted(
                    name
                    for name, digest in expected.items()
                    if name in attested and attested[name] != digest
                )
                if drifted:
                    raise ReleaseError(
                        f"the draft's SHA256SUMS disagrees with the staging "
                        f"attestation for {drifted}; both the asset and its "
                        f"manifest were changed after staging. Refusing to publish."
                    )
                # Digests are taken from the attestation, not the draft.
                expected = {
                    name: attested[name] for name in expected if name in attested
                }
            # Exact set: every covered asset plus the manifest itself, no more.
            want = set(expected) | {"SHA256SUMS"}
            missing = sorted(want - present)
            if missing:
                raise ReleaseError(
                    f"the draft is missing verified assets: {missing}; refusing "
                    f"to publish"
                )
            unexpected = sorted(present - want)
            if unexpected:
                raise ReleaseError(
                    f"the draft carries assets not covered by SHA256SUMS: "
                    f"{unexpected}; refusing to publish unverified bytes"
                )
            for name, digest in sorted(expected.items()):
                local = self._download_asset(name, Path(temp))
                actual = sha256_file(local)
                checked[name] = actual
                if actual != digest:
                    raise ReleaseError(
                        f"{name} in the draft no longer matches its verified "
                        f"digest ({actual[:12]} != {digest[:12]}); refusing to "
                        f"publish"
                    )
        return checked

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
        # Re-validate the draft against its own checksum manifest immediately
        # before the flip. `--only publish` runs standalone in the finalize job,
        # often after an approval delay, so it cannot trust in-process state from
        # staging: a draft asset deleted or replaced since attach would otherwise
        # be made public unverified. SHA256SUMS is the persisted digest manifest.
        #
        # Never rebuild here. A version number on a package index is taken
        # forever; different bytes under the same version are the failure
        # `pypi_digests` is about to catch. The macOS image's published digest
        # is the post-staple digest recorded at staging. A missing macOS or
        # Linux platform proof omits that platform's public asset rather than
        # labelling a preview.
        checked = self._revalidate_draft_from_checksums()
        # ...but that manifest comes from the same *mutable* draft, so a second
        # staging run for this tag that clobbered both the assets and SHA256SUMS
        # would still self-validate while PyPI already holds the first run's
        # bytes. PyPI is immutable per version, so it is the anchor: every Python
        # distribution the draft would publish must be on PyPI with a matching
        # digest. Only wheels and the sdist live on PyPI — the dmg, the Linux
        # tarball, the Windows zip, sbom, provenance and SHA256SUMS are
        # GitHub-only and are not expected there.
        #
        # The sdist is matched by exact name rather than by `.tar.gz`, because
        # it is no longer the only tarball on a draft: the Linux desktop bundle
        # is `OpenAI4S-<version>-linux-<arch>.tar.gz`. Suffix-matching swept it
        # into the anchor set, where it could never be satisfied — PyPI cannot
        # hold that filename — so `finalize` would refuse to flip the draft
        # *after* the immutable PyPI version had already been consumed.
        #
        # Case matters and is the whole reason this is `==` and not a
        # case-folded prefix test: PEP 625 names the sdist `openai4s-...` while
        # the bundle is `OpenAI4S-...`. The two differ only in case.
        sdist_name = f"openai4s-{self.version}.tar.gz"
        draft_dists = {
            name for name in checked if name.endswith(".whl") or name == sdist_name
        }
        published = self._pypi_digests("openai4s", self.version)
        if not published:
            # Fail closed: an empty response is not "everything matches". The
            # PyPI check above said the version exists, so no digests here means
            # the lookup failed or PyPI is mid-propagation — either way there is
            # nothing to anchor against, and the old `name in published` guard
            # would have skipped every file and published unverified.
            #
            # Unconditional, not `if draft_dists and not published`. A draft
            # rewritten to carry *no* distributions has an empty `draft_dists`,
            # and gating the anchor on it meant the one shape that most needs
            # anchoring was the one that skipped it.
            raise ReleaseError(
                f"PyPI returned no file digests for {self.version}; cannot "
                f"anchor the GitHub release to it. Refusing to publish."
            )
        # Exact filename equality, in *both* directions.
        #
        # `draft_dists - published` alone is a one-way check, and the direction
        # it misses is the dangerous one: if the mutable draft and its
        # SHA256SUMS are rewritten after upload to drop the wheel and/or the
        # sdist, `_revalidate_draft_from_checksums()` self-validates the
        # reduced set, this difference is empty, and the finalizer publishes a
        # GitHub release for a version whose distributions are simply absent —
        # while the immutable PyPI version says exactly what should have been
        # there. PyPI is the anchor precisely because it cannot be rewritten,
        # so the draft has to match it, not merely be a subset of it.
        missing_on_pypi = sorted(draft_dists - set(published))
        absent_from_draft = sorted(set(published) - draft_dists)
        if missing_on_pypi or absent_from_draft:
            parts = []
            if missing_on_pypi:
                # A partial upload (e.g. the wheel landed but not the sdist)
                # must not let the missing files ride onto GitHub unverified.
                parts.append(f"PyPI is missing {missing_on_pypi}")
            if absent_from_draft:
                parts.append(f"the draft is missing {absent_from_draft}")
            raise ReleaseError(
                f"the GitHub draft and PyPI do not carry the same "
                f"distributions for {self.version}: {'; '.join(parts)}. PyPI is "
                f"immutable and is the anchor, so the draft must hold exactly "
                f"what it holds — complete the upload or re-stage the draft, "
                f"then re-run `--only publish`."
            )
        divergent = sorted(
            f"{name}: github {checked[name][:12]} != pypi {published[name][:12]}"
            for name in draft_dists
            if checked[name] != published[name]
        )
        if divergent:
            raise ReleaseError(
                f"the draft's bytes disagree with what PyPI already published "
                f"for {self.version}: {divergent}. Refusing to publish two "
                f"channels with different bytes for one version — this tag was "
                f"most likely staged twice."
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
                failed = self.report(stopped_at=name, planned=planned)
                # A stopped run is the one somebody most wants the record of, so
                # it is still sealed -- but best-effort *here*, because the run
                # has already failed and a second failure cannot make it worse.
                # The good path seals through `step_evidence`, where a failure
                # does stop the release.
                self._seal_stopped_run(failed)
                return failed
            self.performed.append(name)
            self.results.append(result)
        return self.report(planned=planned)

    def _seal_stopped_run(self, report: Mapping[str, Any]) -> None:
        """Record a run that stopped, without being able to fail it further."""
        if self.dry_run:
            # `--dry-run` is documented as performing no external call and is how
            # the ordering is tested; a dry run that left a real bundle beside
            # the artifacts would be a dry run with a side effect.
            return
        try:
            destination = (
                self.assets_dir / f"openai4s-{self.version}-evidence-stopped.zip"
            )
            seal_evidence_bundle(destination, dict(report))
        except Exception as error:  # noqa: BLE001
            print(f"[release] could not seal the stopped run's evidence: {error}")

    def report(
        self,
        stopped_at: str | None = None,
        planned: Sequence[str] = STEPS,
        *,
        sealing: bool = False,
    ) -> dict[str, Any]:
        document: dict[str, Any] = {
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
        if sealing:
            # Only when sealing: `_frozen_sha` runs git and can raise, and the
            # ordinary report is also built on the failure path where raising
            # would replace the real reason with a git error.
            document["source_sha"] = self.source_sha or self._head_sha()
            document["builder"] = release_gates.builder_facts()
            document["artifacts"] = {
                asset.name: sha256_file(asset)
                for asset in sorted(self.assets)
                if asset.is_file()
            }
            document["sandbox"] = _sandbox_posture()
        return document


def seal_evidence_bundle(
    destination: Path, payload: Mapping[str, Any], *, files: Sequence[Path] = ()
) -> dict[str, Any]:
    """Write the release's own claims as a package `evidence.verify_package` reads.

    The pipeline printed a report to stdout. A report on stdout is evidence for
    whoever was watching the job; it is nothing at all to a person holding the
    artifacts a week later, which is the person a release's claims are for. So
    the same facts are sealed into the archive format this product already
    ships a verifier for, and that verifier is the product's own -- not a
    second implementation that could disagree with it.

    What it establishes is internal consistency: the manifest vouches for
    itself, every listed file matches its recorded hash, and nothing unlisted
    is present. It does **not** establish who produced the bundle. Signing is a
    separate question with a separate answer (`signing_state`), and conflating
    the two is how a "verified" archive smuggles something.
    """
    import zipfile

    destination.parent.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}

    report_bytes = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    contents["release-report.json"] = report_bytes
    for source in files:
        source = Path(source)
        if not source.is_file():
            continue
        contents[f"artifacts/{source.name}"] = source.read_bytes()

    for name, data in sorted(contents.items()):
        entries.append(
            {
                "path": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )

    manifest: dict[str, Any] = {
        "format": "openai4s-release-evidence",
        "schema_version": 1,
        "version": payload.get("version"),
        "files": entries,
    }
    # The manifest vouches for itself last, over everything else in it. Without
    # this an editor could rewrite a payload and its recorded hash together and
    # every per-file check would still pass.
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        for name, data in sorted(contents.items()):
            archive.writestr(name, data)
    return manifest


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


def _pypi_file_digests(project: str, version: str) -> dict[str, str]:
    """``{filename: sha256}`` for every file PyPI holds for this exact version.

    PyPI is immutable per version, which makes these digests the one anchor the
    finalize step can trust: the SHA256SUMS it re-hashes the draft against comes
    from that same *mutable* draft, so a second staging run that clobbered both
    the assets and the manifest would still self-validate. What PyPI already
    published cannot be rewritten, so it decides.
    """
    import json as _json
    import urllib.request

    url = f"https://pypi.org/pypi/{project}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = _json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - unreachable index adds no constraint
        return {}
    out: dict[str, str] = {}
    for item in payload.get("urls") or []:
        name = str(item.get("filename") or "")
        digest = str((item.get("digests") or {}).get("sha256") or "")
        if name and digest:
            out[name] = digest
    return out


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
    parser.add_argument(
        "--source-sha",
        default="",
        help=(
            "the full commit SHA the release workflow froze once, before any job "
            "checked anything out. Checked against this checkout rather than "
            "believed: a job holding a tag that has since moved is refused."
        ),
    )
    parser.add_argument(
        "--attestation",
        type=Path,
        default=None,
        help=(
            "the staging job's stage-attestation.json, delivered out of band. "
            "The finalize job compares the draft against this rather than "
            "against the draft's own SHA256SUMS."
        ),
    )
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
        source_sha=args.source_sha,
        attestation=args.attestation,
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
