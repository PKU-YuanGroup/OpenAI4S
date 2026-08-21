"""Verification of a frozen tool bring-up record.

A bring-up campaign runs with its design and prediction tools *not*
preinstalled: the run builds the tool environment from a public source,
downloads weights, writes a running adapter, proves the tool on a canary
against a real campaign target, proves the canary output parses and a
downstream adapter consumes it, and freezes the image digest, weights
checksums, runtime and cost into ``bringup.json``. Only a record that
verifies — and whose admission says so — proceeds.

This module is the evaluator half of that contract. It depends on nothing but
the standard library and reads only the record and the files the record names,
so a reviewer can run it against a submission in a clean environment — the
same rule ``evidence.verify_package`` was built under.

What verification establishes, stated precisely so it is not over-read:

  * the record's own body hashes to ``record_sha256``, so a record edited
    after sealing is detected;
  * every weights file the record names is present inside the submission
    root, and its bytes hash to the recorded digest and match the recorded
    size;
  * the built environment generation the record claims exists on disk and is
    marked ready;
  * the canary output is present, unmodified, parses as the declared format,
    and carries every declared field;
  * the downstream consumption proof is present, unmodified, and passed;
  * admission says ``verified`` with reasons, runtime and cost are sane, and
    the cost does not exceed the declared budget.

What it does NOT establish: authorship, and ground truth. Anyone can rewrite
a weights file *and* its recorded digest and re-seal the record; every
internal check will pass. Detecting that requires digests the verifier
already trusts — ``expected_weights``, supplied by the evaluator from the
reference build, is the seam that closes the loop. Saying so here is better
than letting "verified" quietly imply more than it means.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
BRINGUP_FILENAME = "bringup.json"
#: The directory, relative to the submission root, where the record and the
#: canary/downstream artifacts live.
RECORD_DIR = "bringup"

#: Read size for hashing. Weights are routinely hundreds of megabytes, so the
#: file is streamed rather than read into memory (compute/manifest.hash_file).
_CHUNK = 1024 * 1024


class BringupError(ValueError):
    """The record is missing, unreadable, or not a bring-up record at all."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha256(path: Path) -> str:
    """Streaming SHA-256 of one file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(payload: Any) -> bytes:
    """Byte-for-byte what ``seal_record`` hashed.

    Key order and separators are part of the digest: a differently-formatted
    but semantically identical record hashes differently, so seal and verify
    share this one serialisation by construction.
    """
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _resolve(root: Path, rel: str) -> Path | None:
    """Resolve a record-declared relative path inside ``root``, or None.

    The record is not trusted: a path that leaves the root — via ``..``, an
    absolute path, or a symlink pointing outside — must not be hashed where
    the verifier did not expect. Mirrors ``env_generations._is_within``.
    """
    if not isinstance(rel, str) or not rel:
        return None
    try:
        resolved = Path(os.path.realpath(str(root / rel)))
        anchor = Path(os.path.realpath(str(root)))
    except OSError:  # pragma: no cover - unreadable path
        return None
    if resolved == anchor or anchor in resolved.parents:
        return resolved
    return None


def _segment_ok(value: str) -> bool:
    """One safe path component: no separators, not ``.``/``..``."""
    return (
        bool(value)
        and value not in (".", "..")
        and not any(marker in value for marker in ("/", "\\", "\x00"))
    )


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
    root = Path(root)
    path = (
        Path(record_path)
        if record_path is not None
        else root / RECORD_DIR / BRINGUP_FILENAME
    )

    problems: list[str] = []
    checks: list[dict[str, Any]] = []

    def emit(check_id: str, ok: bool, detail: str = "") -> None:
        checks.append({"id": check_id, "ok": ok, "detail": detail})
        if not ok:
            problems.append(f"{check_id}: {detail}")

    # The record must be a record before any of its fields mean anything.
    # Everything below reports through `problems`; this is the one thing that
    # raises, because a verifier that cannot find the record cannot produce a
    # report about it.
    if not path.is_file():
        raise BringupError(f"no bringup record at {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise BringupError(f"cannot read bringup record {path}: {e}") from e
    try:
        record = json.loads(raw)
    except ValueError as e:
        raise BringupError(f"bringup record {path} is not JSON: {e}") from e
    if not isinstance(record, dict):
        raise BringupError(f"bringup record {path} is not a JSON object")
    emit("record", True, str(path))

    # schema_version
    version = record.get("schema_version")
    emit(
        "schema_version",
        version == SCHEMA_VERSION,
        (
            f"declares {version!r}, expected {SCHEMA_VERSION}"
            if version != SCHEMA_VERSION
            else str(version)
        ),
    )

    # self_vouch — the record must vouch for itself before its contents mean
    # anything: without this, an editor could rewrite a payload and its
    # recorded digest together and every per-file check would still pass.
    recorded_hash = record.get("record_sha256")
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    actual = _sha256(_canonical_json(body))
    emit(
        "self_vouch",
        isinstance(recorded_hash, str) and recorded_hash == actual,
        (
            actual
            if isinstance(recorded_hash, str) and recorded_hash == actual
            else "record_sha256 missing or does not match the record body"
        ),
    )

    # tool — what was brought up, and by which adapter.
    tool = record.get("tool")
    tool_dict = tool if isinstance(tool, dict) else {}
    missing_tool = [
        field
        for field in ("name", "version", "source", "revision", "adapter")
        if not tool_dict.get(field)
    ]
    emit(
        "tool",
        not missing_tool,
        (
            str(tool_dict.get("name"))
            if not missing_tool
            else "missing required fields: " + ", ".join(missing_tool)
        ),
    )

    # env_generation — the built environment the record claims, checked on
    # disk: a generation whose apply never finished has no manifest.json, and
    # one that failed is never marked ready.
    env_name = tool_dict.get("env_name")
    env_generation = tool_dict.get("env_generation")
    if not isinstance(env_name, str) or not _segment_ok(env_name):
        emit("env_generation", False, "env_name is missing or not a safe path segment")
    elif not isinstance(env_generation, str) or not _segment_ok(env_generation):
        emit(
            "env_generation",
            False,
            "env_generation is missing or not a safe path segment",
        )
    else:
        manifest_rel = (
            f"environments/{env_name}/generations/{env_generation}/manifest.json"
        )
        manifest_path = _resolve(root, manifest_rel)
        state = None
        if manifest_path is not None and manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifest = None
            if isinstance(manifest, dict):
                state = manifest.get("state")
        if state == "ready":
            emit("env_generation", True, env_generation)
        elif manifest_path is None or not (manifest_path and manifest_path.is_file()):
            emit(
                "env_generation",
                False,
                "no generation manifest on disk for the recorded generation",
            )
        else:
            emit(
                "env_generation",
                False,
                f'generation manifest state is {state!r}, expected "ready"',
            )

    # weights — presence (confined), hash, size, verified, one aggregated
    # check each; then the evaluator-held reference seam.
    weights = record.get("weights")
    weight_list = weights if isinstance(weights, list) else None
    weight_entries = weight_list if weight_list is not None else []
    verified_count = 0
    if weight_list is None or not weight_list:
        emit("weights_present", False, "weights is missing, empty, or not a list")
        emit("weights_hash", False, "no weight entries to hash")
        emit("weights_size", False, "no weight entries to size")
        emit("weights_verified", False, "no weight entries to verify")
    else:
        absent = []
        for entry in weight_entries:
            rel = entry.get("path") if isinstance(entry, dict) else None
            resolved = _resolve(root, rel) if isinstance(rel, str) else None
            if resolved is None or not resolved.is_file():
                absent.append(str(rel))
        emit(
            "weights_present",
            not absent,
            "" if not absent else "weight file absent: " + ", ".join(absent),
        )

        hash_problems: list[str] = []
        size_problems: list[str] = []
        unverified: list[str] = []
        for entry in weight_entries:
            if not isinstance(entry, dict):
                continue
            rel = entry.get("path")
            resolved = _resolve(root, rel) if isinstance(rel, str) else None
            if resolved is None or not resolved.is_file():
                continue  # the presence check already reported it
            digest = _file_sha256(resolved)
            if digest != entry.get("sha256"):
                hash_problems.append(
                    f"{rel}: content hash mismatch (recorded "
                    f"{str(entry.get('sha256'))[:16]}…, computed {digest[:16]}…)"
                )
            elif (
                entry.get("size") is not None
                and resolved.stat().st_size != entry["size"]
            ):
                size_problems.append(
                    f"{rel}: size {resolved.stat().st_size} does not match the "
                    f"recorded {entry['size']}"
                )
            elif not entry.get("verified"):
                unverified.append(str(rel))
            else:
                verified_count += 1
        emit("weights_hash", not hash_problems, "; ".join(hash_problems))
        emit("weights_size", not size_problems, "; ".join(size_problems))
        emit(
            "weights_verified",
            not unverified,
            (
                ""
                if not unverified
                else "weights recorded without verification: " + ", ".join(unverified)
            ),
        )

    if expected_weights is None:
        emit("weights_reference", True, "skipped: no reference digests supplied")
    else:
        mismatched = []
        for rel, wanted in expected_weights.items():
            found = next(
                (
                    e
                    for e in weight_entries
                    if isinstance(e, dict) and e.get("path") == rel
                ),
                None,
            )
            if found is None:
                mismatched.append(f"{rel}: no record entry")
            elif found.get("sha256") != wanted:
                mismatched.append(
                    f"{rel}: record digest {str(found.get('sha256'))[:16]}…"
                )
        emit(
            "weights_reference",
            not mismatched,
            (
                ""
                if not mismatched
                else "expected reference digest mismatch: " + "; ".join(mismatched)
            ),
        )

    # canary — target, outputs, and the parse proof.
    canary = record.get("canary")
    canary_dict = canary if isinstance(canary, dict) else {}
    target = canary_dict.get("target")
    emit(
        "canary_target",
        isinstance(target, str) and bool(target),
        (
            ""
            if isinstance(target, str) and bool(target)
            else "canary target is missing or empty"
        ),
    )

    outputs = canary_dict.get("outputs")
    out_entries = outputs if isinstance(outputs, list) else []
    if not out_entries:
        emit(
            "canary_outputs",
            False,
            "no output declared: the canary produced nothing verifiable",
        )
    else:
        absent_out = []
        for entry in out_entries:
            rel = entry.get("path") if isinstance(entry, dict) else None
            resolved = _resolve(root, rel) if isinstance(rel, str) else None
            if resolved is None or not resolved.is_file():
                absent_out.append(str(rel))
        emit(
            "canary_outputs",
            not absent_out,
            "" if not absent_out else "canary output absent: " + ", ".join(absent_out),
        )

        hash_out: list[str] = []
        for entry in out_entries:
            if not isinstance(entry, dict):
                continue
            rel = entry.get("path")
            resolved = _resolve(root, rel) if isinstance(rel, str) else None
            if resolved is None or not resolved.is_file():
                continue  # the presence check already reported it
            digest = _file_sha256(resolved)
            if digest != entry.get("sha256"):
                hash_out.append(
                    f"{rel}: content hash mismatch (recorded "
                    f"{str(entry.get('sha256'))[:16]}…, computed {digest[:16]}…)"
                )
        emit("canary_outputs_hash", not hash_out, "; ".join(hash_out))

    parse = canary_dict.get("parse")
    parse_dict = parse if isinstance(parse, dict) else {}
    parse_status = parse_dict.get("status")
    if parse_status != "ok":
        reason = parse_dict.get("reason")
        emit(
            "canary_parse",
            False,
            f'parse status is {parse_status!r}, expected "ok"'
            + (f": {reason}" if reason else ""),
        )
    elif parse_dict.get("format") != "json":
        emit(
            "canary_parse",
            False,
            f"parse format is {parse_dict.get('format')!r}, expected \"json\"",
        )
    else:
        fields = parse_dict.get("fields")
        field_list = fields if isinstance(fields, list) else []
        if not field_list:
            emit("canary_parse", False, "no fields declared for the parsed output")
        else:
            first = out_entries[0] if out_entries else None
            rel = first.get("path") if isinstance(first, dict) else None
            resolved = _resolve(root, rel) if isinstance(rel, str) else None
            parsed = None
            if resolved is not None and resolved.is_file():
                try:
                    parsed = json.loads(resolved.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    parsed = None
            missing_fields = [
                f for f in field_list if not isinstance(parsed, dict) or f not in parsed
            ]
            emit(
                "canary_parse",
                not missing_fields,
                (
                    ""
                    if not missing_fields
                    else "canary output does not parse or is missing declared fields: "
                    + ", ".join(missing_fields)
                ),
            )

    # downstream — the proof that the next adapter consumed the output.
    downstream = canary_dict.get("downstream")
    downstream_dict = downstream if isinstance(downstream, dict) else {}
    consumer = downstream_dict.get("consumer")
    downstream_status = downstream_dict.get("status")
    if not isinstance(consumer, str) or not consumer:
        emit("downstream", False, "downstream consumer is missing or empty")
    elif downstream_status != "passed":
        emit(
            "downstream",
            False,
            f"downstream consumer refused: status is {downstream_status!r}",
        )
    else:
        rel = downstream_dict.get("output")
        resolved = _resolve(root, rel) if isinstance(rel, str) else None
        if resolved is None or not resolved.is_file():
            emit("downstream", False, f"downstream output absent: {rel!r}")
        elif _file_sha256(resolved) != downstream_dict.get("sha256"):
            emit(
                "downstream", False, f"downstream output content hash mismatch: {rel!r}"
            )
        else:
            emit("downstream", True, str(consumer))

    # admission — only a verified, reasoned admission proceeds.
    admission = record.get("admission")
    admission_dict = admission if isinstance(admission, dict) else {}
    admission_status = admission_dict.get("status")
    reasons = admission_dict.get("reasons")
    if admission_status != "verified":
        emit(
            "admission",
            False,
            f'admission status is {admission_status!r}, expected "verified"',
        )
    elif not isinstance(reasons, list) or not reasons:
        emit("admission", False, "admission status is verified but no reasons recorded")
    else:
        emit("admission", True, "; ".join(map(str, reasons)))

    # runtime — non-negative wall time, attempts as a list.
    runtime = record.get("runtime")
    runtime_dict = runtime if isinstance(runtime, dict) else {}
    wall_s = runtime_dict.get("wall_s")
    attempts = runtime_dict.get("attempts")
    runtime_problems = []
    if isinstance(wall_s, bool) or not isinstance(wall_s, (int, float)) or wall_s < 0:
        runtime_problems.append(f"wall_s is {wall_s!r}, expected a non-negative number")
    if not isinstance(attempts, list):
        runtime_problems.append("attempts is not a list")
    emit("runtime", not runtime_problems, "; ".join(runtime_problems))

    # cost — non-negative, within the declared budget when one is declared.
    cost = record.get("cost")
    cost_dict = cost if isinstance(cost, dict) else {}
    gpu_h = cost_dict.get("gpu_h")
    budget_hours = cost_dict.get("budget_hours")
    if isinstance(gpu_h, bool) or not isinstance(gpu_h, (int, float)) or gpu_h < 0:
        emit("cost", False, f"gpu_h is {gpu_h!r}, expected a non-negative number")
    elif budget_hours is not None and (
        isinstance(budget_hours, bool)
        or not isinstance(budget_hours, (int, float))
        or gpu_h > budget_hours
    ):
        emit(
            "cost",
            False,
            f"cost exceeds declared budget: gpu_h {gpu_h} > budget_hours {budget_hours}",
        )
    else:
        emit(
            "cost",
            True,
            f"gpu_h {gpu_h}"
            + (
                f" within budget_hours {budget_hours}"
                if budget_hours is not None
                else ", no budget declared"
            ),
        )

    ok = not problems
    return {
        "ok": ok,
        # Admission is the gate, not the verification alone: a record that
        # internally verifies but was refused (budget, canary failure) must
        # not proceed.
        "admitted": ok and admission_status == "verified",
        "problems": problems,
        "checks": checks,
        "schema_version": version,
        "record_sha256": recorded_hash if isinstance(recorded_hash, str) else None,
        "tool": tool_dict.get("name") if isinstance(tool, dict) else None,
        "weights_verified": verified_count if weight_list else 0,
        "canary_parse": parse_status if isinstance(parse_status, str) else None,
        "downstream": downstream_status if isinstance(downstream_status, str) else None,
        "admission": admission_status if isinstance(admission_status, str) else None,
        "attempts": len(attempts) if isinstance(attempts, list) else None,
        "runtime_wall_s": (
            wall_s
            if isinstance(wall_s, (int, float)) and not isinstance(wall_s, bool)
            else None
        ),
        "cost_gpu_h": (
            gpu_h
            if isinstance(gpu_h, (int, float)) and not isinstance(gpu_h, bool)
            else None
        ),
    }


__all__ = [
    "BRINGUP_FILENAME",
    "BringupError",
    "RECORD_DIR",
    "SCHEMA_VERSION",
    "seal_record",
    "verify_bringup",
]
