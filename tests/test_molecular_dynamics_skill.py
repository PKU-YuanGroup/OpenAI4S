"""Execute the documented recipes on tiny, offline scientific fixtures.

The optional OpenMM/MDAnalysis tests skip when those packages are not installed.
No GPU, external structure, or live scientific service is needed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "molecular-dynamics"


def _blocks(path):
    return re.findall(r"```python\n(.*?)\n```", path.read_text("utf-8"), re.S)


@pytest.mark.parametrize(
    "relative",
    [
        "SKILL.md",
        "references/system_preparation.md",
        "references/mdanalysis_analysis.md",
    ],
)
def test_documented_python_compiles(relative):
    for block in _blocks(SKILL / relative):
        compile(block, relative, "exec")


@pytest.fixture
def recipe(monkeypatch, tmp_path):
    for package in ("openmm", "MDAnalysis", "matplotlib"):
        pytest.importorskip(package)
    monkeypatch.setenv("MPLBACKEND", "Agg")
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "matplotlib"))
    namespace = {}
    exec("\n\n".join(_blocks(SKILL / "SKILL.md")), namespace)
    return namespace


def _simulation(recipe, tmp_path):
    import openmm as mm
    from openmm import app, unit

    topology = app.Topology()
    chain = topology.addChain()
    system = mm.System()
    force = mm.NonbondedForce()
    force.setNonbondedMethod(mm.NonbondedForce.CutoffPeriodic)
    force.setCutoffDistance(0.9 * unit.nanometer)
    for _ in range(3):
        residue = topology.addResidue("AR", chain)
        topology.addAtom("Ar", app.element.argon, residue)
        system.addParticle(39.9 * unit.dalton)
        force.addParticle(0, 0.3, 0.01)
    system.addForce(force)
    box = [mm.Vec3(3, 0, 0), mm.Vec3(0, 3, 0), mm.Vec3(0, 0, 3)] * unit.nanometer
    topology.setPeriodicBoxVectors(box)
    system.setDefaultPeriodicBoxVectors(*box)
    positions = [
        mm.Vec3(0.5, 0.5, 0.5),
        mm.Vec3(1, 1, 1),
        mm.Vec3(1.5, 1.5, 1.5),
    ] * unit.nanometer
    modeller = app.Modeller(topology, positions)
    return recipe["minimize_energy"](
        modeller,
        system,
        output_pdb=str(tmp_path / "topology.pdb"),
        platform_name="Reference",
        max_iterations=2,
    )


def test_minimize_nvt_npt_and_stage_timestamps(recipe, tmp_path):
    import openmm as mm
    from openmm import unit

    simulation = _simulation(recipe, tmp_path)
    recipe["run_nvt_equilibration"](
        simulation, n_steps=5, report_interval=2, output_prefix=str(tmp_path / "nvt")
    )
    recipe["run_npt_production"](
        simulation, n_steps=7, report_interval=2, output_prefix=str(tmp_path / "npt")
    )
    u = recipe["load_trajectory"](
        str(tmp_path / "topology.pdb"),
        str(tmp_path / "npt_traj.dcd"),
        state_log=str(tmp_path / "npt_log.txt"),
    )
    # NPT starts at step 5; reports occur at steps 6, 8, 10, 12.
    assert [ts.time for ts in u.trajectory] == pytest.approx(
        [0.012, 0.016, 0.020, 0.024]
    )
    assert u.trajectory[2].time == pytest.approx(0.020)
    # The raw DCD header has a stage-local origin; it is not Context time.
    raw = recipe["mda"].Universe(
        str(tmp_path / "topology.pdb"), str(tmp_path / "npt_traj.dcd")
    )
    assert raw.trajectory[0].time == pytest.approx(0.004)
    for bad_times in ([0.012], [0.012, 0.016, float("nan"), 0.024], [0.012] * 4):
        bad_log = tmp_path / "bad_log.txt"
        bad_log.write_text('"Time (ps)"\n' + "\n".join(map(str, bad_times)) + "\n")
        with pytest.raises(ValueError, match="time per frame"):
            recipe["load_trajectory"](
                str(tmp_path / "topology.pdb"),
                str(tmp_path / "npt_traj.dcd"),
                state_log=str(bad_log),
            )
    recipe["run_npt_production"](
        simulation,
        n_steps=2,
        temperature=310,
        pressure=2,
        report_interval=2,
        output_prefix=str(tmp_path / "production"),
    )
    assert (
        sum(isinstance(f, mm.MonteCarloBarostat) for f in simulation.system.getForces())
        == 1
    )
    assert simulation.context.getParameter(mm.MonteCarloBarostat.Temperature()) == 310
    assert simulation.context.getParameter(mm.MonteCarloBarostat.Pressure()) == 2
    assert simulation.integrator.getTemperature().value_in_unit(unit.kelvin) == 310
    assert (tmp_path / "production_checkpoint.chk").stat().st_size > 0
    assert (tmp_path / "production_state.xml").stat().st_size > 0
    with pytest.raises(ValueError, match="NVT"):
        recipe["run_nvt_equilibration"](simulation, n_steps=1)


def _contact_universe(box):
    import MDAnalysis as mda
    import numpy as np

    u = mda.Universe.empty(
        3,
        n_residues=3,
        n_segments=2,
        atom_resindex=[0, 1, 2],
        residue_segindex=[0, 1, 1],
        trajectory=True,
    )
    u.add_TopologyAttr("names", ["CA", "CA", "C1"])
    u.add_TopologyAttr("resnames", ["ALA", "ALA", "LIG"])
    u.add_TopologyAttr("resids", [1, 1, 2])
    u.add_TopologyAttr("segids", ["A", "B"])
    u.load_new(np.array([[[0.5, 0, 0], [4, 0, 0], [9.5, 0, 0]]], dtype=np.float32))
    u.dimensions = box
    return u


def test_contacts_use_minimum_image_and_distinct_residue_identity(recipe):
    u = _contact_universe([10, 10, 10, 90, 90, 90])
    assert recipe["analyze_contacts"](u, radius=2) == [{(0, "A", 1, "ALA")}]
    assert recipe["analyze_contacts"](u, radius=2, periodic=False) == [set()]
    assert recipe["analyze_contacts"](u, radius=6) == [
        {(0, "A", 1, "ALA"), (1, "B", 1, "ALA")}
    ]


@pytest.mark.parametrize(
    "box",
    [
        None,
        [0, 10, 10, 90, 90, 90],
        [10, 10, 10, 90, 90, 0],
        [10, 10, 10, 90, 90, 180],
        [10, 10, 10, 10, 10, 170],
    ],
)
def test_periodic_contacts_reject_degenerate_boxes(recipe, box):
    with pytest.raises(ValueError, match="box"):
        recipe["analyze_contacts"](_contact_universe(box))


def test_contacts_reject_nonfinite_coordinates(recipe):
    u = _contact_universe([10, 10, 10, 90, 90, 90])
    u.atoms[0].position = [float("nan"), 0, 0]
    with pytest.raises(ValueError, match="coordinates"):
        recipe["analyze_contacts"](u)


def test_rmsf_uses_selected_atom_indices(recipe):
    import MDAnalysis as mda
    import numpy as np

    u = mda.Universe.empty(4, n_residues=2, atom_resindex=[0, 0, 1, 1], trajectory=True)
    u.add_TopologyAttr("names", ["N", "CA", "N", "CA"])
    u.add_TopologyAttr("resnames", ["ALA", "GLY"])
    u.add_TopologyAttr("resids", [1, 2])
    u.add_TopologyAttr("segids", ["A"])
    u.load_new(
        np.array(
            [
                [[0, 0, 0], [1, 0, 0], [0, 0, 0], [3, 0, 0]],
                [[0, 0, 0], [-1, 0, 0], [0, 0, 0], [-3, 0, 0]],
            ],
            dtype=np.float32,
        )
    )
    keys, values = recipe["compute_rmsf"](u)
    assert keys == [(0, "A", 1, "ALA"), (1, "A", 2, "GLY")]
    assert values == pytest.approx([1, 3])
