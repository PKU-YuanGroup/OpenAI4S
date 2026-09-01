#!/usr/bin/env python3
"""Generate independent Scenario codebases through the real OpenAI4S CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

NAMES = (
    "01_single_step_retrosynthesis",
    "02_multistep_route_planning",
    "03_atom_mapping",
    "04_forward_prediction",
    "05_condition_recommendation",
    "06_yield_estimation",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _root() -> Path:
    return Path(__file__).resolve().parents[4]


def _command(root: Path, query: Path) -> list[str]:
    local = root / ".venv" / "bin" / "openai4s"
    executable = str(local) if local.is_file() else (shutil.which("openai4s") or "")
    if not executable:
        raise RuntimeError("openai4s executable not found")
    task = (
        f"Read {query.relative_to(root)} completely and implement exactly the "
        "requested codebase. Do not read private_evaluator, gt_codebase.py, "
        "scenarios/gt_codebases, or scenarios/pipelines. Run focused public-input "
        "tests and finish only after saving the requested source file."
    )
    return [executable, "run", task, "--mode", "codebase_change", "--json"]


def _checked(command: list[str], *, root: Path, environment: dict[str, str]) -> str:
    completed = subprocess.run(
        command,
        cwd=root,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            f"verification command failed ({completed.returncode}): "
            f"{' '.join(command[:3])}\n{completed.stdout[-2000:]}"
        )
    return completed.stdout


def _verify_case(root: Path, name: str, gt: Path, generated: Path) -> str:
    test_cases = gt.parent.parent / "test_cases"
    case = test_cases / f"{name}.json"
    installer = test_cases / "install.py"
    evaluator = test_cases / "evaluate.py"
    scenario = json.loads(case.read_text(encoding="utf-8"))["scenario"]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(root / "skills"), environment.get("PYTHONPATH", ""))
    )
    with tempfile.TemporaryDirectory(prefix=f"openai4s-{name}-") as temporary:
        scratch = Path(temporary)
        gt_workspace = scratch / "gt"
        generated_workspace = scratch / "generated"
        for workspace in (gt_workspace, generated_workspace):
            _checked(
                [
                    sys.executable,
                    str(installer),
                    "--case",
                    str(case),
                    "--workspace",
                    str(workspace),
                ],
                root=root,
                environment=environment,
            )
        _checked(
            [sys.executable, str(gt), "--workspace", str(gt_workspace)],
            root=root,
            environment=environment,
        )
        hidden = scratch / "private-evaluator-hidden"
        (generated_workspace / "private_evaluator").rename(hidden)
        try:
            _checked(
                [
                    sys.executable,
                    str(generated),
                    "--workspace",
                    str(generated_workspace),
                ],
                root=root,
                environment=environment,
            )
        finally:
            hidden.rename(generated_workspace / "private_evaluator")
        gt_artifact = gt_workspace / "results" / "intermediate_results.json"
        generated_artifact = (
            generated_workspace / "results" / "intermediate_results.json"
        )
        if gt_artifact.read_bytes() != generated_artifact.read_bytes():
            raise RuntimeError("generated artifact does not exactly match GT artifact")
        _checked(
            [
                sys.executable,
                str(evaluator),
                "--scenario",
                scenario,
                "--workspace",
                str(generated_workspace),
            ],
            root=root,
            environment=environment,
        )
        return _sha256(generated_artifact)


def _entry(root: Path, name: str, *, overwrite: bool) -> dict[str, object]:
    base = Path(__file__).resolve().parent
    query = base.parent / "queries" / f"{name}.query.md"
    gt = base.parent / "gt_codebases" / f"{name}.py"
    generated = base / f"{name}.py"
    if generated.exists() and not overwrite:
        raise RuntimeError(f"refusing to overwrite {generated}; pass --overwrite")
    if generated.exists():
        generated.unlink()
    command = _command(root, query)
    completed = subprocess.run(
        command,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output_sha256 = hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest()
    record: dict[str, object] = {
        "name": name,
        "query": str(query.relative_to(root)),
        "query_sha256": _sha256(query),
        "gt_codebase": str(gt.relative_to(root)),
        "gt_sha256": _sha256(gt),
        "openai4s_command": ["openai4s", *command[1:]],
        "openai4s_output_sha256": output_sha256,
        "openai4s_exit_code": completed.returncode,
        "generated_codebase": str(generated.relative_to(root)),
        "status": "failed",
    }
    if completed.returncode == 0 and generated.is_file():
        source = generated.read_text(encoding="utf-8")
        forbidden = ("gt_codebase", "gt_codebases", "private_evaluator")
        hits = [item for item in forbidden if item in source]
        if hits:
            record["verification_error"] = f"forbidden source references: {hits}"
        else:
            py_compile.compile(str(generated), doraise=True)
            record["generated_sha256"] = _sha256(generated)
            try:
                record["verified_artifact_sha256"] = _verify_case(
                    root, name, gt, generated
                )
                record["status"] = "generated_verified"
            except Exception as error:
                record["verification_error"] = str(error)
                record["status"] = "generated_verification_failed"
    elif completed.returncode == 0:
        record["verification_error"] = "OpenAI4S exited successfully without source"
    return record


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate matched retrosynthesis codebases through OpenAI4S"
    )
    parser.add_argument("--scenario", choices=(*NAMES, "all"), default="all")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    root = _root()
    selected = NAMES if args.scenario == "all" else (args.scenario,)
    records = []
    failed = False
    for name in selected:
        try:
            record = _entry(root, name, overwrite=args.overwrite)
        except Exception as error:  # Keep per-Scenario provenance on failure.
            record = {"name": name, "status": "failed", "error": str(error)}
        records.append(record)
        failed = failed or record["status"] != "generated_verified"
    manifest = {
        "schema_version": 2,
        "generator": "OpenAI4S CLI",
        "generation_kind": "actual_cli_run",
        "entries": records,
    }
    _write_json(Path(__file__).with_name("generation_manifest.json"), manifest)
    print(json.dumps(manifest, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
