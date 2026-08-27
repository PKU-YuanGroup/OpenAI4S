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


def _descriptive_config(path: Path) -> dict:
    return {
        "schema_version": 1,
        "analysis_mode": "descriptive",
        "organism": "human",
        "modality": "scrna",
        "input": {
            "mode": "h5ad",
            "path": str(path),
            "counts_layer": "X",
            "sample_id": "pbmc3k",
        },
        "reference": {
            "gene_id_type": "symbol",
            "genome_build": "hg19",
            "annotation_release": "GENCODE 19",
        },
        "integration": {"method": "none", "batch_keys": []},
        "qc": {"doublet_detection": False, "ambient_correction": "upstream"},
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


def test_descriptive_preflight_accepts_single_h5ad_without_design_metadata(
    tmp_path, kernel, monkeypatch
):
    path = tmp_path / "pbmc3k.h5ad"
    path.write_bytes(b"synthetic-h5ad-placeholder")
    monkeypatch.setattr(kernel, "_inspect_h5ad", lambda *args, **kwargs: ([], [], None))

    result = kernel.preflight(_descriptive_config(path))

    assert result["status"] == "valid"
    assert result["sample_count"] == 1
    assert result["resolved_config"]["analysis_mode"] == "descriptive"
    assert result["resolved_config"]["design"] == {"sample_key": "sample_id"}
    assert result["resolved_config"]["statistics"] == {"de": False, "da": False}


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"analysis_mode": "unsupported"}, "analysis_mode"),
        ({"input": {"mode": "sample_sheet"}}, "requires input.mode=h5ad"),
        ({"integration": {"method": "harmony"}}, "does not support integration"),
        ({"statistics": {"de": True}}, "statistics.de=false"),
        ({"design": {"tested": "stim"}}, "does not accept"),
    ],
)
def test_descriptive_preflight_rejects_inferential_configuration(
    tmp_path, kernel, update, message
):
    path = tmp_path / "pbmc3k.h5ad"
    path.write_bytes(b"synthetic-h5ad-placeholder")
    config = _descriptive_config(path)
    for key, value in update.items():
        if isinstance(value, dict):
            config.setdefault(key, {}).update(value)
        else:
            config[key] = value

    result = kernel.preflight(config)

    assert result["status"] == "invalid"
    assert any(message in error for error in result["errors"])


def test_raw_count_and_replication_gates_are_explicit(kernel):
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    assert kernel._is_raw_counts(np.array([[0, 1], [2, 3]]), np) == (True, None)
    assert kernel._is_raw_counts(np.array([[0.0, 0.5]]), np)[0] is False
    assert kernel._is_raw_counts(np.array([[0, -1]]), np)[0] is False
    # Large magnitudes must not relax the integer gate (rtol must be zero).
    assert kernel._is_raw_counts(np.array([[50000.4]]), np)[0] is False

    metadata = pd.DataFrame(
        {
            "condition": ["control", "control", "stim", "stim"],
            "donor_id": ["d1", "d2", "d1", "d2"],
        }
    )
    enough, counts = kernel._replication_status(metadata, _base_config(Path("unused")))
    assert enough is False
    assert counts == {"control": 2, "stim": 2}


def test_descriptive_load_assigns_only_technical_sample_id(tmp_path, kernel):
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    class FakeAdata:
        def __init__(self):
            self.X = np.array([[1, 0], [0, 2]], dtype=np.int32)
            self.obs = pd.DataFrame(index=["cell-1", "cell-2"])
            self.obs_names = self.obs.index
            self.var_names = pd.Index(["G1", "G2"])
            self.layers = {}

        def obs_names_make_unique(self, join="-"):
            self.obs_names = self.obs_names.astype(str)

    class FakeScanpy:
        @staticmethod
        def read_h5ad(path):
            return FakeAdata()

    path = tmp_path / "pbmc3k.h5ad"
    path.write_bytes(b"synthetic-h5ad-placeholder")
    config = kernel._resolved_config(_descriptive_config(path))

    adata, warnings = kernel._load_data(config, np, pd, FakeScanpy(), None)

    assert warnings == []
    assert set(adata.obs.columns) == {"sample_id"}
    assert set(adata.obs["sample_id"]) == {"pbmc3k"}
    assert np.array_equal(adata.layers["counts"], adata.X)


def test_descriptive_load_normalizes_existing_sample_ids(tmp_path, kernel):
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    class FakeAdata:
        def __init__(self):
            self.X = np.array([[1, 0], [0, 2]], dtype=np.int32)
            self.obs = pd.DataFrame(
                {"sample_id": ["s1", " s1 "]}, index=["cell-1", "cell-2"]
            )
            self.obs_names = self.obs.index
            self.var_names = pd.Index(["G1", "G2"])
            self.layers = {}

        def obs_names_make_unique(self, join="-"):
            self.obs_names = self.obs_names.astype(str)

    class FakeScanpy:
        @staticmethod
        def read_h5ad(path):
            return FakeAdata()

    path = tmp_path / "single-sample.h5ad"
    path.write_bytes(b"synthetic-h5ad-placeholder")
    config = kernel._resolved_config(_descriptive_config(path))

    adata, warnings = kernel._load_data(config, np, pd, FakeScanpy(), None)

    assert warnings == []
    assert list(adata.obs["sample_id"]) == ["s1", "s1"]


def test_descriptive_cleanup_and_delivery_exclude_inferential_outputs(tmp_path, kernel):
    output = tmp_path / "reused-run"
    for relative in kernel.INFERENTIAL_OUTPUTS:
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stale comparative result", encoding="utf-8")
    analysis = output / "analysis.h5ad"
    analysis.write_bytes(b"descriptive-analysis")

    kernel._remove_inferential_outputs(output)

    assert all(
        not (output / relative).exists() for relative in kernel.INFERENTIAL_OUTPUTS
    )
    assert analysis.is_file()

    stale = output / "tables" / "differential_expression.csv"
    stale.write_text("externally restored stale result", encoding="utf-8")
    assert str(stale.resolve()) not in kernel._featured(output, "descriptive")
    assert str(stale.resolve()) in kernel._featured(output, "comparative")
    manifest = kernel._finalize_manifest(output, {"analysis_mode": "descriptive"})
    assert "tables/differential_expression.csv" not in {
        item["path"] for item in manifest["files"]
    }


def test_boolean_config_fields_reject_truthy_strings(tmp_path, kernel):
    matrix = tmp_path / "matrix"
    matrix.mkdir()
    sheet = tmp_path / "samples.csv"
    _write_sheet(
        sheet,
        [
            {
                "sample_id": "s1",
                "donor_id": "d1",
                "condition": "control",
                "matrix_path": "matrix",
                "matrix_format": "10x_mtx",
            },
            {
                "sample_id": "s2",
                "donor_id": "d1",
                "condition": "stim",
                "matrix_path": "matrix",
                "matrix_format": "10x_mtx",
            },
        ],
    )
    config = _base_config(sheet)
    config["design"]["paired"] = "false"
    config["statistics"]["de"] = "false"
    result = kernel.preflight(config)
    assert result["status"] == "invalid"
    assert any("design.paired must be a JSON boolean" in e for e in result["errors"])
    assert any("statistics.de must be a JSON boolean" in e for e in result["errors"])


def test_formula_unsafe_keys_and_levels_are_rejected(tmp_path, kernel):
    config = _base_config(tmp_path / "unused.csv")
    config["design"]["donor_key"] = "donor-id"
    config["design"]["tested"] = "anti-PD1"
    config["statistics"] = {"de": True, "da": True}
    result = kernel.preflight(config)
    assert result["status"] == "invalid"
    assert any("not formula-safe" in error for error in result["errors"])
    assert any("Milo's contrast string" in error for error in result["errors"])

    # With inference disabled, the same names never reach a formula or a
    # contrast string, so they must not be rejected for it.
    config["statistics"] = {"de": False, "da": False}
    result = kernel.preflight(config)
    assert not any("not formula-safe" in error for error in result["errors"])
    assert not any("Milo's contrast string" in error for error in result["errors"])

    # DE alone passes levels to PyDESeq2 as a list, so only the column-name
    # rule applies.
    config["statistics"] = {"de": True, "da": False}
    result = kernel.preflight(config)
    assert any("not formula-safe" in error for error in result["errors"])
    assert not any("Milo's contrast string" in error for error in result["errors"])


def test_paired_design_rejects_covariates(tmp_path, kernel):
    config = _base_config(tmp_path / "unused.csv")
    config["design"]["covariates"] = ["sex"]
    result = kernel.preflight(config)
    assert result["status"] == "invalid"
    assert any("covariates are not supported" in e for e in result["errors"])


def test_ragged_sample_sheet_rows_are_rejected(tmp_path, kernel):
    sheet = tmp_path / "samples.csv"
    sheet.write_text(
        "sample_id,donor_id,condition,matrix_path,matrix_format\n" "s1,d1,control\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing or extra fields"):
        kernel._read_sample_sheet(sheet)


def test_resume_with_invalid_preflight_leaves_deliverables_untouched(tmp_path, kernel):
    run_dir = tmp_path / "completed-run"
    run_dir.mkdir()
    config = _base_config(tmp_path / "gone.csv")
    kernel._write_json(run_dir / "config.resolved.json", config)
    kernel._write_json(run_dir / "run_manifest.json", {"status": "completed"})
    analysis = run_dir / "analysis.h5ad"
    analysis.write_bytes(b"finished-deliverable")

    result = kernel.resume(run_dir)

    assert result["status"] == "failed"
    assert result["errors"]
    assert analysis.read_bytes() == b"finished-deliverable"
    assert not (run_dir / ".invalidated").exists()
    assert json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8")) == {
        "status": "completed"
    }


def test_annotation_stage_hash_tracks_side_input_content(tmp_path, kernel):
    panel = tmp_path / "markers.csv"
    panel.write_text("cell_type,gene,direction,weight\nT,CD3D,positive,1\n")
    config = _base_config(tmp_path / "unused.csv")
    config["annotation"] = {"marker_panel": str(panel)}
    resolved = kernel._resolved_config(config)
    before = kernel._stage_hashes(resolved, "fingerprint")

    panel.write_text("cell_type,gene,direction,weight\nNK,NKG7,positive,1\n")
    after = kernel._stage_hashes(resolved, "fingerprint")

    assert before["clustering"] == after["clustering"]
    assert before["annotation"] != after["annotation"]
    assert before["statistics"] != after["statistics"]


def test_partial_confirmed_mapping_never_pools_unknown_clusters(kernel):
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    class FakeAdata:
        pass

    adata = FakeAdata()
    adata.obs = pd.DataFrame(
        {
            "sample_id": ["s1", "s1", "s1", "s1"],
            "donor_id": ["d1", "d1", "d1", "d1"],
            "condition": ["control", "control", "control", "control"],
            "cluster": ["0", "1", "2", "2"],
            "confirmed_cell_type": ["T", "Unknown", "Unknown", "Unknown"],
        },
        index=["c1", "c2", "c3", "c4"],
    )
    adata.layers = {"counts": np.eye(4, 2, dtype=np.int64)}
    adata.var_names = pd.Index(["G1", "G2"])
    config = kernel._resolved_config(_base_config(Path("unused")))

    _, metadata, group_key = kernel._pseudobulk(adata, config, pd, np)

    assert group_key == "confirmed_cell_type"
    groups = set(metadata["analysis_group"])
    # Clusters 1 and 2 are both unmapped but must stay separate units.
    assert groups == {"T", "Unknown:cluster_1", "Unknown:cluster_2"}


def test_10x_h5_reader_deduplicates_gene_symbols(kernel):
    pd = pytest.importorskip("pandas")

    class FakeAdata:
        def __init__(self):
            self.var_names = pd.Index(["TBCE", "TBCE", "CD3D"])

        def var_names_make_unique(self, join="-"):
            counts: dict[str, int] = {}
            names = []
            for name in self.var_names:
                seen = counts.get(name, 0)
                names.append(name if seen == 0 else f"{name}{join}{seen}")
                counts[name] = seen + 1
            self.var_names = pd.Index(names)

    class FakeScanpy:
        @staticmethod
        def read_10x_h5(path):
            return FakeAdata()

    adata = kernel._read_one("matrix.h5", "10x_h5", "counts", FakeScanpy())

    # Stock CellRanger references carry duplicate symbols; the reader must
    # hand back unique names or the sample-sheet gate rejects every real file.
    assert adata.var_names.is_unique
    assert list(adata.var_names) == ["TBCE", "TBCE-1", "CD3D"]


def test_descriptive_load_rejects_leftover_design_columns(tmp_path, kernel):
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    class FakeAdata:
        def __init__(self):
            self.X = np.array([[1, 0], [0, 2]], dtype=np.int32)
            self.obs = pd.DataFrame(
                {"condition": ["stim", "stim"]}, index=["cell-1", "cell-2"]
            )
            self.obs_names = self.obs.index
            self.var_names = pd.Index(["G1", "G2"])
            self.layers = {}

        def obs_names_make_unique(self, join="-"):
            self.obs_names = self.obs_names.astype(str)

    class FakeScanpy:
        @staticmethod
        def read_h5ad(path):
            return FakeAdata()

    path = tmp_path / "leftover.h5ad"
    path.write_bytes(b"synthetic-h5ad-placeholder")
    config = kernel._resolved_config(_descriptive_config(path))

    with pytest.raises(ValueError, match="must not carry donor/condition"):
        kernel._load_data(config, np, pd, FakeScanpy(), None)


def test_reference_screening_does_not_claim_transfer(tmp_path, kernel):
    ad = pytest.importorskip("anndata")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    pytest.importorskip("scanpy")
    genes = [f"G{i}" for i in range(30)]
    adata = ad.AnnData(
        np.ones((2, 30)),
        obs=pd.DataFrame({"cluster": pd.Categorical(["0", "1"])}),
        var=pd.DataFrame(index=genes),
    )
    reference = ad.AnnData(
        np.ones((2, 30)),
        obs=pd.DataFrame({"cell_type": ["T", "NK"]}),
        var=pd.DataFrame(index=genes),
    )
    reference_path = tmp_path / "reference.h5ad"
    reference.write_h5ad(reference_path)
    config = _base_config(Path("unused"))
    config["annotation"] = {"reference_h5ad": str(reference_path)}
    resolved = kernel._resolved_config(config)

    _, status, warnings, _ = kernel._annotate(adata, resolved, pd, np)

    # Screening alone must not elevate the status to a reference claim.
    assert status == "not_requested"
    assert any("no label transfer was performed" in warning for warning in warnings)


def test_comparative_h5ad_preflight_reports_sample_count(tmp_path, kernel):
    pytest.importorskip("anndata")
    path = _synthetic_h5ad(tmp_path)
    result = kernel.preflight(_base_config(path, mode="h5ad"))
    assert result["status"] == "valid"
    assert result["sample_count"] == 6


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
    status, notes = kernel._run_deseq(
        pd.DataFrame(count_rows),
        pd.DataFrame(metadata_rows),
        config,
        output,
        pd,
    )
    assert status == "completed"
    assert notes == []
    results = pd.read_csv(output)
    g0 = results.loc[results["gene"] == "G0"].iloc[0]
    assert g0["log2FoldChange"] > 0


@pytest.mark.slow
def test_descriptive_synthetic_run_omits_inferential_outputs(tmp_path, kernel):
    ad = pytest.importorskip("anndata")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    pytest.importorskip("scanpy")
    pytest.importorskip("skmisc")
    source = ad.read_h5ad(_synthetic_h5ad(tmp_path))
    source.obs = pd.DataFrame(index=source.obs_names.copy())
    path = tmp_path / "descriptive.h5ad"
    source.write_h5ad(path)

    config = _descriptive_config(path)
    config["clustering"] = {
        "resolutions": [0.2, 0.5],
        "selected_resolution": 0.5,
        "n_neighbors": 8,
        "n_pcs": 12,
    }
    run_dir = tmp_path / "descriptive-run"
    for relative in kernel.INFERENTIAL_OUTPUTS:
        stale = run_dir / relative
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("stale comparative result", encoding="utf-8")

    result = kernel.run(config, run_dir)

    assert result["status"] == "completed", json.loads(
        (run_dir / "run_manifest.json").read_text(encoding="utf-8")
    )
    assert result["analysis_mode"] == "descriptive"
    assert result["statistics_status"] == {
        "de": "not_applicable_descriptive",
        "da": "not_applicable_descriptive",
    }
    analyzed = ad.read_h5ad(run_dir / "analysis.h5ad")
    assert set(analyzed.obs["sample_id"]) == {"pbmc3k"}
    assert "donor_id" not in analyzed.obs
    assert "condition" not in analyzed.obs
    raw = analyzed.layers["counts"]
    values = raw.toarray() if hasattr(raw, "toarray") else np.asarray(raw)
    assert (values >= 0).all()
    assert (values == values.astype(int)).all()
    assert all(
        not (run_dir / relative).exists() for relative in kernel.INFERENTIAL_OUTPUTS
    )


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


@pytest.mark.slow
def test_derived_checkpoints_share_the_matrix_payload(tmp_path, kernel):
    ad = pytest.importorskip("anndata")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    pytest.importorskip("scanpy")
    pytest.importorskip("skmisc")
    source = ad.read_h5ad(_synthetic_h5ad(tmp_path))
    source.obs = pd.DataFrame(index=source.obs_names.copy())
    path = tmp_path / "descriptive.h5ad"
    source.write_h5ad(path)
    config = _descriptive_config(path)
    config["clustering"] = {
        "resolutions": [0.5],
        "selected_resolution": 0.5,
        "n_neighbors": 8,
        "n_pcs": 12,
    }
    run_dir = tmp_path / "run"
    result = kernel.run(config, run_dir)
    assert result["status"] == "completed"

    def dense(value):
        return value.toarray() if hasattr(value, "toarray") else np.asarray(value)

    embedded = ad.read_h5ad(run_dir / "02_embedding.h5ad")
    for name in ("03_clustering.h5ad", "04_annotation.h5ad", "analysis.h5ad"):
        derived = ad.read_h5ad(run_dir / name)
        # Derived checkpoints rewrite obs/uns (and obsm for the final file)
        # in place; the matrix payload must stay byte-equal upstream data.
        assert np.array_equal(dense(derived.X), dense(embedded.X)), name
        assert np.array_equal(
            dense(derived.layers["counts"]), dense(embedded.layers["counts"])
        ), name
        assert derived.raw is not None, name
    clustered = ad.read_h5ad(run_dir / "03_clustering.h5ad")
    assert "cluster" in clustered.obs
    final = ad.read_h5ad(run_dir / "analysis.h5ad")
    assert final.uns["openai4s"]["analysis_mode"] == "descriptive"
    assert "X_umap" in final.obsm


@pytest.mark.slow
def test_resume_drops_warnings_from_recomputed_stages(tmp_path, kernel):
    ad = pytest.importorskip("anndata")
    pytest.importorskip("scanpy")
    pytest.importorskip("skmisc")
    pytest.importorskip("pydeseq2")
    full_path = _synthetic_h5ad(tmp_path)
    source = ad.read_h5ad(full_path)
    two_donor = source[source.obs["donor_id"].isin(["d1", "d2"])].copy()
    path = tmp_path / "input.h5ad"
    two_donor.write_h5ad(path)

    config = _base_config(path, mode="h5ad")
    config["clustering"] = {
        "resolutions": [0.5],
        "selected_resolution": 0.5,
        "n_neighbors": 8,
        "n_pcs": 12,
    }
    config["statistics"] = {"de": True, "da": False}
    run_dir = tmp_path / "run"
    first = kernel.run(config, run_dir)
    assert first["statistics_status"]["de"] == "skipped_insufficient_replicates"
    assert any("three independent donors" in w for w in first["warnings"])

    # Add the third donor pair: the input fingerprint changes, resume
    # recomputes from preflight, and the now-false replication warning must
    # not survive into the new report.
    source.write_h5ad(path)
    resumed = kernel.resume(run_dir)
    assert resumed["statistics_status"]["de"] == "completed"
    assert not any("three independent donors" in w for w in resumed["warnings"])
    manifest = json.loads(Path(resumed["manifest"]).read_text(encoding="utf-8"))
    assert not any(
        "three independent donors" in w
        for warnings in manifest["stage_warnings"].values()
        for w in warnings
    )


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
