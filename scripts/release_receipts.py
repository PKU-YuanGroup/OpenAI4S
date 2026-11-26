#!/usr/bin/env python3
"""Build receipts, and the attestation that survives a mutable draft.

Two documents, both answering a question the pipeline could not previously ask.

**Build receipt** — "which sources is this artifact from, and what built it?"
Every job in the release workflow checked out `inputs.tag` independently. A tag is
mutable, so the wheel, the sdist and the DMG could each come from a different
commit, and nothing recorded or compared where any of them came from. The quality
receipt binds the *gates* to a SHA; nothing bound the *bytes*. A receipt is
written beside each artifact by the job that built it, naming the source SHA, the
artifact digests, and the OS/arch/interpreter of the builder — and staging
verifies every one of them against the frozen SHA before it stages anything.

**Stage attestation** — "is the draft still the release that was verified?"
`step_publish` re-hashed the draft's assets against the draft's own `SHA256SUMS`.
`SHA256SUMS` is itself a draft asset, so anything able to replace an asset can
replace the manifest in the same motion and the check passes: it is a document
vouching for itself. The attestation is written by the staging job and travels
through the workflow's artifact store instead of through the draft, so the
finalize job compares the draft against a record the draft cannot reach.

Neither document is signed, and neither claims to establish *who* produced it.
They establish that a set of bytes, a commit and a builder go together.

**Executed runtime evidence** — schema 3 adds `runtime_smoke` to the Linux build
receipt. A digest proves the artifact bytes did not change; it says nothing
about whether those bytes can start a daemon, spawn a kernel, execute a Cell,
capture the resulting Artifact, read it back after a restart, or open it in a
browser. The Linux receipt therefore carries the appendix-B.4 report of
`scripts/installed_runtime_smoke.py`, which ran exactly that scenario inside the
packaged runtime and is bound to the tarball's own digest. `verify_build_receipts`
refuses a Linux receipt whose evidence is missing, was recorded as `not_run`,
failed, timed out, describes other bytes, or comes from an older scenario.
Without this a release could publish a package nothing ever executed.

**Upgrade readback** -- the same report may carry `upgrade_readback`: the
previous stable Linux bundle (pinned by name, version and SHA-256 in
`.github/workflows/release.yml`) wrote data that this tarball then migrated and
read back, and the pre-migration copy was read by the previous bundle again. A
Linux receipt for any release after 1.0.1 must carry it, bound to this
tarball's digest and to the pinned previous bundle. An unavailable previous
bundle, a wrong digest, a scenario that did not run and a failed check are
refused with different messages. Receipts for 1.0.1 and older are not asked
for it, so they stay verifiable.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

BUILD_RECEIPT_FORMAT = "openai4s-build-receipt"
#: Schema 3 adds the executed-runtime evidence (`runtime_smoke`).
#: An older receipt stays readable JSON but cannot satisfy this pipeline: it
#: would describe a package whose bytes nothing ever ran.
BUILD_RECEIPT_SCHEMA_VERSION = 3

#: Appendix B.4, scenario version 1. Every one of these checks must say
#: `passed` for a Linux receipt: `figure` because the bundle pre-bakes the
#: science stack, and `browser_open` because the release job runs the browser
#: layer, so a `not_run` there means the evidence was not collected.
RUNTIME_SMOKE_SCENARIO_VERSION = 1
RUNTIME_SMOKE_CHECKS = (
    "serve",
    "auth",
    "kernel_origin",
    "state_shared",
    "artifact_capture",
    "figure",
    "exact_version",
    "restart_readback",
    "browser_open",
    "clean_exit",
)
#: The only platform a Linux package receipt's runtime evidence may describe.
RUNTIME_SMOKE_PLATFORM = ("linux", "x86_64")

#: `runtime_smoke.upgrade_readback`: the previous stable Linux bundle wrote a
#: project, a session, three Cells and two versions of one CSV; the candidate
#: took over the same data directory, migrated it and read all of it back, and
#: the pre-migration copy was then read by the previous bundle again. Written by
#: `scripts/installed_runtime_smoke.py --baseline-bundle ... --baseline-sha256`.
UPGRADE_READBACK_SCENARIO_VERSION = 1
UPGRADE_READBACK_CHECKS = (
    "baseline_digest",
    "baseline_seed",
    "baseline_stop",
    "backup",
    "schema_before",
    "candidate_takeover",
    "schema_after",
    "project_session",
    "cell_records",
    "csv_versions",
    "candidate_restart",
    "restore_backup",
    "baseline_readback",
    "clean_exit",
)
#: Which release a receipt is for is read from the one name it always binds by
#: digest, `OpenAI4S-<version>-linux-<arch>.tar.gz`. 1.0.1 is the last release
#: whose Linux receipt predates `upgrade_readback` (and the first previous
#: bundle the gate starts from), so a receipt for 1.0.1 or older stays
#: verifiable without it and every later one must carry it. A version that
#: cannot be read is treated as later: it fails closed.
UPGRADE_READBACK_REQUIRED_AFTER = (1, 0, 1)
#: The previous stable bundle is pinned once, in the release workflow, as the
#: `linux-runtime-smoke` job's environment. The smoke step, this module and the
#: staging verifier all read the same three values.
RELEASE_WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"
)
UPGRADE_BASELINE_PIN_KEYS = {
    "name": "OPENAI4S_UPGRADE_BASELINE_NAME",
    "version": "OPENAI4S_UPGRADE_BASELINE_VERSION",
    "sha256": "OPENAI4S_UPGRADE_BASELINE_SHA256",
}
_RELEASE_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_LINUX_TARBALL_NAME = re.compile(
    r"^OpenAI4S-(?P<version>[^-/]+)-linux-(?P<arch>x86_64|aarch64)\.tar\.gz$"
)
_RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

#: The archive suffix that names the tarball a Linux receipt describes.
_LINUX_TARBALL = re.compile(r"-linux-(?P<arch>x86_64|aarch64)\.tar\.gz$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

STAGE_ATTESTATION_FORMAT = "openai4s-stage-attestation"
STAGE_ATTESTATION_SCHEMA_VERSION = 2
STAGE_ATTESTATION_NAME = "stage-attestation.json"

MACOS_ASSET_VALUES = frozenset({"omit", "notarized"})

#: Suffix for a per-artifact-group build receipt: `build-receipt-<kind>.json`.
BUILD_RECEIPT_PREFIX = "build-receipt-"


class ReceiptError(Exception):
    """A receipt or attestation is not proof. Do not release."""


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    if value in (0, 1):
        return bool(value)
    raise ReceiptError(f"workflow input is not a boolean: {value!r}")


def normalize_workflow_inputs(raw: Any) -> dict[str, Any]:
    """Closed-set dispatch inputs. Unknown keys are ignored, missing ones default."""
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ReceiptError("workflow inputs must be an object")
    macos_asset = str(raw.get("macos_asset") or "omit").strip() or "omit"
    if macos_asset not in MACOS_ASSET_VALUES:
        raise ReceiptError(
            f"macos_asset must be omit or notarized, got {macos_asset!r}"
        )
    return {
        "tag": str(raw.get("tag") or ""),
        "publish": _as_bool(raw.get("publish", False)),
        "pypi_only": _as_bool(raw.get("pypi_only", False)),
        "macos_asset": macos_asset,
    }


def normalize_notary(raw: Any, *, required: bool) -> dict[str, Any] | None:
    if raw is None:
        if required:
            raise ReceiptError("macos build receipt records no notary result")
        return None
    if not isinstance(raw, Mapping):
        raise ReceiptError("notary result is not an object")
    stapler = raw.get("stapler_returncode")
    spctl = raw.get("spctl_returncode")
    try:
        stapler_code = None if stapler is None else int(stapler)
        spctl_code = None if spctl is None else int(spctl)
    except (TypeError, ValueError) as error:
        raise ReceiptError(f"notary return codes are not integers: {error}") from error
    return {
        "requested": bool(raw.get("requested")),
        "submitted": bool(raw.get("submitted")),
        "stapled": bool(raw.get("stapled")),
        "stapler_returncode": stapler_code,
        "spctl_returncode": spctl_code,
        "post_staple_sha256": str(raw.get("post_staple_sha256") or ""),
    }


def notary_succeeded(notary: Mapping[str, Any] | None) -> bool:
    """A stapled ticket bound to the post-staple bytes.

    This is the same definition `describe_macos_image.py` writes as
    ``notarized`` and `release_pipeline.signing_state` reads: ``stapler
    validate`` succeeded and the digest is the image's. ``spctl`` is recorded
    beside it as the assessment a first launch performs, and it stays
    informational here too -- it is a Gatekeeper *policy* answer that depends
    on the runner's own state, and requiring it made this gate refuse an
    image the sibling gate had just called ``verified``.
    """
    if not isinstance(notary, Mapping):
        return False
    return bool(
        notary.get("requested")
        and notary.get("submitted")
        and notary.get("stapled")
        and notary.get("stapler_returncode") == 0
        and str(notary.get("post_staple_sha256") or "")
    )


def normalize_check_runs(raw: Any) -> list[dict[str, str]]:
    if raw in (None, ()):
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ReceiptError("check_runs must be a list")
    rows: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ReceiptError("a check_runs row is not an object")
        rows.append(
            {
                "name": str(item.get("name") or ""),
                "check_run_id": str(item.get("check_run_id") or ""),
                "head_sha": str(item.get("head_sha") or ""),
            }
        )
    return rows


def workflow_run_id_from(explicit: str = "") -> str:
    return str(explicit or os.environ.get("GITHUB_RUN_ID") or "").strip()


def verify_rehearsal(
    *,
    workflow_inputs: Mapping[str, Any],
    candidate_sha: str,
    expected_sha: str,
    workflow_run_id: str,
    dmg_count: int,
    notary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """A non-publish run's receipt. publish=true cannot masquerade as this."""
    inputs = normalize_workflow_inputs(workflow_inputs)
    if inputs["publish"]:
        raise ReceiptError("a publish=true run is not a rehearsal")
    if inputs["pypi_only"]:
        raise ReceiptError("pypi_only is not a rehearsal")
    if not expected_sha or candidate_sha != expected_sha:
        raise ReceiptError(
            f"rehearsal candidate {candidate_sha[:12] or '<none>'} is not "
            f"{expected_sha[:12] or '<none>'}"
        )
    if not workflow_run_id:
        raise ReceiptError("rehearsal receipt records no workflow run id")
    if inputs["macos_asset"] == "omit" and dmg_count:
        raise ReceiptError("macos_asset=omit cannot carry a DMG")
    if dmg_count and not notary_succeeded(notary):
        raise ReceiptError("no notarization success receipt; DMG count must be zero")
    return inputs


def _notary_from_codesign_sidecars(artifacts: Sequence[Path]) -> dict[str, Any] | None:
    """Read stapler/spctl evidence written beside a DMG by the macOS job."""
    for artifact in artifacts:
        sidecar = Path(str(artifact) + ".codesign.json")
        if not sidecar.is_file():
            continue
        try:
            payload = json.loads(sidecar.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, Mapping):
            continue
        notarized = bool(payload.get("notarized"))
        return {
            "requested": True,
            "submitted": notarized,
            "stapled": notarized,
            "stapler_returncode": payload.get("stapler_returncode"),
            "spctl_returncode": payload.get("spctl_returncode"),
            "post_staple_sha256": str(payload.get("post_staple_sha256") or ""),
        }
    return None


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _artifact_rows(artifacts: Iterable[Path]) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(Path(p) for p in artifacts):
        if not path.is_file():
            raise ReceiptError(f"cannot receipt {path}: not a file")
        rows.append(
            {"name": path.name, "sha256": _digest(path), "size": path.stat().st_size}
        )
    return rows


# --------------------------------------------------------------------------- #
# Upgrade readback: the previous stable bundle's data, opened by this release
# --------------------------------------------------------------------------- #
def _version_key(version: str) -> tuple[int, int, int] | None:
    matched = _RELEASE_VERSION.match(str(version or ""))
    if not matched:
        return None
    major, minor, patch = (int(part) for part in matched.groups())
    return major, minor, patch


def tarball_release(name: str) -> str:
    """The version a Linux tarball's file name declares, or ``""``."""
    matched = _LINUX_TARBALL_NAME.match(Path(str(name or "")).name)
    return matched.group("version") if matched else ""


def upgrade_readback_required(name: str) -> bool:
    """Whether a Linux receipt for this tarball must carry `upgrade_readback`."""
    key = _version_key(tarball_release(name))
    return key is None or key > UPGRADE_READBACK_REQUIRED_AFTER


def pinned_upgrade_baseline(workflow: Path | None = None) -> dict[str, str]:
    """The previous stable bundle the release workflow pins: name, version, SHA-256.

    Read as text, line by line: each key must be set exactly once as a YAML
    mapping entry, so a commented-out pin or a second, conflicting one is a
    refusal rather than a guess.
    """
    path = Path(workflow) if workflow is not None else RELEASE_WORKFLOW
    try:
        text = path.read_text("utf-8")
    except OSError as error:
        raise ReceiptError(
            f"the pinned upgrade baseline cannot be read from {path.name}: {error}"
        ) from error
    pin: dict[str, str] = {}
    for field, key in UPGRADE_BASELINE_PIN_KEYS.items():
        found = re.findall(
            rf"^[ \t]+{key}:[ \t]*[\"']?([^\"'\s#]+)[\"']?[ \t]*(?:#.*)?$",
            text,
            re.M,
        )
        if len(found) != 1:
            raise ReceiptError(
                f"{path.name} sets {key} {len(found)} time(s); the upgrade "
                "baseline must be pinned exactly once"
            )
        pin[field] = found[0]
    if not _SHA256.match(pin["sha256"]):
        raise ReceiptError(f"{path.name} pins no SHA-256 for the upgrade baseline")
    if _version_key(pin["version"]) is None:
        raise ReceiptError(
            f"{path.name} pins upgrade baseline version {pin['version']!r}, not "
            "MAJOR.MINOR.PATCH"
        )
    expected_name = (
        f"OpenAI4S-{pin['version']}-linux-{RUNTIME_SMOKE_PLATFORM[1]}.tar.gz"
    )
    if pin["name"] != expected_name:
        raise ReceiptError(
            f"{path.name} pins upgrade baseline {pin['name']!r}; version "
            f"{pin['version']} is {expected_name!r}"
        )
    return pin


def _bundle_side(raw: Any, role: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ReceiptError(f"upgrade readback records no {role} bundle")
    name = str(raw.get("name") or "")
    version = str(raw.get("version") or "")
    if not name or tarball_release(name) != version or not version:
        raise ReceiptError(
            f"upgrade readback names {role} bundle {name or '<none>'!r} with version "
            f"{version or '<none>'!r}; the name must be "
            "OpenAI4S-<version>-linux-x86_64.tar.gz for that version"
        )
    sha256 = str(raw.get("sha256") or "").lower()
    if not _SHA256.match(sha256):
        raise ReceiptError(f"upgrade readback records no {role} bundle SHA-256")
    schema = raw.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        raise ReceiptError(f"upgrade readback records no {role} schema")
    return {"name": name, "version": version, "sha256": sha256, "schema": schema}


def _upgrade_records(raw: Any) -> dict[str, Any]:
    """The ids and raw digests the acceptance record keeps, each one checked."""
    if not isinstance(raw, Mapping):
        raise ReceiptError("upgrade readback records no ids or digests")
    ids = {}
    for field in ("project_id", "frame_id", "artifact_id"):
        value = str(raw.get(field) or "")
        if not _RECORD_ID.match(value):
            raise ReceiptError(f"upgrade readback records no {field}")
        ids[field] = value
    history = raw.get("history_entries")
    cells = raw.get("cell_ids")
    if (
        not isinstance(history, int)
        or isinstance(history, bool)
        or not isinstance(cells, list)
        or len(cells) != history
        or history < 3
        or len(set(map(str, cells))) != len(cells)
        or not all(isinstance(cell, str) and _RECORD_ID.match(cell) for cell in cells)
    ):
        raise ReceiptError(
            "upgrade readback does not record the three Cells the previous bundle "
            "ran, each by its own id"
        )
    versions = raw.get("csv_versions")
    if not isinstance(versions, list) or len(versions) != 2:
        count = len(versions) if isinstance(versions, list) else 0
        raise ReceiptError(
            f"upgrade readback records {count} CSV version(s), expected both; a "
            "history version was lost"
        )
    rows = []
    for row in versions:
        if not isinstance(row, Mapping):
            raise ReceiptError("upgrade readback has a malformed CSV version row")
        version_id = str(row.get("version_id") or "")
        digest = str(row.get("sha256") or "").lower()
        size = row.get("size_bytes")
        if (
            not _RECORD_ID.match(version_id)
            or not _SHA256.match(digest)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise ReceiptError(
                "upgrade readback records a CSV version without its id, raw SHA-256 "
                "or size"
            )
        rows.append({"version_id": version_id, "sha256": digest, "size_bytes": size})
    if (
        len({row["version_id"] for row in rows}) != 2
        or len({row["sha256"] for row in rows}) != 2
    ):
        raise ReceiptError(
            "upgrade readback records the same CSV version twice; a history version "
            "was lost"
        )
    manifest = str(raw.get("backup_manifest_sha256") or "").lower()
    if not _SHA256.match(manifest):
        raise ReceiptError(
            "upgrade readback records no digest of the pre-migration copy"
        )
    return {
        **ids,
        "cell_ids": [str(cell) for cell in cells],
        "history_entries": history,
        "csv_versions": rows,
        "backup_manifest_sha256": manifest,
    }


def normalize_upgrade_readback(
    raw: Any,
    *,
    artifact_sha256: str,
    artifact_name: str | None = None,
    baseline_pin: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate `upgrade_readback` against the tarball and the pinned baseline.

    The refusals keep their causes apart, because each has a different remedy:
    the previous bundle could not be obtained, it was not the pinned bytes, the
    scenario was not run, or it ran and a check failed. Each blocks.

    ``baseline_pin`` defaults to the release workflow's pin. The previous
    bundle must be exactly that pin for a receipt whose version is newer than
    the pinned one -- the release the pin exists for. A receipt at or below the
    pinned version was written before the pin moved there, so only its own
    consistency is checked.
    """
    if not isinstance(raw, Mapping):
        raise ReceiptError("upgrade readback evidence is not an object")
    if raw.get("scenario_version") != UPGRADE_READBACK_SCENARIO_VERSION:
        raise ReceiptError(
            "upgrade readback has scenario_version "
            f"{raw.get('scenario_version')!r}, this gate requires "
            f"{UPGRADE_READBACK_SCENARIO_VERSION}"
        )
    status = str(raw.get("status") or "")
    failure = raw.get("failure") if isinstance(raw.get("failure"), Mapping) else {}
    detail = str(failure.get("detail") or "")
    if status == "baseline_unavailable":
        raise ReceiptError(
            "upgrade readback could not start: the previous stable Linux bundle was "
            f"unavailable ({detail or 'no detail'})"
        )
    if status == "baseline_digest_mismatch":
        raise ReceiptError(
            "upgrade readback refused the previous stable Linux bundle: its SHA-256 "
            f"is not the pinned one ({detail or 'no detail'})"
        )
    if status == "not_run":
        raise ReceiptError(f"upgrade readback was not run: {detail or 'no detail'}")
    if status != "passed":
        raise ReceiptError(
            "upgrade readback did not pass: "
            f"{failure.get('check', 'scenario')}: {detail}"
        )
    if raw.get("failure") is not None:
        raise ReceiptError(
            "upgrade readback reports a failure while its status is passed"
        )
    baseline = _bundle_side(raw.get("baseline"), "previous")
    candidate = _bundle_side(raw.get("candidate"), "candidate")
    expected = str(artifact_sha256 or "").lower()
    if candidate["sha256"] != expected:
        raise ReceiptError(
            f"upgrade readback describes candidate {candidate['sha256'][:12]}, not "
            f"the tarball being released ({expected[:12] or '<none>'})"
        )
    if artifact_name is not None and candidate["name"] != Path(artifact_name).name:
        raise ReceiptError(
            f"upgrade readback describes candidate {candidate['name']}, not "
            f"{Path(artifact_name).name}"
        )
    if baseline["sha256"] == candidate["sha256"]:
        raise ReceiptError(
            "upgrade readback used the candidate itself as the previous bundle"
        )
    # A previous bundle newer than the candidate is a downgrade, never evidence.
    # The same version is allowed only where the field is not required: that is
    # a rebuild of a released version rehearsing the scenario against itself.
    baseline_key = _version_key(baseline["version"])
    candidate_key = _version_key(candidate["version"])
    if baseline_key is None or candidate_key is None:
        raise ReceiptError(
            f"upgrade readback names versions {baseline['version']!r} → "
            f"{candidate['version']!r}; both must be MAJOR.MINOR.PATCH"
        )
    if baseline_key > candidate_key or (
        baseline_key == candidate_key and upgrade_readback_required(candidate["name"])
    ):
        raise ReceiptError(
            f"upgrade readback started from {baseline['version']}, which is not "
            f"older than the candidate {candidate['version']}"
        )
    pin = dict(baseline_pin) if baseline_pin is not None else pinned_upgrade_baseline()
    pin_key = _version_key(str(pin.get("version") or ""))
    if pin_key is None or candidate_key > pin_key:
        for field in ("name", "version", "sha256"):
            if baseline[field] != str(pin.get(field) or ""):
                raise ReceiptError(
                    f"upgrade readback started from previous bundle {field} "
                    f"{baseline[field]!r}, not the pinned {pin.get(field)!r}"
                )
    if candidate["schema"] < baseline["schema"]:
        raise ReceiptError(
            f"upgrade readback reports schema {baseline['schema']} → "
            f"{candidate['schema']}; a migration cannot go backwards"
        )
    checks = raw.get("checks")
    if not isinstance(checks, Mapping):
        raise ReceiptError("upgrade readback records no checks")
    canonical_checks: dict[str, str] = {}
    for name in UPGRADE_READBACK_CHECKS:
        value = str(checks.get(name) or "missing")
        if value != "passed":
            raise ReceiptError(
                f"upgrade readback check {name!r} is {value!r}, not 'passed'"
            )
        canonical_checks[name] = "passed"
    try:
        duration = float(raw.get("duration_s"))
    except (TypeError, ValueError):
        raise ReceiptError("upgrade readback records no duration") from None
    if not math.isfinite(duration) or duration < 0:
        raise ReceiptError(f"upgrade readback records no duration (got {duration!r})")
    return {
        "scenario_version": UPGRADE_READBACK_SCENARIO_VERSION,
        "status": "passed",
        "baseline": baseline,
        "candidate": candidate,
        "checks": canonical_checks,
        "records": _upgrade_records(raw.get("records")),
        "duration_s": round(duration, 3),
        "failure": None,
    }


# --------------------------------------------------------------------------- #
# Executed-runtime evidence (appendix B.4, schema 3)
# --------------------------------------------------------------------------- #
def normalize_runtime_smoke(
    raw: Any,
    *,
    artifact_sha256: str,
    artifact_name: str | None = None,
    upgrade_baseline: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """The appendix-B.4 runtime-smoke report, bound to the tarball being released.

    A digest proves the bytes did not change; this report is the executed
    evidence for them. Every frozen check must say ``passed`` — including
    ``figure`` (the Linux bundle pre-bakes the science stack) and
    ``browser_open`` (the release job runs the browser layer) — so a skip, a
    ``not_run`` or a timed-out run cannot pass. The report is canonicalised
    before storage: only the B.4 fields and the frozen check names survive, so a
    probe addition cannot smuggle an unbounded diagnostic into a receipt.

    ``upgrade_readback`` is kept, canonicalised the same way, whenever the
    report carries it. ``artifact_name`` is the tarball the receipt describes;
    given one whose version is after ``UPGRADE_READBACK_REQUIRED_AFTER``, a
    report without the field is refused. Without a name (a report checked
    outside a receipt) the field is validated when present and not demanded.
    """
    if not isinstance(raw, Mapping):
        raise ReceiptError("runtime smoke evidence is not an object")
    scenario_version = raw.get("scenario_version")
    if scenario_version != RUNTIME_SMOKE_SCENARIO_VERSION:
        raise ReceiptError(
            "runtime smoke evidence has scenario_version "
            f"{scenario_version!r}, this gate requires "
            f"{RUNTIME_SMOKE_SCENARIO_VERSION}; rerun the installed-runtime smoke"
        )
    status = str(raw.get("status") or "")
    if status == "not_run":
        raise ReceiptError(
            "runtime smoke evidence was not run; a Linux package receipt needs an "
            "executed run, not a skip"
        )
    if status != "passed":
        failure = raw.get("failure") if isinstance(raw.get("failure"), Mapping) else {}
        raise ReceiptError(
            "runtime smoke evidence did not pass: "
            f"{failure.get('check', 'scenario')}: {failure.get('detail', '')}"
        )
    if str(raw.get("target") or "") != "bundle":
        raise ReceiptError(
            "runtime smoke evidence was run against a "
            f"{raw.get('target')!r} runtime; a Linux package receipt requires the "
            "bundle run"
        )
    recorded = str(raw.get("artifact_sha256") or "").lower()
    if not _SHA256.match(recorded):
        raise ReceiptError("runtime smoke evidence records no artifact SHA-256")
    if recorded != str(artifact_sha256).lower():
        raise ReceiptError(
            "runtime smoke evidence is bound to artifact "
            f"{recorded[:12]}, not the tarball being released "
            f"({str(artifact_sha256).lower()[:12]})"
        )
    platform_name = str(raw.get("platform") or "").lower()
    arch = str(raw.get("arch") or "").lower()
    if (platform_name, arch) != RUNTIME_SMOKE_PLATFORM:
        raise ReceiptError(
            "runtime smoke evidence describes "
            f"{platform_name or '<none>'}/{arch or '<none>'}, not "
            f"{RUNTIME_SMOKE_PLATFORM[0]}/{RUNTIME_SMOKE_PLATFORM[1]}"
        )
    python = str(raw.get("python") or "")
    if not python:
        raise ReceiptError("runtime smoke evidence records no interpreter version")
    try:
        duration = float(raw.get("duration_s"))
    except (TypeError, ValueError):
        raise ReceiptError("runtime smoke evidence records no duration") from None
    if not math.isfinite(duration) or duration < 0:
        # NaN/Infinity would be written into the receipt as non-standard JSON
        # that strict verifiers refuse to parse.
        raise ReceiptError(
            f"runtime smoke evidence records no duration (got {duration!r})"
        )
    checks = raw.get("checks")
    if not isinstance(checks, Mapping):
        raise ReceiptError("runtime smoke evidence records no checks")
    canonical_checks: dict[str, str] = {}
    for name in RUNTIME_SMOKE_CHECKS:
        value = str(checks.get(name) or "missing")
        if value != "passed":
            raise ReceiptError(
                f"runtime smoke check {name!r} is {value!r}, not 'passed'"
            )
        canonical_checks[name] = "passed"
    if raw.get("failure") is not None:
        raise ReceiptError(
            "runtime smoke evidence reports a failure while status is passed"
        )
    document: dict[str, Any] = {
        "scenario_version": RUNTIME_SMOKE_SCENARIO_VERSION,
        "target": "bundle",
        "artifact_sha256": recorded,
        "platform": RUNTIME_SMOKE_PLATFORM[0],
        "arch": RUNTIME_SMOKE_PLATFORM[1],
        "python": python,
        "checks": canonical_checks,
        "status": "passed",
        "duration_s": round(duration, 3),
        "failure": None,
    }
    upgrade = raw.get("upgrade_readback")
    if upgrade is None:
        if artifact_name is not None and upgrade_readback_required(artifact_name):
            raise ReceiptError(
                f"runtime smoke evidence for {Path(artifact_name).name} carries no "
                "upgrade_readback; a Linux release after "
                f"{'.'.join(map(str, UPGRADE_READBACK_REQUIRED_AFTER))} must show "
                "that the previous stable bundle's data upgrades and reads back"
            )
    else:
        document["upgrade_readback"] = normalize_upgrade_readback(
            upgrade,
            artifact_sha256=recorded,
            artifact_name=artifact_name,
            baseline_pin=upgrade_baseline,
        )
    return document


def _require_smoke_arch(name: str) -> None:
    """The tarball must be the architecture its runtime evidence is pinned to.

    `normalize_runtime_smoke` checks what the *report* says it ran on; this
    checks what the *tarball* is, so an aarch64 bundle cannot borrow an x86_64
    run's evidence through a digest that merely happens to be the one listed.
    """
    matched = _LINUX_TARBALL.search(name)
    arch = matched.group("arch") if matched else ""
    if arch != RUNTIME_SMOKE_PLATFORM[1]:
        raise ReceiptError(
            f"{name} is a linux-{arch or '<unknown>'} tarball; runtime smoke "
            f"evidence is pinned to {RUNTIME_SMOKE_PLATFORM[0]}/"
            f"{RUNTIME_SMOKE_PLATFORM[1]}"
        )


def build_receipt_name(kind: str) -> str:
    return f"{BUILD_RECEIPT_PREFIX}{kind}.json"


def build_build_receipt(
    kind: str,
    source_sha: str,
    artifacts: Sequence[Path],
    *,
    workflow_run_id: str = "",
    workflow_inputs: Mapping[str, Any] | None = None,
    check_runs: Sequence[Mapping[str, Any]] = (),
    notary: Mapping[str, Any] | None = None,
    runtime_smoke: Mapping[str, Any] | None = None,
    upgrade_baseline: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """What one build job produced, and from what.

    `kind` names the job (`dist`, `macos`) so a release carrying several
    receipts can say which is missing rather than "a receipt is missing".
    For a notarized DMG this hashes the *post-staple* bytes: the macOS job
    staples first, then calls this, so the digest staging verifies is the
    digest a user downloads.

    Schema 2 also binds the candidate commit, the workflow run, the dispatch
    inputs, optional check-run ids, and — for a macOS image — the notary
    and staple result. Schema 3 additionally requires the `linux` receipt to
    carry the appendix-B.4 runtime-smoke report (`runtime_smoke`), bound to the
    tarball's own digest, because "the bytes did not change" is not "the bytes
    run". Old receipts remain readable JSON but cannot satisfy this pipeline:
    they describe a package nothing executed.

    For a tarball newer than ``UPGRADE_READBACK_REQUIRED_AFTER`` the report
    must also carry ``upgrade_readback`` for this tarball and for the pinned
    previous bundle (``upgrade_baseline``, by default the release workflow's
    pin). The receipt schema stays 3: the field is required by the version the
    receipt is for, so a receipt for an older release stays verifiable.
    """
    from scripts import release_gates

    if not source_sha:
        raise ReceiptError(
            "a build receipt with no source SHA binds nothing; the workflow must "
            "pass the frozen SHA"
        )
    document: dict[str, Any] = {
        "format": BUILD_RECEIPT_FORMAT,
        "schema_version": BUILD_RECEIPT_SCHEMA_VERSION,
        "kind": str(kind),
        "source_sha": str(source_sha),
        "candidate_sha": str(source_sha),
        "workflow_run_id": workflow_run_id_from(workflow_run_id),
        "workflow_inputs": normalize_workflow_inputs(workflow_inputs),
        "check_runs": normalize_check_runs(check_runs),
        "builder": release_gates.builder_facts(),
        "artifacts": _artifact_rows(artifacts),
    }
    if notary is None and str(kind) == "macos":
        notary = _notary_from_codesign_sidecars(artifacts)
    if notary is not None or str(kind) == "macos":
        recorded = normalize_notary(notary, required=False)
        if recorded is not None:
            document["notary"] = recorded
    if str(kind) == "linux":
        linux_artifacts = [
            Path(artifact)
            for artifact in artifacts
            if _LINUX_TARBALL.search(Path(artifact).name)
        ]
        if not linux_artifacts:
            raise ReceiptError(
                "a linux build receipt must describe the linux-x86_64/aarch64 "
                "tarball it was written for"
            )
        if len(linux_artifacts) != 1:
            # Verification requires exactly one; binding the evidence to
            # whichever tarball happened to be listed first would not be.
            raise ReceiptError(
                f"a linux build receipt describes {len(linux_artifacts)} Linux "
                "tarballs; it must describe exactly the one its runtime smoke ran"
            )
        linux_artifact = linux_artifacts[0]
        _require_smoke_arch(linux_artifact.name)
        if runtime_smoke is None:
            raise ReceiptError(
                f"a linux build receipt for {linux_artifact.name} must carry the "
                "installed-runtime smoke report (runtime_smoke); a package whose "
                "bytes were never executed cannot be released"
            )
        document["runtime_smoke"] = normalize_runtime_smoke(
            runtime_smoke,
            artifact_sha256=_digest(linux_artifact),
            artifact_name=linux_artifact.name,
            upgrade_baseline=upgrade_baseline,
        )
    elif runtime_smoke is not None:
        raise ReceiptError(
            f"a {kind} build receipt cannot carry runtime smoke evidence; it "
            "describes no Linux desktop package"
        )
    return document


def _verify_linux_smoke(
    document: Mapping[str, Any],
    receipt_path: Path,
    *,
    assets_dir: Path,
    digests: Mapping[str, str] | None,
    upgrade_baseline: Mapping[str, str] | None = None,
) -> None:
    """A Linux receipt must carry executed-runtime evidence for *these* bytes.

    The receipt is not itself hashed, so this re-checks the embedded report
    against the artifact row the outer loop already matched to a file on disk:
    the same tarball digest the report claims to describe, and -- for a release
    after the first pinned baseline -- the same candidate and pinned previous
    bundle in its upgrade readback.
    """
    rows = [
        row for row in (document.get("artifacts") or []) if isinstance(row, Mapping)
    ]
    linux_rows = [
        row for row in rows if _LINUX_TARBALL.search(str(row.get("name") or ""))
    ]
    if len(linux_rows) != 1:
        raise ReceiptError(
            f"build receipt {receipt_path.name} describes {len(linux_rows)} Linux "
            "tarball(s), expected exactly one"
        )
    name = str(linux_rows[0].get("name") or "")
    _require_smoke_arch(name)
    digest = (digests or {}).get(name) or _digest(Path(assets_dir) / name)
    if not isinstance(document.get("runtime_smoke"), Mapping):
        raise ReceiptError(
            f"build receipt {receipt_path.name} is for {name} but carries no "
            "runtime smoke evidence; the package bytes being published were never "
            "executed"
        )
    normalize_runtime_smoke(
        document.get("runtime_smoke"),
        artifact_sha256=digest,
        artifact_name=name,
        upgrade_baseline=upgrade_baseline,
    )


def verify_build_receipts(
    receipts: Sequence[Path],
    *,
    expected_sha: str,
    assets_dir: Path,
    required_kinds: Sequence[str] = (),
    digests: Mapping[str, str] | None = None,
    upgrade_baseline: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Every receipt must name the frozen SHA, and describe bytes that are here.

    The two halves matter separately. Checking only the SHA would accept a
    receipt for the right commit listing digests nothing on disk has; checking
    only the digests would accept an artifact built from a different commit.

    ``digests`` are SHA-256s the caller has already taken of the files in
    ``assets_dir``; a name found there is not read a second time.
    """
    if not expected_sha:
        raise ReceiptError("cannot verify build receipts without the frozen source SHA")
    seen: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(p) for p in receipts):
        try:
            document = json.loads(Path(path).read_text("utf-8"))
        except (OSError, ValueError) as error:
            raise ReceiptError(f"build receipt {path.name} is unreadable: {error}")
        if not isinstance(document, Mapping):
            raise ReceiptError(f"build receipt {path.name} is not an object")
        if document.get("format") != BUILD_RECEIPT_FORMAT:
            raise ReceiptError(f"build receipt {path.name} has an unrecognised format")
        if document.get("schema_version") != BUILD_RECEIPT_SCHEMA_VERSION:
            raise ReceiptError(
                f"build receipt {path.name} has schema_version "
                f"{document.get('schema_version')!r}, this pipeline requires "
                f"{BUILD_RECEIPT_SCHEMA_VERSION}"
            )
        kind = str(document.get("kind") or "")
        if not kind:
            raise ReceiptError(f"build receipt {path.name} names no kind")
        if kind in seen:
            raise ReceiptError(f"two build receipts claim kind {kind!r}")
        recorded = str(document.get("source_sha") or "")
        candidate = str(document.get("candidate_sha") or recorded)
        if candidate != recorded:
            raise ReceiptError(
                f"build receipt {path.name} candidate_sha {candidate[:12]} "
                f"disagrees with source_sha {recorded[:12]}"
            )
        if not str(document.get("workflow_run_id") or ""):
            raise ReceiptError(f"build receipt {path.name} records no workflow run id")
        if not isinstance(document.get("workflow_inputs"), Mapping):
            # The writer always records them; a receipt without them would
            # otherwise be normalized to the defaults and escape the
            # publish/pypi_only comparison staging performs.
            raise ReceiptError(f"build receipt {path.name} records no workflow inputs")
        try:
            normalize_workflow_inputs(document.get("workflow_inputs"))
        except ReceiptError as error:
            raise ReceiptError(f"build receipt {path.name} {error}") from error
        try:
            normalize_check_runs(document.get("check_runs") or [])
        except ReceiptError as error:
            raise ReceiptError(f"build receipt {path.name} {error}") from error
        if document.get("notary") is not None:
            try:
                normalize_notary(document.get("notary"), required=False)
            except ReceiptError as error:
                raise ReceiptError(f"build receipt {path.name} {error}") from error
        if recorded != expected_sha:
            # The retag case. The gates ran on one commit; this artifact was
            # built from another.
            raise ReceiptError(
                f"build receipt {path.name} is for {recorded[:12] or '<none>'} "
                f"but this release is {expected_sha[:12]}; the artifacts and the "
                f"sources are not the same commit"
            )
        builder = document.get("builder")
        if not isinstance(builder, Mapping) or not builder.get("os"):
            raise ReceiptError(f"build receipt {path.name} records no builder platform")
        rows = document.get("artifacts")
        if not isinstance(rows, list) or not rows:
            raise ReceiptError(f"build receipt {path.name} lists no artifacts")
        for row in rows:
            if not isinstance(row, Mapping):
                raise ReceiptError(f"build receipt {path.name} has a malformed row")
            name = str(row.get("name") or "")
            candidate = Path(assets_dir) / name
            if not candidate.is_file():
                raise ReceiptError(
                    f"build receipt {path.name} describes {name}, which is not "
                    f"among the assets being staged"
                )
            actual = (digests or {}).get(name) or _digest(candidate)
            if actual != str(row.get("sha256") or ""):
                raise ReceiptError(
                    f"{name} does not match its build receipt "
                    f"({actual[:12]} != {str(row.get('sha256'))[:12]}); the bytes "
                    f"changed after they were built"
                )
        if kind == "linux":
            _verify_linux_smoke(
                document,
                Path(path),
                assets_dir=Path(assets_dir),
                digests=digests,
                upgrade_baseline=upgrade_baseline,
            )
        seen[kind] = dict(document)

    missing = sorted(set(required_kinds) - set(seen))
    if missing:
        raise ReceiptError(
            f"no build receipt for: {', '.join(missing)}; an artifact with no "
            f"receipt cannot be bound to these sources"
        )
    return seen


def build_stage_attestation(
    *,
    version: str,
    source_sha: str,
    assets: Sequence[Path],
    workflow_run_id: str = "",
    workflow_inputs: Mapping[str, Any] | None = None,
    check_runs: Sequence[Mapping[str, Any]] = (),
    linux_boundary: Mapping[str, Any] | None = None,
    notary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The exact asset set the staging job verified, recorded outside the draft."""
    from scripts import release_gates

    if not source_sha:
        raise ReceiptError("a stage attestation with no source SHA binds nothing")
    rows = _artifact_rows(assets)
    if not rows:
        raise ReceiptError("a stage attestation with no assets binds nothing")
    document: dict[str, Any] = {
        "format": STAGE_ATTESTATION_FORMAT,
        "schema_version": STAGE_ATTESTATION_SCHEMA_VERSION,
        "version": str(version),
        "source_sha": str(source_sha),
        "candidate_sha": str(source_sha),
        "workflow_run_id": workflow_run_id_from(workflow_run_id),
        "workflow_inputs": normalize_workflow_inputs(workflow_inputs),
        "check_runs": normalize_check_runs(check_runs),
        "attested_by": release_gates.builder_facts(),
        "assets": rows,
    }
    if linux_boundary is not None:
        try:
            document["linux_boundary"] = release_gates.verify_linux_boundary(
                linux_boundary
            )
        except release_gates.GateManifestError as error:
            raise ReceiptError(str(error)) from error
    if notary is not None:
        document["notary"] = normalize_notary(notary, required=False)
    return document


def verify_stage_attestation(
    path: Path,
    *,
    version: str,
) -> dict[str, str]:
    """Read the attestation and return the asset set it vouches for.

    Returns `{name: sha256}` rather than a verdict; the caller compares it to
    what the draft currently holds. Keeping the comparison at the call site is
    deliberate — the finalize job needs to report *which* asset drifted.
    """
    try:
        document = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as error:
        raise ReceiptError(f"stage attestation is unreadable: {error}")
    if not isinstance(document, Mapping):
        raise ReceiptError("stage attestation is not an object")
    if document.get("format") != STAGE_ATTESTATION_FORMAT:
        raise ReceiptError("stage attestation has an unrecognised format")
    if document.get("schema_version") != STAGE_ATTESTATION_SCHEMA_VERSION:
        raise ReceiptError(
            f"stage attestation has schema_version "
            f"{document.get('schema_version')!r}, this pipeline requires "
            f"{STAGE_ATTESTATION_SCHEMA_VERSION}"
        )
    if str(document.get("version") or "") != str(version):
        raise ReceiptError(
            f"stage attestation is for version {document.get('version')!r}, not "
            f"{version!r}"
        )
    # Both facts, each on its own: `or`-ing them together made the run-id
    # requirement vacuous, because a schema-2 attestation always carries a
    # source SHA, so an attestation binding no workflow run passed.
    if not str(document.get("workflow_run_id") or ""):
        raise ReceiptError("stage attestation records no workflow run id")
    candidate = str(document.get("candidate_sha") or document.get("source_sha") or "")
    if not candidate:
        raise ReceiptError("stage attestation records no candidate commit")
    if document.get("workflow_inputs") is not None:
        try:
            normalize_workflow_inputs(document.get("workflow_inputs"))
        except ReceiptError as error:
            raise ReceiptError(f"stage attestation {error}") from error
    if document.get("linux_boundary") is not None:
        from scripts import release_gates

        try:
            release_gates.verify_linux_boundary(document.get("linux_boundary"))
        except release_gates.GateManifestError as error:
            raise ReceiptError(f"stage attestation {error}") from error
    rows = document.get("assets")
    if not isinstance(rows, list) or not rows:
        raise ReceiptError("stage attestation lists no assets")
    digests: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ReceiptError("stage attestation has a malformed asset row")
        name = str(row.get("name") or "")
        digest = str(row.get("sha256") or "")
        if not name or not digest:
            raise ReceiptError("stage attestation has an asset with no name or digest")
        if name in digests:
            raise ReceiptError(f"stage attestation lists {name} twice")
        digests[name] = digest
    return digests


def main(argv: Sequence[str] | None = None) -> int:
    """Write a build receipt for the artifacts a build job just produced.

        python scripts/release_receipts.py --kind dist --source-sha "$SHA" dist/*.whl

    Called by the wheel/sdist and macOS jobs. `--source-sha` is the workflow's
    frozen SHA rather than `git rev-parse HEAD`, because the point of the receipt
    is to let staging compare what a job *thought* it was building against the one
    commit the release was frozen at.
    """
    import argparse
    import sys

    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--kind",
        required=True,
        help=(
            "dist | macos | linux | windows. A linux receipt also needs "
            "--runtime-smoke <report.json>"
        ),
    )
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--workflow-run-id", default="")
    parser.add_argument(
        "--workflow-inputs-json",
        default="",
        help="dispatch inputs as JSON: tag, publish, pypi_only, macos_asset",
    )
    parser.add_argument(
        "--notary-json",
        default="",
        help="notary/staple result as JSON (macos kind)",
    )
    parser.add_argument(
        "--check-runs-json",
        default="",
        help="check-run identifiers as a JSON list",
    )
    parser.add_argument(
        "--runtime-smoke",
        type=Path,
        default=None,
        help=(
            "the appendix-B.4 runtime smoke report (required for --kind linux); "
            "see scripts/installed_runtime_smoke.py"
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)

    inputs = None
    if args.workflow_inputs_json:
        try:
            inputs = json.loads(args.workflow_inputs_json)
        except ValueError as error:
            print(
                f"::error::workflow-inputs-json is not JSON: {error}", file=sys.stderr
            )
            return 2
    elif os.environ.get("OPENAI4S_RELEASE_INPUTS_JSON"):
        try:
            inputs = json.loads(os.environ["OPENAI4S_RELEASE_INPUTS_JSON"])
        except ValueError as error:
            print(
                f"::error::OPENAI4S_RELEASE_INPUTS_JSON is not JSON: {error}",
                file=sys.stderr,
            )
            return 2
    notary = None
    if args.notary_json:
        try:
            notary = json.loads(args.notary_json)
        except ValueError as error:
            print(f"::error::notary-json is not JSON: {error}", file=sys.stderr)
            return 2
    check_runs: Sequence[Mapping[str, Any]] = ()
    if args.check_runs_json:
        try:
            loaded = json.loads(args.check_runs_json)
        except ValueError as error:
            print(f"::error::check-runs-json is not JSON: {error}", file=sys.stderr)
            return 2
        if not isinstance(loaded, list):
            print("::error::check-runs-json must be a list", file=sys.stderr)
            return 2
        check_runs = loaded
    runtime_smoke = None
    if args.runtime_smoke is not None:
        try:
            runtime_smoke = json.loads(Path(args.runtime_smoke).read_text("utf-8"))
        except (OSError, ValueError) as error:
            print(
                f"::error::the runtime smoke report is unreadable: {error}",
                file=sys.stderr,
            )
            return 2

    try:
        document = build_build_receipt(
            args.kind,
            args.source_sha,
            args.artifacts,
            workflow_run_id=args.workflow_run_id,
            workflow_inputs=inputs,
            check_runs=check_runs,
            notary=notary,
            runtime_smoke=runtime_smoke,
        )
    except ReceiptError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 2
    out_dir = args.output_dir or Path(args.artifacts[0]).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / build_receipt_name(args.kind)
    target.write_text(json.dumps(document, indent=2, sort_keys=True), "utf-8")
    print(
        f"{target.name}: {len(document['artifacts'])} artifact(s) bound to "
        f"{document['source_sha'][:12]} built on "
        f"{document['builder']['os']}/{document['builder']['arch']} "
        f"python {document['builder']['interpreter_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
