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
from pathlib import Path

import pytest

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
    canary_out = record_dir / "canary_output.json"
    canary_out.write_text(json.dumps(_payload(), sort_keys=True), encoding="utf-8")
    downstream_out = record_dir / "downstream_result.json"
    downstream_out.write_text(
        json.dumps({"consumer": "sequence-design", **_payload()}, sort_keys=True),
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
        manifest.write_text(
            json.dumps({"state": manifest_state, "generation_id": GENERATION}),
            encoding="utf-8",
        )
    record = {
        "schema_version": SCHEMA_VERSION,
        "tool": {
            "name": "design-tool",
            "version": "1.0.0",
            "source": "https://github.com/openai4s/offline-design-tool",
            "revision": "abc123",
            "adapter": "bringup/adapter.py",
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
            "command": ["python", "tool", "--target", "P01308"],
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
        "runtime": {"wall_s": 0.5, "attempts": [{"status": "passed", "reason": ""}]},
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


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------


def test_a_complete_bring_up_verifies(tmp_path):
    record = _make_bringup(tmp_path)
    report = verify_bringup(tmp_path)
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
    (tmp_path / "weights" / "model.weights").write_bytes(forged)
    record = _load_record(tmp_path)
    record["weights"][0]["sha256"] = hashlib.sha256(forged).hexdigest()
    record["weights"][0]["size"] = len(forged)
    _save_record(tmp_path, seal_record(record))
    report = verify_bringup(
        tmp_path, expected_weights={"weights/model.weights": WEIGHTS_SHA256}
    )
    assert report["ok"] is False
    assert "weights_reference" in _problem_ids(report)
    assert "self_vouch" not in _problem_ids(report)


def test_without_reference_digests_a_forgery_passes(tmp_path):
    """The seam is optional: without it the verifier can only establish
    internal consistency, and must say so rather than overclaim."""
    _make_bringup(tmp_path)
    forged = WEIGHTS_BYTES[:-1] + bytes([WEIGHTS_BYTES[-1] ^ 0x01])
    (tmp_path / "weights" / "model.weights").write_bytes(forged)
    record = _load_record(tmp_path)
    record["weights"][0]["sha256"] = hashlib.sha256(forged).hexdigest()
    record["weights"][0]["size"] = len(forged)
    _save_record(tmp_path, seal_record(record))
    report = verify_bringup(tmp_path)
    assert report["ok"] is True
    reference_check = next(
        check for check in report["checks"] if check["id"] == "weights_reference"
    )
    assert reference_check["ok"] is True
    assert "skipped" in reference_check["detail"]


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


def test_a_weight_path_escaping_the_root_is_caught(tmp_path):
    _make_bringup(tmp_path)
    record = _load_record(tmp_path)
    record["weights"][0]["path"] = "../escape/model.weights"
    _save_record(tmp_path, seal_record(record))
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "weights_present" in _problem_ids(report)


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


def test_a_missing_generation_manifest_is_caught(tmp_path):
    _make_bringup(tmp_path, with_env_generation=False)
    report = verify_bringup(tmp_path)
    assert report["ok"] is False
    assert "env_generation" in _problem_ids(report)


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


def test_a_non_object_record_raises(tmp_path):
    (tmp_path / RECORD_DIR).mkdir(parents=True)
    (tmp_path / RECORD_DIR / BRINGUP_FILENAME).write_text("[]", encoding="utf-8")
    with pytest.raises(BringupError, match="not a JSON object"):
        verify_bringup(tmp_path)
