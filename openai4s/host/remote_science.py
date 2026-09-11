"""Real remote scientific execution over registered SSH capabilities.

JSON host calls orchestrate these remote services, while the services return
real scientific outputs for the Code-as-Action runtime.  Missing services and
failed jobs are hard soft-errors: this layer never fabricates a structure or a
mutation score.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import math
import os
import re
import shlex
import subprocess
import uuid
from collections.abc import Callable, Mapping
from typing import Any


class RemoteScienceService:
    """Run verified folding and mutation-scoring wrappers on remote GPUs."""

    def __init__(
        self,
        *,
        registry_factory: Callable[[], Any] | None = None,
        run_command: Callable[..., Any] | None = None,
        environment: Callable[[], Mapping[str, str]] | None = None,
        job_suffix: Callable[[], str] | None = None,
        provenance_recorder: Callable[..., None] | None = None,
    ) -> None:
        self._registry_factory = registry_factory
        self._run_command = run_command
        self._environment = environment
        self._job_suffix_factory = job_suffix
        self._provenance_recorder = provenance_recorder
        self._remote_provenance: list[dict] | None = None

    def _registry(self) -> Any:
        if self._registry_factory is not None:
            return self._registry_factory()
        from openai4s.compute import registry

        return registry

    def _runner(self) -> Callable[..., Any]:
        return self._run_command or subprocess.run

    def _env(self) -> Mapping[str, str]:
        return self._environment() if self._environment is not None else os.environ

    def _job_suffix(self) -> str:
        if self._job_suffix_factory is not None:
            return self._job_suffix_factory()
        return uuid.uuid4().hex[:8]

    def record_remote_provenance(
        self,
        service: str,
        host: str,
        engine: str | None,
        remote_dir: str,
        provenance_json: str | None,
    ) -> None:
        """Buffer one remote job's environment for the producing cell."""
        environment = None
        if provenance_json:
            try:
                environment = json.loads(provenance_json.strip())
            except Exception:  # noqa: BLE001 - malformed provenance is non-fatal
                environment = None
        entry = {
            "service": service,
            "host": host,
            "engine": engine,
            "remote_dir": remote_dir,
            "env": environment,
        }
        buffer = getattr(self, "_remote_provenance", None)
        if buffer is None:
            buffer = []
            self._remote_provenance = buffer
        buffer.append(entry)

    def pop_remote_provenance(self) -> list:
        """Return and clear buffered remote-job provenance, drained per cell."""
        buffer = getattr(self, "_remote_provenance", None) or []
        self._remote_provenance = []
        return buffer

    def _record_provenance(
        self,
        service: str,
        host: str,
        engine: str | None,
        remote_dir: str,
        provenance_json: str | None,
    ) -> None:
        recorder = self._provenance_recorder or self.record_remote_provenance
        recorder(service, host, engine, remote_dir, provenance_json)

    def fold(self, spec: dict) -> dict:
        """Run a real remote Protenix/AF3-class single-sequence prediction."""
        try:
            sequence = _sequence(spec.get("sequence"), 1200)
            gpu = _integer(spec.get("gpu", 0), "gpu", minimum=0)
            cycle = _integer(spec.get("cycle", 10), "cycle", minimum=1)
            step = _integer(spec.get("step", 40), "step", minimum=1)
        except ValueError as error:
            return {"error": f"fold: invalid_input: {error}"}
        name = (
            re.sub(r"[^A-Za-z0-9_-]", "", str(spec.get("name") or "protein"))
            or "protein"
        )
        registry = self._registry()
        host, capability = registry.capability_host("fold")
        if not host:
            return {
                "error": "fold: no remote GPU host with a folding service is "
                "configured (Settings → Remote GPU). Refusing to fabricate a "
                "structure — configure a host first."
            }
        try:
            _gpu_bound(registry, host, gpu)
        except ValueError as error:
            return {"error": f"fold: invalid_input: {error}"}
        environment = self._env()
        script = (capability or {}).get("script") or environment.get(
            "OPENAI4S_FOLD_SCRIPT", "/opt/os-fold/fold.sh"
        )
        base = environment.get("OPENAI4S_FOLD_JOBS_DIR", "/opt/os-fold/jobs")
        jobdir = f"{base}/{name}_{self._job_suffix()}"
        remote = (
            f"mkdir -p {shlex.quote(jobdir)} && {shlex.quote(script)} "
            f"--seq {shlex.quote(sequence)} --name {shlex.quote(name)} "
            f"--out {shlex.quote(jobdir)} --gpu {gpu} --cycle {cycle} --step {step}"
        )
        try:
            process = self._runner()(
                [
                    "ssh",
                    "-o",
                    "ConnectTimeout=15",
                    "-o",
                    "BatchMode=yes",
                    host,
                    remote,
                ],
                capture_output=True,
                timeout=900,
            )
        except subprocess.TimeoutExpired:
            return {"error": f"fold: timed out after 900s on {host}"}
        except OSError:
            return {"error": f"fold: transport_error: ssh to {host} failed"}
        if process.returncode != 0:
            return {
                "error": f"fold: remote_exit: prediction failed on {host} (rc={process.returncode})"
            }
        try:
            output = process.stdout.decode("utf-8")
        except UnicodeError:
            return {"error": "fold: invalid_output: output is not UTF-8"}

        if output.count("===FOLD_DONE===") != 1:
            return {
                "error": "fold: invalid_output: missing or ambiguous completion marker"
            }
        manifest_json = _block(
            output, "===FOLD_RESULT_JSON===", "===END_FOLD_RESULT_JSON==="
        )
        pdb_base64 = _block(output, "===FOLD_PDB_B64===", "===FOLD_PLDDT_CSV_B64===")
        plddt_base64 = _block(
            output,
            "===FOLD_PLDDT_CSV_B64===",
            "===FOLD_CONFIDENCE_JSON_B64===",
        )
        confidence_base64 = _block(
            output,
            "===FOLD_CONFIDENCE_JSON_B64===",
            "===PROVENANCE_JSON===",
        ) or _block(
            output,
            "===FOLD_CONFIDENCE_JSON_B64===",
            "===FOLD_DONE===",
        )
        try:
            manifest = _json_object(manifest_json, "manifest")
            pdb_text = _decoded_text(pdb_base64, "PDB")
            plddt_csv = _decoded_text(plddt_base64, "pLDDT CSV")
            confidence = _json_object(
                _decoded_text(confidence_base64, "confidence"), "confidence"
            )
            residues = _validate_pdb(pdb_text, sequence)
            _matches_length(manifest, "length", len(sequence))
            _matches_length(manifest, "residues_modeled", len(residues))
            _validate_plddt(plddt_csv, residues)
            engine = _metadata_text(
                (
                    manifest["engine"]
                    if manifest.get("engine") is not None
                    else (capability or {}).get("engine")
                ),
                "engine",
            )
            if manifest.get("msa") is not None and not isinstance(
                manifest["msa"], bool
            ):
                raise ValueError("msa must be a boolean when present")
            for field in ("mean_plddt", "ptm"):
                if manifest.get(field) is not None:
                    _finite(manifest[field], field)
        except ValueError as error:
            return {"error": f"fold: invalid_output: {error}"}
        provenance_json = _block(
            output, "===PROVENANCE_JSON===", "===END_PROVENANCE_JSON==="
        )
        self._record_provenance("fold", host, engine, jobdir, provenance_json)
        return {
            "ok": True,
            "pdb": pdb_text,
            "plddt_csv": plddt_csv,
            "confidence": confidence,
            "mean_plddt": manifest.get("mean_plddt"),
            "ptm": manifest.get("ptm"),
            "length": manifest.get("length"),
            "residues_modeled": manifest.get("residues_modeled"),
            "engine": engine,
            "msa": manifest.get("msa"),
            "host": host,
            "remote_dir": jobdir,
        }

    def score_mutations(self, spec: dict) -> dict:
        """Run real remote ESM masked-marginal mutation scoring."""
        try:
            sequence = _sequence(spec.get("sequence"), 1024)
            gpu = _integer(spec.get("gpu", 0), "gpu", minimum=0)
            positions = _positions(spec.get("positions"), len(sequence))
        except ValueError as error:
            return {"error": f"score_mutations: invalid_input: {error}"}
        registry = self._registry()
        host, capability = registry.capability_host("score_mutations")
        if not host:
            return {
                "error": "score_mutations: no remote GPU host has a mutation-"
                "scoring service configured, so there is no real predictor "
                "available. Do NOT fabricate scores (no np.random, no "
                "BLOSUM-as-ESM, no fake heatmap) — report that this step "
                "cannot be done for real. Provision a service via "
                "Settings → Remote GPU."
            }
        try:
            _gpu_bound(registry, host, gpu)
        except ValueError as error:
            return {"error": f"score_mutations: invalid_input: {error}"}
        script = (capability or {}).get("script")
        if not script:
            return {"error": f"score_mutations: host {host} has no script recorded"}
        name = (
            re.sub(r"[^A-Za-z0-9_-]", "", str(spec.get("name") or "protein"))
            or "protein"
        )
        base = self._env().get("OPENAI4S_ESM_JOBS_DIR", "/opt/os-esm/jobs")
        jobdir = f"{base}/{name}_{self._job_suffix()}"
        remote = (
            f"mkdir -p {shlex.quote(jobdir)} && {shlex.quote(script)} "
            f"--seq {shlex.quote(sequence)} --name {shlex.quote(name)} "
            f"--out {shlex.quote(jobdir)} --gpu {gpu}"
        )
        if positions is not None:
            position_string = ",".join(str(position) for position in positions)
            remote += f" --positions {shlex.quote(position_string)}"
        try:
            process = self._runner()(
                [
                    "ssh",
                    "-o",
                    "ConnectTimeout=15",
                    "-o",
                    "BatchMode=yes",
                    host,
                    remote,
                ],
                capture_output=True,
                timeout=1200,
            )
        except subprocess.TimeoutExpired:
            return {"error": f"score_mutations: timed out after 1200s on {host}"}
        except OSError:
            return {"error": f"score_mutations: transport_error: ssh to {host} failed"}
        if process.returncode != 0:
            return {
                "error": f"score_mutations: remote_exit: scoring failed on {host} (rc={process.returncode})"
            }
        try:
            output = process.stdout.decode("utf-8")
        except UnicodeError:
            return {"error": "score_mutations: invalid_output: output is not UTF-8"}

        if output.count("===MUT_DONE===") != 1:
            return {
                "error": "score_mutations: invalid_output: missing or ambiguous completion marker"
            }
        summary_json = _block(
            output, "===MUT_RESULT_JSON===", "===END_MUT_RESULT_JSON==="
        )
        csv_base64 = _block(
            output, "===MUT_CSV_B64===", "===PROVENANCE_JSON==="
        ) or _block(output, "===MUT_CSV_B64===", "===MUT_DONE===")
        try:
            summary = _json_object(summary_json, "summary")
            scores_csv = _decoded_text(csv_base64, "scores CSV")
            _matches_length(summary, "length", len(sequence))
            scores = _validate_mutations(scores_csv, sequence, positions)
            _validate_mutation_summary(summary, scores)
            model = _metadata_text(
                (
                    summary["model"]
                    if summary.get("model") is not None
                    else (capability or {}).get("engine")
                ),
                "model",
            )
            if summary.get("mean_score") is not None:
                _finite(summary["mean_score"], "mean_score")
                # The registered scoring protocol does not define the summary
                # statistic. Keep the compatible field without endorsing an
                # unverifiable aggregate or inventing a CSV averaging rule.
                summary["mean_score"] = None
        except ValueError as error:
            return {"error": f"score_mutations: invalid_output: {error}"}
        provenance_json = _block(
            output, "===PROVENANCE_JSON===", "===END_PROVENANCE_JSON==="
        )
        self._record_provenance(
            "score_mutations",
            host,
            model,
            jobdir,
            provenance_json,
        )
        return {
            "ok": True,
            "scores_csv": scores_csv,
            "summary": summary,
            "mean_score": summary.get("mean_score"),
            "top5": summary.get("top5"),
            "length": summary.get("length"),
            "model": model,
            "host": host,
            "remote_dir": jobdir,
        }


# The wrappers accept the 20 canonical amino acids. Never erase unsupported
# input: otherwise provenance and the submitted biological question diverge.
_AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"
_RESIDUES = dict(
    zip(
        "ALA CYS ASP GLU PHE GLY HIS ILE LYS LEU MET ASN PRO GLN ARG SER THR VAL TRP TYR".split(),
        _AMINO_ACIDS,
    )
)


def _sequence(value: Any, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("a non-empty protein sequence string is required")
    sequence = "".join(value.split())
    for position, residue in enumerate(sequence, 1):
        if not residue.isascii() or residue.upper() not in _AMINO_ACIDS:
            raise ValueError(
                f"unsupported residue at normalized position {position} (1-based)"
            )
    sequence = sequence.upper()
    if len(sequence) > limit:
        raise ValueError(f"sequence too long ({len(sequence)} aa); cap is {limit}")
    return sequence


def _integer(value: Any, field: str, *, minimum: int) -> int:
    if isinstance(value, str) and re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        try:
            value = int(value.strip())
        except ValueError:
            raise ValueError(f"{field} must be an integer >= {minimum}") from None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _positions(value: Any, length: int) -> list[int] | None:
    if value is None:
        return None
    if isinstance(value, str):
        # "" would otherwise become [""] and report a per-item error; every
        # empty spelling is the same mistake and gets the same message.
        value = value.split(",") if value.strip() else []
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(
            "positions must be a non-empty list, tuple or comma-separated "
            "integers; pass None to score every position"
        )
    positions = [_integer(item, "position", minimum=1) for item in value]
    if any(position > length for position in positions):
        raise ValueError("position exceeds sequence length")
    return positions


def _gpu_bound(registry: Any, host: str, gpu: int) -> None:
    # A zero count is the existing registry's unknown sentinel.
    get_host = getattr(registry, "get_host", None)
    metadata = get_host(host) if get_host is not None else None
    count = (metadata or {}).get("gpu_count")
    if (
        isinstance(count, int)
        and not isinstance(count, bool)
        and count > 0
        and gpu >= count
    ):
        raise ValueError("gpu index exceeds registered device count")


def _decoded_text(value: str | None, field: str) -> str:
    if value is None:
        raise ValueError(f"missing {field} block")
    try:
        # base64 from the wrapper can be line-wrapped, but arbitrary bytes and
        # invalid UTF-8 must not be silently discarded or replaced. A leading
        # BOM is the one exception: it is an encoding signature, not content,
        # and a wrapper written on a BOM-emitting toolchain is not malformed.
        text = base64.b64decode("".join(value.split()), validate=True).decode(
            "utf-8-sig"
        )
    except (ValueError, binascii.Error, UnicodeError):
        raise ValueError(f"invalid base64 or UTF-8 in {field}") from None
    if not text.strip():
        raise ValueError(f"empty {field}")
    return text


class _NonFiniteJSON(ValueError):
    """A NaN/Infinity token inside an otherwise well-formed JSON document."""


def _json_object(value: str | None, field: str) -> dict:
    def invalid_number(_: str) -> None:
        raise _NonFiniteJSON("non-finite JSON number")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict:
        result: dict = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    try:
        result = json.loads(
            value or "", parse_constant=invalid_number, object_pairs_hook=unique_object
        )
    except _NonFiniteJSON:
        # json.loads wraps hook errors in its own ValueError family; keep the
        # diagnosis distinct from "not JSON at all", which it is not.
        raise ValueError(f"non-finite number in {field}") from None
    except (ValueError, RecursionError):
        raise ValueError(f"invalid {field} JSON") from None
    if not isinstance(result, dict) or not result:
        raise ValueError(f"{field} JSON must be a non-empty object")
    # JSON exponents can overflow even though they are not NaN/Infinity tokens.
    pending = list(result.values())
    while pending:
        item = pending.pop()
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"non-finite number in {field}")
        if isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return result


def _metadata_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string when present")
    return value


_ASCII_FLOAT = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _finite(value: Any, field: str) -> float:
    # Text fields (PDB columns, CSV cells) must be plain ASCII decimals:
    # ``float()`` alone also accepts ``1_0.5`` and Unicode digits, which no
    # PDB reader or the workbench viewer parse the same way. ``[0-9]`` rather
    # than ``\d`` because ``\d`` matches those Unicode digits too.
    if isinstance(value, str) and not _ASCII_FLOAT.fullmatch(value.strip()):
        raise ValueError(f"invalid numeric {field}")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"invalid numeric {field}") from None
    if isinstance(value, bool) or not math.isfinite(result):
        raise ValueError(f"non-finite or invalid {field}")
    return result


def _matches_length(manifest: dict, field: str, length: int) -> None:
    value = manifest.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value != length:
        raise ValueError(f"{field} conflicts with input or modeled residues")


def _validate_pdb(text: str, sequence: str) -> dict[tuple[str, int, str], str]:
    residues: dict[tuple[str, int, str], str] = {}
    alpha_carbons: set[tuple[str, int, str]] = set()
    models = 0
    in_model = False
    ended = False
    coordinates_seen = False
    for line in text.splitlines():
        record = line[:6].strip()
        if not record:
            continue
        if ended:
            raise ValueError("PDB records follow the END boundary")
        if record == "MODEL":
            models += 1
            if models > 1 or coordinates_seen:
                raise ValueError("multiple or misplaced PDB models are ambiguous")
            in_model = True
            continue
        if record == "ENDMDL":
            if not in_model:
                raise ValueError("PDB ENDMDL has no open model")
            in_model = False
            continue
        if record == "END":
            if in_model:
                raise ValueError("PDB model is missing ENDMDL")
            ended = True
            continue
        if record.startswith("END"):
            raise ValueError("unsupported PDB END record")
        if record not in {"ATOM", "HETATM"}:
            continue
        if models and not in_model:
            raise ValueError("PDB coordinates fall outside the model")
        coordinates_seen = True
        if len(line) < 54 or not line[12:16].strip():
            raise ValueError("malformed PDB coordinate record")
        try:
            int(line[6:11])
            number = int(line[22:26])
        except ValueError:
            raise ValueError("invalid PDB atom or residue identity") from None
        for start in (30, 38, 46):
            _finite(line[start : start + 8], "PDB coordinate")
        if record != "ATOM":
            continue
        name = line[17:20]
        if name not in _RESIDUES:
            raise ValueError("unsupported modeled PDB residue")
        identity = (line[21], number, line[26])
        if identity in residues and residues[identity] != name:
            raise ValueError("conflicting PDB residue identity")
        residues[identity] = name
        if line[12:16].strip() == "CA":
            if identity in alpha_carbons:
                raise ValueError("ambiguous PDB alpha-carbon record")
            alpha_carbons.add(identity)
    if in_model:
        raise ValueError("PDB model is missing ENDMDL")
    if (
        len({key[0] for key in residues}) != 1
        or len(residues) != len(sequence)
        or alpha_carbons != set(residues)
        or "".join(_RESIDUES[name] for name in residues.values()) != sequence
    ):
        raise ValueError(
            "PDB modeled residues do not match the complete input sequence"
        )
    return residues


def _csv_rows(text: str, field: str) -> tuple[list[str], list[dict[str, str]]]:
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error:
        raise ValueError(f"malformed {field} CSV") from None
    # ``csv.reader`` yields ``[]`` for a blank line. Trailing ones are how
    # ``print(df.to_csv())`` and friends terminate a file, not a short row;
    # a blank line *between* rows still fails the width check below.
    while rows and not rows[-1]:
        rows.pop()
    if len(rows) < 2 or not rows[0] or len(set(rows[0])) != len(rows[0]):
        raise ValueError(f"empty or ambiguous {field} CSV")
    headers = rows[0]
    if any(len(row) != len(headers) for row in rows[1:]):
        raise ValueError(f"malformed {field} CSV row")
    return headers, [dict(zip(headers, row)) for row in rows[1:]]


def _validate_plddt(text: str, residues: dict[tuple[str, int, str], str]) -> None:
    headers, rows = _csv_rows(text, "pLDDT")
    canonical = {"chain", "resid", "resname", "plddt"}.issubset(headers)
    legacy = {"residue", "plddt"}.issubset(headers)
    if canonical == legacy or (legacy and set(headers) & {"chain", "resid", "resname"}):
        raise ValueError("missing or ambiguous pLDDT CSV columns")
    identities = list(residues)
    seen: set[tuple[str, int, str]] = set()
    for row in rows:
        if canonical:
            identity = (
                row["chain"],
                _integer(row["resid"], "pLDDT residue", minimum=1),
                " ",
            )
            if residues.get(identity) != row["resname"]:
                raise ValueError("pLDDT residue conflicts with PDB")
        else:
            position = _integer(row["residue"], "pLDDT residue", minimum=1)
            if position > len(identities):
                raise ValueError("pLDDT residue exceeds input length")
            identity = identities[position - 1]
        if identity in seen:
            raise ValueError("duplicate pLDDT residue")
        seen.add(identity)
        _finite(row["plddt"], "pLDDT")
    if seen != set(residues):
        raise ValueError("incomplete pLDDT residues")


def _validate_mutations(
    text: str, sequence: str, positions: list[int] | None
) -> dict[str, float]:
    headers, rows = _csv_rows(text, "mutation")
    compact = {"mutation", "score"}.issubset(headers)
    explicit = {"position", "wt", "mut", "mutation", "esm_score"}.issubset(headers)
    if compact == explicit or (
        compact and set(headers) & {"position", "wt", "mut", "esm_score"}
    ):
        raise ValueError("missing or ambiguous mutation CSV columns")
    seen: dict[str, float] = {}
    covered: set[int] = set()
    requested = (
        set(positions) if positions is not None else set(range(1, len(sequence) + 1))
    )
    for row in rows:
        match = re.fullmatch(
            r"([ACDEFGHIKLMNPQRSTVWY])([1-9][0-9]*)([ACDEFGHIKLMNPQRSTVWY])",
            row["mutation"],
        )
        if match is None or row["mutation"] in seen:
            raise ValueError("invalid or duplicate mutation row")
        wt, position_text, mutant = match.groups()
        position = _integer(position_text, "mutation position", minimum=1)
        if (
            position > len(sequence)
            or sequence[position - 1] != wt
            or position not in requested
        ):
            raise ValueError("mutation position or wild-type conflicts with input")
        if explicit and (
            _integer(row["position"], "mutation position", minimum=1) != position
            or row["wt"] != wt
            or row["mut"] != mutant
        ):
            raise ValueError("explicit mutation columns conflict")
        seen[row["mutation"]] = _finite(
            row["score"] if compact else row["esm_score"], "mutation score"
        )
        covered.add(position)
    if covered != requested:
        raise ValueError("mutation CSV is missing requested positions")
    return seen


#: Absolute agreement required between a top5 score and its CSV row.
_SCORE_TOLERANCE = 1e-4


def _validate_mutation_summary(summary: dict, scores: dict[str, float]) -> None:
    top = summary.get("top5")
    if top is None:
        return
    if not isinstance(top, list) or len(top) > 5:
        raise ValueError("top5 must be a list of at most five mutation objects")
    seen: set[str] = set()
    for row in top:
        if not isinstance(row, dict):
            raise ValueError("top5 contains a non-object entry")
        mutation = row.get("mutation")
        if not isinstance(mutation, str) or mutation not in scores or mutation in seen:
            raise ValueError("top5 mutation conflicts with validated CSV")
        seen.add(mutation)
        score_keys = [key for key in ("score", "esm_score") if key in row]
        if len(score_keys) != 1:
            raise ValueError("top5 has missing or ambiguous score fields")
        # The CSV and the summary are two serializations of one number (a
        # float32 tensor printed by pandas vs. json.dumps(float(x)) already
        # differ in the 8th digit). Scores are log-likelihood ratios of order
        # 0.1–10, so this tolerance cannot hide a substantively different top5
        # while it stops honest rounding from being reported as fabrication.
        if not math.isclose(
            _finite(row[score_keys[0]], "top5 score"),
            scores[mutation],
            rel_tol=1e-6,
            abs_tol=_SCORE_TOLERANCE,
        ):
            raise ValueError("top5 score conflicts with validated CSV")
        # Optional explicit identities must be complete and agree with the
        # mutation already checked against the input sequence and CSV.
        identity_keys = {"position", "wt", "mut"}
        if identity_keys.intersection(row):
            if not identity_keys.issubset(row):
                raise ValueError("top5 has incomplete explicit mutation fields")
            if (
                _integer(row["position"], "top5 position", minimum=1)
                != int(mutation[1:-1])
                or row["wt"] != mutation[0]
                or row["mut"] != mutation[-1]
            ):
                raise ValueError("top5 explicit mutation fields conflict with CSV")


def _block(output: str, start: str, end: str) -> str | None:
    if output.count(start) != 1 or output.count(end) != 1:
        return None
    index = output.find(start)
    if index < 0:
        return None
    index += len(start)
    stop = output.find(end, index)
    return output[index:stop] if stop >= 0 else None


__all__ = ["RemoteScienceService"]
