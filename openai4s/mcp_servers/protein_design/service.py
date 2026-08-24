"""Pure-stdlib orchestration for auditable protein-design model processes."""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai4s.host.model_admission import (
    ModelAdmissionError,
    ModelAdmissionLedger,
)

from .schemas import TOOL_NAMES, TOOL_SCHEMAS

_AA3 = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
    "MSE": "M",
}
_VALID_AA = frozenset("ACDEFGHIKLMNPQRSTVWY")
_ATTEMPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HOTSPOT_RE = re.compile(r"^([A-Za-z0-9])(-?[0-9]+)$")
_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SCHEMA_VERSION = "openai4s.protein-design-attempt.v1"


class DesignToolError(RuntimeError):
    """A fail-closed validation or backend failure safe to publish."""


class InterruptedAttempt(DesignToolError):
    """An attempt has partial outputs but no trustworthy terminal record."""


@dataclass(frozen=True)
class Residue:
    chain: str
    number: int
    insertion_code: str
    name3: str

    @property
    def label(self) -> str:
        return f"{self.chain}{self.number}{self.insertion_code}".rstrip()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_json(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _clean_error(error: BaseException) -> str:
    text = str(error).replace("\n", " ").replace("\r", " ").strip()
    return text[:2000] or type(error).__name__


def _sequence(value: Any, field: str = "sequence") -> str:
    if not isinstance(value, str):
        raise DesignToolError(f"{field} must be a string")
    sequence = "".join(value.split()).upper()
    invalid = sorted(set(sequence) - _VALID_AA)
    if not sequence or invalid:
        detail = f": {''.join(invalid)}" if invalid else ""
        raise DesignToolError(
            f"{field} must contain only the 20 standard amino acids{detail}"
        )
    return sequence


def _read_pdb_residues(path: Path) -> dict[str, list[Residue]]:
    chains: dict[str, list[Residue]] = {}
    seen: set[tuple[str, int, str]] = set()
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.startswith("ATOM  ") or len(line) < 27:
                continue
            altloc = line[16:17]
            if altloc not in (" ", "A"):
                continue
            chain = line[21:22].strip()
            if not chain:
                raise DesignToolError("PDB contains a blank protein chain identifier")
            try:
                number = int(line[22:26])
            except ValueError as error:
                raise DesignToolError(
                    "PDB contains an invalid residue number"
                ) from error
            insertion = line[26:27].strip()
            key = (chain, number, insertion)
            if key in seen:
                continue
            name3 = line[17:20].strip().upper()
            if name3 not in _AA3:
                raise DesignToolError(
                    f"unsupported protein residue {name3 or '<blank>'} at {chain}{number}{insertion}"
                )
            seen.add(key)
            chains.setdefault(chain, []).append(
                Residue(chain, number, insertion, name3)
            )
    if not chains:
        raise DesignToolError("PDB contains no parseable ATOM protein residues")
    return chains


def _chain_sequences(chains: dict[str, list[Residue]]) -> dict[str, str]:
    return {
        chain: "".join(_AA3[item.name3] for item in residues)
        for chain, residues in chains.items()
    }


def _target_contig(residues: list[Residue]) -> str:
    if any(item.insertion_code for item in residues):
        raise DesignToolError(
            "RFdiffusion target chains with insertion codes require explicit preprocessing and a residue map"
        )
    ordered = sorted({item.number for item in residues})
    if any(number <= 0 for number in ordered):
        raise DesignToolError(
            "RFdiffusion target residue numbers must be positive; preprocess and preserve a residue map"
        )
    spans: list[tuple[int, int]] = []
    start = previous = ordered[0]
    for number in ordered[1:]:
        if number == previous + 1:
            previous = number
            continue
        spans.append((start, previous))
        start = previous = number
    spans.append((start, previous))
    chain = residues[0].chain
    return "/".join(f"{chain}{start}-{end}" for start, end in spans)


def _parse_json_array_env(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DesignToolError(f"{name} must be a JSON string array") from error
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise DesignToolError(f"{name} must be a non-empty JSON string array")
    return value


def _mpnn_chain_order(header: str) -> list[str] | None:
    """The '/'-separated chain order a ProteinMPNN header declares, if any.

    Returns ``None`` when the header carries no mapping at all, which is the
    normal shape of a sampled record, and raises only when a mapping is
    present but unreadable.
    """
    match_designed = re.search(r"designed_chains=(\[[^\]]*\])", header)
    match_fixed = re.search(r"fixed_chains=(\[[^\]]*\])", header)
    if not match_designed or not match_fixed:
        return None
    try:
        return list(ast.literal_eval(match_designed.group(1))) + list(
            ast.literal_eval(match_fixed.group(1))
        )
    except (ValueError, SyntaxError, TypeError) as error:
        raise DesignToolError("ProteinMPNN FASTA chain mapping is invalid") from error


def _tail_text(path: Path, limit: int) -> str:
    """The last ``limit`` characters of a log, bounded at the read.

    Backends write progress lines without a TTY, so these logs reach gigabytes.
    Slicing after `read_text()` applies the bound only once the whole file (and
    its decoded copy) is already resident, which turns reporting a backend's
    exit status into a `MemoryError` of our own.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            # 4 bytes per character is the UTF-8 worst case, so this window
            # cannot cut short of `limit` characters.
            handle.seek(max(0, size - limit * 4))
            raw = handle.read()
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace")[-limit:].strip()


def _network_isolation_prefix() -> list[str]:
    """Accept only command prefixes that visibly request a separate net namespace."""
    prefix = _parse_json_array_env("OPENAI4S_PROTEIN_DESIGN_OFFLINE_PREFIX")
    if not prefix:
        return []
    executable = Path(prefix[0]).name
    flags = set(prefix[1:])
    docker_network_none = (
        executable in {"docker", "podman"}
        and "run" in flags
        and (
            bool(flags & {"--network=none", "--net=none"})
            or any(
                prefix[index] in {"--network", "--net"}
                and index + 1 < len(prefix)
                and prefix[index + 1] == "none"
                for index in range(1, len(prefix))
            )
        )
    )
    valid = (
        (executable in {"bwrap", "bubblewrap"} and "--unshare-net" in flags)
        or (executable == "unshare" and bool(flags & {"--net", "-n"}))
        or (executable == "firejail" and "--net=none" in flags)
        or docker_network_none
    )
    if not valid:
        raise DesignToolError(
            "OPENAI4S_PROTEIN_DESIGN_OFFLINE_PREFIX must visibly request an "
            "isolated network namespace (for example bwrap --unshare-net)"
        )
    # Presence of the isolating token is not the same as the token being in
    # effect: bwrap applies options in order and docker takes the last
    # --network, so a prefix can name both and end up fully networked. The
    # terminal record publishes `network_isolation_enforced: true` off the back
    # of this function, so a claim it cannot substantiate must fail closed.
    rejoining = {
        "--share-net",
        "--net=host",
        "--network=host",
        "--net=bridge",
        "--network=bridge",
    }
    contradicted = sorted(flags & rejoining)
    for index in range(1, len(prefix) - 1):
        if prefix[index] in {"--network", "--net"} and prefix[index + 1] != "none":
            contradicted.append(f"{prefix[index]} {prefix[index + 1]}")
    if contradicted:
        raise DesignToolError(
            "OPENAI4S_PROTEIN_DESIGN_OFFLINE_PREFIX also re-enables networking "
            f"({', '.join(sorted(set(contradicted)))}); the isolated namespace "
            "it requests would not be in effect"
        )
    return prefix


class ProteinDesignService:
    """Validate calls, launch pinned backends and always persist terminal records."""

    def __init__(self, *, root: str | Path | None = None, timeout: float | None = None):
        configured_root = (
            root or os.environ.get("OPENAI4S_PROTEIN_DESIGN_ROOT") or os.getcwd()
        )
        self.root = Path(configured_root).resolve()
        self.timeout = float(
            timeout or os.environ.get("OPENAI4S_PROTEIN_DESIGN_TIMEOUT_S", "7200")
        )
        self.require_admission = (
            os.environ.get("OPENAI4S_PROTEIN_DESIGN_REQUIRE_ADMISSION", "0") == "1"
        )
        # Admission belongs to this live backend process. Restarting the MCP
        # server deliberately requires a fresh canary on the current machine.
        self._admission = ModelAdmissionLedger("protein-design")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("protein-design timeout must be finite and positive")

    def _path(
        self, raw: Any, *, existing: bool = False, directory: bool = False
    ) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise DesignToolError("path must be a non-empty string")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise DesignToolError(
                f"path escapes configured protein-design root: {raw}"
            ) from error
        if existing and not candidate.is_file():
            raise DesignToolError(f"input file does not exist: {raw}")
        if directory:
            candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    def _prepare(
        self, name: str, args: dict[str, Any]
    ) -> tuple[str, int, Path, float, str]:
        if name not in TOOL_NAMES:
            raise DesignToolError(f"unknown protein-design tool: {name}")
        if not isinstance(args, dict):
            raise DesignToolError("tool arguments must be an object")
        required = TOOL_SCHEMAS[name].get("required") or []
        missing = [field for field in required if field not in args]
        if missing:
            raise DesignToolError(
                "missing required tool arguments: " + ", ".join(missing)
            )
        run_mode = args.get("run_mode", "formal")
        if run_mode not in {"canary", "formal"}:
            raise DesignToolError("run_mode must be canary or formal")
        execution_target = args.get("execution_target", "local")
        if not isinstance(execution_target, str) or not execution_target.strip():
            raise DesignToolError("execution_target must be a non-empty string")
        if execution_target != "local":
            raise DesignToolError(
                "this connector process is local; selected remote execution_target "
                f"{execution_target!r} requires a verified remote adapter"
            )
        attempt = args.get("attempt_id")
        if not isinstance(attempt, str):
            raise DesignToolError("attempt_id must be a string")
        if not _ATTEMPT_RE.fullmatch(attempt):
            raise DesignToolError(
                "attempt_id must use only letters, digits, dot, underscore and hyphen"
            )
        seed = args.get("seed")
        if (
            isinstance(seed, bool)
            or not isinstance(seed, int)
            or not 0 <= seed <= 2147483647
        ):
            raise DesignToolError("seed must be an integer in [0, 2147483647]")
        revision = args.get("backend_revision")
        if not isinstance(revision, str) or len(revision.strip()) < 7:
            raise DesignToolError(
                "backend_revision must identify a pinned source or image"
            )
        output_root = self._path(args.get("output_dir"), directory=True)
        output = output_root / attempt
        output.mkdir(parents=True, exist_ok=True)
        return attempt, seed, output, time.monotonic(), _utc_now()

    def _operator_path(self, variable: str, default: str) -> Path:
        """Resolve an operator-configured backend location.

        Deliberately *not* `_path`: that confinement exists to keep an
        agent-supplied path inside the session root, and an operator's model
        checkout legitimately lives at `/opt/RFdiffusion`. Routing config
        through the agent's fence rejected every correct install with
        "path escapes configured protein-design root" -- a refusal aimed at
        the wrong party, and one that named neither the variable nor the root.
        """
        raw = os.environ.get(variable) or default
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        return candidate.resolve()

    def _admission_key(self, name: str, args: dict[str, Any]) -> str | None:
        return self._admission.key(
            operation=name,
            backend_revision=args.get("backend_revision"),
            checkpoint_digest=args.get("checkpoint_sha256"),
            execution_target=args.get("execution_target", "local"),
        )

    def _verify_checkpoint(self, args: dict[str, Any]) -> tuple[Path, str]:
        checkpoint = self._path(args.get("checkpoint_path"), existing=True)
        expected = args.get("checkpoint_sha256")
        if not isinstance(expected, str) or not _HEX64_RE.fullmatch(expected):
            raise DesignToolError("checkpoint_sha256 must be 64 hexadecimal characters")
        actual = _sha256(checkpoint)
        if actual != expected.lower():
            raise DesignToolError(
                f"checkpoint SHA-256 mismatch: expected {expected.lower()}, observed {actual}"
            )
        return checkpoint, actual

    def _verify_checkpoint_bundle(
        self, args: dict[str, Any]
    ) -> tuple[Path, Path, str, list[dict[str, str]]]:
        """Verify a ColabFold model-data manifest and every file it pins."""
        manifest, manifest_digest = self._verify_checkpoint(args)
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DesignToolError(
                "prediction checkpoint_path must be a JSON checkpoint-bundle manifest"
            ) from error
        files = value.get("files") if isinstance(value, dict) else None
        raw_data_dir = value.get("data_dir") if isinstance(value, dict) else None
        if (
            not isinstance(raw_data_dir, str)
            or not raw_data_dir
            or Path(raw_data_dir).is_absolute()
        ):
            raise DesignToolError(
                "prediction checkpoint manifest needs a relative data_dir"
            )
        raw_data_path = manifest.parent / raw_data_dir
        if raw_data_path.is_symlink():
            raise DesignToolError("checkpoint data_dir must not be a symlink")
        data_dir = raw_data_path.resolve()
        try:
            data_dir.relative_to(manifest.parent.resolve())
            data_dir.relative_to(self.root)
        except ValueError as error:
            raise DesignToolError("checkpoint data_dir escapes its bundle") from error
        if not data_dir.is_dir() or data_dir.is_symlink():
            raise DesignToolError("checkpoint data_dir must be a real local directory")
        if not isinstance(files, list) or not files:
            raise DesignToolError(
                "prediction checkpoint manifest must contain a non-empty files list"
            )
        verified: list[dict[str, str]] = []
        for index, item in enumerate(files):
            if not isinstance(item, dict):
                raise DesignToolError(
                    f"checkpoint manifest files[{index}] must be an object"
                )
            raw_path = item.get("path")
            expected = item.get("sha256")
            if (
                not isinstance(raw_path, str)
                or not raw_path
                or Path(raw_path).is_absolute()
                or not isinstance(expected, str)
                or not _HEX64_RE.fullmatch(expected)
            ):
                raise DesignToolError(
                    f"checkpoint manifest files[{index}] needs a relative path and SHA-256"
                )
            raw_candidate = manifest.parent / raw_path
            if raw_candidate.is_symlink():
                raise DesignToolError(
                    f"checkpoint bundle file must not be a symlink: {raw_path}"
                )
            candidate = raw_candidate.resolve()
            try:
                candidate.relative_to(manifest.parent.resolve())
                candidate.relative_to(data_dir)
                candidate.relative_to(self.root)
            except ValueError as error:
                raise DesignToolError(
                    f"checkpoint manifest file escapes its bundle: {raw_path}"
                ) from error
            if not candidate.is_file():
                raise DesignToolError(f"checkpoint bundle file is missing: {raw_path}")
            observed = _sha256(candidate)
            if observed != expected.lower():
                raise DesignToolError(
                    f"checkpoint bundle SHA-256 mismatch for {raw_path}: "
                    f"expected {expected.lower()}, observed {observed}"
                )
            verified.append({"path": raw_path, "sha256": observed})
        declared = {
            str((manifest.parent / item["path"]).resolve()) for item in verified
        }
        observed_files = {
            str(path.resolve())
            for path in data_dir.rglob("*")
            if path.is_file() and not path.is_symlink()
        }
        symlinks = [path for path in data_dir.rglob("*") if path.is_symlink()]
        if symlinks:
            raise DesignToolError("checkpoint data_dir must not contain symlinks")
        if declared != observed_files:
            missing = sorted(
                Path(path).relative_to(data_dir).as_posix()
                for path in observed_files - declared
            )
            extra = sorted(Path(path).name for path in declared - observed_files)
            raise DesignToolError(
                f"checkpoint manifest must pin every data file; unpinned={missing}, absent={extra}"
            )
        return manifest, data_dir, manifest_digest, verified

    def _verify_revision(
        self, backend: str, expected: str, repository: Path | None = None
    ) -> str:
        variable = f"OPENAI4S_{backend.upper()}_REVISION"
        observed = os.environ.get(variable, "").strip()
        if not observed and repository is not None and (repository / ".git").exists():
            completed = subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
            if completed.returncode == 0:
                observed = completed.stdout.strip()
        if not observed:
            raise DesignToolError(
                f"{variable} is required when the backend revision cannot be read from a git checkout"
            )
        if observed != expected:
            raise DesignToolError(
                f"{backend} revision mismatch: expected {expected}, observed {observed}"
            )
        return observed

    def _run(
        self,
        command: list[str],
        output: Path,
        *,
        cwd: Path | None = None,
        offline: bool = False,
    ) -> dict[str, Any]:
        stdout_path = output / "stdout.log"
        stderr_path = output / "stderr.log"
        env = dict(os.environ)
        if offline:
            for key in tuple(env):
                if key.lower() in {
                    "http_proxy",
                    "https_proxy",
                    "all_proxy",
                    "ftp_proxy",
                    "no_proxy",
                    "rsync_proxy",
                }:
                    env.pop(key, None)
            env.update(
                {
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "WANDB_MODE": "offline",
                }
            )
        started = time.monotonic()
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(cwd or self.root),
                    env=env,
                    # This process reads MCP protocol frames from fd 0. An
                    # inherited stdin lets a backend that reads it consume
                    # requests the server still needed, desynchronising the
                    # stream for the rest of the process lifetime, or block on
                    # a pipe nobody will write until the timeout.
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise DesignToolError(
                    f"backend exceeded {self.timeout:g} second timeout"
                ) from error
            except OSError as error:
                raise DesignToolError(
                    f"backend could not start: {_clean_error(error)}"
                ) from error
        runtime = time.monotonic() - started
        if completed.returncode != 0:
            tail = _tail_text(stderr_path, 2000)
            suffix = f": {tail}" if tail else ""
            raise DesignToolError(
                f"backend exited with code {completed.returncode}{suffix}"
            )
        return {
            "returncode": completed.returncode,
            # Distinct from the record's own `runtime_seconds`, which times the
            # whole attempt. One name for two spans made a succeeded record and
            # a failed record report different quantities under one key, and
            # silently dropped checkpoint hashing time from the successful one.
            "backend_runtime_seconds": round(runtime, 6),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        }

    #: Fields the host computes about the attempt itself. A handler's payload
    #: may add to a terminal record, never restate its identity.
    _AUDIT_IDENTITY_KEYS = (
        "schema_version",
        "tool",
        "attempt_id",
        "seed",
        "backend_revision",
        "started_at",
        "runtime_seconds",
        "config_digest",
    )

    def _base_record(
        self,
        name: str,
        args: dict[str, Any],
        attempt: str,
        seed: int,
        started_at: str,
        start: float,
    ) -> dict[str, Any]:
        # Named `redacted` while redacting nothing, which is worse than no name
        # at all: the next author to add a credential-shaped tool field would
        # read it and assume the digest already excludes the value.
        return {
            "schema_version": _SCHEMA_VERSION,
            "tool": name,
            "attempt_id": attempt,
            "seed": seed,
            "backend_revision": args["backend_revision"],
            "started_at": started_at,
            "finished_at": _utc_now(),
            "runtime_seconds": round(time.monotonic() - start, 6),
            "config_digest": _digest_json(args),
        }

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        attempt = (
            str(args["attempt_id"])
            if isinstance(args, dict) and "attempt_id" in args
            else "unassigned"
        )
        output: Path | None = None
        seed = args.get("seed", -1) if isinstance(args, dict) else -1
        start = time.monotonic()
        started_at = _utc_now()
        try:
            attempt, seed, output, start, started_at = self._prepare(name, args)
            manifest = output / "terminal.json"
            requested_digest = _digest_json(args)
            if manifest.is_file():
                try:
                    existing = json.loads(manifest.read_text(encoding="utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return {
                        "schema_version": _SCHEMA_VERSION,
                        "tool": name,
                        "status": "failed",
                        "attempt_id": attempt,
                        "seed": seed,
                        "started_at": started_at,
                        "finished_at": _utc_now(),
                        "runtime_seconds": round(time.monotonic() - start, 6),
                        "error_type": "AttemptManifestUnreadable",
                        "error": (
                            "existing terminal manifest is unreadable; manual "
                            "reconciliation is required and the file was preserved"
                        ),
                        "config_digest": requested_digest,
                        "manifest_path": str(manifest),
                    }
                if (
                    isinstance(existing, dict)
                    and existing.get("tool") == name
                    and existing.get("attempt_id") == attempt
                    and existing.get("config_digest") == requested_digest
                ):
                    existing["manifest_path"] = str(manifest)
                    # A stored record outlives the process that earned it, so
                    # replaying one must not re-assert live-process admission
                    # the ledger no longer holds -- that is exactly what a
                    # restart is supposed to revoke. Re-derive it here, and say
                    # plainly that nothing ran, so a caller cannot read a
                    # replayed failure as a fresh one.
                    existing["replayed"] = True
                    replay_key = self._admission_key(name, args)
                    live_admission = self._admission.get(replay_key)
                    if live_admission is not None:
                        existing["bringup_admission"] = live_admission
                    else:
                        existing.pop("bringup_admission", None)
                    if (
                        self.require_admission
                        and existing.get("run_mode") == "formal"
                        and replay_key is not None
                        and live_admission is None
                    ):
                        raise DesignToolError(
                            "a stored formal record cannot be replayed in a process "
                            "that has not been admitted; run a real inference with "
                            "run_mode=canary and retry with a new attempt_id"
                        )
                    return existing
                return {
                    "schema_version": _SCHEMA_VERSION,
                    "tool": name,
                    "status": "failed",
                    "attempt_id": attempt,
                    "seed": seed,
                    "started_at": started_at,
                    "finished_at": _utc_now(),
                    "runtime_seconds": round(time.monotonic() - start, 6),
                    "error_type": "AttemptConflict",
                    "error": (
                        "attempt_id already has a terminal record with a different "
                        "tool or configuration; the original record was preserved"
                    ),
                    "config_digest": requested_digest,
                    "manifest_path": str(manifest),
                    "existing_status": (
                        existing.get("status") if isinstance(existing, dict) else None
                    ),
                }
            partial_files = [
                path for path in output.iterdir() if path.name != manifest.name
            ]
            if partial_files:
                raise InterruptedAttempt(
                    "attempt directory contains an interrupted run without a terminal "
                    "record; it is now failed and requires manual reconciliation"
                )
            run_mode = args.get("run_mode", "formal")
            admission_key = self._admission_key(name, args)
            prior_admission = self._admission.get(admission_key)
            if self.require_admission and run_mode == "formal":
                try:
                    prior_admission = self._admission.require(admission_key)
                except ModelAdmissionError as error:
                    raise DesignToolError(str(error)) from error
            handler = getattr(self, f"_tool_{name}")
            payload = handler(args, output)
            record = self._base_record(name, args, attempt, seed, started_at, start)
            base_identity = {
                key: record[key] for key in self._AUDIT_IDENTITY_KEYS if key in record
            }
            record.update({"status": "succeeded", **payload})
            # `payload` carries `**result`, read back from a separately
            # installed worker process. Spreading it last let the least
            # trusted component rewrite the attempt's own audit header --
            # `runtime_seconds` already means the backend's wall time on
            # success and the whole attempt's on failure, and a result key
            # named `config_digest` would break the replay comparison above.
            record.update(base_identity)
            record["status"] = "succeeded"
            record["run_mode"] = run_mode
            record["execution_target"] = args.get("execution_target", "local")
            if admission_key is not None and run_mode == "canary":
                try:
                    admission = self._admission.admit(
                        admission_key,
                        canary_attempt_id=attempt,
                        operation=name,
                        backend_revision=args.get("backend_revision"),
                        requested_checkpoint_digest=args["checkpoint_sha256"],
                        observed_checkpoint_digest=payload.get("checkpoint_digest"),
                        execution_target=args.get("execution_target", "local"),
                        verified_at=record["finished_at"],
                    )
                except ModelAdmissionError as error:
                    raise DesignToolError(str(error)) from error
                record["bringup_admission"] = admission
            elif prior_admission is not None:
                record["bringup_admission"] = prior_admission
        except Exception as error:  # terminal records are part of the public contract
            record = {
                "schema_version": _SCHEMA_VERSION,
                "tool": name,
                "status": "failed",
                "attempt_id": attempt,
                "seed": seed,
                "started_at": started_at,
                "finished_at": _utc_now(),
                "runtime_seconds": round(time.monotonic() - start, 6),
                "error_type": type(error).__name__,
                "error": _clean_error(error),
            }
            if isinstance(args, dict):
                record["config_digest"] = _digest_json(args)
                if isinstance(args.get("backend_revision"), str):
                    record["backend_revision"] = args["backend_revision"]
                record["run_mode"] = args.get("run_mode", "formal")
                record["execution_target"] = args.get("execution_target", "local")
        if output is not None:
            manifest = output / "terminal.json"
            record["manifest_path"] = str(manifest)
            _atomic_json(manifest, record)
        return record

    def _tool_generate_backbone(
        self, args: dict[str, Any], output: Path
    ) -> dict[str, Any]:
        if args.get("num_designs", 1) != 1:
            raise DesignToolError(
                "generate_backbone is one attempt per call; num_designs must equal 1"
            )
        target = self._path(args.get("target_pdb"), existing=True)
        checkpoint, checkpoint_digest = self._verify_checkpoint(args)
        repository = self._operator_path(
            "OPENAI4S_RFDIFFUSION_PATH", "vendor/RFdiffusion"
        )
        self._verify_revision("rfdiffusion", args["backend_revision"], repository)
        chains = _read_pdb_residues(target)
        target_chain = args.get("target_chain")
        if (
            not isinstance(target_chain, str)
            or len(target_chain) != 1
            or target_chain not in chains
        ):
            raise DesignToolError(f"target_chain is absent from PDB: {target_chain}")
        target_chains = args.get("target_chains", [target_chain])
        if (
            not isinstance(target_chains, list)
            or not target_chains
            or target_chains[0] != target_chain
            or len(set(target_chains)) != len(target_chains)
            or any(
                not isinstance(chain, str) or len(chain) != 1 or chain not in chains
                for chain in target_chains
            )
        ):
            raise DesignToolError(
                "target_chains must be unique existing chains and start with target_chain"
            )
        hotspots = args.get("hotspot_residues")
        if not isinstance(hotspots, list) or not hotspots:
            raise DesignToolError("hotspot_residues must be a non-empty list")
        available = {
            (item.chain, item.number)
            for chain in target_chains
            for item in chains[chain]
            if not item.insertion_code
        }
        normalized: list[str] = []
        for raw in hotspots:
            match = _HOTSPOT_RE.fullmatch(raw) if isinstance(raw, str) else None
            if not match:
                raise DesignToolError(f"invalid hotspot residue: {raw}")
            chain, number = match.group(1), int(match.group(2))
            if chain not in target_chains:
                raise DesignToolError(
                    f"hotspot {raw} is not on an explicit target chain: {target_chains}"
                )
            if (chain, number) not in available:
                raise DesignToolError(
                    f"hotspot residue does not exist in target PDB: {raw}"
                )
            normalized.append(f"{chain}{number}")
        if len(set(normalized)) != len(normalized):
            raise DesignToolError("hotspot_residues contains duplicates")
        length = args.get("binder_length")
        if (
            isinstance(length, bool)
            or not isinstance(length, int)
            or not 20 <= length <= 500
        ):
            raise DesignToolError("binder_length must be an integer in [20, 500]")
        prefix = output / "design"
        command = _parse_json_array_env("OPENAI4S_RFDIFFUSION_COMMAND")
        custom_command = bool(command)
        if not command:
            python = os.environ.get("OPENAI4S_RFDIFFUSION_PYTHON", sys.executable)
            command = [python, str(repository / "scripts" / "run_inference.py")]
        command.extend(
            [
                f"inference.input_pdb={target}",
                f"inference.output_prefix={prefix}",
                "inference.num_designs=1",
                "inference.deterministic=True",
                f"inference.design_startnum={args['seed']}",
                f"inference.ckpt_override_path={checkpoint}",
                f"diffuser.T={int(args.get('diffusion_steps', 50))}",
                f"denoiser.noise_scale_ca={float(args.get('noise_scale_ca', 1))}",
                f"denoiser.noise_scale_frame={float(args.get('noise_scale_frame', 1))}",
                "contigmap.contigs=["
                + "/0 ".join(
                    [_target_contig(chains[chain]) for chain in target_chains]
                    + [f"{length}-{length}"]
                )
                + "]",
                f"ppi.hotspot_res=[{','.join(normalized)}]",
            ]
        )
        config = {
            "command": command,
            "target_chain": target_chain,
            "target_chains": target_chains,
            "hotspot_residues": normalized,
            "input_digest": _sha256(target),
            "checkpoint_digest": checkpoint_digest,
        }
        _atomic_json(output / "resolved_config.json", config)
        run = self._run(
            command,
            output,
            cwd=self.root if custom_command else repository,
            offline=True,
        )
        pdb = output / f"design_{args['seed']}.pdb"
        trb = output / f"design_{args['seed']}.trb"
        if not pdb.is_file() or not trb.is_file():
            raise DesignToolError(
                "RFdiffusion did not produce the required PDB/TRB pair"
            )
        pdb_digest = _sha256(pdb)
        trb_digest = _sha256(trb)
        return {
            **run,
            "pdb_path": str(pdb),
            "trb_path": str(trb),
            "input_digest": config["input_digest"],
            "checkpoint_digest": checkpoint_digest,
            "output_digest": _digest_json({"pdb": pdb_digest, "trb": trb_digest}),
            "pdb_digest": pdb_digest,
            "trb_digest": trb_digest,
            "command": command,
            "resolved_config_path": str(output / "resolved_config.json"),
        }

    def _tool_design_sequence(
        self, args: dict[str, Any], output: Path
    ) -> dict[str, Any]:
        backbone = self._path(args.get("backbone_pdb"), existing=True)
        checkpoint, checkpoint_digest = self._verify_checkpoint(args)
        repository = self._operator_path(
            "OPENAI4S_PROTEINMPNN_PATH", "vendor/ProteinMPNN"
        )
        self._verify_revision("proteinmpnn", args["backend_revision"], repository)
        model_name = args.get("model_name", "v_48_020")
        if (
            not isinstance(model_name, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]+", model_name)
            or checkpoint.name != f"{model_name}.pt"
        ):
            raise DesignToolError(
                "ProteinMPNN checkpoint_path must end with the selected model_name plus .pt"
            )
        chains = _read_pdb_residues(backbone)
        source_sequences = _chain_sequences(chains)
        design_chains = args.get("design_chains")
        if (
            not isinstance(design_chains, list)
            or not design_chains
            or len(set(design_chains)) != len(design_chains)
        ):
            raise DesignToolError("design_chains must be a non-empty unique list")
        if any(chain not in chains for chain in design_chains):
            raise DesignToolError("every design chain must exist in backbone_pdb")
        fixed = args.get("fixed_positions")
        if not isinstance(fixed, dict) or set(fixed) != set(chains):
            raise DesignToolError(
                "fixed_positions must explicitly name every PDB chain"
            )
        fixed_json: dict[str, list[int]] = {}
        fixed_indexes: dict[str, set[int]] = {}
        for chain, residues in chains.items():
            available = set(range(1, len(residues) + 1))
            raw = fixed[chain]
            if raw == "all":
                positions = sorted(available)
            elif isinstance(raw, list) and all(
                isinstance(item, int) and not isinstance(item, bool) for item in raw
            ):
                if len(set(raw)) != len(raw):
                    raise DesignToolError(
                        f"fixed_positions[{chain}] contains duplicate positions"
                    )
                positions = sorted(set(raw))
                missing = set(positions) - available
                if missing:
                    raise DesignToolError(
                        f"fixed positions are absent from chain {chain}: {sorted(missing)}"
                    )
            else:
                raise DesignToolError(
                    f"fixed_positions[{chain}] must be 'all' or an integer list"
                )
            if chain not in design_chains and set(positions) != available:
                raise DesignToolError(
                    f"non-designed target chain {chain} must be fixed as 'all'"
                )
            fixed_json[chain] = positions
            fixed_indexes[chain] = {position - 1 for position in positions}
        stem = backbone.stem
        fixed_path = output / "fixed_positions.jsonl"
        fixed_path.write_text(
            json.dumps({stem: fixed_json}, sort_keys=True) + "\n", encoding="utf-8"
        )
        number = args.get("num_sequences")
        temperature = args.get("sampling_temp")
        if (
            isinstance(number, bool)
            or not isinstance(number, int)
            or not 1 <= number <= 10000
        ):
            raise DesignToolError("num_sequences must be an integer in [1, 10000]")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not 0 < temperature <= 1
        ):
            raise DesignToolError("sampling_temp must be in (0, 1]")
        command = _parse_json_array_env("OPENAI4S_PROTEINMPNN_COMMAND")
        custom_command = bool(command)
        if not command:
            python = os.environ.get("OPENAI4S_PROTEINMPNN_PYTHON", sys.executable)
            command = [python, str(repository / "protein_mpnn_run.py")]
        command.extend(
            [
                "--pdb_path",
                str(backbone),
                "--pdb_path_chains",
                " ".join(design_chains),
                "--fixed_positions_jsonl",
                str(fixed_path),
                "--out_folder",
                str(output),
                "--num_seq_per_target",
                str(number),
                "--sampling_temp",
                str(float(temperature)),
                "--model_name",
                model_name,
                "--seed",
                str(args["seed"]),
                "--path_to_model_weights",
                str(checkpoint.parent) + os.sep,
            ]
        )
        config = {
            "command": command,
            "input_digest": _sha256(backbone),
            "checkpoint_digest": checkpoint_digest,
            "source_sequences": source_sequences,
            "fixed_positions": fixed_json,
        }
        _atomic_json(output / "resolved_config.json", config)
        run = self._run(
            command,
            output,
            cwd=self.root if custom_command else repository,
            offline=True,
        )
        fasta = output / "seqs" / f"{stem}.fa"
        if not fasta.is_file():
            raise DesignToolError(
                f"ProteinMPNN did not produce expected FASTA: {fasta}"
            )
        designs = self._validate_mpnn_fasta(
            fasta, source_sequences, chains, design_chains, fixed_indexes, number
        )
        map_rows = [
            {
                "chain": chain,
                "input_length": len(sequence),
                "output_length": len(sequence),
                "positions": [
                    {
                        "chain_position": index,
                        "input_residue": residue.label,
                        "output_position": index,
                    }
                    for index, residue in enumerate(chains[chain], start=1)
                ],
            }
            for chain, sequence in source_sequences.items()
        ]
        map_path = output / "residue_map.json"
        _atomic_json(map_path, {"closed": True, "chains": map_rows})
        return {
            **run,
            "fasta_path": str(fasta),
            "residue_map_path": str(map_path),
            "sequence_count": len(designs),
            "sequences": designs,
            "input_digest": config["input_digest"],
            "checkpoint_digest": checkpoint_digest,
            "output_digest": _sha256(fasta),
            "command": command,
            "resolved_config_path": str(output / "resolved_config.json"),
            "validation": {
                "target_chains_unchanged": True,
                "fixed_positions_unchanged": True,
                "chain_lengths_match": True,
                "residue_map_closed": True,
            },
        }

    def _validate_mpnn_fasta(
        self,
        path: Path,
        source: dict[str, str],
        residues: dict[str, list[Residue]],
        design_chains: list[str],
        fixed_indexes: dict[str, set[int]],
        expected: int,
    ) -> list[dict[str, Any]]:
        records: list[tuple[str, str]] = []
        header: str | None = None
        chunks: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(">"):
                if header is not None:
                    records.append((header, "".join(chunks)))
                header, chunks = line[1:], []
            elif line.strip():
                chunks.append(line.strip())
        if header is not None:
            records.append((header, "".join(chunks)))
        if len(records) != expected + 1:
            raise DesignToolError(
                "ProteinMPNN FASTA must contain one native record plus exactly the requested designs"
            )
        # ProteinMPNN declares the chain mapping once, in the native record's
        # header; every sampled record carries only T/sample/score/
        # global_score/seq_recovery. Requiring it per design rejected every
        # genuine run, so read the order from the native header and let a
        # design header override it only where a fork does emit one.
        native_order = _mpnn_chain_order(records[0][0])
        validated: list[dict[str, Any]] = []
        for index, (header, combined) in enumerate(records[1 : expected + 1]):
            pieces = combined.split("/")
            order = _mpnn_chain_order(header) or native_order
            if order is None:
                raise DesignToolError(
                    "ProteinMPNN FASTA header lacks designed/fixed chain mapping"
                )
            if (
                set(order) != set(source)
                or len(order) != len(source)
                or len(pieces) != len(order)
            ):
                raise DesignToolError(
                    "ProteinMPNN output residue map does not close over all input chains"
                )
            output = dict(zip(order, pieces))
            for chain, original in source.items():
                proposed = _sequence(output[chain], f"ProteinMPNN output chain {chain}")
                if len(proposed) != len(original) or len(proposed) != len(
                    residues[chain]
                ):
                    raise DesignToolError(f"ProteinMPNN changed chain {chain} length")
                if chain not in design_chains and proposed != original:
                    raise DesignToolError(
                        f"ProteinMPNN changed non-designed target chain {chain}"
                    )
                for position in fixed_indexes[chain]:
                    if proposed[position] != original[position]:
                        residue = residues[chain][position]
                        raise DesignToolError(
                            f"ProteinMPNN changed fixed motif residue {residue.label}"
                        )
            validated.append(
                {"id": f"sequence-{index:04d}", "chains": output, "header": header}
            )
        return validated

    def _prediction(
        self, args: dict[str, Any], output: Path, *, complex_: bool
    ) -> dict[str, Any]:
        checkpoint, checkpoint_data_dir, checkpoint_digest, checkpoint_files = (
            self._verify_checkpoint_bundle(args)
        )
        self._verify_revision("colabfold", args["backend_revision"])
        if args.get("msa_mode", "single_sequence") != "single_sequence":
            raise DesignToolError(
                "formal prediction currently accepts only single_sequence no-MSA mode"
            )
        if complex_:
            sequences_raw = args.get("sequences")
            names = args.get("chain_names")
            if not isinstance(sequences_raw, list) or len(sequences_raw) < 2:
                raise DesignToolError("predict_complex requires at least two sequences")
            sequences = [
                _sequence(item, f"sequences[{index}]")
                for index, item in enumerate(sequences_raw)
            ]
            if (
                not isinstance(names, list)
                or len(names) != len(sequences)
                or len(set(names)) != len(names)
            ):
                raise DesignToolError("chain_names must be unique and match sequences")
            if any(not isinstance(name, str) or len(name) != 1 for name in names):
                raise DesignToolError("each chain name must be one character")
            fasta_text = ">complex\n" + ":".join(sequences) + "\n"
        else:
            sequences = [_sequence(args.get("sequence"))]
            names = ["A"]
            fasta_text = ">monomer\n" + sequences[0] + "\n"
        model_type = args.get("model_type")
        expected_model = "alphafold2_multimer_v3" if complex_ else None
        if expected_model and model_type != expected_model:
            raise DesignToolError(
                f"predict_complex model_type must be {expected_model}"
            )
        recycles = args.get("recycles")
        models = args.get("model_count")
        if (
            isinstance(recycles, bool)
            or not isinstance(recycles, int)
            or not 0 <= recycles <= 100
        ):
            raise DesignToolError("recycles must be an integer in [0, 100]")
        if (
            isinstance(models, bool)
            or not isinstance(models, int)
            or not 1 <= models <= 5
        ):
            raise DesignToolError("model_count must be an integer in [1, 5]")
        fasta = output / "input.fasta"
        fasta.write_text(fasta_text, encoding="utf-8")
        prediction_dir = output / "prediction"
        prediction_dir.mkdir()
        command = _parse_json_array_env("OPENAI4S_COLABFOLD_COMMAND") or [
            "colabfold_batch"
        ]
        if args.get("require_network_isolation", True) is not True:
            raise DesignToolError(
                "require_network_isolation must be true for formal structure "
                "prediction"
            )
        offline_prefix = _network_isolation_prefix()
        if not offline_prefix:
            raise DesignToolError(
                "OPENAI4S_PROTEIN_DESIGN_OFFLINE_PREFIX is required for formal structure prediction"
            )
        command.extend(
            [
                "--msa-mode",
                "single_sequence",
                "--model-type",
                str(model_type),
                "--num-recycle",
                str(recycles),
                "--num-models",
                str(models),
                "--num-seeds",
                "1",
                "--random-seed",
                str(args["seed"]),
                "--data",
                str(checkpoint_data_dir),
                str(fasta),
                str(prediction_dir),
            ]
        )
        guarded_command = offline_prefix + command
        config = {
            "command": guarded_command,
            "model_type": model_type,
            "checkpoint_digest": checkpoint_digest,
            "checkpoint_files": checkpoint_files,
            "recycles": recycles,
            "seed": args["seed"],
            "model_count": models,
            "msa_mode": "single_sequence",
            "templates": False,
            "initial_guess": False,
            "network_policy": (
                "os-isolated" if offline_prefix else "offline-environment"
            ),
            "sequence_digest": _digest_json(dict(zip(names, sequences))),
        }
        _atomic_json(output / "resolved_config.json", config)
        run = self._run(guarded_command, output, offline=True)
        pdbs = sorted(prediction_dir.rglob("*rank_001*.pdb"))
        if not pdbs:
            pdbs = sorted(prediction_dir.rglob("*.pdb"))
        scores = sorted(prediction_dir.rglob("*scores_rank_001*.json"))
        if not scores:
            scores = sorted(prediction_dir.rglob("*scores*.json"))
        if not pdbs or not scores:
            raise DesignToolError(
                "prediction backend did not emit both a PDB and raw scores JSON"
            )
        raw_scores = json.loads(scores[0].read_text(encoding="utf-8"))
        pae = raw_scores.get("pae")
        if not isinstance(pae, list):
            raise DesignToolError("prediction scores do not contain raw PAE")
        result: dict[str, Any] = {
            **run,
            "pdb_path": str(pdbs[0]),
            "raw_scores_path": str(scores[0]),
            "sequence_digest": config["sequence_digest"],
            "checkpoint_digest": checkpoint_digest,
            "output_digest": _sha256(pdbs[0]),
            "raw_scores_digest": _sha256(scores[0]),
            "ptm": raw_scores.get("ptm"),
            "mean_plddt": self._mean(raw_scores.get("plddt")),
            "command": guarded_command,
            "resolved_config_path": str(output / "resolved_config.json"),
            "templates": False,
            "initial_guess": False,
            "msa_mode": "single_sequence",
            "network_isolation_enforced": bool(offline_prefix),
        }
        if complex_:
            if raw_scores.get("iptm") is None:
                raise DesignToolError("complex prediction scores do not contain ipTM")
            result["iptm"] = raw_scores["iptm"]
            result["interface_pae"] = self._interface_pae(
                pae, [len(item) for item in sequences]
            )
        return result

    @staticmethod
    def _mean(values: Any) -> float | None:
        if not isinstance(values, list) or not values:
            return None
        numeric = [float(value) for value in values]
        return sum(numeric) / len(numeric)

    @staticmethod
    def _interface_pae(pae: list[Any], lengths: list[int]) -> float:
        total = sum(lengths)
        if len(pae) != total or any(
            not isinstance(row, list) or len(row) != total for row in pae
        ):
            raise DesignToolError(
                "raw PAE shape does not match complex sequence lengths"
            )
        chain_by_index: list[int] = []
        for chain, length in enumerate(lengths):
            chain_by_index.extend([chain] * length)
        cross = [
            float(pae[i][j])
            for i in range(total)
            for j in range(total)
            if chain_by_index[i] != chain_by_index[j]
        ]
        if not cross:
            raise DesignToolError("complex has no cross-chain PAE entries")
        return sum(cross) / len(cross)

    def _tool_predict_structure(
        self, args: dict[str, Any], output: Path
    ) -> dict[str, Any]:
        return self._prediction(args, output, complex_=False)

    def _tool_predict_complex(
        self, args: dict[str, Any], output: Path
    ) -> dict[str, Any]:
        return self._prediction(args, output, complex_=True)

    def _scientific_worker(
        self, name: str, args: dict[str, Any], output: Path
    ) -> dict[str, Any]:
        backend = (
            "esm2"
            if name == "score_stability"
            else "openmm" if name == "energy_minimize" else "pyrosetta"
        )
        self._verify_revision(backend, args["backend_revision"])
        request = dict(args)
        request["output_dir"] = str(output)
        if "pdb_path" in request:
            pdb = self._path(request["pdb_path"], existing=True)
            request["pdb_path"] = str(pdb)
            request["input_digest"] = _sha256(pdb)
        if name == "score_stability":
            checkpoint, digest = self._verify_checkpoint(args)
            request["checkpoint_path"] = str(checkpoint)
            request["checkpoint_digest"] = digest
            request["sequence"] = _sequence(args.get("sequence"))
            request["sequence_digest"] = _digest_json(request["sequence"])
        request_path = output / "worker_request.json"
        result_path = output / "worker_result.json"
        _atomic_json(request_path, request)
        env_name = f"OPENAI4S_{backend.upper()}_PYTHON"
        python = os.environ.get(env_name, sys.executable)
        worker = Path(__file__).with_name("scientific_backend.py")
        command = [
            python,
            str(worker),
            name,
            str(request_path),
            str(result_path),
        ]
        config = {
            "command": command,
            "input_digest": request.get("input_digest")
            or request.get("sequence_digest"),
            "checkpoint_digest": request.get("checkpoint_digest"),
        }
        _atomic_json(output / "resolved_config.json", config)
        run = self._run(command, output, offline=True)
        if not result_path.is_file():
            raise DesignToolError("scientific worker did not produce its result JSON")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise DesignToolError("scientific worker result must be an object")
        forbidden = {"interface_hbonds"} & set(result)
        if forbidden:
            raise DesignToolError(
                "scientific worker returned the incorrect interface_hbonds field"
            )
        return {
            **run,
            **result,
            "input_digest": config["input_digest"],
            "checkpoint_digest": config["checkpoint_digest"],
            "result_digest": _sha256(result_path),
            "result_path": str(result_path),
            "command": command,
            "resolved_config_path": str(output / "resolved_config.json"),
        }

    def _tool_rosetta_score(self, args: dict[str, Any], output: Path) -> dict[str, Any]:
        return self._scientific_worker("rosetta_score", args, output)

    def _tool_rosetta_relax(self, args: dict[str, Any], output: Path) -> dict[str, Any]:
        return self._scientific_worker("rosetta_relax", args, output)

    def _tool_rosetta_interface_score(
        self, args: dict[str, Any], output: Path
    ) -> dict[str, Any]:
        return self._scientific_worker("rosetta_interface_score", args, output)

    def _tool_score_stability(
        self, args: dict[str, Any], output: Path
    ) -> dict[str, Any]:
        return self._scientific_worker("score_stability", args, output)

    def _tool_energy_minimize(
        self, args: dict[str, Any], output: Path
    ) -> dict[str, Any]:
        return self._scientific_worker("energy_minimize", args, output)


__all__ = ["DesignToolError", "ProteinDesignService", "Residue"]
