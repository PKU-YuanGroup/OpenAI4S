"""Offline contracts for the bundled atomic protein-design MCP server."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

from openai4s.mcp_client import (
    OPENAI4S_PYTHON,
    MCPConnection,
    protein_design_server_config,
)
from openai4s.mcp_servers.protein_design.schemas import TOOLS
from openai4s.mcp_servers.protein_design.service import ProteinDesignService
from openai4s.server.gateway import _CONNECTOR_DIRECTORY

pytestmark = pytest.mark.stubbed_backend

_REVISION = "1234567890abcdef1234567890abcdef12345678"


def test_bundled_adapter_is_offered_in_the_connector_directory():
    entry = next(
        item for item in _CONNECTOR_DIRECTORY if item["id"] == "protein-design"
    )

    assert entry["name"] == "Protein Design"
    assert entry["command"][0] == OPENAI4S_PYTHON
    assert entry["command"][-2:] == [
        "-m",
        "openai4s.mcp_servers.protein_design",
    ]
    assert "backends" in entry["description"].lower()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pdb(path: Path, chains: dict[str, list[tuple[int, str]]]) -> None:
    lines = []
    serial = 1
    x = 0.0
    for chain, residues in chains.items():
        for number, name3 in residues:
            lines.append(
                f"ATOM  {serial:5d}  CA  {name3:>3s} {chain}{number:4d}    "
                f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00 20.00           C  "
            )
            serial += 1
            x += 3.8
    lines.extend(["TER", "END"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _executable(path: Path, source: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _base(tmp_path: Path, *, attempt: str = "attempt-009", seed: int = 9) -> dict:
    return {
        "attempt_id": attempt,
        "seed": seed,
        "output_dir": str(tmp_path / "out"),
        "backend_revision": _REVISION,
    }


def test_catalog_is_exactly_the_nine_atomic_tools_and_has_no_composite():
    names = [tool["name"] for tool in TOOLS]
    assert names == [
        "generate_backbone",
        "design_sequence",
        "predict_structure",
        "predict_complex",
        "rosetta_score",
        "rosetta_relax",
        "rosetta_interface_score",
        "score_stability",
        "energy_minimize",
    ]
    assert "design_binder" not in names
    interface = next(
        tool for tool in TOOLS if tool["name"] == "rosetta_interface_score"
    )
    assert "interface_delta_unsat_hbonds" in interface["description"]
    assert "interface hydrogen bonds" not in interface["description"].lower()
    stability = next(tool for tool in TOOLS if tool["name"] == "score_stability")
    assert "not thermodynamic stability" in stability["description"]


def test_empty_call_reports_all_missing_required_arguments_without_guessing_an_id(
    tmp_path,
):
    result = ProteinDesignService(root=tmp_path).call("generate_backbone", {})

    assert result["status"] == "failed"
    assert result["attempt_id"] == "unassigned"
    assert result["seed"] == -1
    assert result["error"].startswith("missing required tool arguments: ")
    assert "attempt_id" in result["error"]
    assert "target_pdb" in result["error"]
    assert "attempt_id must use only" not in result["error"]


def test_checkpointed_formal_call_requires_a_successful_live_canary(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI4S_PROTEIN_DESIGN_REQUIRE_ADMISSION", "1")
    service = ProteinDesignService(root=tmp_path)
    checkpoint = tmp_path / "v_48_020.pt"
    checkpoint.write_bytes(b"checkpoint")
    digest = _digest(checkpoint)
    calls = []

    def fake_handler(args, output):
        calls.append((args["attempt_id"], args.get("run_mode", "formal")))
        return {"checkpoint_digest": digest, "sequences": ["AC"]}

    monkeypatch.setattr(service, "_tool_design_sequence", fake_handler)

    def request(attempt, run_mode):
        return {
            **_base(tmp_path, attempt=attempt),
            "run_mode": run_mode,
            "execution_target": "local",
            "checkpoint_path": str(checkpoint),
            "checkpoint_sha256": digest,
            "backbone_pdb": "unused-by-stub.pdb",
            "design_chains": ["B"],
            "fixed_positions": {"A": "all", "B": []},
            "num_sequences": 1,
            "sampling_temp": 0.1,
        }

    refused = service.call("design_sequence", request("formal-before-canary", "formal"))
    assert refused["status"] == "failed"
    assert "not admitted" in refused["error"]
    assert calls == []

    canary = service.call("design_sequence", request("canary-001", "canary"))
    assert canary["status"] == "succeeded"
    assert canary["bringup_admission"]["status"] == "verified"
    assert canary["bringup_admission"]["checkpoint_digest"] == digest

    formal = service.call("design_sequence", request("formal-after-canary", "formal"))
    assert formal["status"] == "succeeded"
    assert formal["bringup_admission"]["canary_attempt_id"] == "canary-001"
    assert calls == [("canary-001", "canary"), ("formal-after-canary", "formal")]

    restarted = ProteinDesignService(root=tmp_path)
    monkeypatch.setattr(restarted, "_tool_design_sequence", fake_handler)
    after_restart = restarted.call(
        "design_sequence", request("formal-after-restart", "formal")
    )
    assert after_restart["status"] == "failed"
    assert "not admitted" in after_restart["error"]


def test_stdio_server_initializes_and_lists_the_real_catalog(tmp_path):
    config = protein_design_server_config(str(tmp_path))
    connection = MCPConnection(config["command"], env=config["env"], timeout=5)
    try:
        assert [item["name"] for item in connection.list_tools()] == [
            item["name"] for item in TOOLS
        ]
    finally:
        assert connection.close()


def test_rfdiffusion_is_one_seeded_attempt_and_preserves_pdb_trb(tmp_path, monkeypatch):
    target = tmp_path / "target.pdb"
    _pdb(target, {"A": [(1, "ALA"), (2, "CYS"), (4, "ASP")]})
    checkpoint = tmp_path / "Complex_base_ckpt.pt"
    checkpoint.write_bytes(b"fixed-rfdiffusion-checkpoint")
    fake = _executable(
        tmp_path / "fake_rfdiffusion",
        """
import json, pathlib, sys
args = sys.argv[1:]
prefix = pathlib.Path(next(x.split('=', 1)[1] for x in args if x.startswith('inference.output_prefix=')))
seed = int(next(x.split('=', 1)[1] for x in args if x.startswith('inference.design_startnum=')))
prefix.parent.mkdir(parents=True, exist_ok=True)
prefix.with_name(prefix.name + f'_{seed}.pdb').write_text('MODEL\\nEND\\n')
prefix.with_name(prefix.name + f'_{seed}.trb').write_bytes(b'trb-mapping')
(prefix.parent / 'fake_argv.json').write_text(json.dumps(args))
""",
    )
    monkeypatch.setenv("OPENAI4S_RFDIFFUSION_COMMAND", json.dumps([str(fake)]))
    monkeypatch.setenv("OPENAI4S_RFDIFFUSION_REVISION", _REVISION)
    service = ProteinDesignService(root=tmp_path, timeout=5)
    args = {
        **_base(tmp_path),
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": _digest(checkpoint),
        "target_pdb": str(target),
        "target_chain": "A",
        "hotspot_residues": ["A2", "A4"],
        "binder_length": 80,
        "num_designs": 1,
    }

    result = service.call("generate_backbone", args)

    assert result["status"] == "succeeded"
    assert result["attempt_id"] == "attempt-009" and result["seed"] == 9
    assert Path(result["pdb_path"]).is_file()
    assert Path(result["trb_path"]).is_file()
    assert result["checkpoint_digest"] == _digest(checkpoint)
    assert result["output_digest"]
    command = result["command"]
    assert "inference.num_designs=1" in command
    assert "inference.deterministic=True" in command
    assert "inference.design_startnum=9" in command
    assert "contigmap.contigs=[A1-2/A4-4/0 80-80]" in command
    assert "ppi.hotspot_res=[A2,A4]" in command
    assert not any(item.startswith("inference.seed=") for item in command)
    terminal = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert terminal["status"] == "succeeded"
    assert terminal["pdb_digest"] == _digest(Path(result["pdb_path"]))


def test_rfdiffusion_rejects_missing_or_cross_chain_hotspots_with_terminal_record(
    tmp_path, monkeypatch
):
    target = tmp_path / "target.pdb"
    _pdb(target, {"A": [(1, "ALA")], "B": [(1, "GLY")]})
    checkpoint = tmp_path / "checkpoint.pt"
    checkpoint.write_bytes(b"checkpoint")
    monkeypatch.setenv("OPENAI4S_RFDIFFUSION_REVISION", _REVISION)
    args = {
        **_base(tmp_path, attempt="bad-hotspot"),
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": _digest(checkpoint),
        "target_pdb": str(target),
        "target_chain": "A",
        "hotspot_residues": ["B1"],
        "binder_length": 80,
    }

    result = ProteinDesignService(root=tmp_path).call("generate_backbone", args)

    assert result["status"] == "failed"
    assert "explicit target chain" in result["error"]
    terminal = tmp_path / "out" / "bad-hotspot" / "terminal.json"
    assert terminal.is_file()
    assert json.loads(terminal.read_text())["status"] == "failed"


def test_rfdiffusion_multichain_target_is_explicit_not_inferred_from_hotspot(
    tmp_path, monkeypatch
):
    target = tmp_path / "multichain.pdb"
    _pdb(target, {"A": [(1, "ALA")], "B": [(10, "GLY"), (11, "SER")]})
    checkpoint = tmp_path / "checkpoint.pt"
    checkpoint.write_bytes(b"checkpoint")
    fake = _executable(
        tmp_path / "fake_multichain_rfdiffusion",
        """
import pathlib, sys
args = sys.argv[1:]
prefix = pathlib.Path(next(x.split('=', 1)[1] for x in args if x.startswith('inference.output_prefix=')))
seed = int(next(x.split('=', 1)[1] for x in args if x.startswith('inference.design_startnum=')))
prefix.with_name(prefix.name + f'_{seed}.pdb').write_text('MODEL\\nEND\\n')
prefix.with_name(prefix.name + f'_{seed}.trb').write_bytes(b'trb')
""",
    )
    monkeypatch.setenv("OPENAI4S_RFDIFFUSION_COMMAND", json.dumps([str(fake)]))
    monkeypatch.setenv("OPENAI4S_RFDIFFUSION_REVISION", _REVISION)
    args = {
        **_base(tmp_path, attempt="multichain-001"),
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": _digest(checkpoint),
        "target_pdb": str(target),
        "target_chain": "A",
        "target_chains": ["A", "B"],
        "hotspot_residues": ["A1", "B10"],
        "binder_length": 60,
    }

    result = ProteinDesignService(root=tmp_path).call("generate_backbone", args)

    assert result["status"] == "succeeded"
    assert "contigmap.contigs=[A1-1/0 B10-11/0 60-60]" in result["command"]
    assert "ppi.hotspot_res=[A1,B10]" in result["command"]


def _mpnn_fake(path: Path, designed_b: str) -> Path:
    return _executable(
        path,
        f"""
import json, pathlib, sys
args = sys.argv[1:]
def value(flag): return args[args.index(flag) + 1]
out = pathlib.Path(value('--out_folder'))
stem = pathlib.Path(value('--pdb_path')).stem
(out / 'seqs').mkdir(parents=True, exist_ok=True)
(out / 'seqs' / (stem + '.fa')).write_text(
    ">native, designed_chains=['B'], fixed_chains=['A']\\nGSY/AC\\n"
    ">design, score=1.0, designed_chains=['B'], fixed_chains=['A']\\n{designed_b}/AC\\n"
)
(out / 'fake_argv.json').write_text(json.dumps(args))
""",
    )


def _mpnn_args(tmp_path: Path, checkpoint: Path, backbone: Path) -> dict:
    return {
        **_base(tmp_path, attempt="mpnn-001", seed=1042),
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": _digest(checkpoint),
        "backbone_pdb": str(backbone),
        "design_chains": ["B"],
        "fixed_positions": {"A": "all", "B": [2]},
        "num_sequences": 1,
        "sampling_temp": 0.1,
        "model_name": "v_48_020",
    }


def test_proteinmpnn_uses_official_flags_and_verifies_target_motif_and_map(
    tmp_path, monkeypatch
):
    backbone = tmp_path / "backbone.pdb"
    _pdb(
        backbone,
        {"A": [(45, "ALA"), (46, "CYS")], "B": [(10, "GLY"), (11, "SER"), (12, "TYR")]},
    )
    checkpoint = tmp_path / "v_48_020.pt"
    checkpoint.write_bytes(b"fixed-proteinmpnn-checkpoint")
    fake = _mpnn_fake(tmp_path / "fake_proteinmpnn", "ASV")
    monkeypatch.setenv("OPENAI4S_PROTEINMPNN_COMMAND", json.dumps([str(fake)]))
    monkeypatch.setenv("OPENAI4S_PROTEINMPNN_REVISION", _REVISION)

    result = ProteinDesignService(root=tmp_path, timeout=5).call(
        "design_sequence", _mpnn_args(tmp_path, checkpoint, backbone)
    )

    assert result["status"] == "succeeded"
    command = result["command"]
    assert command[command.index("--pdb_path_chains") + 1] == "B"
    assert command[command.index("--seed") + 1] == "1042"
    assert "--fixed_positions_jsonl" in command
    assert "--jsonl_path" not in command and "--chain_id_design" not in command
    fixed_path = Path(command[command.index("--fixed_positions_jsonl") + 1])
    assert json.loads(fixed_path.read_text()) == {"backbone": {"A": [1, 2], "B": [2]}}
    assert result["validation"] == {
        "target_chains_unchanged": True,
        "fixed_positions_unchanged": True,
        "chain_lengths_match": True,
        "residue_map_closed": True,
    }
    mapping = json.loads(Path(result["residue_map_path"]).read_text())
    assert mapping["closed"] is True
    assert mapping["chains"][0]["positions"][0]["input_residue"] == "A45"


def test_proteinmpnn_fails_closed_when_a_fixed_motif_changes(tmp_path, monkeypatch):
    backbone = tmp_path / "backbone.pdb"
    _pdb(
        backbone,
        {"A": [(1, "ALA"), (2, "CYS")], "B": [(1, "GLY"), (2, "SER"), (3, "TYR")]},
    )
    checkpoint = tmp_path / "v_48_020.pt"
    checkpoint.write_bytes(b"checkpoint")
    fake = _mpnn_fake(tmp_path / "bad_proteinmpnn", "AAV")
    monkeypatch.setenv("OPENAI4S_PROTEINMPNN_COMMAND", json.dumps([str(fake)]))
    monkeypatch.setenv("OPENAI4S_PROTEINMPNN_REVISION", _REVISION)

    result = ProteinDesignService(root=tmp_path, timeout=5).call(
        "design_sequence", _mpnn_args(tmp_path, checkpoint, backbone)
    )

    assert result["status"] == "failed"
    assert "changed fixed motif residue B2" in result["error"]
    assert Path(result["manifest_path"]).is_file()


def _colabfold_fake(path: Path) -> Path:
    return _executable(
        path,
        """
import json, pathlib, sys
args = sys.argv[1:]
out = pathlib.Path(args[-1])
out.mkdir(parents=True, exist_ok=True)
(out / 'query_unrelaxed_rank_001_model.pdb').write_text('MODEL\\nEND\\n')
scores = {
  'plddt': [80.0, 81.0, 82.0, 83.0],
  'ptm': 0.7,
  'iptm': 0.8,
  'pae': [[0, 1, 4, 5], [1, 0, 6, 7], [4, 6, 0, 1], [5, 7, 1, 0]],
}
(out / 'query_scores_rank_001_model.json').write_text(json.dumps(scores))
(out.parent / 'fake_colabfold_argv.json').write_text(json.dumps(args))
""",
    )


def _checkpoint_bundle(tmp_path: Path, name: str = "colabfold-bundle.json") -> Path:
    data_dir = tmp_path / (Path(name).stem + "-data")
    data_dir.mkdir()
    weight = data_dir / "params_model_1.npz"
    weight.write_bytes(b"frozen-colabfold-model-parameters")
    manifest = tmp_path / name
    manifest.write_text(
        json.dumps(
            {
                "data_dir": data_dir.name,
                "files": [
                    {
                        "path": f"{data_dir.name}/{weight.name}",
                        "sha256": _digest(weight),
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return manifest


def _offline_guard(path: Path) -> Path:
    return _executable(
        path,
        """
import os, sys
assert sys.argv[1:3] == ['--unshare-net', '--']
os.execv(sys.argv[3], sys.argv[3:])
""",
    )


def test_complex_prediction_freezes_blind_offline_configuration(tmp_path, monkeypatch):
    checkpoint = _checkpoint_bundle(tmp_path)
    fake = _colabfold_fake(tmp_path / "fake_colabfold")
    guard = _offline_guard(tmp_path / "bwrap")
    monkeypatch.setenv("OPENAI4S_COLABFOLD_COMMAND", json.dumps([str(fake)]))
    monkeypatch.setenv(
        "OPENAI4S_PROTEIN_DESIGN_OFFLINE_PREFIX",
        json.dumps([str(guard), "--unshare-net", "--"]),
    )
    monkeypatch.setenv("OPENAI4S_COLABFOLD_REVISION", _REVISION)
    args = {
        **_base(tmp_path, attempt="complex-001", seed=77),
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": _digest(checkpoint),
        "sequences": ["AC", "GY"],
        "chain_names": ["A", "B"],
        "model_type": "alphafold2_multimer_v3",
        "recycles": 3,
        "model_count": 1,
        "msa_mode": "single_sequence",
        "require_network_isolation": True,
    }

    result = ProteinDesignService(root=tmp_path, timeout=5).call(
        "predict_complex", args
    )

    assert result["status"] == "succeeded"
    assert result["network_isolation_enforced"] is True
    assert result["msa_mode"] == "single_sequence"
    assert result["templates"] is False and result["initial_guess"] is False
    assert result["iptm"] == 0.8
    assert result["interface_pae"] == pytest.approx(5.5)
    command = result["command"]
    assert command[:3] == [str(guard), "--unshare-net", "--"]
    assert command[command.index("--random-seed") + 1] == "77"
    assert command[command.index("--num-recycle") + 1] == "3"
    assert command[command.index("--num-models") + 1] == "1"
    assert command[command.index("--msa-mode") + 1] == "single_sequence"
    assert "--templates" not in command and "--use-templates" not in command


def test_complex_prediction_refuses_environment_only_offline_claim(
    tmp_path, monkeypatch
):
    checkpoint = _checkpoint_bundle(tmp_path, "checkpoint.json")
    monkeypatch.setenv("OPENAI4S_COLABFOLD_REVISION", _REVISION)
    monkeypatch.delenv("OPENAI4S_PROTEIN_DESIGN_OFFLINE_PREFIX", raising=False)
    args = {
        **_base(tmp_path, attempt="no-net-boundary"),
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": _digest(checkpoint),
        "sequences": ["AC", "GY"],
        "chain_names": ["A", "B"],
        "model_type": "alphafold2_multimer_v3",
        "recycles": 3,
        "model_count": 1,
    }

    result = ProteinDesignService(root=tmp_path).call("predict_complex", args)

    assert result["status"] == "failed"
    assert "OFFLINE_PREFIX is required" in result["error"]
    assert Path(result["manifest_path"]).is_file()


def test_prediction_cannot_disable_required_network_isolation(tmp_path, monkeypatch):
    checkpoint = _checkpoint_bundle(tmp_path, "disable-isolation.json")
    monkeypatch.setenv("OPENAI4S_COLABFOLD_REVISION", _REVISION)
    args = {
        **_base(tmp_path, attempt="disable-isolation"),
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": _digest(checkpoint),
        "sequence": "ACGY",
        "model_type": "alphafold2_ptm",
        "recycles": 3,
        "model_count": 1,
        "require_network_isolation": False,
    }

    result = ProteinDesignService(root=tmp_path).call("predict_structure", args)

    assert result["status"] == "failed"
    assert "require_network_isolation must be true" in result["error"]


def test_prediction_rejects_unpinned_files_in_checkpoint_data_tree(
    tmp_path, monkeypatch
):
    checkpoint = _checkpoint_bundle(tmp_path)
    data_dir = tmp_path / "colabfold-bundle-data"
    (data_dir / "unlisted-params.npz").write_bytes(b"not in manifest")
    guard = _offline_guard(tmp_path / "bwrap")
    monkeypatch.setenv(
        "OPENAI4S_PROTEIN_DESIGN_OFFLINE_PREFIX",
        json.dumps([str(guard), "--unshare-net", "--"]),
    )
    monkeypatch.setenv("OPENAI4S_COLABFOLD_REVISION", _REVISION)
    args = {
        **_base(tmp_path, attempt="unpinned-checkpoint"),
        "checkpoint_path": str(checkpoint),
        "checkpoint_sha256": _digest(checkpoint),
        "sequence": "ACGY",
        "model_type": "alphafold2_ptm",
        "recycles": 3,
        "model_count": 1,
    }

    result = ProteinDesignService(root=tmp_path).call("predict_structure", args)

    assert result["status"] == "failed"
    assert "must pin every data file" in result["error"]


def _scientific_python(path: Path, result: dict) -> Path:
    return _executable(
        path,
        f"""
import json, pathlib, sys
pathlib.Path(sys.argv[4]).write_text(json.dumps({result!r}))
""",
    )


def test_rosetta_interface_publishes_unsatisfied_hbond_field_not_hbond_count(
    tmp_path, monkeypatch
):
    pdb = tmp_path / "complex.pdb"
    _pdb(pdb, {"A": [(1, "ALA")], "B": [(1, "GLY")]})
    worker = _scientific_python(
        tmp_path / "fake_pyrosetta_python",
        {
            "dG_separated": -10.0,
            "dSASA": 900.0,
            "packstat": 0.7,
            "interface_delta_unsat_hbonds": 2.0,
        },
    )
    monkeypatch.setenv("OPENAI4S_PYROSETTA_PYTHON", str(worker))
    monkeypatch.setenv("OPENAI4S_PYROSETTA_REVISION", _REVISION)
    args = {
        **_base(tmp_path, attempt="interface-001"),
        "pdb_path": str(pdb),
        "chains": "A_B",
        "score_function": "ref2015",
    }

    result = ProteinDesignService(root=tmp_path, timeout=5).call(
        "rosetta_interface_score", args
    )

    assert result["status"] == "succeeded"
    assert result["interface_delta_unsat_hbonds"] == 2.0
    assert "interface_hbonds" not in result


def test_rosetta_interface_rejects_legacy_misnamed_hbond_field(tmp_path, monkeypatch):
    pdb = tmp_path / "complex.pdb"
    _pdb(pdb, {"A": [(1, "ALA")], "B": [(1, "GLY")]})
    worker = _scientific_python(
        tmp_path / "bad_pyrosetta_python", {"interface_hbonds": 2}
    )
    monkeypatch.setenv("OPENAI4S_PYROSETTA_PYTHON", str(worker))
    monkeypatch.setenv("OPENAI4S_PYROSETTA_REVISION", _REVISION)
    args = {
        **_base(tmp_path, attempt="bad-interface-field"),
        "pdb_path": str(pdb),
        "chains": "A_B",
    }

    result = ProteinDesignService(root=tmp_path).call("rosetta_interface_score", args)

    assert result["status"] == "failed"
    assert "incorrect interface_hbonds field" in result["error"]


def test_paths_outside_the_configured_root_fail_closed_with_no_backend_start(
    tmp_path, monkeypatch
):
    outside = tmp_path.parent / "outside-protein-design.pdb"
    outside.write_text("END\n")
    monkeypatch.setenv("OPENAI4S_PYROSETTA_REVISION", _REVISION)
    args = {
        **_base(tmp_path, attempt="escape-001"),
        "pdb_path": str(outside),
        "score_function": "ref2015",
    }

    result = ProteinDesignService(root=tmp_path).call("rosetta_score", args)

    assert result["status"] == "failed"
    assert "escapes configured protein-design root" in result["error"]
    assert Path(result["manifest_path"]).is_file()


def test_identical_attempt_retry_returns_terminal_record_without_rerunning_backend(
    tmp_path, monkeypatch
):
    service = ProteinDesignService(root=tmp_path)
    invocations = []

    def fake_handler(args, output):
        invocations.append((args["seed"], output))
        return {"total_score": -12.5}

    monkeypatch.setattr(service, "_tool_rosetta_score", fake_handler)
    args = {
        **_base(tmp_path, attempt="idempotent-001", seed=17),
        "pdb_path": "unused-by-stub.pdb",
    }

    first = service.call("rosetta_score", args)
    second = service.call("rosetta_score", args)

    assert first["status"] == second["status"] == "succeeded"
    assert first["config_digest"] == second["config_digest"]
    assert second["manifest_path"] == first["manifest_path"]
    assert invocations == [(17, tmp_path / "out" / "idempotent-001")]


def test_attempt_id_conflict_preserves_original_terminal_record(tmp_path, monkeypatch):
    service = ProteinDesignService(root=tmp_path)
    invocations = []

    def fake_handler(args, output):
        invocations.append(args["seed"])
        return {"total_score": -9.0}

    monkeypatch.setattr(service, "_tool_rosetta_score", fake_handler)
    original_args = {
        **_base(tmp_path, attempt="conflict-001", seed=11),
        "pdb_path": "unused-by-stub.pdb",
    }
    first = service.call("rosetta_score", original_args)
    manifest = Path(first["manifest_path"])
    original_terminal = manifest.read_bytes()

    conflict = service.call("rosetta_score", {**original_args, "seed": 12})

    assert conflict["status"] == "failed"
    assert conflict["error_type"] == "AttemptConflict"
    assert conflict["existing_status"] == "succeeded"
    assert manifest.read_bytes() == original_terminal
    assert invocations == [11]


def test_partial_attempt_is_closed_as_interrupted_without_backend_start(
    tmp_path, monkeypatch
):
    attempt_dir = tmp_path / "out" / "interrupted-001"
    attempt_dir.mkdir(parents=True)
    partial = attempt_dir / "partial-output.pdb"
    partial.write_text("MODEL\n", encoding="utf-8")
    service = ProteinDesignService(root=tmp_path)

    def forbidden_handler(args, output):
        raise AssertionError("backend handler must not start")

    monkeypatch.setattr(service, "_tool_rosetta_score", forbidden_handler)

    result = service.call(
        "rosetta_score",
        {
            **_base(tmp_path, attempt="interrupted-001", seed=23),
            "pdb_path": "unused-by-stub.pdb",
        },
    )

    assert result["status"] == "failed"
    assert result["error_type"] == "InterruptedAttempt"
    assert "interrupted run" in result["error"]
    assert partial.is_file()
    terminal = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert terminal["status"] == "failed"
    assert terminal["error_type"] == "InterruptedAttempt"


def test_unreadable_terminal_manifest_is_preserved_for_manual_reconciliation(
    tmp_path, monkeypatch
):
    attempt_dir = tmp_path / "out" / "unreadable-terminal"
    attempt_dir.mkdir(parents=True)
    manifest = attempt_dir / "terminal.json"
    original = b"{truncated-terminal"
    manifest.write_bytes(original)
    service = ProteinDesignService(root=tmp_path)

    def forbidden_handler(args, output):
        raise AssertionError("backend handler must not start")

    monkeypatch.setattr(service, "_tool_rosetta_score", forbidden_handler)

    result = service.call(
        "rosetta_score",
        {
            **_base(tmp_path, attempt="unreadable-terminal", seed=29),
            "pdb_path": "unused-by-stub.pdb",
        },
    )

    assert result["status"] == "failed"
    assert result["error_type"] == "AttemptManifestUnreadable"
    assert manifest.read_bytes() == original
