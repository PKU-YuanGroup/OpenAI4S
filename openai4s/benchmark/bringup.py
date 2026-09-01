"""Verification of a frozen tool bring-up record.

The stdlib-only verifier binds a strict sealed record to confined adapter and
weight snapshots, an immutable environment generation, one canary result, its
downstream consumption proof, and cumulative attempt/runtime/cost evidence.
The self-seal establishes internal consistency, not authorship: admission also
requires the evaluator's independently supplied exact weight digest set.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

SCHEMA_VERSION = 1
BRINGUP_FILENAME = "bringup.json"
#: The directory, relative to the submission root, where the record and the
#: canary/downstream artifacts live.
RECORD_DIR = "bringup"

#: Read size for hashing. Weights are routinely hundreds of megabytes, so the
#: file is streamed rather than read into memory (compute/manifest.hash_file).
_CHUNK = 1024 * 1024
_MAX_JSON_BYTES = 8 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CANARY_FIELDS = ("target", "sequence", "plddt", "weights_sha256")
_DOWNSTREAM_FIELDS = (
    "consumer",
    "target",
    "sequence",
    "plddt",
    "consumed_weights_sha256",
)


class BringupError(ValueError):
    """The record is missing, unreadable, or not a bring-up record at all."""


@dataclass(frozen=True)
class _Snapshot:
    """One stable read of a confined regular file."""

    path: Path
    sha256: str
    size: int
    identity: tuple[str | int, ...]
    data: bytes | None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(payload: Any) -> bytes:
    """The canonical semantic representation sealed by ``seal_record``."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r} is not permitted")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _strict_json(data: bytes) -> Any:
    text = data.decode("utf-8", errors="strict")
    return json.loads(
        text,
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicate_keys,
    )


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sha256_string(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _finite_nonnegative(value: Any) -> bool:
    if type(value) is int:
        return value >= 0
    return type(value) is float and math.isfinite(value) and value >= 0


def _numbers_match(left: Any, right: Any) -> bool:
    if type(left) not in (int, float) or type(right) not in (int, float):
        return False
    if type(left) is int and type(right) is int:
        return left == right
    try:
        return math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)
    except (OverflowError, TypeError, ValueError):
        return False


def _sum_finite_numbers(values: list[int | float]) -> int | float | None:
    try:
        total = sum(values)
    except OverflowError:
        return None
    if type(total) is float and not math.isfinite(total):
        return None
    return total


def _segment_ok(value: Any, *, generation: bool = False) -> bool:
    """One valid environment-store path component."""

    if not isinstance(value, str):
        return False
    if generation:
        return _GENERATION_RE.fullmatch(value) is not None
    return (
        bool(value)
        and value not in (".", "..")
        and not value.startswith(".")
        and not any(marker in value for marker in ("/", "\\", "\x00"))
    )


def _declared_path(root: Path, rel: Any) -> Path | None:
    """Turn one portable record-relative path into a candidate under root."""

    if not isinstance(rel, str) or not rel or "\x00" in rel or "\\" in rel:
        return None
    path = Path(rel)
    windows_path = PureWindowsPath(rel)
    if path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        return None
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return root.joinpath(*parts)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _stat_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _artifact_identity(path: Path, info: os.stat_result) -> tuple[str | int, ...]:
    """A same-physical-file key, with a path fallback for zero-inode hosts."""

    if info.st_ino:
        return ("inode", info.st_dev, info.st_ino)
    return ("path", os.path.normcase(str(path)))


def _snapshot_path(
    root: Path, candidate: Path, *, keep_data: bool
) -> tuple[_Snapshot | None, str]:
    """Read one confined regular-file snapshot through a single descriptor."""

    try:
        anchor = root.resolve(strict=True)
        if not anchor.is_dir():
            return None, f"submission root is not a directory: {root}"
        requested = candidate if candidate.is_absolute() else root / candidate
        resolved = requested.resolve(strict=True)
    except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
        return None, f"file is missing or cannot be resolved: {exc}"
    if not _is_within(resolved, anchor):
        return None, f"path escapes the submission root: {candidate}"

    # Open non-blocking until fstat proves this is a regular file. Without it,
    # an untrusted FIFO path can stall the verifier forever before the regular-
    # file check has a chance to reject it. O_NONBLOCK is inert for regular
    # files and absent platforms fall back to their normal open semantics.
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
    except (OSError, UnicodeError, ValueError) as exc:
        return None, f"file cannot be opened: {exc}"

    digest = hashlib.sha256()
    chunks: list[bytes] | None = [] if keep_data else None
    retained_size = 0
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                return None, "path is not a regular file"
            while True:
                chunk = handle.read(_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                if chunks is not None:
                    chunks.append(chunk)
                    retained_size += len(chunk)
                    if retained_size > _MAX_JSON_BYTES:
                        return None, "JSON-bearing file exceeds the 8 MiB limit"
            after = os.fstat(handle.fileno())
            if _stat_identity(before) != _stat_identity(after):
                return None, "file changed while it was being read"
    except (OSError, UnicodeError, ValueError) as exc:
        return None, f"file cannot be read: {exc}"

    try:
        final_resolved = requested.resolve(strict=True)
        if not _is_within(final_resolved, anchor):
            return None, "path escaped the submission root while it was read"
        final_stat = os.stat(final_resolved, follow_symlinks=False)
    except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
        return None, f"file changed after it was read: {exc}"
    if _stat_identity(after) != _stat_identity(final_stat):
        return None, "file identity changed while it was being verified"

    return (
        _Snapshot(
            path=final_resolved,
            sha256=digest.hexdigest(),
            size=after.st_size,
            identity=_artifact_identity(final_resolved, after),
            data=b"".join(chunks) if chunks is not None else None,
        ),
        "",
    )


def _snapshot_declared(
    root: Path, rel: Any, *, keep_data: bool
) -> tuple[_Snapshot | None, str]:
    candidate = _declared_path(root, rel)
    if candidate is None:
        return None, f"invalid record-relative path: {rel!r}"
    return _snapshot_path(root, candidate, keep_data=keep_data)


def _parse_snapshot(snapshot: _Snapshot) -> tuple[Any | None, str]:
    try:
        if snapshot.data is None:
            return None, "snapshot bytes were not retained"
        return _strict_json(snapshot.data), ""
    except (RecursionError, UnicodeError, ValueError) as exc:
        return None, str(exc)


def seal_record(record: dict[str, Any]) -> dict[str, Any]:
    """Inject or refresh ``record_sha256`` over the canonical record body.

    Public so that a test (or an evaluator building a fixture) can seal a
    record with exactly the same serialisation the verifier re-hashes — the
    exporter/verifier split that would let the two drift is not made.
    """
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    sealed = dict(record)
    sealed["record_sha256"] = _sha256(_canonical_json(body))
    return sealed


def verify_bringup(
    root: Path,
    record_path: Path | None = None,
    *,
    expected_weights: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Validate a frozen bring-up record. Returns a structured report.

    Never raises for a *failed* verification — only for input that is not a
    record at all (missing file, unreadable, non-JSON, non-object). A caller
    deciding whether to admit a tool needs the list of problems, not one
    exception naming whichever happened to be found first.

    ``expected_weights`` maps record-relative weights paths to the digests
    the evaluator froze from the reference build. Without it the verifier can
    only establish internal consistency; with it, a re-sealed forgery and an
    honest download of the wrong weights are both caught.
    """
    try:
        root = Path(root).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise BringupError(f"invalid submission root {root}: {exc}") from exc
    try:
        path = (
            root / RECORD_DIR / BRINGUP_FILENAME
            if record_path is None
            else Path(record_path)
        )
    except (TypeError, ValueError) as exc:
        raise BringupError(f"invalid bringup record path: {exc}") from exc
    if not path.is_absolute():
        path = root / path

    problems: list[str] = []
    checks: list[dict[str, Any]] = []
    artifact_identities: dict[tuple[str | int, ...], str] = {}

    def emit(
        check_id: str, ok: bool, detail: str = "", *, blocking: bool = True
    ) -> None:
        checks.append({"id": check_id, "ok": ok, "detail": detail})
        if blocking and not ok:
            problems.append(f"{check_id}: {detail}")

    def claim_artifact(snapshot: _Snapshot, role: str) -> str:
        """Register a physical file role and reject symlink/hardlink aliases."""

        previous = artifact_identities.get(snapshot.identity)
        if previous is not None:
            return f"{role} aliases the same physical file as {previous}"
        artifact_identities[snapshot.identity] = role
        return ""

    # The record must be a record before any of its fields mean anything.
    # Everything below reports through `problems`; this is the one thing that
    # raises, because a verifier that cannot find the record cannot produce a
    # report about it.
    record_snapshot, record_error = _snapshot_path(root, path, keep_data=True)
    if record_snapshot is None:
        raise BringupError(f"no bringup record at {path}: {record_error}")
    try:
        record = _strict_json(record_snapshot.data or b"")
    except (RecursionError, UnicodeError, ValueError) as e:
        raise BringupError(f"bringup record {path} is not JSON (strict): {e}") from e
    if not isinstance(record, dict):
        raise BringupError(f"bringup record {path} is not a JSON object")
    artifact_identities[record_snapshot.identity] = "the bring-up record"
    emit("record", True, str(record_snapshot.path))

    # schema_version
    version = record.get("schema_version")
    version_ok = type(version) is int and version == SCHEMA_VERSION
    emit(
        "schema_version",
        version_ok,
        (
            f"declares {version!r}, expected integer {SCHEMA_VERSION}"
            if not version_ok
            else str(version)
        ),
    )

    # self_vouch — the record must vouch for itself before its contents mean
    # anything: without this, an editor could rewrite a payload and its
    # recorded digest together and every per-file check would still pass.
    recorded_hash = record.get("record_sha256")
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    try:
        actual = _sha256(_canonical_json(body))
    except (RecursionError, TypeError, UnicodeError, ValueError) as exc:
        actual = ""
        seal_error = f"record body cannot be canonically sealed: {exc}"
    else:
        seal_error = "record_sha256 missing, malformed, or does not match the body"
    seal_ok = _sha256_string(recorded_hash) and recorded_hash == actual
    emit(
        "self_vouch",
        seal_ok,
        actual if seal_ok else seal_error,
    )

    # tool — what was brought up, and by which adapter.
    tool = record.get("tool")
    tool_dict = tool if isinstance(tool, dict) else {}
    invalid_tool = [
        field
        for field in ("name", "version", "source", "revision")
        if not _nonempty_string(tool_dict.get(field))
    ]
    if not isinstance(tool_dict.get("adapter"), dict):
        invalid_tool.append("adapter")
    emit(
        "tool",
        not invalid_tool,
        (
            str(tool_dict.get("name"))
            if not invalid_tool
            else "missing or invalid required fields: " + ", ".join(invalid_tool)
        ),
    )

    adapter = tool_dict.get("adapter")
    adapter_dict = adapter if isinstance(adapter, dict) else {}
    adapter_path = adapter_dict.get("path")
    adapter_sha = adapter_dict.get("sha256")
    adapter_size = adapter_dict.get("size")
    adapter_issues: list[str] = []
    if _declared_path(root, adapter_path) is None:
        adapter_issues.append("path is missing or invalid")
    if not _sha256_string(adapter_sha):
        adapter_issues.append("sha256 is not 64 lowercase hex characters")
    if not _nonnegative_int(adapter_size):
        adapter_issues.append("size is not a non-negative integer")
    if not adapter_issues:
        adapter_snapshot, error = _snapshot_declared(
            root, adapter_path, keep_data=False
        )
        if adapter_snapshot is None:
            adapter_issues.append(error)
        else:
            alias = claim_artifact(adapter_snapshot, "the adapter")
            if alias:
                adapter_issues.append(alias)
            if adapter_snapshot.sha256 != adapter_sha:
                adapter_issues.append("content hash mismatch")
            if adapter_snapshot.size != adapter_size:
                adapter_issues.append(
                    f"size {adapter_snapshot.size} does not match {adapter_size}"
                )
    emit("adapter", not adapter_issues, "; ".join(adapter_issues))

    # env_generation — bind the manifest to this environment and generation,
    # and require the immutable prefix it names to remain confined and present.
    env_name = tool_dict.get("env_name")
    env_generation = tool_dict.get("env_generation")
    env_issues: list[str] = []
    if not _segment_ok(env_name):
        env_issues.append("env_name is missing or invalid")
    if not _segment_ok(env_generation, generation=True):
        env_issues.append("env_generation is missing or invalid")
    if not env_issues:
        generation_dir = (
            root / "environments" / env_name / "generations" / env_generation
        )
        layout_paths = (
            root / "environments" / env_name,
            root / "environments" / env_name / "generations",
            generation_dir,
        )
        try:
            # Path.is_symlink() suppresses every OSError on CPython 3.14, so
            # an unreadable layout is indistinguishable from a non-symlink.
            # lstat preserves the verifier's fail-closed error boundary.
            layout_has_symlink = any(
                stat.S_ISLNK(os.lstat(path).st_mode) for path in layout_paths
            )
        except (OSError, RuntimeError, ValueError) as exc:
            env_issues.append(
                f"environment generation layout cannot be inspected: {exc}"
            )
        else:
            if layout_has_symlink:
                env_issues.append("environment generation layout contains a symlink")
        manifest_rel = (
            f"environments/{env_name}/generations/{env_generation}/manifest.json"
        )
        manifest_snapshot, error = _snapshot_declared(
            root, manifest_rel, keep_data=True
        )
        if manifest_snapshot is None:
            env_issues.append(error)
        else:
            alias = claim_artifact(manifest_snapshot, "the generation manifest")
            if alias:
                env_issues.append(alias)
            manifest, parse_error = _parse_snapshot(manifest_snapshot)
            if not isinstance(manifest, dict):
                env_issues.append(
                    "generation manifest is not a strict JSON object"
                    + (f": {parse_error}" if parse_error else "")
                )
            else:
                if manifest.get("generation_id") != env_generation:
                    env_issues.append(
                        "manifest generation_id does not match the record"
                    )
                if manifest.get("environment") != env_name:
                    env_issues.append("manifest environment does not match the record")
                state = manifest.get("state")
                if state not in ("ready", "superseded"):
                    env_issues.append(
                        f"generation manifest state is {state!r}, expected ready or superseded"
                    )
                prefix_value = manifest.get("prefix")
                if not _nonempty_string(prefix_value) or "\x00" in prefix_value:
                    env_issues.append("manifest prefix is missing or invalid")
                else:
                    prefix = Path(prefix_value)
                    if not prefix.is_absolute():
                        prefix = generation_dir / prefix
                    try:
                        resolved_generation = generation_dir.resolve(strict=True)
                        resolved_prefix = prefix.resolve(strict=True)
                    except (OSError, RuntimeError, ValueError) as exc:
                        env_issues.append(f"generation prefix is missing: {exc}")
                    else:
                        if not _is_within(resolved_prefix, resolved_generation):
                            env_issues.append(
                                "generation prefix escapes its generation"
                            )
                        else:
                            try:
                                prefix_is_dir = resolved_prefix.is_dir()
                            except (OSError, RuntimeError, ValueError) as exc:
                                env_issues.append(
                                    f"generation prefix cannot be inspected: {exc}"
                                )
                            else:
                                if not prefix_is_dir:
                                    env_issues.append(
                                        "generation prefix is not a directory"
                                    )
    emit("env_generation", not env_issues, "; ".join(env_issues))

    # weights — validate the complete manifest before opening anything, then
    # hash and size each unique path from one descriptor snapshot.
    weights = record.get("weights")
    weight_entries = weights if isinstance(weights, list) else []
    weight_schema: list[str] = []
    weight_presence: list[str] = []
    weight_hashes: list[str] = []
    weight_sizes: list[str] = []
    weight_unverified: list[str] = []
    weight_paths: list[str] = []
    weight_digest_by_path: dict[str, str] = {}
    verified_count = 0
    seen_paths: set[str] = set()

    if not weight_entries:
        weight_schema.append("weights is missing, empty, or not a list")
    else:
        for index, entry in enumerate(weight_entries):
            label = f"weights[{index}]"
            if not isinstance(entry, dict):
                weight_schema.append(f"{label} is not an object")
                continue
            rel = entry.get("path")
            source = entry.get("source")
            digest = entry.get("sha256")
            size = entry.get("size")
            valid = True
            if _declared_path(root, rel) is None:
                weight_schema.append(f"{label}.path is missing or invalid")
                valid = False
            elif rel in seen_paths:
                weight_schema.append(f"duplicate weights path: {rel}")
                valid = False
            else:
                seen_paths.add(rel)
                weight_paths.append(rel)
            if not _nonempty_string(source):
                weight_schema.append(f"{label}.source is missing or invalid")
                valid = False
            if not _sha256_string(digest):
                weight_schema.append(f"{label}.sha256 is invalid")
                valid = False
            if not _nonnegative_int(size):
                weight_schema.append(f"{label}.size is missing or invalid")
                valid = False
            if entry.get("verified") is not True:
                weight_unverified.append(str(rel))
                valid = False
            if not valid:
                continue
            snapshot, error = _snapshot_declared(root, rel, keep_data=False)
            if snapshot is None:
                weight_presence.append(f"{rel}: {error}")
                continue
            alias = claim_artifact(snapshot, f"weight {rel}")
            if alias:
                weight_schema.append(alias)
            if snapshot.sha256 != digest:
                weight_hashes.append(
                    f"{rel}: content hash mismatch (recorded {digest[:16]}…, "
                    f"computed {snapshot.sha256[:16]}…)"
                )
            if snapshot.size != size:
                weight_sizes.append(
                    f"{rel}: size {snapshot.size} does not match the recorded {size}"
                )
            if snapshot.sha256 == digest and snapshot.size == size:
                verified_count += 1
                weight_digest_by_path[rel] = digest

    emit("weights_schema", not weight_schema, "; ".join(weight_schema))
    emit(
        "weights_present",
        bool(weight_entries) and not weight_presence,
        "; ".join(weight_presence),
    )
    emit(
        "weights_hash",
        bool(weight_entries) and not weight_hashes,
        "; ".join(weight_hashes),
    )
    emit(
        "weights_size",
        bool(weight_entries) and not weight_sizes,
        "; ".join(weight_sizes),
    )
    emit(
        "weights_verified",
        bool(weight_entries) and not weight_unverified,
        (
            ""
            if not weight_unverified
            else "weights recorded without verified=true: "
            + ", ".join(weight_unverified)
        ),
    )

    reference_verified = False
    reference_issues: list[str] = []
    if expected_weights is None:
        emit(
            "weights_reference",
            False,
            "not supplied: internal consistency only; admission is disabled",
            blocking=False,
        )
    elif not isinstance(expected_weights, dict) or not expected_weights:
        emit("weights_reference", False, "expected_weights must be a non-empty map")
    else:
        valid_reference_paths: set[str] = set()
        for rel, wanted in expected_weights.items():
            if _declared_path(root, rel) is None or not _sha256_string(wanted):
                reference_issues.append(f"invalid reference entry: {rel!r}")
            else:
                valid_reference_paths.add(rel)
        record_path_set = set(weight_paths)
        missing = sorted(valid_reference_paths - record_path_set)
        extra = sorted(record_path_set - valid_reference_paths)
        if missing:
            reference_issues.append(
                "record is missing reference paths: " + ", ".join(missing)
            )
        if extra:
            reference_issues.append(
                "record has unreferenced weight paths: " + ", ".join(extra)
            )
        entries_by_path = {
            entry.get("path"): entry
            for entry in weight_entries
            if isinstance(entry, dict) and isinstance(entry.get("path"), str)
        }
        for rel, wanted in expected_weights.items():
            found = entries_by_path.get(rel) if isinstance(rel, str) else None
            if found is not None and found.get("sha256") != wanted:
                reference_issues.append(f"{rel}: expected reference digest mismatch")
        reference_verified = not reference_issues and not weight_schema
        emit("weights_reference", reference_verified, "; ".join(reference_issues))

    # canary — command, outputs and parse all describe one coherent snapshot.
    canary = record.get("canary")
    canary_dict = canary if isinstance(canary, dict) else {}
    target = canary_dict.get("target")
    emit(
        "canary_target",
        _nonempty_string(target),
        "" if _nonempty_string(target) else "canary target is missing or invalid",
    )
    command = canary_dict.get("command")
    command_issues: list[str] = []
    command_weight_path: str | None = None
    if (
        not isinstance(command, list)
        or not command
        or not all(_nonempty_string(item) for item in command)
    ):
        command_issues.append("canary command must be a non-empty list of strings")
    elif len(command) != 6 or command[:3] != ["python", "bin/tool", "--target"]:
        command_issues.append(
            "schema v1 canary command must start with the portable logical "
            "invocation 'python bin/tool --target' and contain exactly six items"
        )
    elif command[4] != "--weights":
        command_issues.append(
            "schema v1 canary command must place --weights after the target"
        )
    else:
        if command[3] != target:
            command_issues.append("canary --target does not match the record target")
        recorded_weight = command[5]
        if (
            Path(recorded_weight).is_absolute()
            or PureWindowsPath(recorded_weight).is_absolute()
            or _declared_path(root, recorded_weight) is None
        ):
            command_issues.append(
                "canary --weights must be a portable record-relative path"
            )
        elif recorded_weight not in weight_paths:
            command_issues.append("canary --weights does not name a recorded weight")
        else:
            command_weight_path = recorded_weight
    emit("canary_command", not command_issues, "; ".join(command_issues))

    outputs = canary_dict.get("outputs")
    out_entries = outputs if isinstance(outputs, list) else []
    output_issues: list[str] = []
    output_hash_issues: list[str] = []
    output_snapshots: list[_Snapshot] = []
    output_paths: set[str] = set()
    if len(out_entries) != 1:
        output_issues.append(
            f"schema v1 requires exactly one canary output, found {len(out_entries)}"
        )
    if not out_entries:
        output_issues.append(
            "no output declared: the canary produced nothing verifiable"
        )
    else:
        for index, entry in enumerate(out_entries):
            if not isinstance(entry, dict):
                output_issues.append(f"outputs[{index}] is not an object")
                continue
            rel = entry.get("path")
            digest = entry.get("sha256")
            if _declared_path(root, rel) is None:
                output_issues.append(f"outputs[{index}].path is missing or invalid")
                continue
            if rel in output_paths:
                output_issues.append(f"duplicate canary output path: {rel}")
                continue
            output_paths.add(rel)
            if not _sha256_string(digest):
                output_hash_issues.append(f"{rel}: recorded sha256 is invalid")
                continue
            snapshot, error = _snapshot_declared(root, rel, keep_data=True)
            if snapshot is None:
                output_issues.append(
                    f"canary output absent or unreadable: {rel}: {error}"
                )
                continue
            alias = claim_artifact(snapshot, f"canary output {rel}")
            if alias:
                output_issues.append(alias)
            output_snapshots.append(snapshot)
            if snapshot.sha256 != digest:
                output_hash_issues.append(f"{rel}: content hash mismatch")
    emit("canary_outputs", not output_issues, "; ".join(output_issues))
    emit(
        "canary_outputs_hash",
        bool(out_entries) and not output_hash_issues,
        "; ".join(output_hash_issues),
    )

    parse = canary_dict.get("parse")
    parse_dict = parse if isinstance(parse, dict) else {}
    parse_status = parse_dict.get("status")
    parse_issues: list[str] = []
    fields = parse_dict.get("fields")
    field_list = fields if isinstance(fields, list) else []
    fields_shape_ok = (
        bool(field_list)
        and all(_nonempty_string(field) for field in field_list)
        and len(set(field_list)) == len(field_list)
    )
    if parse_status != "ok":
        parse_issues.append(f"parse status is {parse_status!r}, expected 'ok'")
    if parse_dict.get("format") != "json":
        parse_issues.append("parse format must be 'json'")
    if not fields_shape_ok:
        parse_issues.append("parse fields must be a non-empty unique list of strings")
    else:
        missing_declared = [
            field for field in _CANARY_FIELDS if field not in field_list
        ]
        if missing_declared:
            parse_issues.append(
                "parse fields omit required fields: " + ", ".join(missing_declared)
            )

    parsed_canary: dict[str, Any] | None = None
    if not output_snapshots:
        parse_issues.append("no readable canary output to parse")
    else:
        parsed, parse_error = _parse_snapshot(output_snapshots[0])
        if not isinstance(parsed, dict):
            parse_issues.append(
                "canary output is not a strict JSON object"
                + (f": {parse_error}" if parse_error else "")
            )
        else:
            parsed_canary = parsed
            missing_fields = (
                [field for field in field_list if field not in parsed]
                if fields_shape_ok
                else []
            )
            if missing_fields:
                parse_issues.append(
                    "canary output is missing declared fields: "
                    + ", ".join(missing_fields)
                )
            if parsed.get("target") != target:
                parse_issues.append("canary output target does not match the record")
            if not _nonempty_string(parsed.get("sequence")):
                parse_issues.append("canary output sequence is missing or invalid")
            plddt = parsed.get("plddt")
            if not _finite_nonnegative(plddt) or plddt > 100:
                parse_issues.append(
                    "canary output plddt must be finite and in [0, 100]"
                )
            parsed_weight = parsed.get("weights_sha256")
            expected_canary_weight = weight_digest_by_path.get(
                command_weight_path or ""
            )
            if (
                not _sha256_string(parsed_weight)
                or expected_canary_weight is None
                or parsed_weight != expected_canary_weight
            ):
                parse_issues.append(
                    "canary output weights_sha256 does not match the commanded "
                    "verified weight"
                )
    emit("canary_parse", not parse_issues, "; ".join(parse_issues))

    # downstream — the proof that the next adapter consumed the output.
    downstream = canary_dict.get("downstream")
    downstream_dict = downstream if isinstance(downstream, dict) else {}
    consumer = downstream_dict.get("consumer")
    downstream_status = downstream_dict.get("status")
    downstream_issues: list[str] = []
    if not _nonempty_string(consumer):
        downstream_issues.append("downstream consumer is missing or invalid")
    if downstream_status != "passed":
        downstream_issues.append(
            f"downstream status is {downstream_status!r}, expected 'passed'"
        )
    downstream_rel = downstream_dict.get("output")
    downstream_sha = downstream_dict.get("sha256")
    if _declared_path(root, downstream_rel) is None:
        downstream_issues.append("downstream output path is missing or invalid")
    elif downstream_rel in output_paths:
        downstream_issues.append(
            "downstream output must be distinct from the canary output"
        )
    elif downstream_rel == adapter_path or downstream_rel in weight_paths:
        downstream_issues.append(
            "downstream output must be distinct from adapter and weight artifacts"
        )
    if not _sha256_string(downstream_sha):
        downstream_issues.append("downstream sha256 is invalid")
    downstream_snapshot: _Snapshot | None = None
    if not downstream_issues:
        downstream_snapshot, error = _snapshot_declared(
            root, downstream_rel, keep_data=True
        )
        if downstream_snapshot is None:
            downstream_issues.append(error)
        else:
            alias = claim_artifact(
                downstream_snapshot, f"downstream output {downstream_rel}"
            )
            if alias:
                downstream_issues.append(alias)
            if downstream_snapshot.sha256 != downstream_sha:
                downstream_issues.append("downstream output content hash mismatch")
    if downstream_snapshot is not None:
        parsed_downstream, parse_error = _parse_snapshot(downstream_snapshot)
        if not isinstance(parsed_downstream, dict):
            downstream_issues.append(
                "downstream output is not a strict JSON object"
                + (f": {parse_error}" if parse_error else "")
            )
        elif parsed_canary is None:
            downstream_issues.append(
                "downstream output cannot bind to an invalid canary"
            )
        else:
            missing = [
                field for field in _DOWNSTREAM_FIELDS if field not in parsed_downstream
            ]
            if missing:
                downstream_issues.append(
                    "downstream output is missing fields: " + ", ".join(missing)
                )
            if parsed_downstream.get("consumer") != consumer:
                downstream_issues.append(
                    "downstream consumer does not match the record"
                )
            for field in ("target", "sequence"):
                if parsed_downstream.get(field) != parsed_canary.get(field):
                    downstream_issues.append(
                        f"downstream {field} does not match the canary output"
                    )
            downstream_plddt = parsed_downstream.get("plddt")
            canary_plddt = parsed_canary.get("plddt")
            if not _finite_nonnegative(downstream_plddt) or downstream_plddt > 100:
                downstream_issues.append(
                    "downstream plddt must be finite and in [0, 100]"
                )
            elif not _numbers_match(downstream_plddt, canary_plddt):
                downstream_issues.append(
                    "downstream plddt does not match the canary output"
                )
            if parsed_downstream.get("consumed_weights_sha256") != parsed_canary.get(
                "weights_sha256"
            ):
                downstream_issues.append(
                    "downstream consumed_weights_sha256 does not match the canary"
                )
    emit("downstream", not downstream_issues, "; ".join(downstream_issues))

    # admission — only a verified, reasoned admission proceeds.
    admission = record.get("admission")
    admission_dict = admission if isinstance(admission, dict) else {}
    admission_status = admission_dict.get("status")
    reasons = admission_dict.get("reasons")
    admission_issues: list[str] = []
    if admission_status != "verified":
        admission_issues.append(
            f'admission status is {admission_status!r}, expected "verified"',
        )
    if (
        not isinstance(reasons, list)
        or not reasons
        or not all(_nonempty_string(reason) for reason in reasons)
    ):
        admission_issues.append("admission reasons must be a non-empty list of strings")
    emit("admission", not admission_issues, "; ".join(admission_issues))

    # runtime/cost — every attempt carries its own finite totals; the frozen
    # aggregate must equal their exact sum and the final attempt must pass.
    runtime = record.get("runtime")
    runtime_dict = runtime if isinstance(runtime, dict) else {}
    wall_s = runtime_dict.get("wall_s")
    attempts = runtime_dict.get("attempts")
    runtime_issues: list[str] = []
    attempt_statuses: list[str] = []
    attempt_reasons: list[str | None] = []
    attempt_wall_values: list[int | float] = []
    attempt_gpu_values: list[int | float] = []
    if not _finite_nonnegative(wall_s):
        runtime_issues.append("wall_s must be a finite non-negative number")
    if not isinstance(attempts, list) or not attempts:
        runtime_issues.append("attempts must be a non-empty list")
    else:
        for index, attempt in enumerate(attempts):
            if not isinstance(attempt, dict):
                runtime_issues.append(f"attempts[{index}] is not an object")
                continue
            status_value = attempt.get("status")
            reason = attempt.get("reason")
            attempt_reasons.append(reason if isinstance(reason, str) else None)
            attempt_wall = attempt.get("wall_s")
            attempt_gpu = attempt.get("gpu_h")
            if status_value not in ("passed", "failed"):
                runtime_issues.append(f"attempts[{index}].status is invalid")
            else:
                attempt_statuses.append(status_value)
            if not isinstance(reason, str) or (
                status_value == "failed" and not reason.strip()
            ):
                runtime_issues.append(f"attempts[{index}].reason is invalid")
            if not _finite_nonnegative(attempt_wall):
                runtime_issues.append(f"attempts[{index}].wall_s is invalid")
            else:
                attempt_wall_values.append(attempt_wall)
            if not _finite_nonnegative(attempt_gpu):
                runtime_issues.append(f"attempts[{index}].gpu_h is invalid")
            else:
                attempt_gpu_values.append(attempt_gpu)
        if len(attempt_statuses) != len(attempts) or attempt_statuses[-1:] != [
            "passed"
        ]:
            runtime_issues.append("the final attempt must have status 'passed'")
    attempt_wall_total = _sum_finite_numbers(attempt_wall_values)
    attempt_gpu_total = _sum_finite_numbers(attempt_gpu_values)
    if attempt_wall_total is None:
        runtime_issues.append("attempt wall_s total is not finite")
    elif _finite_nonnegative(wall_s) and not _numbers_match(wall_s, attempt_wall_total):
        runtime_issues.append(
            f"runtime wall_s {wall_s} does not equal attempt total {attempt_wall_total}"
        )
    emit("runtime", not runtime_issues, "; ".join(runtime_issues))

    cost = record.get("cost")
    cost_dict = cost if isinstance(cost, dict) else {}
    gpu_h = cost_dict.get("gpu_h")
    budget_hours = cost_dict.get("budget_hours")
    cost_issues: list[str] = []
    if not _finite_nonnegative(gpu_h):
        cost_issues.append("gpu_h must be a finite non-negative number")
    if not _finite_nonnegative(budget_hours):
        cost_issues.append("budget_hours must be a finite non-negative number")
    if attempt_gpu_total is None:
        cost_issues.append("attempt gpu_h total is not finite")
    elif _finite_nonnegative(gpu_h) and not _numbers_match(gpu_h, attempt_gpu_total):
        cost_issues.append(
            f"cost gpu_h {gpu_h} does not equal attempt total {attempt_gpu_total}"
        )
    if (
        _finite_nonnegative(gpu_h)
        and _finite_nonnegative(budget_hours)
        and gpu_h > budget_hours
    ):
        cost_issues.append(
            f"cost exceeds declared budget: gpu_h {gpu_h} > budget_hours {budget_hours}",
        )
    emit("cost", not cost_issues, "; ".join(cost_issues))

    recovered = (
        bool(attempt_statuses)
        and attempt_statuses[-1] == "passed"
        and "failed" in attempt_statuses[:-1]
    )
    ok = not problems
    return {
        "ok": ok,
        # Admission is the gate, not the verification alone: a record that
        # internally verifies but was refused (budget, canary failure) must
        # not proceed.
        "admitted": ok and reference_verified and admission_status == "verified",
        "reference_verified": reference_verified,
        "problems": problems,
        "checks": checks,
        "schema_version": version,
        "record_sha256": recorded_hash if isinstance(recorded_hash, str) else None,
        "tool": (
            tool_dict.get("name") if isinstance(tool_dict.get("name"), str) else None
        ),
        "weights_verified": verified_count,
        "canary_parse": parse_status if isinstance(parse_status, str) else None,
        "downstream": downstream_status if isinstance(downstream_status, str) else None,
        "admission": admission_status if isinstance(admission_status, str) else None,
        "attempts": len(attempts) if isinstance(attempts, list) else None,
        "attempt_statuses": attempt_statuses,
        "attempt_reasons": attempt_reasons,
        "recovered": recovered,
        "runtime_wall_s": wall_s if _finite_nonnegative(wall_s) else None,
        "cost_gpu_h": gpu_h if _finite_nonnegative(gpu_h) else None,
    }


__all__ = [
    "BRINGUP_FILENAME",
    "BringupError",
    "RECORD_DIR",
    "SCHEMA_VERSION",
    "seal_record",
    "verify_bringup",
]
