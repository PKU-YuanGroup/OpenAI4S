"""Real remote scientific execution over registered SSH capabilities.

JSON host calls orchestrate these remote services, while the services return
real scientific outputs for the Code-as-Action runtime.  Missing services and
failed jobs are hard soft-errors: this layer never fabricates a structure or a
mutation score.
"""

from __future__ import annotations

import base64
import json
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
        sequence = "".join((spec.get("sequence") or "").split()).upper()
        sequence = re.sub(r"[^ACDEFGHIKLMNPQRSTVWY]", "", sequence)
        if not sequence:
            return {"error": "fold: a protein 'sequence' (amino acids) is required"}
        if len(sequence) > 1200:
            return {
                "error": f"fold: sequence too long ({len(sequence)} aa); the demo "
                "host caps single-sequence folds at 1200 aa"
            }
        name = (
            re.sub(r"[^A-Za-z0-9_-]", "", str(spec.get("name") or "protein"))
            or "protein"
        )
        gpu = int(spec.get("gpu", 0))
        cycle = int(spec.get("cycle", 10))
        step = int(spec.get("step", 40))
        host, capability = self._registry().capability_host("fold")
        if not host:
            return {
                "error": "fold: no remote GPU host with a folding service is "
                "configured (Settings → Remote GPU). Refusing to fabricate a "
                "structure — configure a host first."
            }
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
        except OSError as error:
            return {"error": f"fold: ssh to {host} failed: {error}"}
        output = process.stdout.decode("utf-8", "replace")
        error_output = process.stderr.decode("utf-8", "replace")

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
        if not (manifest_json and pdb_base64):
            tail = (
                error_output[-800:] if error_output.strip() else output[-800:]
            ).strip()
            return {
                "error": f"fold: prediction did not complete on {host} "
                f"(rc={process.returncode}). tail: {tail}"
            }
        try:
            manifest = json.loads(manifest_json.strip())
            pdb_text = base64.b64decode(pdb_base64.strip()).decode("utf-8", "replace")
            plddt_csv = (
                base64.b64decode(plddt_base64.strip()).decode("utf-8", "replace")
                if plddt_base64
                else ""
            )
            confidence = (
                json.loads(
                    base64.b64decode(confidence_base64.strip()).decode(
                        "utf-8", "replace"
                    )
                )
                if confidence_base64
                else {}
            )
        except Exception as error:  # noqa: BLE001 - preserve soft-fail wire contract
            return {"error": f"fold: could not parse prediction output: {error}"}
        provenance_json = _block(
            output, "===PROVENANCE_JSON===", "===END_PROVENANCE_JSON==="
        )
        self._record_provenance(
            "fold", host, manifest.get("engine"), jobdir, provenance_json
        )
        return {
            "ok": True,
            "pdb": pdb_text,
            "plddt_csv": plddt_csv,
            "confidence": confidence,
            "mean_plddt": manifest.get("mean_plddt"),
            "ptm": manifest.get("ptm"),
            "length": manifest.get("length"),
            "residues_modeled": manifest.get("residues_modeled"),
            "engine": manifest.get("engine", "protenix_base_default_v1.0.0"),
            "msa": manifest.get("msa", False),
            "host": f"{host} (8×NVIDIA A100-80GB · Protenix AF3-class)",
            "remote_dir": jobdir,
        }

    def score_mutations(self, spec: dict) -> dict:
        """Run real remote ESM masked-marginal mutation scoring."""
        sequence = "".join((spec.get("sequence") or "").split()).upper()
        sequence = re.sub(r"[^ACDEFGHIKLMNPQRSTVWY]", "", sequence)
        if not sequence:
            return {"error": "score_mutations: a protein 'sequence' is required"}
        if len(sequence) > 1024:
            return {
                "error": f"score_mutations: sequence too long ({len(sequence)} aa); "
                "cap is 1024"
            }
        host, capability = self._registry().capability_host("score_mutations")
        if not host:
            return {
                "error": "score_mutations: no remote GPU host has a mutation-"
                "scoring service configured, so there is no real predictor "
                "available. Do NOT fabricate scores (no np.random, no "
                "BLOSUM-as-ESM, no fake heatmap) — report that this step "
                "cannot be done for real. Provision a service via "
                "Settings → Remote GPU."
            }
        script = (capability or {}).get("script")
        if not script:
            return {"error": f"score_mutations: host {host} has no script recorded"}
        name = (
            re.sub(r"[^A-Za-z0-9_-]", "", str(spec.get("name") or "protein"))
            or "protein"
        )
        gpu = int(spec.get("gpu", 0))
        positions = spec.get("positions")
        base = self._env().get("OPENAI4S_ESM_JOBS_DIR", "/opt/os-esm/jobs")
        jobdir = f"{base}/{name}_{self._job_suffix()}"
        remote = (
            f"mkdir -p {shlex.quote(jobdir)} && {shlex.quote(script)} "
            f"--seq {shlex.quote(sequence)} --name {shlex.quote(name)} "
            f"--out {shlex.quote(jobdir)} --gpu {gpu}"
        )
        if positions:
            position_string = (
                ",".join(str(int(position)) for position in positions)
                if isinstance(positions, (list, tuple))
                else str(positions)
            )
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
        except OSError as error:
            return {"error": f"score_mutations: ssh to {host} failed: {error}"}
        output = process.stdout.decode("utf-8", "replace")
        error_output = process.stderr.decode("utf-8", "replace")

        summary_json = _block(
            output, "===MUT_RESULT_JSON===", "===END_MUT_RESULT_JSON==="
        )
        csv_base64 = _block(
            output, "===MUT_CSV_B64===", "===PROVENANCE_JSON==="
        ) or _block(output, "===MUT_CSV_B64===", "===MUT_DONE===")
        if not (summary_json and csv_base64):
            tail = (
                error_output[-800:] if error_output.strip() else output[-800:]
            ).strip()
            return {
                "error": f"score_mutations: no real result from {host} "
                f"(rc={process.returncode}) — report the failure, do NOT "
                f"fabricate. tail: {tail}"
            }
        try:
            summary = json.loads(summary_json.strip())
            scores_csv = base64.b64decode(csv_base64.strip()).decode("utf-8", "replace")
        except Exception as error:  # noqa: BLE001 - preserve soft-fail wire contract
            return {"error": f"score_mutations: could not parse output: {error}"}
        provenance_json = _block(
            output, "===PROVENANCE_JSON===", "===END_PROVENANCE_JSON==="
        )
        self._record_provenance(
            "score_mutations",
            host,
            (capability or {}).get("engine"),
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
            "model": summary.get("model") or (capability or {}).get("engine"),
            "host": f"{host} · {(capability or {}).get('engine', 'ESM')}",
            "remote_dir": jobdir,
        }


def _block(output: str, start: str, end: str) -> str | None:
    index = output.find(start)
    if index < 0:
        return None
    index += len(start)
    stop = output.find(end, index)
    return output[index:stop] if stop >= 0 else None


__all__ = ["RemoteScienceService"]
