"""Contracts for the curated single-cell RNA analysis workflow."""

from __future__ import annotations

import csv
import importlib
import json
import sys
from pathlib import Path

import pytest

from openai4s.skills_loader import SkillLoader


@pytest.fixture(scope="module")
def kernel():
    skills = str(Path(__file__).resolve().parents[1] / "skills")
    sys.path.insert(0, skills)
    try:
        yield importlib.import_module("single-cell-rna-analysis.kernel")
    finally:
        sys.path.remove(skills)


def _base_config(path: Path, mode: str = "sample_sheet") -> dict:
    return {
        "schema_version": 1,
        "organism": "human",
        "modality": "scrna",
        "input": {"mode": mode, "path": str(path), "counts_layer": "counts"},
        "reference": {
            "gene_id_type": "symbol",
            "genome_build": "GRCh38",
            "annotation_release": "GENCODE 46",
        },
        "design": {
            "sample_key": "sample_id",
            "donor_key": "donor_id",
            "condition_key": "condition",
            "tested": "stim",
            "reference": "control",
            "paired": True,
            "covariates": [],
        },
        "integration": {"method": "none", "batch_keys": []},
        "qc": {"doublet_detection": False, "ambient_correction": "upstream"},
        "statistics": {"de": False, "da": False},
        "seed": 13,
    }


def _write_sheet(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_skill_is_discoverable_retrievable_and_sidecar_compiles():
    skill = SkillLoader().discover()["single-cell-rna-analysis"]
    assert skill.origin == "openai4s"
    assert skill.read_only is True
    assert skill.has_kernel is True
    assert skill.sidecar_gate() == {"ok": True, "error": None}
    hits = SkillLoader().search(
        "Scanpy single cell RNA pseudobulk donor Harmony Scrublet", limit=3
    )
    assert "single-cell-rna-analysis" in [hit["name"] for hit in hits]


def test_preflight_resolves_sample_paths_and_rejects_harmony_without_batch(
    tmp_path, kernel
):
    matrix = tmp_path / "matrix"
    matrix.mkdir()
    sheet = tmp_path / "samples.csv"
    _write_sheet(
        sheet,
        [
            {
                "sample_id": "d1_control",
                "donor_id": "d1",
                "condition": "control",
                "matrix_path": "matrix",
                "matrix_format": "10x_mtx",
            },
            {
                "sample_id": "d1_stim",
                "donor_id": "d1",
                "condition": "stim",
                "matrix_path": "matrix",
                "matrix_format": "10x_mtx",
            },
        ],
    )
    config = _base_config(sheet)
    result = kernel.preflight(config)
    assert result["status"] == "valid"
    assert result["input_fingerprint"]
    assert result["sample_count"] == 2

    config["integration"] = {"method": "harmony", "batch_keys": []}
    result = kernel.preflight(config)
    assert result["status"] == "invalid"
    assert any("explicit" in error for error in result["errors"])


def test_preflight_rejects_confounded_harmony_and_reference_mismatch(tmp_path, kernel):
    matrix = tmp_path / "matrix"
    matrix.mkdir()
    sheet = tmp_path / "samples.csv"
    rows = []
    for index, condition in enumerate(("control", "stim"), start=1):
        rows.append(
            {
                "sample_id": f"s{index}",
                "donor_id": f"d{index}",
                "condition": condition,
                "matrix_path": str(matrix),
                "matrix_format": "10x_mtx",
                "batch": condition,
                "genome_build": "GRCh38" if index == 1 else "GRCm39",
            }
        )
    _write_sheet(sheet, rows)
    config = _base_config(sheet)
    config["integration"] = {"method": "harmony", "batch_keys": ["batch"]}
    result = kernel.preflight(config)
    assert result["status"] == "invalid"
    assert any("fully confounded" in error for error in result["errors"])
    assert any("genome_build" in error for error in result["errors"])


def test_raw_count_and_replication_gates_are_explicit(kernel):
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    assert kernel._is_raw_counts(np.array([[0, 1], [2, 3]]), np) == (True, None)
    assert kernel._is_raw_counts(np.array([[0.0, 0.5]]), np)[0] is False
    assert kernel._is_raw_counts(np.array([[0, -1]]), np)[0] is False

    metadata = pd.DataFrame(
        {
            "condition": ["control", "control", "stim", "stim"],
            "donor_id": ["d1", "d2", "d1", "d2"],
        }
    )
    enough, counts = kernel._replication_status(metadata, _base_config(Path("unused")))
    assert enough is False
    assert counts == {"control": 2, "stim": 2}


def _synthetic_h5ad(tmp_path: Path):
    ad = pytest.importorskip("anndata")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    rng = np.random.default_rng(13)
    genes = ["MT-CO1", "RPL3", "CD3D", "NKG7", *[f"G{i}" for i in range(56)]]
    counts = []
    obs = []
    for donor in ("d1", "d2", "d3"):
        for condition in ("control", "stim"):
            sample = f"{donor}_{condition}"
            for cell_index in range(24):
                cluster = "T" if cell_index < 12 else "NK"
                mean = np.full(len(genes), 1.5)
                mean[2 if cluster == "T" else 3] = 10
                if condition == "stim":
                    mean[4:8] += 5
                counts.append(rng.poisson(mean))
                obs.append(
                    {
                        "sample_id": sample,
                        "donor_id": donor,
                        "condition": condition,
                        "batch": "b1" if donor in {"d1", "d3"} else "b2",
                    }
                )
    matrix = np.asarray(counts, dtype=np.int32)
    adata = ad.AnnData(
        matrix.copy(), obs=pd.DataFrame(obs), var=pd.DataFrame(index=genes)
    )
    adata.obs_names = [f"cell-{index}" for index in range(adata.n_obs)]
    adata.layers["counts"] = matrix.copy()
    path = tmp_path / "synthetic.h5ad"
    adata.write_h5ad(path)
    return path


def test_h5ad_preflight_rejects_normalized_only_matrix(tmp_path, kernel):
    path = _synthetic_h5ad(tmp_path)
    import scanpy as sc

    adata = sc.read_h5ad(path)
    del adata.layers["counts"]
    adata.X = adata.X.astype(float) / 3.0
    normalized_path = tmp_path / "normalized-only.h5ad"
    adata.write_h5ad(normalized_path)
    result = kernel.preflight(_base_config(normalized_path, mode="h5ad"))
    assert result["status"] == "invalid"
    assert any("counts" in error for error in result["errors"])


def test_marker_conflict_stays_unknown_until_confirmed(tmp_path, kernel):
    ad = pytest.importorskip("anndata")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    adata = ad.AnnData(
        np.array([[2.0, 2.0], [2.0, 2.0]]),
        obs=pd.DataFrame({"cluster": pd.Categorical(["0", "0"])}),
        var=pd.DataFrame(index=["CD3D", "NKG7"]),
    )
    panel = tmp_path / "markers.csv"
    pd.DataFrame(
        [
            {"cell_type": "T", "gene": "CD3D", "direction": "positive", "weight": 1},
            {"cell_type": "NK", "gene": "NKG7", "direction": "positive", "weight": 1},
        ]
    ).to_csv(panel, index=False)
    config = _base_config(Path("unused"))
    config["annotation"] = {"marker_panel": str(panel), "minimum_margin": 0.1}
    resolved = kernel._resolved_config(config)
    annotated, status, _, evidence = kernel._annotate(adata, resolved, pd, np)
    assert status == "candidate_labels"
    assert set(annotated.obs["candidate_cell_type"]) == {"Unknown"}
    assert set(evidence["cell_type"]) == {"T", "NK"}


@pytest.mark.slow
def test_paired_pydeseq2_uses_donor_and_preserves_effect_direction(tmp_path, kernel):
    pd = pytest.importorskip("pandas")
    pytest.importorskip("pydeseq2")
    count_rows = []
    metadata_rows = []
    for donor_index, donor in enumerate(("d1", "d2", "d3"), start=1):
        for condition in ("control", "stim"):
            sample = f"{donor}_{condition}"
            genes = {
                f"G{gene}": 10
                + donor_index
                + gene
                + (70 if condition == "stim" and gene == 0 else 0)
                for gene in range(20)
            }
            count_rows.append(
                {"sample_id": sample, "analysis_group": "cluster_0", **genes}
            )
            metadata_rows.append(
                {
                    "sample_id": sample,
                    "analysis_group": "cluster_0",
                    "donor_id": donor,
                    "condition": condition,
                }
            )
    output = tmp_path / "de.csv"
    config = _base_config(Path("unused"))
    config["statistics"]["de"] = True
    status = kernel._run_deseq(
        pd.DataFrame(count_rows),
        pd.DataFrame(metadata_rows),
        config,
        output,
        pd,
    )
    assert status == "completed"
    results = pd.read_csv(output)
    g0 = results.loc[results["gene"] == "G0"].iloc[0]
    assert g0["log2FoldChange"] > 0


@pytest.mark.slow
def test_deterministic_synthetic_run_preserves_counts_and_resumes(tmp_path, kernel):
    pytest.importorskip("scanpy")
    pytest.importorskip("skmisc")
    path = _synthetic_h5ad(tmp_path)
    config = _base_config(path, mode="h5ad")
    config["clustering"] = {
        "resolutions": [0.2, 0.5],
        "selected_resolution": 0.5,
        "n_neighbors": 8,
        "n_pcs": 12,
    }
    config["statistics"] = {"de": True, "da": True}
    run_dir = tmp_path / "run"
    result = kernel.run(config, run_dir)
    assert result["status"] in {"completed", "completed_with_warnings"}, json.loads(
        (run_dir / "run_manifest.json").read_text(encoding="utf-8")
    )
    assert result["statistics_status"] == {"de": "completed", "da": "completed"}
    assert Path(result["manifest"]).is_file()
    assert all(Path(path).is_file() for path in result["featured_files"])
    featured_names = {Path(path).name for path in result["featured_files"]}
    assert {
        "analysis.h5ad",
        "run_manifest.json",
        "resolution_sweep.pdf",
        "cluster_markers.pdf",
        "differential_expression.pdf",
        "differential_abundance.pdf",
    } <= featured_names

    import scanpy as sc

    analyzed = sc.read_h5ad(run_dir / "analysis.h5ad")
    raw = analyzed.layers["counts"]
    import numpy as np

    values = raw.toarray() if hasattr(raw, "toarray") else np.asarray(raw)
    assert (values >= 0).all()
    assert (values == values.astype(int)).all()
    assert "X_pca_harmony" not in analyzed.obsm

    import pandas as pd

    pseudobulk = pd.read_csv(run_dir / "tables" / "pseudobulk_counts.csv")
    metadata = pd.read_csv(run_dir / "tables" / "pseudobulk_metadata.csv")
    merged = pseudobulk.merge(metadata[["sample_id", "analysis_group", "condition"]])
    assert (
        merged.loc[merged["condition"] == "stim", "G0"].mean()
        > merged.loc[merged["condition"] == "control", "G0"].mean()
    )
    first_hash = Path(result["manifest"]).read_bytes()
    resumed = kernel.resume(run_dir)
    assert resumed["status"] == result["status"]
    assert Path(resumed["manifest"]).read_bytes() == first_hash

    checkpoint_hashes = {
        filename: kernel._sha256_file(run_dir / filename)
        for filename in (
            "01_qc.h5ad",
            "02_embedding.h5ad",
            "03_clustering.h5ad",
            "04_annotation.h5ad",
        )
    }
    (run_dir / "analysis.h5ad").unlink()
    recovered = kernel.resume(run_dir)
    recovered_manifest = json.loads(
        Path(recovered["manifest"]).read_text(encoding="utf-8")
    )
    assert recovered_manifest["resumed_from_stage"] == "statistics"
    assert checkpoint_hashes == {
        filename: kernel._sha256_file(run_dir / filename)
        for filename in checkpoint_hashes
    }

    source = sc.read_h5ad(path)
    source.layers["counts"][0, 4] += 1
    source.X[0, 4] += 1
    source.write_h5ad(path)
    rebuilt = kernel.resume(run_dir)
    assert rebuilt["status"] in {"completed", "completed_with_warnings"}
    assert (run_dir / ".invalidated" / "run_manifest.json").is_file()
    assert Path(rebuilt["manifest"]).read_bytes() != first_hash


@pytest.mark.network
def test_kang_2018_optional_real_data_smoke(tmp_path, kernel):
    pt = pytest.importorskip("pertpy")
    adata = pt.data.kang_2018()
    adata = adata[:1200].copy()
    if "sample_id" not in adata.obs:
        source = next(
            key for key in ("sample", "replicate", "donor") if key in adata.obs
        )
        adata.obs["sample_id"] = adata.obs[source].astype(str)
    if "donor_id" not in adata.obs:
        adata.obs["donor_id"] = adata.obs["sample_id"].astype(str)
    if "condition" not in adata.obs:
        source = next(key for key in ("label", "condition") if key in adata.obs)
        adata.obs["condition"] = adata.obs[source].astype(str)
    if "counts" not in adata.layers:
        adata.layers["counts"] = adata.X.copy()
    path = tmp_path / "kang.h5ad"
    adata.write_h5ad(path)
    levels = sorted(adata.obs["condition"].astype(str).unique())
    config = _base_config(path, mode="h5ad")
    config["design"]["reference"], config["design"]["tested"] = levels[:2]
    config["design"]["paired"] = False
    result = kernel.run(config, tmp_path / "kang-run")
    assert result["status"] in {"completed", "completed_with_warnings"}
