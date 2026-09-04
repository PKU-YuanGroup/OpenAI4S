"""A bring-up record must be verifiable by someone who does not trust us yet.

The verifier's job is the evaluator half of the tool bring-up contract: the
record's own seal, the weights digests, the canary parse proof, the downstream
consumption proof, and the admission gate. The tamper shapes below are ordered
by subtlety, the same way the evidence-package tests are: the lazy forge
(rewrite the file *and* its recorded digest, no re-seal) is caught by the
record's own digest, while the full forge (re-seal included) is exactly what
the evaluator-held ``expected_weights`` seam exists to catch — internal
consistency alone can never notice it.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from openai4s.benchmark import bringup as bringup_module
from openai4s.benchmark.bringup import (
    BRINGUP_FILENAME,
    RECORD_DIR,
    SCHEMA_VERSION,
    BringupError,
    seal_record,
    verify_bringup,
)

WEIGHTS_BYTES = hashlib.sha256(b"unit-test-weights").digest()
WEIGHTS_SHA256 = hashlib.sha256(WEIGHTS_BYTES).hexdigest()
GENERATION = "env-0123456789abcdef"
CANARY_FIELDS = ["target", "sequence", "plddt", "weights_sha256"]


def _payload(weights_sha256: str = WEIGHTS_SHA256) -> dict:
    return {
        "target": "P01308",
        "sequence": "SEQP01308",
        "plddt": 92.5,
        "weights_sha256": weights_sha256,
    }


def _make_bringup(
    root: Path,
    *,
    with_env_generation: bool = True,
    manifest_state: str = "ready",
) -> dict:
    """A complete, untampered bring-up: record plus every file it names."""
    record_dir = root / RECORD_DIR
    record_dir.mkdir(parents=True, exist_ok=True)
    weights = root / "weights" / "model.weights"
    weights.parent.mkdir(parents=True, exist_ok=True)
    weights.write_bytes(WEIGHTS_BYTES)
    adapter = record_dir / "adapter.py"
    adapter.write_text("# unit-test adapter\n", encoding="utf-8")
    canary_out = record_dir / "canary_output.json"
    canary_out.write_text(json.dumps(_payload(), sort_keys=True), encoding="utf-8")
    downstream_out = record_dir / "downstream_result.json"
    downstream_out.write_text(
        json.dumps(
            {
                "consumer": "sequence-design",
                "target": "P01308",
                "sequence": "SEQP01308",
                "plddt": 92.5,
                "consumed_weights_sha256": WEIGHTS_SHA256,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    if with_env_generation:
        manifest = (
            root
            / "environments"
            / "design-tool"
            / "generations"
            / GENERATION
            / "manifest.json"
        )
        manifest.parent.mkdir(parents=True, exist_ok=True)
        prefix = manifest.parent / "prefix"
        prefix.mkdir()
        manifest.write_text(
            json.dumps(
                {
                    "state": manifest_state,
                    "generation_id": GENERATION,
                    "environment": "design-tool",
                    "prefix": str(prefix),
                }
            ),
            encoding="utf-8",
        )
    record = {
        "schema_version": SCHEMA_VERSION,
        "tool": {
            "name": "design-tool",
            "version": "1.0.0",
            "source": "https://github.com/openai4s/offline-design-tool",
            "revision": "abc123",
            "adapter": {
                "path": "bringup/adapter.py",
                "sha256": hashlib.sha256(adapter.read_bytes()).hexdigest(),
                "size": adapter.stat().st_size,
            },
            "env_name": "design-tool",
            "env_generation": GENERATION,
        },
        "weights": [
            {
                "path": "weights/model.weights",
                "sha256": WEIGHTS_SHA256,
                "size": len(WEIGHTS_BYTES),
                "source": "https://example.com/design-tool/weights",
                "verified": True,
            }
        ],
        "canary": {
            "target": "P01308",
            "command": [
                "python",
                "bin/tool",
                "--target",
                "P01308",
                "--weights",
                "weights/model.weights",
            ],
            "outputs": [
                {
                    "path": "bringup/canary_output.json",
                    "sha256": hashlib.sha256(canary_out.read_bytes()).hexdigest(),
                }
            ],
            "parse": {"status": "ok", "format": "json", "fields": CANARY_FIELDS},
            "downstream": {
                "consumer": "sequence-design",
                "status": "passed",
                "output": "bringup/downstream_result.json",
                "sha256": hashlib.sha256(downstream_out.read_bytes()).hexdigest(),
            },
        },
        "admission": {
            "status": "verified",
            "reasons": ["weights verified", "canary parseable", "downstream consumed"],
        },
        "runtime": {
            "wall_s": 0.5,
            "attempts": [
                {"status": "passed", "reason": "", "wall_s": 0.5, "gpu_h": 0.5}
            ],
        },
        "cost": {"gpu_h": 0.5, "budget_hours": 8.0},
    }
    record = seal_record(record)
    (record_dir / BRINGUP_FILENAME).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return record


def _load_record(root: Path) -> dict:
    return json.loads(
        (root / RECORD_DIR / BRINGUP_FILENAME).read_text(encoding="utf-8")
    )


def _save_record(root: Path, record: dict) -> None:
    (root / RECORD_DIR / BRINGUP_FILENAME).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _problem_ids(report: dict) -> set[str]:
    return {problem.split(":", 1)[0] for problem in report["problems"]}


def _verify_with_reference(root: Path) -> dict:
    return verify_bringup(
        root, expected_weights={"weights/model.weights": WEIGHTS_SHA256}
    )


def _rewrite_canary(root: Path, payload: dict) -> None:
    path = root / RECORD_DIR / "canary_output.json"
    path.write_text(json.dumps(payload, sort_keys=True), "utf-8")
    record = _load_record(root)
    record["canary"]["outputs"][0]["sha256"] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    _save_record(root, seal_record(record))


def _rewrite_downstream(root: Path, payload: dict) -> None:
    path = root / RECORD_DIR / "downstream_result.json"
    path.write_text(json.dumps(payload, sort_keys=True), "utf-8")
    record = _load_record(root)
    record["canary"]["downstream"]["sha256"] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    _save_record(root, seal_record(record))


def _forge_weights_coherently(root: Path, forged: bytes) -> str:
    """Rewrite every internally linked digest, then re-seal the record."""
    weights = root / "weights" / "model.weights"
    weights.write_bytes(forged)
    forged_sha = hashlib.sha256(forged).hexdigest()
    record = _load_record(root)
    record["weights"][0]["sha256"] = forged_sha
    record["weights"][0]["size"] = len(forged)

    canary_path = root / RECORD_DIR / "canary_output.json"
    canary_path.write_text(json.dumps(_payload(forged_sha), sort_keys=True), "utf-8")
    record["canary"]["outputs"][0]["sha256"] = hashlib.sha256(
        canary_path.read_bytes()
    ).hexdigest()

    downstream_path = root / RECORD_DIR / "downstream_result.json"
    downstream_path.write_text(
        json.dumps(
            {
                "consumer": "sequence-design",
                "target": "P01308",
                "sequence": "SEQP01308",
                "plddt": 92.5,
                "consumed_weights_sha256": forged_sha,
            },
            sort_keys=True,
        ),
        "utf-8",
    )
    record["canary"]["downstream"]["sha256"] = hashlib.sha256(
        downstream_path.read_bytes()
    ).hexdigest()
    _save_record(root, seal_record(record))
    return forged_sha


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------


def test_a_complete_bring_up_verifies(tmp_path):
    record = _make_bringup(tmp_path)
    report = verify_bringup(
        tmp_path, expected_weights={"weights/model.weights": WEIGHTS_SHA256}
    )
    assert report["ok"] is True
    assert report["admitted"] is True
    assert report["problems"] == []
    assert all(check["ok"] for check in report["checks"])
    assert report["weights_verified"] == 1
    assert report["canary_parse"] == "ok"
    assert report["downstream"] == "passed"
    assert report["admission"] == "verified"
    assert report["record_sha256"] == record["record_sha256"]
    assert len(report["record_sha256"]) == 64
    assert report["attempts"] == 1
    assert report["attempt_statuses"] == ["passed"]
    assert report["attempt_reasons"] == [""]
    assert report["recovered"] is False
    assert report["reference_verified"] is True
    assert report["tool"] == "design-tool"


def test_sealing_is_deterministic(tmp_path):
    record = _make_bringup(tmp_path)
    again = seal_record(json.loads(json.dumps(record)))
    assert again == record
    report = verify_bringup(tmp_path)
    assert report["record_sha256"] == record["record_sha256"]


# --------------------------------------------------------------------------
# tampering, by increasing subtlety
# --------------------------------------------------------------------------


def test_editing_the_record_without_resealing_is_caught(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["admission"]["reasons"] = ["edited"]
    _save_record(tmp_path, record)
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "self_vouch" in _problem_ids(report)


def test_a_lazy_forge_is_caught_by_the_self_vouch(tmp_path):
    """Rewriting the payload *and* its recorded digest, without re-sealing,
    defeats every per-file check — the record's own digest is what notices."""
    _make_bringup(tmp_path)
    forged = WEIGHTS_BYTES[:-1] + bytes([WEIGHTS_BYTES[-1] ^ 0x01])
    (tmp_path / "weights" / "model.weights").write_bytes(forged)
    record = _load_record(tmp_path)
    record["weights"][0]["sha256"] = hashlib.sha256(forged).hexdigest()
    record["weights"][0]["size"] = len(forged)
    _save_record(tmp_path, record)
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "self_vouch" in _problem_ids(report)
    assert "weights_hash" not in _problem_ids(report)


def test_a_full_forge_is_caught_only_by_the_reference(tmp_path):
    """A re-sealed forgery is internally consistent — the reference digest is
    the only check that notices. This is the documented limit of the seal."""
    _make_bringup(tmp_path)
    forged = WEIGHTS_BYTES[:-1] + bytes([WEIGHTS_BYTES[-1] ^ 0x01])
    _forge_weights_coherently(tmp_path, forged)
    report = verify_bringup(
        tmp_path, expected_weights={"weights/model.weights": WEIGHTS_SHA256}
    )
    assert report["ok"] is False
    assert _problem_ids(report) == {"weights_reference"}


def test_without_reference_digests_a_coherent_forgery_is_not_admitted(tmp_path):
    """Internal consistency may pass without an evaluator reference, but the
    record must not cross the admission boundary."""
    _make_bringup(tmp_path)
    forged = WEIGHTS_BYTES[:-1] + bytes([WEIGHTS_BYTES[-1] ^ 0x01])
    _forge_weights_coherently(tmp_path, forged)
    report = verify_bringup(tmp_path)
    assert report["ok"] is True
    assert report["admitted"] is False
    assert report["reference_verified"] is False
    reference_check = next(
        check for check in report["checks"] if check["id"] == "weights_reference"
    )
    assert reference_check["ok"] is False
    assert "internal consistency only" in reference_check["detail"]


def test_a_modified_weights_file_is_caught(tmp_path):
    _make_bringup(tmp_path)
    path = tmp_path / "weights" / "model.weights"
    path.write_bytes(WEIGHTS_BYTES[:-1] + bytes([WEIGHTS_BYTES[-1] ^ 0x01]))
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "weights_hash" in _problem_ids(report)
    assert any("content hash mismatch" in p for p in report["problems"])


def test_a_deleted_weights_file_is_caught(tmp_path):
    _make_bringup(tmp_path)
    (tmp_path / "weights" / "model.weights").unlink()
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "weights_present" in _problem_ids(report)


def test_a_wrong_recorded_size_is_caught(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["weights"][0]["size"] += 1
    _save_record(tmp_path, seal_record(record))
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "weights_size" in _problem_ids(report)


def test_wrong_weights_against_the_reference_are_caught(tmp_path):
    _make_bringup(tmp_path)
    report = verify_bringup(
        tmp_path,
        expected_weights={"weights/model.weights": "0" * 64},
    )
    assert report["ok"] is False
    assert "weights_reference" in _problem_ids(report)
    assert any("expected reference" in p for p in report["problems"])


def test_reference_must_cover_the_exact_unique_weights_set(tmp_path):
    _make_bringup(tmp_path)
    extra = tmp_path / "weights" / "extra.weights"
    extra.write_bytes(b"extra")
    record = _load_record(tmp_path)
    record["weights"].append(
        {
            "path": "weights/extra.weights",
            "sha256": hashlib.sha256(extra.read_bytes()).hexdigest(),
            "size": extra.stat().st_size,
            "source": "https://example.com/extra",
            "verified": True,
        }
    )
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert report["admitted"] is False
    assert report["reference_verified"] is False
    assert "weights_reference" in _problem_ids(report)
    assert any("unreferenced" in problem for problem in report["problems"])


def test_malformed_reference_keys_are_reported_not_raised(tmp_path):
    _make_bringup(tmp_path)
    report = verify_bringup(
        tmp_path,
        expected_weights={("weights", "model.weights"): WEIGHTS_SHA256},
    )
    assert report["ok"] is False
    assert report["admitted"] is False
    assert "weights_reference" in _problem_ids(report)
    assert any("invalid reference" in problem for problem in report["problems"])


def test_duplicate_weight_paths_are_rejected_before_hashing(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["weights"].append(dict(record["weights"][0]))
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert "weights_schema" in _problem_ids(report)
    assert any("duplicate" in problem for problem in report["problems"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda entry: entry.pop("size"),
        lambda entry: entry.__setitem__("verified", "false"),
        lambda entry: entry.__setitem__("source", []),
    ],
)
def test_weight_fields_have_exact_types(tmp_path, mutation):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    mutation(record["weights"][0])
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert report["ok"] is False
    assert {"weights_schema", "weights_verified"} & _problem_ids(report)


def test_schema_version_bool_is_not_integer_one(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["schema_version"] = True
    _save_record(tmp_path, seal_record(record))
    assert "schema_version" in _problem_ids(_verify_with_reference(tmp_path))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_seal_record_rejects_nonfinite_numbers(value):
    with pytest.raises(ValueError):
        seal_record({"schema_version": SCHEMA_VERSION, "value": value})


def test_adapter_is_a_confined_hashed_snapshot(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["tool"]["adapter"]["path"] = "../adapter.py"
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert "adapter" in _problem_ids(report)


@pytest.mark.parametrize("record_path", ["C:/adapter.py", "C:adapter.py"])
def test_windows_drive_paths_are_never_portable_record_paths(tmp_path, record_path):
    _make_bringup(tmp_path)
    original = tmp_path / RECORD_DIR / "adapter.py"
    candidate = tmp_path.joinpath(*record_path.split("/"))
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(original.read_bytes())
    record = _load_record(tmp_path)
    record["tool"]["adapter"]["path"] = record_path
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert "adapter" in _problem_ids(report)
    assert any("invalid" in problem for problem in report["problems"])


def test_deleted_adapter_is_rejected(tmp_path):
    _make_bringup(tmp_path)
    (tmp_path / RECORD_DIR / "adapter.py").unlink()
    assert "adapter" in _problem_ids(_verify_with_reference(tmp_path))


def test_fifo_artifact_is_rejected_without_blocking(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs are unavailable on this platform")
    _make_bringup(tmp_path)
    adapter = tmp_path / RECORD_DIR / "adapter.py"
    adapter.unlink()
    os.mkfifo(adapter)
    script = (
        "from pathlib import Path; "
        "from openai4s.benchmark.bringup import verify_bringup; "
        "report = verify_bringup(Path(__import__('sys').argv[1]), "
        f"expected_weights={{'weights/model.weights': '{WEIGHTS_SHA256}'}}); "
        "assert not report['ok']; "
        "assert any(p.startswith('adapter:') for p in report['problems'])"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert completed.returncode == 0, completed.stderr


def test_generation_manifest_identity_is_bound(tmp_path):
    _make_bringup(tmp_path)
    manifest = (
        tmp_path
        / "environments"
        / "design-tool"
        / "generations"
        / GENERATION
        / "manifest.json"
    )
    payload = json.loads(manifest.read_text("utf-8"))
    payload.update({"generation_id": "env-someone-else", "environment": "other"})
    manifest.write_text(json.dumps(payload), "utf-8")
    report = _verify_with_reference(tmp_path)
    assert "env_generation" in _problem_ids(report)
    assert any("generation_id" in problem for problem in report["problems"])


def test_superseded_generation_remains_verifiable(tmp_path):
    _make_bringup(tmp_path, manifest_state="superseded")
    report = _verify_with_reference(tmp_path)
    assert report["ok"] is True
    assert report["admitted"] is True


def test_generation_prefix_must_exist_inside_its_generation(tmp_path):
    _make_bringup(tmp_path)
    manifest = (
        tmp_path
        / "environments"
        / "design-tool"
        / "generations"
        / GENERATION
        / "manifest.json"
    )
    payload = json.loads(manifest.read_text("utf-8"))
    payload["prefix"] = str(tmp_path / "weights")
    manifest.write_text(json.dumps(payload), "utf-8")
    assert "env_generation" in _problem_ids(_verify_with_reference(tmp_path))


def test_unreadable_environment_layout_is_reported_not_raised(tmp_path):
    _make_bringup(tmp_path)
    env_dir = tmp_path / "environments" / "design-tool"
    os.chmod(env_dir, 0)
    try:
        if os.access(env_dir, os.X_OK):
            pytest.skip("filesystem permissions are not enforced for this process")
        report = _verify_with_reference(tmp_path)
    finally:
        os.chmod(env_dir, 0o700)

    assert report["ok"] is False
    assert "env_generation" in _problem_ids(report)
    # 3.13: Path.is_symlink() raises on a mode-0 directory, so the layout
    # probe reports "cannot be inspected". 3.14's is_symlink() returns False
    # without raising; the same unreadable tree then fails at snapshot with
    # Permission denied. Either way the verifier must report, not raise.
    assert any(
        "cannot be inspected" in problem
        or "cannot be resolved" in problem
        or "Permission denied" in problem
        for problem in report["problems"]
    )


@pytest.mark.parametrize("error_type", [OSError, ValueError, RuntimeError])
def test_environment_layout_probe_errors_are_reported_not_raised(
    tmp_path, monkeypatch, error_type
):
    _make_bringup(tmp_path)
    target = tmp_path / "environments" / "design-tool" / "generations"
    real_lstat = bringup_module.os.lstat

    def racing_lstat(path, *args, **kwargs):
        if Path(path) == target:
            raise error_type("injected layout race")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(bringup_module.os, "lstat", racing_lstat)
    report = _verify_with_reference(tmp_path)

    assert report["ok"] is False
    assert "env_generation" in _problem_ids(report)
    assert any(
        "layout cannot be inspected" in problem for problem in report["problems"]
    )


@pytest.mark.parametrize("error_type", [OSError, ValueError, RuntimeError])
def test_generation_prefix_probe_errors_are_reported_not_raised(
    tmp_path, monkeypatch, error_type
):
    _make_bringup(tmp_path)
    target = (
        tmp_path
        / "environments"
        / "design-tool"
        / "generations"
        / GENERATION
        / "prefix"
    ).resolve()
    real_is_dir = Path.is_dir

    def racing_is_dir(path):
        if path == target:
            raise error_type("injected prefix race")
        return real_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", racing_is_dir)
    report = _verify_with_reference(tmp_path)

    assert report["ok"] is False
    assert "env_generation" in _problem_ids(report)
    assert any(
        "prefix cannot be inspected" in problem for problem in report["problems"]
    )


# --------------------------------------------------------------------------
# canary and downstream proofs
# --------------------------------------------------------------------------


def test_a_canary_with_no_output_is_caught(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["canary"]["outputs"] = []
    _save_record(tmp_path, seal_record(record))
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "canary_outputs" in _problem_ids(report)
    assert any("no output" in p for p in report["problems"])


def test_a_deleted_canary_output_is_caught(tmp_path):
    _make_bringup(tmp_path)
    (tmp_path / RECORD_DIR / "canary_output.json").unlink()
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "canary_outputs" in _problem_ids(report)
    assert any("absent" in p for p in report["problems"])


def test_a_modified_canary_output_is_caught(tmp_path):
    _make_bringup(tmp_path)
    path = tmp_path / RECORD_DIR / "canary_output.json"
    path.write_text(
        json.dumps({**_payload(), "plddt": 0.0}, sort_keys=True), encoding="utf-8"
    )
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "canary_outputs_hash" in _problem_ids(report)


def test_an_unparseable_canary_output_is_caught(tmp_path):
    _make_bringup(tmp_path)
    (tmp_path / RECORD_DIR / "canary_output.json").write_text(
        "not json", encoding="utf-8"
    )
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "canary_parse" in _problem_ids(report)
    assert any("parse" in p for p in report["problems"])


def test_a_missing_declared_field_is_caught(tmp_path):
    _make_bringup(tmp_path)
    path = tmp_path / RECORD_DIR / "canary_output.json"
    payload = _payload()
    payload.pop("plddt")
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    record = _load_record(tmp_path)
    record["canary"]["outputs"][0]["sha256"] = hashlib.sha256(
        path.read_bytes()
    ).hexdigest()
    _save_record(tmp_path, seal_record(record))
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "canary_parse" in _problem_ids(report)


def test_canary_command_binds_target_and_recorded_weights(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["canary"]["command"][3] = "OTHER"
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert "canary_command" in _problem_ids(report)
    assert any("--target" in problem for problem in report["problems"])


@pytest.mark.parametrize(
    "interpreter,tool",
    [
        ("/usr/bin/python3", "/tmp/generation/bin/tool"),
        (r"C:\Python313\python.exe", r"C:\Temp\generation\bin\tool.py"),
    ],
)
def test_canary_command_rejects_absolute_execution_argv(tmp_path, interpreter, tool):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["canary"]["command"][:2] = [interpreter, tool]
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert "canary_command" in _problem_ids(report)
    assert any("portable logical" in problem for problem in report["problems"])


def test_canary_digest_matches_the_commanded_weight_not_any_recorded_weight(tmp_path):
    _make_bringup(tmp_path)
    other = tmp_path / "weights" / "other.weights"
    other.write_bytes(b"other weight")
    other_sha = hashlib.sha256(other.read_bytes()).hexdigest()
    record = _load_record(tmp_path)
    record["weights"].append(
        {
            "path": "weights/other.weights",
            "sha256": other_sha,
            "size": other.stat().st_size,
            "source": "https://example.com/design-tool/other.weights",
            "verified": True,
        }
    )
    _save_record(tmp_path, seal_record(record))
    _rewrite_canary(tmp_path, _payload(other_sha))
    _rewrite_downstream(
        tmp_path,
        {
            "consumer": "sequence-design",
            "target": "P01308",
            "sequence": "SEQP01308",
            "plddt": 92.5,
            "consumed_weights_sha256": other_sha,
        },
    )
    report = verify_bringup(
        tmp_path,
        expected_weights={
            "weights/model.weights": WEIGHTS_SHA256,
            "weights/other.weights": other_sha,
        },
    )
    assert "canary_parse" in _problem_ids(report)
    assert any("commanded" in problem for problem in report["problems"])


@pytest.mark.parametrize(
    "weight_path", ["/tmp/model.weights", r"C:\weights\model.weights"]
)
def test_canary_command_rejects_platform_absolute_weight_paths(tmp_path, weight_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["canary"]["command"][5] = weight_path
    _save_record(tmp_path, seal_record(record))
    assert "canary_command" in _problem_ids(_verify_with_reference(tmp_path))


def test_schema_v1_has_exactly_one_canary_output(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["canary"]["outputs"].append(dict(record["canary"]["outputs"][0]))
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert "canary_outputs" in _problem_ids(report)
    assert any("exactly one" in problem for problem in report["problems"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("target", "WRONG"),
        ("weights_sha256", "0" * 64),
        ("plddt", 100.1),
        ("plddt", "not-a-number"),
    ],
)
def test_canary_semantics_are_bound_to_the_record(tmp_path, field, value):
    _make_bringup(tmp_path)
    payload = _payload()
    payload[field] = value
    _rewrite_canary(tmp_path, payload)
    assert "canary_parse" in _problem_ids(_verify_with_reference(tmp_path))


def test_unhashable_declared_field_is_a_problem_not_an_exception(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["canary"]["parse"]["fields"] = [{}]
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert "canary_parse" in _problem_ids(report)


def test_downstream_payload_must_match_the_canary(tmp_path):
    _make_bringup(tmp_path)
    _rewrite_downstream(
        tmp_path,
        {
            "consumer": "sequence-design",
            "target": "WRONG",
            "sequence": "SEQP01308",
            "plddt": 92.5,
            "consumed_weights_sha256": WEIGHTS_SHA256,
        },
    )
    report = _verify_with_reference(tmp_path)
    assert "downstream" in _problem_ids(report)
    assert any("target" in problem for problem in report["problems"])


def test_downstream_boolean_plddt_does_not_equal_numeric_canary_plddt(tmp_path):
    _make_bringup(tmp_path)
    _rewrite_canary(tmp_path, {**_payload(), "plddt": 1})
    _rewrite_downstream(
        tmp_path,
        {
            "consumer": "sequence-design",
            "target": "P01308",
            "sequence": "SEQP01308",
            "plddt": True,
            "consumed_weights_sha256": WEIGHTS_SHA256,
        },
    )
    report = _verify_with_reference(tmp_path)
    assert "downstream" in _problem_ids(report)
    assert any("plddt" in problem for problem in report["problems"])


def test_downstream_requires_a_distinct_consumption_artifact(tmp_path):
    _make_bringup(tmp_path)
    canary_path = tmp_path / RECORD_DIR / "canary_output.json"
    payload = _payload()
    payload.update(
        {
            "consumer": "sequence-design",
            "consumed_weights_sha256": WEIGHTS_SHA256,
        }
    )
    canary_path.write_text(json.dumps(payload, sort_keys=True), "utf-8")
    digest = hashlib.sha256(canary_path.read_bytes()).hexdigest()
    record = _load_record(tmp_path)
    record["canary"]["outputs"][0]["sha256"] = digest
    record["canary"]["downstream"].update(
        {"output": "bringup/canary_output.json", "sha256": digest}
    )
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert "downstream" in _problem_ids(report)
    assert any("distinct" in problem for problem in report["problems"])


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_downstream_cannot_alias_the_canary_physical_file(tmp_path, link_kind):
    _make_bringup(tmp_path)
    canary_path = tmp_path / RECORD_DIR / "canary_output.json"
    downstream_path = tmp_path / RECORD_DIR / "downstream_result.json"
    payload = _payload()
    payload.update(
        {
            "consumer": "sequence-design",
            "consumed_weights_sha256": WEIGHTS_SHA256,
        }
    )
    canary_path.write_text(json.dumps(payload, sort_keys=True), "utf-8")
    downstream_path.unlink()
    try:
        if link_kind == "symlink":
            downstream_path.symlink_to(canary_path.name)
        else:
            os.link(canary_path, downstream_path)
    except OSError as exc:
        pytest.skip(f"{link_kind} unavailable: {exc}")
    digest = hashlib.sha256(canary_path.read_bytes()).hexdigest()
    record = _load_record(tmp_path)
    record["canary"]["outputs"][0]["sha256"] = digest
    record["canary"]["downstream"]["sha256"] = digest
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert "downstream" in _problem_ids(report)
    assert any("aliases" in problem for problem in report["problems"])


def test_a_refused_downstream_is_caught(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["canary"]["downstream"]["status"] = "refused"
    _save_record(tmp_path, seal_record(record))
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "downstream" in _problem_ids(report)
    assert report["downstream"] == "refused"


def test_a_deleted_downstream_output_is_caught(tmp_path):
    _make_bringup(tmp_path)
    (tmp_path / RECORD_DIR / "downstream_result.json").unlink()
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "downstream" in _problem_ids(report)


# --------------------------------------------------------------------------
# admission, runtime, cost, and confinement
# --------------------------------------------------------------------------


def test_a_refused_admission_never_proceeds_even_when_the_rest_verifies(tmp_path):
    """Admission is the gate, not the verification alone: a record whose
    artifacts all verify but whose admission says refused must not proceed."""
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["admission"] = {"status": "refused", "reasons": ["canary failed"]}
    _save_record(tmp_path, seal_record(record))
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert report["admitted"] is False


def test_cost_beyond_the_budget_refuses_admission(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["cost"]["gpu_h"] = 2.0
    record["cost"]["budget_hours"] = 1.0
    _save_record(tmp_path, seal_record(record))
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "cost" in _problem_ids(report)
    assert any("budget" in p for p in report["problems"])


def test_a_negative_wall_time_is_caught(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["runtime"]["wall_s"] = -1.0
    _save_record(tmp_path, seal_record(record))
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "runtime" in _problem_ids(report)


@pytest.mark.parametrize(
    "mutation,check_id",
    [
        (lambda record: record["runtime"].__setitem__("attempts", []), "runtime"),
        (
            lambda record: record["runtime"]["attempts"][0].pop("status"),
            "runtime",
        ),
        (
            lambda record: record["runtime"]["attempts"][0].update(
                {"status": "failed", "reason": "injected failure"}
            ),
            "runtime",
        ),
        (lambda record: record["runtime"].__setitem__("wall_s", 0.75), "runtime"),
        (
            lambda record: record["runtime"]["attempts"][0].__setitem__("gpu_h", 0.25),
            "cost",
        ),
    ],
)
def test_attempts_are_nonempty_complete_finally_passed_and_sum_to_totals(
    tmp_path, mutation, check_id
):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    mutation(record)
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert check_id in _problem_ids(report)


def test_extreme_mixed_numeric_totals_are_reported_not_raised(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["runtime"]["attempts"][0]["wall_s"] = 10**1000
    record["runtime"]["attempts"][0]["gpu_h"] = 10**1000
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert {"runtime", "cost"} <= _problem_ids(report)


def test_a_weight_path_escaping_the_root_is_caught(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["weights"][0]["path"] = "../escape/model.weights"
    _save_record(tmp_path, seal_record(record))
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "weights_schema" in _problem_ids(report)


def test_a_symlink_escaping_the_root_is_caught(tmp_path):
    import tempfile

    _make_bringup(tmp_path)
    with tempfile.TemporaryDirectory() as outside_dir:
        outside = Path(outside_dir) / "outside.weights"
        outside.write_bytes(WEIGHTS_BYTES)
        (tmp_path / "weights" / "model.weights").unlink()
        (tmp_path / "weights" / "model.weights").symlink_to(outside)
        report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "weights_present" in _problem_ids(report)


def test_a_nul_in_an_artifact_path_is_reported_not_raised(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["weights"][0]["path"] = "weights/\x00model.weights"
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert report["ok"] is False
    assert "weights_schema" in _problem_ids(report)


def test_an_artifact_io_error_is_reported_not_raised(tmp_path, monkeypatch):
    _make_bringup(tmp_path)
    weights = (tmp_path / "weights" / "model.weights").resolve()
    real_open = bringup_module.os.open

    def refusing_open(path, flags, *args, **kwargs):
        if Path(path) == weights:
            raise PermissionError("test denial")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(bringup_module.os, "open", refusing_open)
    report = _verify_with_reference(tmp_path)
    assert report["ok"] is False
    assert "weights_present" in _problem_ids(report)


def test_a_post_read_identity_swap_is_caught(tmp_path, monkeypatch):
    _make_bringup(tmp_path)
    weights = (tmp_path / "weights" / "model.weights").resolve()
    real_stat = bringup_module.os.stat
    swapped = False

    def swapping_stat(path, *args, **kwargs):
        nonlocal swapped
        if (
            Path(path) == weights
            and not swapped
            and not kwargs.get("follow_symlinks", True)
        ):
            swapped = True
            replacement = weights.with_name("replacement.weights")
            replacement.write_bytes(b"x" * len(WEIGHTS_BYTES))
            replacement.replace(weights)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(bringup_module.os, "stat", swapping_stat)
    report = _verify_with_reference(tmp_path)
    assert swapped is True
    assert report["ok"] is False
    assert "weights_present" in _problem_ids(report)


def test_a_missing_generation_manifest_is_caught(tmp_path):
    _make_bringup(tmp_path, with_env_generation=False)
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "env_generation" in _problem_ids(report)
    # Absence is reported as absence. The lstat probe must not turn a missing
    # generation into an I/O-fault-sounding "cannot be inspected" first line.
    assert "cannot be inspected" not in json.dumps(report)


def test_a_generation_that_is_not_ready_is_caught(tmp_path):
    _make_bringup(tmp_path, manifest_state="staging")
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "env_generation" in _problem_ids(report)


# --------------------------------------------------------------------------
# the never-raise rule
# --------------------------------------------------------------------------


def test_a_missing_record_raises(tmp_path):
    with pytest.raises(BringupError, match="no bringup record"):
        verify_bringup(tmp_path)


def test_a_non_json_record_raises(tmp_path):
    (tmp_path / RECORD_DIR).mkdir(parents=True)
    (tmp_path / RECORD_DIR / BRINGUP_FILENAME).write_text("not json", encoding="utf-8")
    with pytest.raises(BringupError, match="not JSON"):
        verify_bringup(tmp_path)


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema_version": NaN}',
        '{"schema_version": Infinity}',
        '{"schema_version": -Infinity}',
        '{"schema_version": 1, "schema_version": 1}',
    ],
)
def test_a_non_strict_json_record_raises(tmp_path, raw):
    (tmp_path / RECORD_DIR).mkdir(parents=True)
    (tmp_path / RECORD_DIR / BRINGUP_FILENAME).write_text(raw, encoding="utf-8")
    with pytest.raises(BringupError, match="strict"):
        verify_bringup(tmp_path)


def test_an_overflowing_json_exponent_is_a_problem_not_an_exception(tmp_path):
    (tmp_path / RECORD_DIR).mkdir(parents=True)
    (tmp_path / RECORD_DIR / BRINGUP_FILENAME).write_text(
        '{"schema_version": 1, "cost": {"gpu_h": 1e999}}',
        encoding="utf-8",
    )
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert {"self_vouch", "cost"} <= _problem_ids(report)


def test_an_invalid_utf8_record_raises(tmp_path):
    (tmp_path / RECORD_DIR).mkdir(parents=True)
    (tmp_path / RECORD_DIR / BRINGUP_FILENAME).write_bytes(b"{\xff}")
    with pytest.raises(BringupError, match="not JSON"):
        verify_bringup(tmp_path)


def test_a_recursively_nested_record_raises_bringup_error(tmp_path):
    (tmp_path / RECORD_DIR).mkdir(parents=True)
    nested = "[" * 10000 + "0" + "]" * 10000
    (tmp_path / RECORD_DIR / BRINGUP_FILENAME).write_text(nested, encoding="utf-8")
    # CPython's JSON decoder recursion threshold changed in 3.14; either the
    # decoder rejects the depth or it returns a non-object, but both remain the
    # documented BringupError boundary rather than leaking RecursionError.
    with pytest.raises(BringupError, match="not JSON|not a JSON object"):
        verify_bringup(tmp_path)


def test_a_recursively_nested_artifact_is_reported_not_raised(tmp_path):
    _make_bringup(tmp_path)
    canary_path = tmp_path / RECORD_DIR / "canary_output.json"
    canary_path.write_text("[" * 10000 + "0" + "]" * 10000, encoding="utf-8")
    record = _load_record(tmp_path)
    record["canary"]["outputs"][0]["sha256"] = hashlib.sha256(
        canary_path.read_bytes()
    ).hexdigest()
    _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert "canary_parse" in _problem_ids(report)


@pytest.mark.parametrize(
    "target,raw,check_id",
    [
        ("manifest", b'{"value": 1, "value": 2}', "env_generation"),
        ("manifest", b'{"value": NaN}', "env_generation"),
        ("canary", b'{"value": 1, "value": 2}', "canary_parse"),
        ("canary", b'{"value": NaN}', "canary_parse"),
        ("downstream", b'{"value": 1, "value": 2}', "downstream"),
        ("downstream", b'{"value": NaN}', "downstream"),
    ],
)
def test_every_json_artifact_uses_the_strict_parser(tmp_path, target, raw, check_id):
    _make_bringup(tmp_path)
    paths = {
        "manifest": (
            tmp_path
            / "environments"
            / "design-tool"
            / "generations"
            / GENERATION
            / "manifest.json"
        ),
        "canary": tmp_path / RECORD_DIR / "canary_output.json",
        "downstream": tmp_path / RECORD_DIR / "downstream_result.json",
    }
    path = paths[target]
    path.write_bytes(raw)
    if target != "manifest":
        record = _load_record(tmp_path)
        if target == "canary":
            record["canary"]["outputs"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
        else:
            record["canary"]["downstream"]["sha256"] = hashlib.sha256(raw).hexdigest()
        _save_record(tmp_path, seal_record(record))
    report = _verify_with_reference(tmp_path)
    assert check_id in _problem_ids(report)
    assert any(
        marker in problem
        for marker in ("duplicate JSON key", "non-finite JSON number")
        for problem in report["problems"]
    )


def test_json_artifact_size_cap_is_reported_not_raised(tmp_path, monkeypatch):
    _make_bringup(tmp_path)
    canary_path = tmp_path / RECORD_DIR / "canary_output.json"
    raw = json.dumps({"padding": "x" * 10000}).encode("utf-8")
    canary_path.write_bytes(raw)
    record = _load_record(tmp_path)
    record["canary"]["outputs"][0]["sha256"] = hashlib.sha256(raw).hexdigest()
    _save_record(tmp_path, seal_record(record))
    record_size = (tmp_path / RECORD_DIR / BRINGUP_FILENAME).stat().st_size
    limit = record_size + 100
    assert len(raw) > limit
    monkeypatch.setattr(bringup_module, "_MAX_JSON_BYTES", limit)
    report = _verify_with_reference(tmp_path)
    assert "canary_outputs" in _problem_ids(report)
    assert any("exceeds" in problem for problem in report["problems"])


def test_a_record_symlink_escaping_the_root_raises(tmp_path):
    record_dir = tmp_path / RECORD_DIR
    record_dir.mkdir(parents=True)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-bringup.json"
    outside.write_text("{}", encoding="utf-8")
    try:
        (record_dir / BRINGUP_FILENAME).symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    try:
        with pytest.raises(BringupError, match="no bringup record"):
            verify_bringup(tmp_path)
    finally:
        outside.unlink(missing_ok=True)


def test_a_non_object_record_raises(tmp_path):
    (tmp_path / RECORD_DIR).mkdir(parents=True)
    (tmp_path / RECORD_DIR / BRINGUP_FILENAME).write_text("[]", encoding="utf-8")
    with pytest.raises(BringupError, match="not a JSON object"):
        verify_bringup(tmp_path)
