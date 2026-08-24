"""Optional-dependency worker for Rosetta, OpenMM and local ESM-2.

This module is executed in a separately configured Python environment.  It is
never imported by the OpenAI4S core or by the MCP server process.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import sys
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _init_pyrosetta(seed: int):
    import pyrosetta

    pyrosetta.init(
        f"-mute all -ignore_unrecognized_res true -detect_disulf false "
        f"-constant_seed -jran {seed}",
        silent=True,
    )
    return pyrosetta


def _score(args: dict[str, Any]) -> dict[str, Any]:
    pyrosetta = _init_pyrosetta(int(args["seed"]))
    from pyrosetta.rosetta.core.scoring import ScoreType

    pose = pyrosetta.pose_from_pdb(args["pdb_path"])
    score_name = str(args.get("score_function", "ref2015"))
    score_function = pyrosetta.create_score_function(score_name)
    total = score_function(pose)
    per_residue = [
        {
            "pose_index": index,
            "chain": pose.pdb_info().chain(index),
            "pdb_number": pose.pdb_info().number(index),
            "name3": pose.residue(index).name3().strip(),
            "energy": float(pose.energies().residue_total_energy(index)),
        }
        for index in range(1, pose.total_residue() + 1)
    ]
    components: dict[str, dict[str, float]] = {}
    for score_type in score_function.get_nonzero_weighted_scoretypes():
        name = ScoreType(score_type).name
        weight = float(score_function.get_weight(score_type))
        value = float(pose.energies().total_energies()[score_type])
        components[name] = {
            "unweighted": value,
            "weight": weight,
            "weighted": value * weight,
        }
    return {
        "evidence_type": "rosetta_physical_energy",
        "total_score": float(total),
        "score_function": score_name,
        "num_residues": pose.total_residue(),
        "per_residue": per_residue,
        "energy_components": components,
    }


def _relax(args: dict[str, Any]) -> dict[str, Any]:
    pyrosetta = _init_pyrosetta(int(args["seed"]))
    from pyrosetta.rosetta.core.scoring import CA_rmsd
    from pyrosetta.rosetta.protocols.relax import FastRelax

    initial = pyrosetta.pose_from_pdb(args["pdb_path"])
    score_name = str(args.get("score_function", "ref2015"))
    score_function = pyrosetta.create_score_function(score_name)
    energy_before = float(score_function(initial))
    nstruct = int(args.get("nstruct", 1))
    max_iterations = int(args.get("max_iterations", 200))
    best = None
    best_energy = math.inf
    for _ in range(nstruct):
        pose = initial.clone()
        relax = FastRelax()
        relax.set_scorefxn(score_function)
        relax.max_iter(max_iterations)
        relax.apply(pose)
        energy = float(score_function(pose))
        if energy < best_energy:
            best, best_energy = pose.clone(), energy
    if best is None:
        raise RuntimeError("Rosetta did not complete a relax trajectory")
    output = Path(args["output_dir"]) / "relaxed.pdb"
    best.dump_pdb(str(output))
    return {
        "evidence_type": "rosetta_relaxation",
        "relaxed_pdb_path": str(output),
        "relaxed_pdb_digest": _sha256(output),
        "energy_before": energy_before,
        "energy_after": best_energy,
        "energy_change": best_energy - energy_before,
        "ca_rmsd": float(CA_rmsd(initial, best)),
        "nstruct": nstruct,
        "max_iterations": max_iterations,
        "score_function": score_name,
    }


def _interface(args: dict[str, Any]) -> dict[str, Any]:
    pyrosetta = _init_pyrosetta(int(args["seed"]))
    from pyrosetta.rosetta.protocols.analysis import InterfaceAnalyzerMover

    pose = pyrosetta.pose_from_pdb(args["pdb_path"])
    score_name = str(args.get("score_function", "ref2015"))
    score_function = pyrosetta.create_score_function(score_name)
    score_function(pose)
    chains = str(args["chains"])
    analyzer = InterfaceAnalyzerMover(chains)
    analyzer.set_scorefunction(score_function)
    analyzer.set_pack_separated(True)
    analyzer.set_pack_input(False)
    analyzer.apply(pose)
    return {
        "evidence_type": "rosetta_interface_energy",
        "dG_separated": float(analyzer.get_separated_interface_energy()),
        "dSASA": float(analyzer.get_interface_delta_sasa()),
        # This getter reports the change in unsatisfied H-bond count.  Calling
        # it "interface_hbonds" reverses its scientific meaning.
        "interface_delta_unsat_hbonds": float(
            analyzer.get_interface_delta_hbond_unsat()
        ),
        "packstat": float(analyzer.get_interface_packstat()),
        "nres_interface": int(analyzer.get_num_interface_residues()),
        "chains": chains,
        "score_function": score_name,
    }


def _score_stability(args: dict[str, Any]) -> dict[str, Any]:
    import esm
    import torch

    sequence = str(args["sequence"])
    checkpoint = str(args["checkpoint_path"])
    model, alphabet = esm.pretrained.load_model_and_alphabet_local(checkpoint)
    model.eval()
    converter = alphabet.get_batch_converter()
    _, _, tokens = converter([("query", sequence)])
    mask_index = alphabet.mask_idx
    per_residue: list[float] = []
    with torch.no_grad():
        for offset, amino_acid in enumerate(sequence, start=1):
            masked = tokens.clone()
            masked[0, offset] = mask_index
            logits = model(masked)["logits"][0, offset]
            log_probs = torch.log_softmax(logits, dim=-1)
            per_residue.append(float(log_probs[alphabet.get_idx(amino_acid)].item()))
    mean_pll = sum(per_residue) / len(per_residue)
    return {
        "evidence_type": "esm2_sequence_naturalness",
        "thermodynamic_stability_claim": False,
        "method": "masked_pseudo_log_likelihood",
        "model_name": args["model_name"],
        "sequence_length": len(sequence),
        "mean_log_likelihood": mean_pll,
        "sum_log_likelihood": sum(per_residue),
        "per_residue_log_likelihood": per_residue,
    }


def _minimize(args: dict[str, Any]) -> dict[str, Any]:
    from openmm import LangevinMiddleIntegrator, Vec3, app, unit
    from openmm.app import ForceField, Modeller, PDBFile, Simulation
    from pdbfixer import PDBFixer

    random.seed(int(args["seed"]))
    fixer = PDBFixer(filename=args["pdb_path"])
    fixer.findMissingResidues()
    fixer.findMissingAtoms()
    fixer.addMissingAtoms()
    fixer.addMissingHydrogens(7.0)
    force_fields = [str(args.get("force_field", "amber14-all.xml"))]
    implicit = args.get("implicit_solvent", "implicit/gbn2.xml")
    if implicit:
        force_fields.append(str(implicit))
    modeller = Modeller(fixer.topology, fixer.positions)
    force_field = ForceField(*force_fields)
    system = force_field.createSystem(
        modeller.topology,
        nonbondedCutoff=1.0 * unit.nanometers,
        constraints=app.HBonds,
    )
    integrator = LangevinMiddleIntegrator(
        300 * unit.kelvin,
        1.0 / unit.picoseconds,
        0.002 * unit.picoseconds,
    )
    integrator.setRandomNumberSeed(int(args["seed"]))
    simulation = Simulation(modeller.topology, system, integrator)
    simulation.context.setPositions(modeller.positions)
    before = simulation.context.getState(getEnergy=True, getPositions=True)
    initial_energy = float(
        before.getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole)
    )
    initial_positions = before.getPositions().value_in_unit(unit.nanometers)
    max_iterations = int(args.get("max_iterations", 500))
    tolerance = float(args.get("tolerance_kj_mol_nm", 10))
    simulation.minimizeEnergy(
        maxIterations=max_iterations,
        tolerance=tolerance * unit.kilojoules_per_mole / unit.nanometers,
    )
    after = simulation.context.getState(getEnergy=True, getPositions=True)
    final_energy = float(
        after.getPotentialEnergy().value_in_unit(unit.kilojoules_per_mole)
    )
    final_positions_quantity = after.getPositions()
    final_positions = final_positions_quantity.value_in_unit(unit.nanometers)
    squared = 0.0
    count = len(initial_positions)
    for first, second in zip(initial_positions, final_positions):
        delta: Vec3 = first - second
        squared += float(delta.x * delta.x + delta.y * delta.y + delta.z * delta.z)
    rmsd_angstrom = math.sqrt(squared / count) * 10.0
    output = Path(args["output_dir"]) / "minimized.pdb"
    with output.open("w", encoding="utf-8") as handle:
        PDBFile.writeFile(modeller.topology, final_positions_quantity, handle)
    return {
        "evidence_type": "openmm_energy_minimization",
        "design_success_claim": False,
        "minimized_pdb_path": str(output),
        "minimized_pdb_digest": _sha256(output),
        "initial_energy_kj_mol": initial_energy,
        "final_energy_kj_mol": final_energy,
        "energy_change_kj_mol": final_energy - initial_energy,
        "rmsd_from_prepared_initial_angstrom": rmsd_angstrom,
        "max_iterations": max_iterations,
        "tolerance_kj_mol_nm": tolerance,
        "force_fields": force_fields,
    }


_TOOLS = {
    "rosetta_score": _score,
    "rosetta_relax": _relax,
    "rosetta_interface_score": _interface,
    "score_stability": _score_stability,
    "energy_minimize": _minimize,
}


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 3 or values[0] not in _TOOLS:
        sys.stderr.write("usage: scientific_backend TOOL REQUEST_JSON RESULT_JSON\n")
        return 2
    tool, request_path, result_path = values
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    result = _TOOLS[tool](request)
    Path(result_path).write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
