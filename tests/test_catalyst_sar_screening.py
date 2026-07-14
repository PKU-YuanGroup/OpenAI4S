"""Offline tests for the catalyst_sar_screening skill."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from openai4s.config import get_config
from openai4s.skills_loader import SkillLoader


def _import_skill():
    sys.path.insert(0, str(get_config().skills_dir))
    from catalyst_sar_screening.kernel import (  # noqa: PLC0415
        analyze_structure_activity,
        build_markdown_report,
        build_poscar_from_description,
        build_uma_python_command,
        calculate_dissolution_potential_from_binding,
        calculate_orr_overpotential,
        check_uma_readiness,
        command_to_shell,
        list_catalog_metals,
        load_contcar_catalog,
        normalize_metrics,
        parse_structure_description,
        rank_candidates,
        render_sar_dashboard,
        require_uma_ready,
        run_pipeline,
    )

    return {
        "analyze_structure_activity": analyze_structure_activity,
        "build_markdown_report": build_markdown_report,
        "build_poscar_from_description": build_poscar_from_description,
        "build_uma_python_command": build_uma_python_command,
        "calculate_dissolution_potential_from_binding": calculate_dissolution_potential_from_binding,
        "calculate_orr_overpotential": calculate_orr_overpotential,
        "check_uma_readiness": check_uma_readiness,
        "command_to_shell": command_to_shell,
        "list_catalog_metals": list_catalog_metals,
        "load_contcar_catalog": load_contcar_catalog,
        "normalize_metrics": normalize_metrics,
        "parse_structure_description": parse_structure_description,
        "rank_candidates": rank_candidates,
        "render_sar_dashboard": render_sar_dashboard,
        "require_uma_ready": require_uma_ready,
        "run_pipeline": run_pipeline,
    }


def _metal_bind(metal: str) -> float:
    return {
        "fe": -2.35,
        "co": -2.05,
        "ni": -1.55,
        "cu": -0.95,
        "pt": -1.95,
        "zn": -0.65,
        "ti": -1.20,
        "au": -1.80,
    }.get(metal.lower(), -2.0)


def _install_offline_uma_pipeline(monkeypatch) -> None:
    """Patch UMA gate for offline wiring tests (CI without GPU/token)."""
    sys.path.insert(0, str(get_config().skills_dir))
    from catalyst_sar_screening import kernel as k  # noqa: PLC0415

    monkeypatch.setattr(k, "require_uma_ready", lambda **_kw: {"ok": True})

    class _FakeCalc:
        def __init__(self, calculator_name: str = "UMA", device: str = "cuda") -> None:
            if calculator_name not in {"UMA", "uma"}:
                raise ValueError("unsupported calculator")
            self.protocol = k.CDA_UMA_PROTOCOL

        def evaluate_structure(
            self, atoms, metrics=None, adsorbates=None, reaction="ORR"
        ):
            metal = next(
                (a.symbol for a in atoms if a.symbol in k.METALS),
                "Fe",
            )
            bind = _metal_bind(metal)
            requested = k.normalize_metrics(metrics)
            row = {
                "metal": metal,
                "coordination": ["N", "N", "N", "N"],
                "converged": True,
                "backend": "uma",
                "protocol": k.CDA_UMA_PROTOCOL,
                "mlip_model": k.CDA_UMA_MODEL,
                "mlip_task": k.CDA_UMA_TASK,
            }
            if "dissolution" in requested or "orr" in requested:
                row[
                    "dissolution_potential"
                ] = k.calculate_dissolution_potential_from_binding(metal, bind)
                row["metal_binding_energy"] = bind
            if "adsorption" in requested or "orr" in requested:
                row["adsorption_energies"] = {
                    "*O": 2.0,
                    "*OH": 1.0,
                    "*OOH": 3.8,
                }
            if "orr" in requested:
                op, rds, _ = k.calculate_orr_overpotential(2.0, 1.0, 3.8)
                row["overpotential"] = op
                row["rds"] = rds
            return row

    monkeypatch.setattr(k, "CalculationTools", _FakeCalc)

    class _Atom:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol
            self.tag = 1

    class _Atoms(list):
        pass

    def fake_evaluate_poscars(built, *, metrics=None, adsorbates=None, reaction="ORR"):
        k.require_uma_ready()
        calc = k.CalculationTools(calculator_name="UMA")
        out = []
        for meta in built:
            metal = str(meta.get("metal") or "Fe")
            atoms = _Atoms(
                [_Atom(metal), _Atom("N"), _Atom("N"), _Atom("N"), _Atom("N")]
            )
            row = calc.evaluate_structure(atoms, metrics=metrics)
            row.update(
                {
                    "name": meta.get("name"),
                    "description": meta.get("description"),
                    "source": meta.get("source"),
                    "host": meta.get("host"),
                    "motif": meta.get("motif"),
                    "coordination_label": "N4",
                    "catalog_key": meta.get("catalog_key"),
                    "poscar_path": meta.get("poscar_path"),
                }
            )
            out.append(row)
        return out

    monkeypatch.setattr(k, "evaluate_poscars", fake_evaluate_poscars)


def test_catalyst_sar_skill_is_discovered():
    skills = SkillLoader().discover()
    assert "catalyst_sar_screening" in skills
    skill = skills["catalyst_sar_screening"]
    assert skill.has_kernel
    assert "catalyst_sar_screening.kernel" in (skill.import_hint or "")
    assert "dissolution" in skill.description.lower()


def test_catalyst_sar_skill_is_searchable():
    hits = SkillLoader().search(
        "single-atom catalyst dissolution potential ORR M-N4 SAR screening UMA"
    )
    assert any(hit["name"] == "catalyst_sar_screening" for hit in hits)


def test_skill_doc_covers_pipeline_and_hard_lock():
    skill_root = get_config().skills_dir / "catalyst_sar_screening"
    doc = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    assert "HARD LOCK" in doc or "hard-locked" in doc.lower()
    assert "Catalyst-Design-Agent" in doc
    assert "HF_TOKEN" in doc
    assert "HF_ENDPOINT" in doc
    assert "contcar_catalog.json" in doc
    assert "check_uma_readiness" in doc
    assert "uma-s-1p1" in doc
    assert "oc20" in doc
    assert "metal_center_dissolution_" in doc
    assert "no `data/` or `examples/`" in doc or "flat" in doc.lower()
    assert "FORBIDDEN" in doc or "Forbidden" in doc
    assert "run_pipeline" in doc
    assert "Example user prompt" in doc
    assert "Mn、Fe、Cu" in doc or "Mn, Fe, Cu" in doc or "Mn-N4" in doc
    assert "<HF_TOKEN_PLACEHOLDER>" in doc
    assert "hf_Nueg" not in doc
    assert (skill_root / "kernel.py").exists()
    assert (skill_root / "contcar_catalog.json").exists()
    assert not (skill_root / "data").exists()
    assert not (skill_root / "examples").exists()
    assert not (skill_root / "science.py").exists()
    assert not (skill_root / "viz.py").exists()


def test_examples_are_safe_demo_shells():
    """Demo shells at skill root must not ship PNGs, heuristics, or numerics."""
    skill_root = get_config().skills_dir / "catalyst_sar_screening"
    assert sorted(skill_root.glob("*.png")) == []
    gitignore = (skill_root / ".gitignore").read_text(encoding="utf-8")
    assert "*.png" in gitignore

    summary = json.loads(
        (skill_root / "metal_center_dissolution_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary.get("demo") is True
    assert summary.get("ranked")
    assert {row["name"] for row in summary["ranked"]} == {"Mn-N4", "Fe-N4", "Cu-N4"}
    for row in summary["ranked"]:
        assert row.get("backend") not in {"tabular_heuristic", "heuristic", "lookup"}
        assert row.get("dissolution_potential") is None
    blob = (skill_root / "metal_center_dissolution_dashboard.html").read_text(
        encoding="utf-8"
    )
    report = (skill_root / "metal_center_dissolution_report.md").read_text(
        encoding="utf-8"
    )
    for text in (blob, report, json.dumps(summary)):
        assert "tabular_heuristic" not in text
        assert "/remote-home/" not in text
        assert "hf_Nueg" not in text


def test_metal_center_helper_requires_descriptions():
    sys.path.insert(0, str(get_config().skills_dir))
    from catalyst_sar_screening import kernel as k  # noqa: PLC0415

    with pytest.raises(ValueError, match="descriptions is required"):
        k.run_metal_center_dissolution_case([])
    with pytest.raises(TypeError):
        k.run_metal_center_dissolution_case()  # type: ignore[call-arg]


def test_metal_center_example_is_documented():
    skill_root = get_config().skills_dir / "catalyst_sar_screening"
    doc = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    assert "Developer demos" in doc or "synthetic" in doc.lower()
    assert (skill_root / "build_example.py").exists()
    assert (skill_root / "metal_center_dissolution_descriptions.json").exists()
    assert (skill_root / "metal_center_dissolution_dashboard.html").exists()
    assert (skill_root / "metal_center_dissolution_report.md").exists()
    assert (skill_root / "metal_center_dissolution_summary.json").exists()
    descriptions = json.loads(
        (skill_root / "metal_center_dissolution_descriptions.json").read_text(
            encoding="utf-8"
        )
    )
    assert descriptions == ["Mn-N4", "Fe-N4", "Cu-N4"]

    html = (skill_root / "metal_center_dissolution_dashboard.html").read_text(
        encoding="utf-8"
    )
    report = (skill_root / "metal_center_dissolution_report.md").read_text(
        encoding="utf-8"
    )
    assert "<!doctype html>" in html
    assert 'id="computation-model-panel"' in html or "Computation model" in html
    assert "## Computation model" in report or "uma-s-1p1" in report
    assert "Synthetic" in report or "demo" in report.lower()


def test_contcar_catalog_is_vendored_and_usable(tmp_path: Path):
    funcs = _import_skill()
    catalog = funcs["load_contcar_catalog"]()
    assert catalog["n_entries"] > 0
    assert catalog["hosts"] == ["graphene"]
    assert "pyridineN" in catalog["motifs"] or "pyridine" in catalog["motifs"]
    metals = funcs["list_catalog_metals"](host="graphene", motif="pyridineN")
    assert {"Mn", "Fe", "Cu"}.issubset(set(metals))

    fe = funcs["parse_structure_description"]("Fe-N4")
    assert fe["metal"] == "Fe"
    meta = funcs["build_poscar_from_description"](fe, tmp_path / "Fe-N4.POSCAR")
    assert meta["source"] == "catalog"
    assert Path(meta["poscar_path"]).is_file()
    text = Path(meta["poscar_path"]).read_text(encoding="utf-8")
    assert "Fe" in text

    # Method 2: Bi is absent from the catalog → derive by metal edit.
    derived = funcs["build_poscar_from_description"]("Bi-N4", tmp_path / "Bi-N4.POSCAR")
    assert derived["source"] == "catalog_derived"
    assert derived["reference_metal"]
    assert "Bi" in Path(derived["poscar_path"]).read_text(encoding="utf-8")


def test_uma_only_and_readiness(monkeypatch):
    funcs = _import_skill()
    sys.path.insert(0, str(get_config().skills_dir))
    from catalyst_sar_screening import kernel as k  # noqa: PLC0415

    with pytest.raises(ValueError, match="unsupported calculator|hard-lock"):
        k.CalculationTools(calculator_name="heuristic")
    with pytest.raises(ValueError, match="unsupported calculator|hard-lock"):
        k.CalculationTools(calculator_name="chgnet")

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    ready = funcs["check_uma_readiness"](probe_conda_env=None, probe_hub=False)
    assert ready["ok"] is False
    assert any("HF_TOKEN" in m for m in ready["missing"])
    assert "Do NOT skip" in ready["ask_user"]
    with pytest.raises(RuntimeError, match="HF_TOKEN|Do NOT skip"):
        funcs["require_uma_ready"](probe_conda_env=None, probe_hub=False)

    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setenv("HF_ENDPOINT", "https://huggingface.co")
    monkeypatch.setattr(
        k,
        "probe_huggingface_hub",
        lambda **_kw: {
            "ok": False,
            "endpoint": "https://huggingface.co",
            "error": "URLError: timed out",
            "probed_url": "https://huggingface.co/api/models",
            "status": None,
        },
    )
    ready = funcs["check_uma_readiness"](probe_conda_env=None, probe_hub=True)
    assert ready["ok"] is False
    assert any("HF_ENDPOINT" in m for m in ready["missing"])
    assert "mirror" in ready["ask_user"].lower()


def test_normalize_metrics_and_formulas():
    funcs = _import_skill()
    assert funcs["normalize_metrics"](["udiss"]) == ["dissolution"]
    assert funcs["normalize_metrics"](["op", "ads"]) == ["orr", "adsorption"]
    assert funcs["calculate_dissolution_potential_from_binding"](
        "Fe", -2.0
    ) == pytest.approx(0.553)
    op, rds, steps = funcs["calculate_orr_overpotential"](
        dG_O=2.0, dG_OH=1.0, dG_OOH=3.8
    )
    assert isinstance(op, float)
    assert op == pytest.approx(0.23)
    assert rds in steps
    # RDS is the bottleneck (max-dG) step that sets the limiting potential,
    # NOT the most-downhill (min-dG) step. Locks the argmax fix in place.
    assert rds == "deltaG_OH - deltaG_O"


def test_lean_pipeline_deliverables(tmp_path: Path, monkeypatch):
    funcs = _import_skill()
    _install_offline_uma_pipeline(monkeypatch)

    result = funcs["run_pipeline"](
        ["Fe-N4", "Co-N4", "Pt-N4"],
        workdir=tmp_path / "run",
        metrics=["dissolution"],
        min_dissolution=-2.0,
    )
    assert result["mode"] == "dissolution"
    assert Path(result["report_path"]).is_file()
    assert Path(result["html_path"]).is_file()
    assert Path(result["summary_path"]).is_file()
    names = {Path(p).name for p in result["deliverables"]}
    assert "catalyst_sar_report.md" in names
    assert "catalyst_sar_dashboard.html" in names
    assert "summary.json" in names
    assert "structures_collage.png" in names
    assert "fig01_udiss_by_metal.png" in names

    report = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "## Computation model" in report
    assert "uma-s-1p1" in report
    assert "## Figures" in report
    assert "## Structure renders" in report

    html = Path(result["html_path"]).read_text(encoding="utf-8")
    assert 'id="computation-model-panel"' in html
    assert 'id="figures-panel"' in html
    assert 'id="sar-data"' in html
    assert "3Dmol" not in html
    payload = json.loads(html.split('id="sar-data">')[1].split("</script>")[0])
    assert payload.get("computation", {}).get("mlip_model") == "uma-s-1p1"

    summary = json.loads(Path(result["summary_path"]).read_text(encoding="utf-8"))
    assert summary.get("computation", {}).get("mlip_model") == "uma-s-1p1"
    assert summary.get("figures")
    assert summary.get("structure_renders")


def test_metal_center_ranking_and_report_helpers():
    funcs = _import_skill()
    rows = [
        {
            "name": "Pt-N4",
            "metal": "Pt",
            "dissolution_potential": 1.2,
            "converged": True,
            "source": "catalog",
        },
        {
            "name": "Fe-N4",
            "metal": "Fe",
            "dissolution_potential": 0.4,
            "converged": True,
            "source": "catalog",
        },
        {
            "name": "Zn-N4",
            "metal": "Zn",
            "dissolution_potential": -0.2,
            "converged": True,
            "source": "catalog_derived",
        },
    ]
    ranked = funcs["rank_candidates"](rows, mode="dissolution", min_dissolution=0.0)
    assert ranked[0]["name"] == "Pt-N4"
    analysis = funcs["analyze_structure_activity"](
        rows, metrics=["dissolution"], min_dissolution=0.0
    )
    insights = " ".join(analysis["insights"])
    assert "Metal-center ranking" in insights or "dissolution potential" in insights
    analysis["computation"] = {
        "calculator": "uma",
        "mlip_model": "uma-s-1p1",
        "mlip_task": "oc20",
        "protocol": "catalyst-design-agent/uma-s-1p1+oc20",
    }
    analysis["figures"] = []
    analysis["structure_renders"] = []
    md = funcs["build_markdown_report"](analysis)
    assert "Pt-N4" in md
    assert "uma-s-1p1" in md
    html = funcs["render_sar_dashboard"](analysis)
    assert "sar-data" in html


def test_all_catalog_metals_are_recognized_and_dissolution_support():
    """Every advertised catalog metal must be a recognized center; U_diss support
    is gated on vendored data (never fabricated)."""
    funcs = _import_skill()
    sys.path.insert(0, str(get_config().skills_dir))
    from catalyst_sar_screening import kernel as k  # noqa: PLC0415

    catalog = funcs["load_contcar_catalog"]()
    catalog_metals = {e["metal"] for e in catalog["entries"]}
    # Guards the get_vnn_idx IndexError: the active center is found via METALS.
    assert catalog_metals.issubset(set(k.METALS)), catalog_metals - set(k.METALS)

    supported = k.supported_dissolution_metals()
    assert {"Fe", "Mn", "Cu"}.issubset(supported)
    for metal in supported:
        assert metal.lower() in k.METAL_REDUCTION_POTENTIAL
        assert metal.lower() in k.METAL_REFERENCE_ENERGIES
    # Rh/Os are catalog metals with no vendored reduction/reference data.
    assert k.unsupported_dissolution_metals(["Fe", "Rh", "Cu", "Os"]) == ["Os", "Rh"]
    assert k.unsupported_dissolution_metals(["Fe", "Mn", "Cu"]) == []


def test_adsorption_mode_still_exports_a_figure(tmp_path: Path):
    """metrics=['adsorption'] must yield a statistical figure, not a RuntimeError."""
    sys.path.insert(0, str(get_config().skills_dir))
    from catalyst_sar_screening import kernel as k  # noqa: PLC0415

    analysis = {
        "mode": "adsorption",
        "ranked": [
            {
                "name": "Fe-N4",
                "metal": "Fe",
                "adsorption_energies": {"*OH": 0.9},
                "converged": True,
            },
            {
                "name": "Co-N4",
                "metal": "Co",
                "adsorption_energies": {"*OH": 1.4},
                "converged": True,
            },
        ],
    }
    figures = k.export_publication_figures(analysis, tmp_path / "figures")
    assert figures, "adsorption mode must still produce a statistical figure"
    assert Path(figures[0]["png_path"]).is_file()
    assert figures[0]["id"] == "fig01_oh_adsorption"


def test_parse_poscar_negative_scale_and_truncation():
    sys.path.insert(0, str(get_config().skills_dir))
    from catalyst_sar_screening import kernel as k  # noqa: PLC0415

    # Negative scale is the target cell VOLUME (VASP convention), not a multiplier:
    # a 3x3x3 raw cell (V=27) with scale -216 rescales uniformly to V=216 (6x6x6).
    poscar = "cube\n-216.0\n3 0 0\n0 3 0\n0 0 3\nH\n1\nCartesian\n0 0 0\n"
    parsed = k.parse_poscar(poscar)
    assert parsed["lattice"][0][0] == pytest.approx(6.0)
    assert abs(k._lattice_determinant(parsed["lattice"])) == pytest.approx(216.0)

    # More declared atoms than coordinate lines -> clear ValueError, not IndexError.
    truncated = "cube\n1.0\n3 0 0\n0 3 0\n0 0 3\nH\n2\nCartesian\n0 0 0\n"
    with pytest.raises(ValueError):
        k.parse_poscar(truncated)


def test_build_poscars_sanitizes_structured_names(tmp_path: Path):
    """A structured description name cannot escape the output dir via '..'."""
    sys.path.insert(0, str(get_config().skills_dir))
    from catalyst_sar_screening import kernel as k  # noqa: PLC0415

    out = tmp_path / "poscars"
    built = k.build_poscars_from_descriptions(
        [{"metal": "Fe", "name": "../../../../evil"}], out
    )
    written = Path(built[0]["poscar_path"]).resolve()
    assert out.resolve() in written.parents
    assert "/" not in Path(written).name.replace(".POSCAR", "")
