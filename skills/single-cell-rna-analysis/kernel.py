"""Executable single-cell RNA workflow with lazy scientific imports.

The public surface is deliberately small: ``preflight(config)``,
``run(config, output_dir)`` and ``resume(run_dir)``.  Importing this module is
stdlib-only so the Skill can be discovered and compiled by the core runtime
without installing the optional single-cell stack.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import platform
import re
import shutil
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1
ANALYSIS_MODES = {"comparative", "descriptive"}
DEFAULT_RESOLUTIONS = [0.2, 0.4, 0.5, 0.6, 0.8, 1.0]
REQUIRED_SAMPLE_COLUMNS = {
    "sample_id",
    "donor_id",
    "condition",
    "matrix_path",
    "matrix_format",
}
SUPPORTED_MATRIX_FORMATS = {"10x_mtx", "10x_h5", "h5ad"}
STAGE_FILES = {
    "qc": "01_qc.h5ad",
    "embedding": "02_embedding.h5ad",
    "clustering": "03_clustering.h5ad",
    "annotation": "04_annotation.h5ad",
}
_PIPELINE_OBS_COLUMNS = frozenset(
    {
        "qc_fail",
        "qc_filter_reason",
        "doublet_score",
        "predicted_doublet",
        "cluster",
        "candidate_cell_type",
        "confirmed_cell_type",
    }
)
INFERENTIAL_OUTPUTS = frozenset(
    {
        "tables/pseudobulk_counts.csv",
        "tables/pseudobulk_metadata.csv",
        "tables/differential_expression.csv",
        "tables/differential_expression.error.txt",
        "tables/differential_abundance.csv",
        "tables/differential_abundance.error.txt",
        "figures/differential_expression.pdf",
        "figures/differential_abundance.pdf",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    # Atomic: resume() trusts these files, and a truncated half-write from an
    # interrupted run must never shadow a readable prior state.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


def _load_config(
    config: Mapping[str, Any] | str | os.PathLike[str],
) -> tuple[dict, Path]:
    if isinstance(config, Mapping):
        return json.loads(json.dumps(dict(config))), Path.cwd()
    path = Path(config).expanduser().resolve()
    return _read_json(path), path.parent


def _path(value: Any, base: Path) -> str:
    candidate = Path(str(value)).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return str(candidate.resolve())


def _resolved_config(
    config: Mapping[str, Any] | str | os.PathLike[str],
) -> dict[str, Any]:
    raw, base = _load_config(config)
    resolved = dict(raw)
    resolved.setdefault("schema_version", SCHEMA_VERSION)
    resolved.setdefault("analysis_mode", "comparative")
    resolved.setdefault("seed", 0)
    resolved.setdefault("qc", {})
    resolved.setdefault("clustering", {})
    resolved.setdefault("annotation", {})
    resolved.setdefault("statistics", {})
    resolved.setdefault("integration", {"method": "none", "batch_keys": []})

    resolved["qc"] = {
        "mad_counts": 5.0,
        "mad_genes": 5.0,
        "mad_mt": 3.0,
        "doublet_detection": True,
        "ambient_correction": "none",
        **dict(resolved.get("qc") or {}),
    }
    resolved["clustering"] = {
        "resolutions": list(DEFAULT_RESOLUTIONS),
        "selected_resolution": 0.5,
        "n_neighbors": 15,
        "n_pcs": 30,
        **dict(resolved.get("clustering") or {}),
    }
    resolved["annotation"] = {
        "marker_panel": None,
        "reference_h5ad": None,
        "reference_label_key": "cell_type",
        "confirmed_mapping": None,
        "minimum_margin": 0.1,
        **dict(resolved.get("annotation") or {}),
    }
    descriptive = resolved["analysis_mode"] == "descriptive"
    resolved["statistics"] = {
        "de": not descriptive,
        "da": not descriptive,
        **dict(resolved.get("statistics") or {}),
    }
    resolved["integration"] = {
        "method": "none",
        "batch_keys": [],
        **dict(resolved.get("integration") or {}),
    }
    resolved.setdefault("design", {})
    design_defaults: dict[str, Any] = {"sample_key": "sample_id"}
    if not descriptive:
        design_defaults.update(
            {
                "donor_key": "donor_id",
                "condition_key": "condition",
                "covariates": [],
                "paired": False,
            }
        )
    resolved["design"] = {
        **design_defaults,
        **dict(resolved.get("design") or {}),
    }

    input_config = dict(resolved.get("input") or {})
    if input_config.get("path"):
        input_config["path"] = _path(input_config["path"], base)
        if descriptive and input_config.get("mode") == "h5ad":
            input_config.setdefault("sample_id", Path(input_config["path"]).stem)
    input_config.setdefault("counts_layer", "counts")
    resolved["input"] = input_config

    annotation = resolved["annotation"]
    for key in ("marker_panel", "reference_h5ad", "confirmed_mapping"):
        if annotation.get(key):
            annotation[key] = _path(annotation[key], base)
    return resolved


def _config_errors(config: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if config.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    analysis_mode = config.get("analysis_mode")
    if analysis_mode not in ANALYSIS_MODES:
        errors.append("analysis_mode must be comparative or descriptive")
    if config.get("organism") not in {"human", "mouse"}:
        errors.append("organism must be human or mouse")
    if config.get("modality") not in {"scrna", "snrna"}:
        errors.append("modality must be scrna or snrna")

    input_config = config.get("input") or {}
    if input_config.get("mode") not in {"sample_sheet", "h5ad"}:
        errors.append("input.mode must be sample_sheet or h5ad")
    if not input_config.get("path"):
        errors.append("input.path is required")
    elif not Path(str(input_config["path"])).exists():
        errors.append(f"input path does not exist: {input_config['path']}")

    reference = config.get("reference") or {}
    if reference.get("gene_id_type") not in {
        "symbol",
        "ensembl",
        "ensembl_with_symbol",
    }:
        errors.append(
            "reference.gene_id_type must be symbol, ensembl, or ensembl_with_symbol"
        )
    for key in ("genome_build", "annotation_release"):
        if not reference.get(key):
            errors.append(f"reference.{key} is required")
    if reference.get("gene_id_type") == "ensembl" and not reference.get(
        "gene_symbol_column"
    ):
        warnings.append(
            "Ensembl-only genes have no gene_symbol_column; mitochondrial and "
            "ribosomal QC may be unavailable."
        )

    design = config.get("design") or {}
    if analysis_mode == "descriptive":
        if input_config.get("mode") != "h5ad":
            errors.append("descriptive analysis currently requires input.mode=h5ad")
        if not str(input_config.get("sample_id", "")).strip():
            errors.append("input.sample_id is required for descriptive analysis")
        if any(design.get(key) not in (None, "") for key in ("tested", "reference")):
            errors.append(
                "descriptive analysis does not accept a tested-versus-reference contrast"
            )
    else:
        for key in ("tested", "reference", "condition_key", "donor_key"):
            if design.get(key) in (None, ""):
                errors.append(f"design.{key} is required")
        if (
            design.get("tested") == design.get("reference")
            and design.get("tested") is not None
        ):
            errors.append("design.tested and design.reference must differ")
        if not isinstance(design.get("covariates", []), list):
            errors.append("design.covariates must be a list")
        if design.get("paired") and design.get("covariates"):
            errors.append(
                "paired designs fit ~ donor + condition; covariates are not "
                "supported with design.paired=true"
            )
        # Formula and contrast strings are built by interpolation: a column
        # name like `donor-id` parses as subtraction in formulaic, and a
        # condition level containing `-` makes pertpy's Milo contrast string
        # ambiguous. Reject at preflight instead of failing (or silently
        # mis-specifying the model) inside DESeq2/Milo.
        statistics_config = config.get("statistics") or {}
        wants_de = bool(statistics_config.get("de", True))
        wants_da = bool(statistics_config.get("da", True))
        if wants_de or wants_da:
            covariates = design.get("covariates", [])
            formula_keys = [
                ("design.donor_key", design.get("donor_key")),
                ("design.condition_key", design.get("condition_key")),
                *(
                    [
                        (f"design.covariates[{index}]", value)
                        for index, value in enumerate(covariates)
                    ]
                    if isinstance(covariates, list)
                    else []
                ),
            ]
            for label, value in formula_keys:
                if value and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(value)):
                    errors.append(
                        f"{label} {str(value)!r} is not formula-safe; rename "
                        "the metadata column to letters, digits, and "
                        "underscores (not starting with a digit)"
                    )
        if wants_da:
            for label, value in (
                ("design.tested", design.get("tested")),
                ("design.reference", design.get("reference")),
            ):
                if value and not re.fullmatch(r"[A-Za-z0-9_.]+", str(value)):
                    errors.append(
                        f"{label} {str(value)!r} cannot be expressed in Milo's "
                        "contrast string; rename the level to letters, digits, "
                        "underscores, and dots, or disable statistics.da"
                    )

    integration = config.get("integration") or {}
    if integration.get("method") not in {"none", "harmony"}:
        errors.append("integration.method must be none or harmony")
    batch_keys = integration.get("batch_keys", [])
    if not isinstance(batch_keys, list):
        errors.append("integration.batch_keys must be a list")
    elif integration.get("method") == "harmony" and not batch_keys:
        errors.append("Harmony requires explicit integration.batch_keys")
    if analysis_mode == "descriptive" and integration.get("method") != "none":
        errors.append("descriptive analysis does not support integration")

    statistics = config.get("statistics") or {}
    if analysis_mode == "descriptive" and any(
        statistics.get(key) for key in ("de", "da")
    ):
        errors.append("descriptive analysis requires statistics.de=false and da=false")
    # Consumers use bare truthiness, and JSON strings like "false" are truthy:
    # a value that is not a real boolean would silently invert behaviour.
    for section, key in (
        ("design", "paired"),
        ("statistics", "de"),
        ("statistics", "da"),
        ("qc", "doublet_detection"),
    ):
        value = (config.get(section) or {}).get(key)
        if value is not None and not isinstance(value, bool):
            errors.append(f"{section}.{key} must be a JSON boolean")

    clustering = config.get("clustering") or {}
    resolutions = clustering.get("resolutions", [])
    if not isinstance(resolutions, list) or not resolutions:
        errors.append("clustering.resolutions must be a nonempty list")
    else:
        try:
            resolution_values = [float(value) for value in resolutions]
            if any(value <= 0 for value in resolution_values):
                errors.append("clustering resolutions must be positive")
            selected = float(clustering.get("selected_resolution"))
            if not any(math.isclose(selected, value) for value in resolution_values):
                errors.append("selected_resolution must occur in resolutions")
        except (TypeError, ValueError):
            errors.append("clustering resolutions must be numeric")

    if (config.get("qc") or {}).get("ambient_correction", "none") == "none":
        warnings.append(
            "No upstream ambient-RNA correction was declared; SoupX/CellBender "
            "are outside this workflow and ambient contamination remains a limitation."
        )
    for key in ("marker_panel", "reference_h5ad", "confirmed_mapping"):
        value = (config.get("annotation") or {}).get(key)
        if value and not Path(str(value)).is_file():
            errors.append(f"annotation.{key} does not exist: {value}")
    return errors, warnings


def _read_sample_sheet(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_SAMPLE_COLUMNS - columns)
        if missing:
            raise ValueError(f"sample sheet missing columns: {', '.join(missing)}")
        rows = []
        for row in reader:
            # DictReader fills short rows with None and str(None) is the
            # truthy string "None", which would pass every nonempty gate.
            if None in row or any(value is None for value in row.values()):
                raise ValueError(
                    f"sample sheet row {reader.line_num} has missing or " "extra fields"
                )
            rows.append({str(k): str(v).strip() for k, v in row.items()})
    if not rows:
        raise ValueError("sample sheet contains no samples")
    return rows


def _sample_sheet_errors(
    config: Mapping[str, Any],
) -> tuple[list[str], list[str], list[dict]]:
    errors: list[str] = []
    warnings: list[str] = []
    rows: list[dict] = []
    path = Path(config["input"]["path"])
    try:
        rows = _read_sample_sheet(path)
    except (OSError, ValueError) as exc:
        return [str(exc)], warnings, []

    ids = [row["sample_id"] for row in rows]
    if any(not value for value in ids):
        errors.append("sample_id values must be nonempty")
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        errors.append(f"duplicate sample_id values: {', '.join(duplicates)}")
    for row in rows:
        if not row["donor_id"] or not row["condition"]:
            errors.append(f"sample {row['sample_id']} has empty donor_id or condition")
        if row["matrix_format"] not in SUPPORTED_MATRIX_FORMATS:
            errors.append(
                f"sample {row['sample_id']} has unsupported matrix_format "
                f"{row['matrix_format']!r}"
            )
        matrix = Path(row["matrix_path"]).expanduser()
        if not matrix.is_absolute():
            matrix = path.parent / matrix
        row["matrix_path"] = str(matrix.resolve())
        if not matrix.exists():
            errors.append(f"sample {row['sample_id']} matrix does not exist: {matrix}")
        reference = config.get("reference") or {}
        for key in ("organism", "genome_build", "annotation_release", "gene_id_type"):
            if row.get(key) and str(row[key]) != str(
                config.get(key) if key == "organism" else reference.get(key)
            ):
                errors.append(
                    f"sample {row['sample_id']} disagrees on reference field {key}"
                )
    levels = {row["condition"] for row in rows}
    design = config["design"]
    for value in (str(design["tested"]), str(design["reference"])):
        if value not in levels:
            errors.append(f"contrast level absent from sample sheet: {value}")
    needed = {
        str(design.get("sample_key", "sample_id")),
        str(design["donor_key"]),
        str(design["condition_key"]),
        *[str(value) for value in design.get("covariates", [])],
        *[str(value) for value in config["integration"].get("batch_keys", [])],
    }
    columns = set(rows[0])
    missing = sorted(needed - columns)
    if missing:
        errors.append(
            f"sample sheet lacks configured metadata columns: {', '.join(missing)}"
        )
    design_errors, design_warnings, _ = _design_checks(rows, config)
    errors.extend(design_errors)
    warnings.extend(design_warnings)
    return errors, warnings, rows


def _confounding_errors(
    records: list[Mapping[str, Any]], config: Mapping[str, Any]
) -> list[str]:
    if config["integration"].get("method") != "harmony":
        return []
    condition = str(config["design"]["condition_key"])
    errors: list[str] = []
    for key in config["integration"].get("batch_keys", []):
        by_batch: dict[str, set[str]] = defaultdict(set)
        by_condition: dict[str, set[str]] = defaultdict(set)
        for row in records:
            by_batch[str(row[key])].add(str(row[condition]))
            by_condition[str(row[condition])].add(str(row[key]))
        if all(len(values) == 1 for values in by_batch.values()) and all(
            len(values) == 1 for values in by_condition.values()
        ):
            errors.append(
                f"Harmony batch key {key!r} is fully confounded with {condition!r}"
            )
    return errors


def _matrix_rank(rows: list[list[float]], tolerance: float = 1e-10) -> int:
    """Return matrix rank with small, dependency-free Gaussian elimination."""
    matrix = [list(map(float, row)) for row in rows]
    if not matrix:
        return 0
    n_rows = len(matrix)
    n_cols = len(matrix[0])
    rank = 0
    for column in range(n_cols):
        pivot = max(range(rank, n_rows), key=lambda row: abs(matrix[row][column]))
        if abs(matrix[pivot][column]) <= tolerance:
            continue
        matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
        pivot_value = matrix[rank][column]
        matrix[rank] = [value / pivot_value for value in matrix[rank]]
        for row in range(n_rows):
            if row == rank:
                continue
            factor = matrix[row][column]
            if abs(factor) > tolerance:
                matrix[row] = [
                    value - factor * pivot_part
                    for value, pivot_part in zip(matrix[row], matrix[rank])
                ]
        rank += 1
        if rank == n_rows:
            break
    return rank


def _design_checks(
    records: list[Mapping[str, Any]], config: Mapping[str, Any]
) -> tuple[list[str], list[str], dict[str, int]]:
    errors: list[str] = []
    warnings: list[str] = []
    design = config["design"]
    sample_key = str(design.get("sample_key", "sample_id"))
    donor_key = str(design["donor_key"])
    condition_key = str(design["condition_key"])
    covariates = [str(value) for value in design.get("covariates", [])]
    required = {
        sample_key,
        donor_key,
        condition_key,
        *covariates,
        *[str(value) for value in config["integration"].get("batch_keys", [])],
    }
    missing = sorted(key for key in required if any(key not in row for row in records))
    if missing:
        return (
            [f"metadata lacks configured columns: {', '.join(missing)}"],
            warnings,
            {},
        )

    # Collapse cell-level metadata to one record per sample and refuse samples
    # whose design fields vary from cell to cell.
    by_sample: dict[str, dict[str, str]] = {}
    for record in records:
        sample = str(record[sample_key])
        if not sample:
            errors.append("sample identifiers must be nonempty")
            continue
        values = {key: str(record[key]) for key in required}
        prior = by_sample.get(sample)
        if prior is not None and prior != values:
            errors.append(
                f"sample {sample!r} has inconsistent design metadata across cells"
            )
        by_sample[sample] = values
    samples = list(by_sample.values())
    tested = str(design["tested"])
    reference = str(design["reference"])
    selected = [row for row in samples if row[condition_key] in {tested, reference}]
    levels = {row[condition_key] for row in selected}
    for level in (reference, tested):
        if level not in levels:
            errors.append(f"contrast level absent from metadata: {level}")
    donor_counts = {
        level: len({row[donor_key] for row in selected if row[condition_key] == level})
        for level in (reference, tested)
    }
    if donor_counts and any(value < 3 for value in donor_counts.values()):
        warnings.append(
            "Fewer than three independent donors occur in at least one contrast "
            "level; DE and DA will be skipped."
        )
    if design.get("paired"):
        donor_levels: dict[str, set[str]] = defaultdict(set)
        for row in selected:
            donor_levels[row[donor_key]].add(row[condition_key])
        incomplete = sorted(
            donor
            for donor, donor_level in donor_levels.items()
            if donor_level != {reference, tested}
        )
        if incomplete:
            errors.append(
                "paired design has donors missing a contrast level: "
                + ", ".join(incomplete[:10])
            )

    # Construct the same fixed-effect design shape used downstream. Categorical
    # factors are treatment coded (first sorted level is the reference column).
    factor_keys = ([donor_key] if design.get("paired") else covariates) + [
        condition_key
    ]
    columns: list[tuple[str, str]] = []
    for key in factor_keys:
        factor_levels = sorted({row[key] for row in selected})
        columns.extend((key, level) for level in factor_levels[1:])
    matrix = [
        [1.0, *[1.0 if row[key] == level else 0.0 for key, level in columns]]
        for row in selected
    ]
    expected_rank = 1 + len(columns)
    if matrix and _matrix_rank(matrix) < expected_rank:
        errors.append("the declared statistical design matrix is rank deficient")
    errors.extend(_confounding_errors(selected, config))
    return errors, warnings, donor_counts


def _inspect_h5ad(
    path: Path,
    counts_layer: str,
    config: Mapping[str, Any],
    inspect_metadata: bool = True,
) -> tuple[list[str], list[str], int | None]:
    errors: list[str] = []
    warnings: list[str] = []
    sample_count: int | None = None
    try:
        import anndata as ad
        import numpy as np
    except ImportError:
        warnings.append(
            f"Raw-count and cell-metadata inspection deferred until run because anndata is unavailable: {path}"
        )
        return errors, warnings, sample_count
    try:
        adata = ad.read_h5ad(path)
        if not adata.obs_names.is_unique:
            errors.append(f"h5ad cell IDs are not unique: {path}")
        try:
            counts = _extract_counts(adata, counts_layer)
        except ValueError as exc:
            errors.append(f"{path}: {exc}")
        else:
            ok, reason = _is_raw_counts(counts, np)
            if not ok:
                errors.append(f"{path}: {reason}")
        if inspect_metadata:
            design = config["design"]
            sample_key = str(design.get("sample_key", "sample_id"))
            if config["analysis_mode"] == "descriptive":
                leftovers = sorted(
                    column
                    for column in ("donor_id", "condition")
                    if column in adata.obs.columns
                )
                if leftovers:
                    errors.append(
                        f"{path}: descriptive analysis input must not carry "
                        "donor/condition metadata columns ("
                        + ", ".join(leftovers)
                        + "); remove them or run a comparative analysis"
                    )
                if sample_key in adata.obs.columns:
                    if bool(adata.obs[sample_key].isna().any()):
                        errors.append(f"{path}: sample identifiers must be nonempty")
                    sample_values = {
                        str(value).strip() for value in adata.obs[sample_key]
                    }
                    if "" in sample_values:
                        errors.append(f"{path}: sample identifiers must be nonempty")
                    if len(sample_values - {""}) != 1:
                        errors.append(
                            f"{path}: descriptive analysis requires exactly one sample"
                        )
            else:
                metadata_keys = {
                    sample_key,
                    str(design["donor_key"]),
                    str(design["condition_key"]),
                    *[str(value) for value in design.get("covariates", [])],
                    *[
                        str(value)
                        for value in config["integration"].get("batch_keys", [])
                    ],
                }
                missing = sorted(metadata_keys - set(adata.obs.columns))
                if missing:
                    errors.append(
                        f"{path}: h5ad obs lacks configured metadata: {', '.join(missing)}"
                    )
                else:
                    if bool(adata.obs[list(metadata_keys)].isna().any().any()):
                        errors.append(
                            f"{path}: configured metadata columns contain "
                            "missing values"
                        )
                    sample_count = int(adata.obs[sample_key].astype(str).nunique())
                    records = (
                        adata.obs[list(metadata_keys)].astype(str).to_dict("records")
                    )
                    design_errors, design_warnings, _ = _design_checks(records, config)
                    errors.extend(f"{path}: {value}" for value in design_errors)
                    warnings.extend(f"{path}: {value}" for value in design_warnings)
    except (OSError, ValueError) as exc:
        errors.append(f"cannot inspect h5ad {path}: {exc}")
    return errors, warnings, sample_count


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_path(path: Path) -> str:
    if path.is_file():
        return _sha256_file(path)
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(str(child.relative_to(path)).encode("utf-8"))
        digest.update(_sha256_file(child).encode("ascii"))
    return digest.hexdigest()


def _input_fingerprint(config: Mapping[str, Any]) -> tuple[str, list[dict[str, str]]]:
    paths = [Path(config["input"]["path"])]
    if config["input"]["mode"] == "sample_sheet":
        for row in _read_sample_sheet(paths[0]):
            matrix = Path(row["matrix_path"]).expanduser()
            if not matrix.is_absolute():
                matrix = paths[0].parent / matrix
            paths.append(matrix.resolve())
    summary = [{"path": str(path), "sha256": _sha256_path(path)} for path in paths]
    encoded = json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), summary


def _config_sha(config: Mapping[str, Any]) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stage_hashes(
    config: Mapping[str, Any], input_fingerprint: str | None
) -> dict[str, str]:
    """Build chained hashes so resume can identify the earliest stale stage."""

    def digest(value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    hashes: dict[str, str] = {}
    hashes["preflight"] = digest(
        {
            "input_fingerprint": input_fingerprint,
            "analysis_mode": config.get("analysis_mode"),
            "input": config.get("input"),
            "organism": config.get("organism"),
            "modality": config.get("modality"),
            "reference": config.get("reference"),
        }
    )
    # Every metadata key that _load_data bakes into the checkpoint's obs must
    # invalidate the qc stage: a downstream-only hash would resume from a
    # checkpoint that lacks the newly configured column.
    design = config.get("design") or {}
    integration = config.get("integration") or {}
    metadata_keys = {str(design.get("sample_key", "sample_id"))}
    if config.get("analysis_mode") != "descriptive":
        metadata_keys.update(
            {
                str(design.get("donor_key")),
                str(design.get("condition_key")),
                *[str(value) for value in design.get("covariates", []) or []],
                *[str(value) for value in integration.get("batch_keys", []) or []],
            }
        )
    hashes["qc"] = digest(
        {
            "upstream": hashes["preflight"],
            "qc": config.get("qc"),
            "analysis_mode": config.get("analysis_mode"),
            "metadata_keys": sorted(metadata_keys),
            "seed": config.get("seed"),
        }
    )
    hashes["embedding"] = digest(
        {
            "upstream": hashes["qc"],
            "integration": config.get("integration"),
            "n_neighbors": (config.get("clustering") or {}).get("n_neighbors"),
            "n_pcs": (config.get("clustering") or {}).get("n_pcs"),
            "seed": config.get("seed"),
        }
    )
    hashes["clustering"] = digest(
        {
            "upstream": hashes["embedding"],
            "resolutions": (config.get("clustering") or {}).get("resolutions"),
            "selected_resolution": (config.get("clustering") or {}).get(
                "selected_resolution"
            ),
            "seed": config.get("seed"),
        }
    )
    # The annotation side inputs are files the config names by path only, so
    # their contents must be fingerprinted here or an in-place edit followed
    # by resume() would silently return the stale prior labels.
    side_inputs: dict[str, str | None] = {}
    for key in ("marker_panel", "reference_h5ad", "confirmed_mapping"):
        value = (config.get("annotation") or {}).get(key)
        if value:
            candidate = Path(str(value))
            side_inputs[key] = _sha256_path(candidate) if candidate.exists() else None
    hashes["annotation"] = digest(
        {
            "upstream": hashes["clustering"],
            "annotation": config.get("annotation"),
            "side_input_sha256": side_inputs,
        }
    )
    hashes["statistics"] = digest(
        {
            "upstream": hashes["annotation"],
            "design": config.get("design"),
            "statistics": config.get("statistics"),
        }
    )
    return hashes


def preflight(config: Mapping[str, Any] | str | os.PathLike[str]) -> dict[str, Any]:
    """Resolve and validate configuration without importing scientific packages."""
    try:
        resolved = _resolved_config(config)
        errors, warnings = _config_errors(resolved)
        samples: list[dict] = []
        inspected_count: int | None = None
        if not errors and resolved["input"]["mode"] == "sample_sheet":
            sample_errors, sample_warnings, samples = _sample_sheet_errors(resolved)
            errors.extend(sample_errors)
            warnings.extend(sample_warnings)
            if not errors:
                for row in samples:
                    if row["matrix_format"] == "h5ad":
                        inspect_errors, inspect_warnings, _ = _inspect_h5ad(
                            Path(row["matrix_path"]),
                            resolved["input"]["counts_layer"],
                            resolved,
                            inspect_metadata=False,
                        )
                        errors.extend(inspect_errors)
                        warnings.extend(inspect_warnings)
        elif not errors and resolved["input"]["mode"] == "h5ad":
            inspect_errors, inspect_warnings, inspected_count = _inspect_h5ad(
                Path(resolved["input"]["path"]),
                resolved["input"]["counts_layer"],
                resolved,
            )
            errors.extend(inspect_errors)
            warnings.extend(inspect_warnings)
        fingerprint = None
        inputs: list[dict[str, str]] = []
        if not errors:
            fingerprint, inputs = _input_fingerprint(resolved)
        return {
            "status": "valid" if not errors else "invalid",
            "errors": errors,
            "warnings": warnings,
            "resolved_config": resolved,
            "input_fingerprint": fingerprint,
            "inputs": inputs,
            "sample_count": (
                len(samples)
                if samples
                else (
                    (
                        1
                        if resolved["analysis_mode"] == "descriptive"
                        else inspected_count
                    )
                    if not errors and resolved["input"]["mode"] == "h5ad"
                    else None
                )
            ),
        }
    except Exception as exc:  # public validation must return a structured failure
        return {
            "status": "invalid",
            "errors": [str(exc)],
            "warnings": [],
            "resolved_config": None,
            "input_fingerprint": None,
            "inputs": [],
            "sample_count": None,
        }


def _science_imports() -> tuple[Any, Any, Any, Any]:
    try:
        import anndata as ad
        import numpy as np
        import pandas as pd
        import scanpy as sc
    except ImportError as exc:
        raise RuntimeError(
            "The single-cell stack is not installed. Install the OpenAI4S "
            "singlecell extra (uv sync --extra singlecell) or use envs/python.yml."
        ) from exc
    return ad, np, pd, sc


def _is_raw_counts(matrix: Any, np: Any) -> tuple[bool, str | None]:
    values = (
        matrix.data
        if hasattr(matrix, "data") and hasattr(matrix, "tocsr")
        else np.asarray(matrix)
    )
    values = np.asarray(values)
    if values.size == 0:
        return False, "count matrix is empty"
    if not np.all(np.isfinite(values)):
        return False, "counts contain non-finite values"
    if np.any(values < 0):
        return False, "counts contain negative values"
    # rtol must be zero: numpy's default 1e-5 would auto-accept any value
    # above ~5e4 as "integer" regardless of its fractional part.
    if not np.allclose(values, np.rint(values), rtol=0, atol=1e-8):
        return False, "counts are not integer-valued raw counts"
    return True, None


def _extract_counts(adata: Any, counts_layer: str) -> Any:
    if counts_layer == "X":
        return adata.X
    if counts_layer not in adata.layers:
        raise ValueError(f"declared counts layer {counts_layer!r} is missing")
    return adata.layers[counts_layer]


def _read_one(path: str, matrix_format: str, counts_layer: str, sc: Any) -> Any:
    if matrix_format == "10x_mtx":
        # Stock CellRanger references carry duplicated gene symbols, so the
        # symbols must be de-duplicated on read or every real matrix would
        # trip the duplicate-gene rejection downstream.
        adata = sc.read_10x_mtx(path, var_names="gene_symbols", make_unique=True)
    elif matrix_format == "10x_h5":
        # Same reason as 10x_mtx: read_10x_h5 names genes by symbol and has
        # no make_unique parameter, so de-duplicate after reading.
        adata = sc.read_10x_h5(path)
        adata.var_names_make_unique()
    elif matrix_format == "h5ad":
        adata = sc.read_h5ad(path)
        adata.X = _extract_counts(adata, counts_layer).copy()
    else:
        raise ValueError(f"unsupported matrix format: {matrix_format}")
    return adata


def _load_data(
    config: Mapping[str, Any], np: Any, pd: Any, sc: Any, ad: Any
) -> tuple[Any, list[str]]:
    warnings: list[str] = []
    input_config = config["input"]
    design = config["design"]
    descriptive = config["analysis_mode"] == "descriptive"
    sample_key = str(design.get("sample_key", "sample_id"))
    metadata_keys = {sample_key}
    if not descriptive:
        metadata_keys.update(
            {
                str(design["donor_key"]),
                str(design["condition_key"]),
                *[str(key) for key in design.get("covariates", [])],
                *[str(key) for key in config["integration"].get("batch_keys", [])],
            }
        )
    if input_config["mode"] == "h5ad":
        adata = _read_one(
            input_config["path"], "h5ad", input_config["counts_layer"], sc
        )
        if descriptive and sample_key not in adata.obs.columns:
            adata.obs[sample_key] = str(input_config["sample_id"]).strip()
        missing = sorted(metadata_keys - set(adata.obs.columns))
        if missing:
            raise ValueError(
                f"h5ad obs lacks configured metadata: {', '.join(missing)}"
            )
        if descriptive:
            # The documented descriptive boundary is an object without design
            # metadata; leftover donor/condition columns riding into outputs
            # would imply inference this mode must never support.
            leftovers = sorted(
                column
                for column in ("donor_id", "condition")
                if column in adata.obs.columns
            )
            if leftovers:
                raise ValueError(
                    "descriptive analysis input must not carry donor/condition "
                    "metadata columns (" + ", ".join(leftovers) + "); remove "
                    "them or run a comparative analysis"
                )
            if bool(adata.obs[sample_key].isna().any()):
                raise ValueError("sample identifiers must be nonempty")
            sample_values = {str(value).strip() for value in adata.obs[sample_key]}
            if "" in sample_values:
                raise ValueError("sample identifiers must be nonempty")
            if len(sample_values - {""}) != 1:
                raise ValueError("descriptive analysis requires exactly one sample")
            adata.obs[sample_key] = [
                str(value).strip() for value in adata.obs[sample_key]
            ]
        ok, reason = _is_raw_counts(adata.X, np)
        if not ok:
            raise ValueError(reason)
        if not adata.obs_names.is_unique:
            raise ValueError("h5ad cell IDs are not unique")
        if not adata.var_names.is_unique:
            duplicates = (
                adata.var_names[adata.var_names.duplicated()].unique().tolist()[:5]
            )
            raise ValueError(f"h5ad has duplicate gene IDs: {duplicates}")
        if not descriptive:
            metadata_frame = adata.obs[sorted(metadata_keys)]
            # astype(str) would turn NaN into the truthy level "nan", which
            # passes every nonempty gate and inflates donor counts.
            if bool(metadata_frame.isna().any().any()):
                raise ValueError("configured metadata columns contain missing values")
            records = metadata_frame.astype(str).to_dict("records")
            confounding = _confounding_errors(records, config)
            if confounding:
                raise ValueError("; ".join(confounding))
    else:
        sheet_path = Path(input_config["path"])
        rows = _read_sample_sheet(sheet_path)
        adatas = []
        common_genes: set[str] | None = None
        for row in rows:
            matrix_path = Path(row["matrix_path"]).expanduser()
            if not matrix_path.is_absolute():
                matrix_path = sheet_path.parent / matrix_path
            adata = _read_one(
                str(matrix_path.resolve()),
                row["matrix_format"],
                input_config["counts_layer"],
                sc,
            )
            ok, reason = _is_raw_counts(adata.X, np)
            if not ok:
                raise ValueError(f"sample {row['sample_id']}: {reason}")
            if not adata.var_names.is_unique:
                duplicates = (
                    adata.var_names[adata.var_names.duplicated()].unique().tolist()[:5]
                )
                raise ValueError(
                    f"sample {row['sample_id']} has duplicate gene IDs: {duplicates}"
                )
            genes = set(map(str, adata.var_names))
            if common_genes is None:
                common_genes = genes
            else:
                common_genes &= genes
            for key in metadata_keys:
                adata.obs[key] = str(row[key])
            original = adata.obs_names.astype(str)
            adata.obs_names = [f"{row['sample_id']}:{cell}" for cell in original]
            if not adata.obs_names.is_unique:
                raise ValueError(f"sample {row['sample_id']} has duplicate cell IDs")
            adatas.append(adata)
        if not common_genes:
            raise ValueError("samples have no common genes")
        adata = ad.concat(adatas, join="inner", merge="same", index_unique=None)
        if adata.n_vars < max(20, min(item.n_vars for item in adatas) // 2):
            warnings.append(
                "Cross-sample gene intersection removed more than half of one gene space."
            )

    ok, reason = _is_raw_counts(adata.X, np)
    if not ok:
        raise ValueError(reason)
    # Input obs columns that collide with pipeline outputs must not survive:
    # a foreign confirmed_cell_type would become the DE grouping and foreign
    # leiden_* columns would be exported as this run's assignments.
    reserved = [
        column
        for column in adata.obs.columns
        if column not in metadata_keys
        and (column in _PIPELINE_OBS_COLUMNS or str(column).startswith("leiden_"))
    ]
    if reserved:
        adata.obs = adata.obs.drop(columns=reserved)
        warnings.append(
            "Dropped input metadata columns reserved for pipeline outputs: "
            + ", ".join(sorted(str(column) for column in reserved))
        )
    adata.layers["counts"] = adata.X.copy()
    adata.obs_names_make_unique(join="-")
    return adata, warnings


def _gene_names(adata: Any, config: Mapping[str, Any]) -> list[str]:
    symbol_column = (config.get("reference") or {}).get("gene_symbol_column")
    if symbol_column and symbol_column in adata.var:
        return [str(value) for value in adata.var[symbol_column]]
    return [str(value) for value in adata.var_names]


def _mad_flags(values: Any, side: str, threshold: float, np: Any) -> Any:
    values = np.asarray(values, dtype=float)
    median = float(np.nanmedian(values))
    mad = float(np.nanmedian(np.abs(values - median)))
    if not np.isfinite(mad) or mad == 0:
        return np.zeros(values.shape, dtype=bool)
    scaled = np.abs(values - median) / (1.4826 * mad)
    if side == "low":
        return (values < median) & (scaled > threshold)
    if side == "high":
        return (values > median) & (scaled > threshold)
    return scaled > threshold


def _run_qc(
    adata: Any, config: Mapping[str, Any], np: Any, pd: Any, sc: Any
) -> tuple[Any, Any, Any, list[str]]:
    warnings: list[str] = []
    qc = config["qc"]
    sample_key = config["design"].get("sample_key", "sample_id")
    names = _gene_names(adata, config)
    if config["organism"] == "human":
        mt = [name.startswith("MT-") for name in names]
        ribo = [name.startswith(("RPS", "RPL")) for name in names]
    else:
        mt = [name.startswith("mt-") or name.startswith("Mt-") for name in names]
        ribo = [name.startswith(("Rps", "Rpl")) for name in names]
    adata.var["mt"] = mt
    adata.var["ribo"] = ribo
    if not any(mt):
        warnings.append("No mitochondrial genes matched the declared gene namespace.")
    if not any(ribo):
        warnings.append("No ribosomal genes matched the declared gene namespace.")
    # Scanpy's default ``percent_top=(50, 100, 200, 500)`` rejects small,
    # deterministic fixtures and genuinely targeted panels with fewer genes.
    # These top-N metrics are not part of this workflow's QC contract.
    sc.pp.calculate_qc_metrics(
        adata,
        qc_vars=["mt", "ribo"],
        percent_top=None,
        inplace=True,
        log1p=False,
    )
    adata.obs["qc_fail"] = False
    adata.obs["qc_filter_reason"] = ""
    adata.obs["doublet_score"] = np.nan
    adata.obs["predicted_doublet"] = False
    summary_rows: list[dict[str, Any]] = []

    for sample in sorted(adata.obs[sample_key].astype(str).unique()):
        mask = adata.obs[sample_key].astype(str) == sample
        obs = adata.obs.loc[mask]
        scrublet_threshold = None
        reasons: dict[str, Any] = {}
        reasons["low_counts_mad"] = _mad_flags(
            np.log1p(obs["total_counts"]), "low", float(qc["mad_counts"]), np
        )
        reasons["high_counts_mad"] = _mad_flags(
            np.log1p(obs["total_counts"]), "high", float(qc["mad_counts"]), np
        )
        reasons["low_genes_mad"] = _mad_flags(
            np.log1p(obs["n_genes_by_counts"]), "low", float(qc["mad_genes"]), np
        )
        reasons["high_genes_mad"] = _mad_flags(
            np.log1p(obs["n_genes_by_counts"]), "high", float(qc["mad_genes"]), np
        )
        reasons["high_mt_mad"] = _mad_flags(
            obs["pct_counts_mt"], "high", float(qc["mad_mt"]), np
        )
        if qc.get("min_counts") is not None:
            reasons["below_min_counts"] = obs["total_counts"].to_numpy() < float(
                qc["min_counts"]
            )
        if qc.get("min_genes") is not None:
            reasons["below_min_genes"] = obs["n_genes_by_counts"].to_numpy() < float(
                qc["min_genes"]
            )
        if qc.get("max_mt_pct") is not None:
            reasons["above_max_mt_pct"] = obs["pct_counts_mt"].to_numpy() > float(
                qc["max_mt_pct"]
            )

        reason_lists: list[list[str]] = [[] for _ in range(len(obs))]
        for name, flags in reasons.items():
            for index, flagged in enumerate(flags):
                if bool(flagged):
                    reason_lists[index].append(name)
        adata.obs.loc[mask, "qc_filter_reason"] = [
            ";".join(items) for items in reason_lists
        ]
        adata.obs.loc[mask, "qc_fail"] = [bool(items) for items in reason_lists]

        if qc.get("doublet_detection", True) and int(mask.sum()) >= 50:
            subset = adata[mask].copy()
            try:
                sc.pp.scrublet(subset, random_state=int(config["seed"]))
                scrublet_threshold = (subset.uns.get("scrublet") or {}).get("threshold")
                adata.obs.loc[mask, "doublet_score"] = subset.obs[
                    "doublet_score"
                ].to_numpy()
                predicted = subset.obs["predicted_doublet"].astype(bool).to_numpy()
                adata.obs.loc[mask, "predicted_doublet"] = predicted
                for index, flagged in zip(adata.obs.index[mask], predicted):
                    if flagged:
                        prior = str(adata.obs.at[index, "qc_filter_reason"])
                        adata.obs.at[index, "qc_filter_reason"] = ";".join(
                            value for value in (prior, "scrublet_doublet") if value
                        )
                        adata.obs.at[index, "qc_fail"] = True
            except Exception as exc:
                warnings.append(f"Scrublet skipped for sample {sample}: {exc}")
        elif qc.get("doublet_detection", True):
            warnings.append(
                f"Scrublet skipped for sample {sample}: fewer than 50 cells"
            )

        final_mask = adata.obs.loc[mask, "qc_fail"].astype(bool)
        summary_rows.append(
            {
                "sample_id": sample,
                "cells_before": int(mask.sum()),
                "cells_removed": int(final_mask.sum()),
                "cells_after": int(mask.sum() - final_mask.sum()),
                "median_counts": float(obs["total_counts"].median()),
                "median_genes": float(obs["n_genes_by_counts"].median()),
                "median_pct_mt": float(obs["pct_counts_mt"].median()),
                "scrublet_threshold": scrublet_threshold,
            }
        )
    keep = ~adata.obs["qc_fail"].astype(bool).to_numpy()
    if not bool(np.any(keep)):
        raise ValueError("QC removed every cell")
    qc_columns = [
        sample_key,
        "total_counts",
        "n_genes_by_counts",
        "pct_counts_mt",
        "pct_counts_ribo",
        "doublet_score",
        "predicted_doublet",
        "qc_fail",
        "qc_filter_reason",
    ]
    cell_qc = adata.obs[qc_columns].copy()
    cell_qc.insert(0, "cell_id", adata.obs_names.astype(str))
    return adata[keep].copy(), pd.DataFrame(summary_rows), cell_qc, warnings


def _resolution_key(value: float) -> str:
    return f"leiden_{float(value):g}"


def _run_embedding(adata: Any, config: Mapping[str, Any], sc: Any) -> Any:
    seed = int(config["seed"])
    clustering = config["clustering"]
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)
    adata.raw = adata.copy()
    n_top = min(2000, max(10, adata.n_vars - 1))
    try:
        sc.pp.highly_variable_genes(
            adata,
            layer="counts",
            flavor="seurat_v3",
            n_top_genes=n_top,
            batch_key=config["design"].get("sample_key", "sample_id"),
        )
        adata.uns["openai4s_hvg_method"] = "seurat_v3_counts"
    except ValueError:
        # LOESS can be singular for small targeted panels or deterministic test
        # matrices. Fall back to the log-normalized Seurat dispersion method;
        # the raw count layer remains untouched and is still the only DE input.
        sc.pp.highly_variable_genes(
            adata,
            flavor="seurat",
            n_top_genes=n_top,
            batch_key=config["design"].get("sample_key", "sample_id"),
        )
        adata.uns["openai4s_hvg_method"] = "seurat_log_fallback"
    n_comps = min(int(clustering.get("n_pcs", 30)), adata.n_obs - 1, adata.n_vars - 1)
    if n_comps < 2:
        raise ValueError("not enough cells or genes remain for PCA")
    sc.tl.pca(
        adata,
        n_comps=n_comps,
        mask_var="highly_variable",
        random_state=seed,
    )
    representation = "X_pca"
    if config["integration"]["method"] == "harmony":
        import scanpy.external as sce

        batch_keys = config["integration"]["batch_keys"]
        batch = batch_keys[0] if len(batch_keys) == 1 else "_harmony_batch"
        if len(batch_keys) > 1:
            adata.obs[batch] = adata.obs[batch_keys].astype(str).agg("|".join, axis=1)
        sce.pp.harmony_integrate(
            adata, batch, basis="X_pca", adjusted_basis="X_pca_harmony"
        )
        representation = "X_pca_harmony"
    n_neighbors = min(int(clustering.get("n_neighbors", 15)), adata.n_obs - 1)
    sc.pp.neighbors(adata, n_neighbors=max(2, n_neighbors), use_rep=representation)
    sc.tl.umap(adata, random_state=seed)
    return adata


def _run_clustering(adata: Any, config: Mapping[str, Any], sc: Any) -> Any:
    seed = int(config["seed"])
    clustering = config["clustering"]
    for resolution in clustering["resolutions"]:
        sc.tl.leiden(
            adata,
            resolution=float(resolution),
            key_added=_resolution_key(float(resolution)),
            random_state=seed,
            flavor="igraph",
            n_iterations=2,
            directed=False,
        )
    selected = _resolution_key(float(clustering["selected_resolution"]))
    adata.obs["cluster"] = adata.obs[selected].astype(str).astype("category")
    return adata


def _write_markers(adata: Any, output: Path, pd: Any, sc: Any) -> Any:
    sc.tl.rank_genes_groups(adata, "cluster", method="wilcoxon", use_raw=True)
    rows = []
    for cluster in adata.obs["cluster"].cat.categories:
        frame = sc.get.rank_genes_groups_df(adata, group=cluster)
        frame.insert(0, "cluster", str(cluster))
        rows.append(frame)
    markers = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    markers.to_csv(output, index=False)
    return markers


def _annotate(
    adata: Any, config: Mapping[str, Any], pd: Any, np: Any
) -> tuple[Any, str, list[str], Any]:
    annotation = config["annotation"]
    warnings: list[str] = []
    evidence_rows: list[dict[str, Any]] = []
    candidates: dict[str, str] = {
        str(cluster): "Unknown" for cluster in adata.obs["cluster"].cat.categories
    }
    marker_path = annotation.get("marker_panel")
    if marker_path:
        panel = pd.read_csv(marker_path)
        required = {"cell_type", "gene", "direction", "weight"}
        if not required.issubset(panel.columns):
            raise ValueError(
                "marker panel must contain cell_type,gene,direction,weight"
            )
        if not set(panel["direction"].astype(str)).issubset({"positive", "negative"}):
            raise ValueError("marker direction must be positive or negative")
        if (pd.to_numeric(panel["weight"], errors="coerce") <= 0).any():
            raise ValueError("marker weights must be positive")
        names = _gene_names(adata, config)
        lookup = {name: index for index, name in enumerate(names)}
        matrix = adata.X
        for cluster in candidates:
            mask = adata.obs["cluster"].astype(str).to_numpy() == cluster
            scores = []
            for cell_type, group in panel.groupby("cell_type", sort=True):
                score = 0.0
                supporting = []
                opposing = []
                missing = []
                for row in group.itertuples(index=False):
                    gene = str(row.gene).strip()
                    if gene not in lookup:
                        missing.append(gene)
                        continue
                    column = matrix[mask, lookup[gene]]
                    mean = float(np.asarray(column.mean()).ravel()[0])
                    signed = (
                        mean
                        * float(row.weight)
                        * (1 if row.direction == "positive" else -1)
                    )
                    score += signed
                    (supporting if signed > 0 else opposing).append(gene)
                scores.append((score, str(cell_type)))
                evidence_rows.append(
                    {
                        "cluster": cluster,
                        "cell_type": str(cell_type),
                        "score": score,
                        "supporting_genes": ";".join(sorted(set(supporting))),
                        "opposing_genes": ";".join(sorted(set(opposing))),
                        "missing_genes": ";".join(sorted(set(missing))),
                    }
                )
            scores.sort(reverse=True)
            best = scores[0] if scores else (0.0, "Unknown")
            second = scores[1][0] if len(scores) > 1 else 0.0
            if best[0] > 0 and best[0] - second >= float(annotation["minimum_margin"]):
                candidates[cluster] = best[1]
    adata.obs["candidate_cell_type"] = (
        adata.obs["cluster"].astype(str).map(candidates).fillna("Unknown")
    )
    status = "candidate_labels" if marker_path else "not_requested"

    reference_path = annotation.get("reference_h5ad")
    if reference_path:
        try:
            import scanpy as sc

            reference = sc.read_h5ad(reference_path)
            label_key = annotation.get("reference_label_key", "cell_type")
            if label_key not in reference.obs:
                raise ValueError(f"reference lacks obs[{label_key!r}]")
            overlap = adata.var_names.intersection(reference.var_names)
            if len(overlap) < max(20, min(adata.n_vars, reference.n_vars) // 5):
                raise ValueError("reference gene-space overlap is insufficient")
            # Screening only: no label transfer is implemented, so the status
            # must not claim reference-derived evidence that was never used.
            warnings.append(
                "Reference h5ad passed compatibility screening only; no label "
                "transfer was performed and its labels were not used."
            )
        except Exception as exc:
            warnings.append(f"Reference label-transfer evidence was not used: {exc}")

    mapping_path = annotation.get("confirmed_mapping")
    if mapping_path:
        mapping = pd.read_csv(mapping_path, dtype=str)
        if set(mapping.columns) != {"cluster", "cell_type"}:
            raise ValueError("confirmed mapping must contain exactly cluster,cell_type")
        if mapping["cluster"].duplicated().any():
            raise ValueError("confirmed mapping contains duplicate clusters")
        confirmed = dict(zip(mapping["cluster"], mapping["cell_type"]))
        adata.obs["confirmed_cell_type"] = (
            adata.obs["cluster"]
            .astype(str)
            .map(confirmed)
            .fillna("Unknown")
            .astype("category")
        )
        status = "confirmed"
    evidence = pd.DataFrame(evidence_rows)
    return adata, status, warnings, evidence


def _pseudobulk(
    adata: Any, config: Mapping[str, Any], pd: Any, np: Any
) -> tuple[Any, Any, str]:
    design = config["design"]
    sample_key = design.get("sample_key", "sample_id")
    group_key = (
        "confirmed_cell_type" if "confirmed_cell_type" in adata.obs else "cluster"
    )
    if group_key == "confirmed_cell_type":
        # Unmapped clusters stay Unknown, but they are distinct unconfirmed
        # populations: pooling them into one pseudobulk unit would mix cell
        # types inside the very unit DESeq2 treats as homogeneous.
        labels = adata.obs["confirmed_cell_type"].astype(str)
        clusters = adata.obs["cluster"].astype(str)
        group_series = labels.where(labels != "Unknown", "Unknown:cluster_" + clusters)
    else:
        group_series = adata.obs[group_key].astype(str)
    metadata_keys = [
        sample_key,
        design["donor_key"],
        design["condition_key"],
        *design.get("covariates", []),
    ]
    matrix = adata.layers["counts"]
    count_rows = []
    metadata_rows = []
    for (sample, group), indices in adata.obs.groupby(
        [sample_key, group_series], observed=True
    ).indices.items():
        summed = np.asarray(matrix[indices].sum(axis=0)).ravel().astype(int)
        row = {"sample_id": str(sample), "analysis_group": str(group)}
        row.update(
            {str(gene): int(value) for gene, value in zip(adata.var_names, summed)}
        )
        count_rows.append(row)
        first = adata.obs.iloc[int(indices[0])]
        meta = {
            "sample_id": str(sample),
            "analysis_group": str(group),
            "n_cells": int(len(indices)),
        }
        for key in metadata_keys:
            meta[str(key)] = str(first[key])
        meta["library_size"] = int(summed.sum())
        metadata_rows.append(meta)
    return pd.DataFrame(count_rows), pd.DataFrame(metadata_rows), group_key


def _replication_status(
    metadata: Any, config: Mapping[str, Any]
) -> tuple[bool, dict[str, int]]:
    design = config["design"]
    condition = str(design["condition_key"])
    donor = str(design["donor_key"])
    counts = {
        level: int(
            metadata.loc[metadata[condition].astype(str) == str(level), donor].nunique()
        )
        for level in (design["reference"], design["tested"])
    }
    return all(value >= 3 for value in counts.values()), counts


def _run_deseq(
    pseudobulk: Any, metadata: Any, config: Mapping[str, Any], output: Path, pd: Any
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not config["statistics"].get("de", True):
        return "not_requested", notes
    try:
        from pydeseq2.dds import DeseqDataSet
        from pydeseq2.ds import DeseqStats
    except ImportError:
        return "failed_missing_dependency", notes
    design = config["design"]
    condition = str(design["condition_key"])
    donor = str(design["donor_key"])
    tested = str(design["tested"])
    reference = str(design["reference"])
    covariates = [str(value) for value in design.get("covariates", [])]
    formula_terms = [donor] if design.get("paired") else covariates
    formula = "~ " + " + ".join([*formula_terms, condition])
    result_frames = []
    skipped_groups: list[str] = []
    failed_groups: list[str] = []
    gene_columns = [
        column
        for column in pseudobulk.columns
        if column not in {"sample_id", "analysis_group"}
    ]
    for group in sorted(metadata["analysis_group"].unique()):
        meta = metadata.loc[metadata["analysis_group"] == group].copy()
        # The replication contract holds per fitted model, not just globally:
        # every group must carry both contrast levels with three independent
        # donors each, or its fit would be underpowered pseudoreplication
        # (and a single-level group would abort the whole loop).
        donors_by_level = {
            level: int(meta.loc[meta[condition].astype(str) == level, donor].nunique())
            for level in (reference, tested)
        }
        if any(count < 3 for count in donors_by_level.values()):
            skipped_groups.append(str(group))
            continue
        counts = pseudobulk.loc[
            pseudobulk["analysis_group"] == group, gene_columns
        ].copy()
        counts.index = [f"{sample}|{group}" for sample in meta["sample_id"]]
        meta.index = counts.index
        keep = counts.sum(axis=0) > 0
        counts = counts.loc[:, keep].astype(int)
        if counts.shape[1] < 2:
            skipped_groups.append(str(group))
            continue
        try:
            dds = DeseqDataSet(
                counts=counts,
                metadata=meta,
                design=formula,
                refit_cooks=True,
                n_cpus=1,
            )
            dds.deseq2()
            stats = DeseqStats(
                dds,
                contrast=[condition, tested, reference],
                n_cpus=1,
            )
            stats.summary()
        except Exception as exc:  # one degenerate group must not abort the rest
            failed_groups.append(f"{group}: {exc}")
            continue
        frame = stats.results_df.reset_index().rename(columns={"index": "gene"})
        frame.insert(0, "analysis_group", group)
        frame.insert(1, "tested", tested)
        frame.insert(2, "reference", reference)
        result_frames.append(frame)
    if skipped_groups:
        notes.append(
            "Pseudobulk DE skipped for groups without three donors per "
            "contrast level or enough expressed genes: " + ", ".join(skipped_groups)
        )
    if failed_groups:
        output.with_suffix(".error.txt").write_text(
            "\n".join(failed_groups) + "\n", encoding="utf-8"
        )
        notes.append(
            "Pseudobulk DE failed for groups: "
            + ", ".join(item.split(":", 1)[0] for item in failed_groups)
            + "; see the adjacent error file."
        )
    if not result_frames:
        status = "failed" if failed_groups else "skipped_no_testable_groups"
        return status, notes
    pd.concat(result_frames, ignore_index=True).to_csv(output, index=False)
    return "completed", notes


def _run_milo(adata: Any, config: Mapping[str, Any], output: Path) -> str:
    if not config["statistics"].get("da", True):
        return "not_requested"
    try:
        import pertpy as pt
    except ImportError:
        return "failed_missing_dependency"
    design = config["design"]
    sample = str(design.get("sample_key", "sample_id"))
    condition = str(design["condition_key"])
    terms = (
        [str(design["donor_key"])]
        if design.get("paired")
        else [str(value) for value in design.get("covariates", [])]
    )
    formula = "~ " + " + ".join([*terms, condition])
    try:
        milo = pt.tl.Milo()
        mdata = milo.load(adata)
        milo.make_nhoods(mdata["rna"], prop=0.1, seed=int(config["seed"]))
        milo.count_nhoods(mdata, sample_col=sample)
        milo.da_nhoods(
            mdata,
            design=formula,
            model_contrasts=f"{condition}{design['tested']}-{condition}{design['reference']}",
            solver="pydeseq2",
        )
        frame = mdata["milo"].var.copy()
        frame.index.name = "neighborhood"
        frame.reset_index().to_csv(output, index=False)
        return "completed"
    except Exception as exc:
        output.with_suffix(".error.txt").write_text(str(exc) + "\n", encoding="utf-8")
        return "failed"


def _plot_outputs(adata: Any, qc_summary: Any, output_dir: Path, pd: Any) -> list[str]:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    figure_dir = output_dir / "figures"
    figure_dir.mkdir(parents=True, exist_ok=True)
    files = []
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    axes[0].hist(adata.obs["total_counts"], bins=30)
    axes[0].set_title("Total counts after QC")
    axes[1].hist(adata.obs["n_genes_by_counts"], bins=30)
    axes[1].set_title("Detected genes after QC")
    axes[2].hist(adata.obs["pct_counts_mt"], bins=30)
    axes[2].set_title("Mitochondrial percent")
    fig.tight_layout()
    path = figure_dir / "qc.pdf"
    fig.savefig(path)
    plt.close(fig)
    files.append(str(path))

    coords = adata.obsm["X_umap"]
    labels = adata.obs["cluster"].astype(str)
    categories = sorted(labels.unique())
    colors = {value: index for index, value in enumerate(categories)}
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.scatter(coords[:, 0], coords[:, 1], c=[colors[value] for value in labels], s=5)
    ax.set_title("UMAP by selected cluster")
    ax.set_xlabel("UMAP1")
    ax.set_ylabel("UMAP2")
    fig.tight_layout()
    path = figure_dir / "umap_clusters.pdf"
    fig.savefig(path)
    plt.close(fig)
    files.append(str(path))

    resolution_rows = []
    for column in adata.obs.columns:
        if not column.startswith("leiden_"):
            continue
        try:
            resolution = float(column.removeprefix("leiden_"))
        except ValueError:
            continue
        resolution_rows.append((resolution, int(adata.obs[column].nunique())))
    if resolution_rows:
        resolution_rows.sort()
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot(
            [row[0] for row in resolution_rows],
            [row[1] for row in resolution_rows],
            marker="o",
        )
        ax.set_xlabel("Leiden resolution")
        ax.set_ylabel("Number of clusters")
        ax.set_title("Resolution sensitivity (not an optimum claim)")
        fig.tight_layout()
        path = figure_dir / "resolution_sweep.pdf"
        fig.savefig(path)
        plt.close(fig)
        files.append(str(path))

    marker_path = output_dir / "tables" / "cluster_markers.csv"
    if marker_path.exists():
        markers = pd.read_csv(marker_path)
        score_column = "scores" if "scores" in markers else "score"
        if score_column in markers and not markers.empty:
            top = (
                markers.sort_values(score_column, ascending=False)
                .groupby("cluster", observed=True)
                .head(3)
            )
            fig, ax = plt.subplots(figsize=(max(6, len(top) * 0.28), 4))
            labels = [f"{row.cluster}:{row.names}" for row in top.itertuples()]
            ax.bar(range(len(top)), top[score_column])
            ax.set_xticks(range(len(top)), labels, rotation=90)
            ax.set_ylabel("Descriptive marker score")
            ax.set_title("Top cluster markers")
            fig.tight_layout()
            path = figure_dir / "cluster_markers.pdf"
            fig.savefig(path)
            plt.close(fig)
            files.append(str(path))

    for filename, x_candidates, p_candidates, title, output_name in (
        (
            "differential_expression.csv",
            ("log2FoldChange",),
            ("padj", "pvalue"),
            "Pseudobulk differential expression",
            "differential_expression.pdf",
        ),
        (
            "differential_abundance.csv",
            ("logFC",),
            ("SpatialFDR", "FDR", "PValue"),
            "Milo differential abundance",
            "differential_abundance.pdf",
        ),
    ):
        table_path = output_dir / "tables" / filename
        if not table_path.exists():
            continue
        frame = pd.read_csv(table_path)
        x_column = next((value for value in x_candidates if value in frame), None)
        p_column = next((value for value in p_candidates if value in frame), None)
        if not x_column or not p_column or frame.empty:
            continue
        x_values = pd.to_numeric(frame[x_column], errors="coerce")
        p_values = pd.to_numeric(frame[p_column], errors="coerce").clip(lower=1e-300)
        valid = x_values.notna() & p_values.notna()
        if not valid.any():
            continue
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.scatter(x_values[valid], -p_values[valid].map(math.log10), s=8, alpha=0.7)
        ax.axvline(0, color="black", linewidth=0.8)
        ax.set_xlabel(x_column)
        ax.set_ylabel(f"-log10({p_column})")
        ax.set_title(title)
        fig.tight_layout()
        path = figure_dir / output_name
        fig.savefig(path)
        plt.close(fig)
        files.append(str(path))
    return files


def _package_versions() -> dict[str, str | None]:
    versions = {"python": platform.python_version()}
    try:
        from importlib.metadata import version

        for package in ("anndata", "scanpy", "harmonypy", "pertpy", "pydeseq2"):
            try:
                versions[package] = version(package)
            except Exception:
                versions[package] = None
    except ImportError:
        pass
    return versions


def _remove_inferential_outputs(output_dir: Path) -> None:
    """Remove comparative-only files before publishing a descriptive run."""
    for relative in INFERENTIAL_OUTPUTS:
        path = output_dir / relative
        if path.is_file() or path.is_symlink():
            path.unlink()


def _featured(output_dir: Path, analysis_mode: str = "comparative") -> list[str]:
    candidates = [
        output_dir / "analysis.h5ad",
        output_dir / "report.md",
        output_dir / "config.resolved.json",
        output_dir / "run_manifest.json",
        output_dir / "tables" / "qc_summary.csv",
        output_dir / "tables" / "cell_qc.csv",
        output_dir / "tables" / "cluster_assignments.csv",
        output_dir / "tables" / "cluster_markers.csv",
        output_dir / "tables" / "annotation_assignments.csv",
        output_dir / "tables" / "annotation_evidence.csv",
        output_dir / "tables" / "pseudobulk_counts.csv",
        output_dir / "tables" / "pseudobulk_metadata.csv",
        output_dir / "tables" / "differential_expression.csv",
        output_dir / "tables" / "differential_abundance.csv",
        output_dir / "figures" / "qc.pdf",
        output_dir / "figures" / "umap_clusters.pdf",
        output_dir / "figures" / "resolution_sweep.pdf",
        output_dir / "figures" / "cluster_markers.pdf",
        output_dir / "figures" / "differential_expression.pdf",
        output_dir / "figures" / "differential_abundance.pdf",
    ]
    if analysis_mode == "descriptive":
        candidates = [
            path
            for path in candidates
            if path.relative_to(output_dir).as_posix() not in INFERENTIAL_OUTPUTS
        ]
    return [str(path.resolve()) for path in candidates if path.is_file()]


def _write_report(output_dir: Path, manifest: Mapping[str, Any]) -> None:
    statistics = manifest["statistics_status"]
    warnings = manifest.get("warnings", [])
    analysis_mode = manifest.get("analysis_mode", "comparative")
    lines = [
        "# Single-cell RNA analysis report",
        "",
        f"- Status: `{manifest['status']}`",
        f"- Analysis mode: `{analysis_mode}`",
        f"- Annotation: `{manifest['annotation_status']}`",
        f"- Differential expression: `{statistics['de']}`",
        f"- Differential abundance: `{statistics['da']}`",
        f"- Seed: `{manifest['seed']}`",
        "",
        "## Interpretation boundaries",
        "",
        (
            "This is a descriptive single-sample analysis. Cluster markers describe "
            "within-sample structure; no condition effect, differential expression, "
            "or differential abundance inference was attempted."
            if analysis_mode == "descriptive"
            else "Cluster markers are descriptive, condition DE uses pseudobulk raw "
            "counts, and candidate annotations are not ground truth. Harmony, when "
            "requested, changes the neighbor embedding only."
        ),
        "",
        "## Warnings",
        "",
    ]
    lines.extend([f"- {warning}" for warning in warnings] or ["- None"])
    lines.extend(
        [
            "",
            "## Files",
            "",
            "See `run_manifest.json` for checksums and provenance.",
            "",
        ]
    )
    (output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _finalize_manifest(output_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest["updated_at"] = _utc_now()
    manifest_path = output_dir / "run_manifest.json"
    manifest["files"] = []
    for path in sorted(
        item
        for item in output_dir.rglob("*")
        if item.is_file()
        and item != manifest_path
        and ".invalidated" not in item.relative_to(output_dir).parts
        and not (
            manifest.get("analysis_mode") == "descriptive"
            and item.relative_to(output_dir).as_posix() in INFERENTIAL_OUTPUTS
        )
    ):
        manifest["files"].append(
            {
                "path": str(path.relative_to(output_dir)),
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    _write_json(manifest_path, manifest)
    return manifest


def _result(output_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": manifest["status"],
        "analysis_mode": manifest.get("analysis_mode", "comparative"),
        "run_dir": str(output_dir.resolve()),
        "featured_files": _featured(
            output_dir, str(manifest.get("analysis_mode", "comparative"))
        ),
        "warnings": list(manifest.get("warnings", [])),
        "annotation_status": manifest.get("annotation_status", "not_started"),
        "statistics_status": dict(
            manifest.get(
                "statistics_status", {"de": "not_started", "da": "not_started"}
            )
        ),
        "manifest": str((output_dir / "run_manifest.json").resolve()),
    }


def _run(
    config: Mapping[str, Any] | str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    _resume_stage: str | None = None,
    _prior_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    tables = output / "tables"
    tables.mkdir(exist_ok=True)
    check = preflight(config)
    resolved = check.get("resolved_config")
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_mode": (resolved or {}).get("analysis_mode", "comparative"),
        "created_at": _utc_now(),
        "status": "running",
        "seed": (resolved or {}).get("seed", 0),
        "thresholds": (resolved or {}).get("qc", {}),
        "config_sha256": _config_sha(resolved) if resolved else None,
        "input_fingerprint": check.get("input_fingerprint"),
        "stage_hashes": (
            _stage_hashes(resolved, check.get("input_fingerprint")) if resolved else {}
        ),
        "inputs": check.get("inputs", []),
        "versions": _package_versions(),
        "stages": {
            name: "not_started"
            for name in (
                "preflight",
                "qc",
                "embedding",
                "clustering",
                "annotation",
                "statistics",
            )
        },
        "warnings": [],
        "stage_warnings": {},
        "annotation_status": "not_started",
        "statistics_status": {"de": "not_started", "da": "not_started"},
    }
    stage_order = [
        "preflight",
        "qc",
        "embedding",
        "clustering",
        "annotation",
        "statistics",
    ]
    resume_stage = _resume_stage or "preflight"
    if resume_stage not in stage_order:
        raise ValueError(f"unknown resume stage: {resume_stage}")
    resume_index = stage_order.index(resume_stage)
    if _resume_stage:
        manifest["resumed_from_stage"] = resume_stage
        for stage in stage_order[:resume_index]:
            manifest["stages"][stage] = "completed"

    # Warnings are attributed to the stage that produced them. A stage that is
    # not re-run keeps its recorded warnings; a stage that re-runs starts
    # clean, so a caveat whose cause was fixed cannot survive into the new
    # report and contradict the recomputed result. Preflight always re-runs.
    prior_stage_warnings = dict((_prior_manifest or {}).get("stage_warnings") or {})
    stage_warnings: dict[str, list[str]] = {name: [] for name in stage_order}
    stage_warnings["preflight"] = list(check.get("warnings", []))
    for stage in stage_order[1:resume_index]:
        stage_warnings[stage] = list(prior_stage_warnings.get(stage, []))

    def _sync_warnings() -> None:
        manifest["stage_warnings"] = {
            name: list(values) for name, values in stage_warnings.items()
        }
        manifest["warnings"] = list(
            dict.fromkeys(
                warning
                for name in stage_order
                for warning in stage_warnings.get(name, [])
            )
        )

    if resolved:
        _write_json(output / "config.resolved.json", resolved)
    _write_json(output / "preflight.json", check)
    if check["status"] != "valid":
        manifest["status"] = "failed"
        manifest["stages"]["preflight"] = "failed"
        manifest["errors"] = check["errors"]
        _sync_warnings()
        _write_report(output, manifest)
        _finalize_manifest(output, manifest)
        return _result(output, manifest)
    manifest["stages"]["preflight"] = "completed"

    def _checkpoint_manifest() -> None:
        # Persist progress after every completed stage so an interrupted run
        # can resume from the last durable stage instead of recomputing
        # everything; the file inventory is finalized only at the end.
        _sync_warnings()
        _write_json(output / "run_manifest.json", manifest)

    _checkpoint_manifest()
    try:
        # The statistics stage always re-runs, so inferential outputs from a
        # previous run in this directory must never survive into this run's
        # manifest, featured files, or figures — in either analysis mode.
        _remove_inferential_outputs(output)
        ad, np, pd, sc = _science_imports()
        if resume_index <= stage_order.index("qc"):
            adata, load_warnings = _load_data(resolved, np, pd, sc, ad)
            stage_warnings["qc"].extend(load_warnings)
            qc_adata, qc_summary, cell_qc, qc_warnings = _run_qc(
                adata, resolved, np, pd, sc
            )
            stage_warnings["qc"].extend(qc_warnings)
            qc_summary.to_csv(tables / "qc_summary.csv", index=False)
            cell_qc.to_csv(tables / "cell_qc.csv", index=False)
            qc_adata.write_h5ad(output / STAGE_FILES["qc"], compression="gzip")
            manifest["stages"]["qc"] = "completed"
            _checkpoint_manifest()
        else:
            qc_adata = sc.read_h5ad(output / STAGE_FILES["qc"])
            qc_summary = pd.read_csv(tables / "qc_summary.csv")

        if resume_index <= stage_order.index("embedding"):
            embedded = _run_embedding(qc_adata, resolved, sc)
            embedded.write_h5ad(output / STAGE_FILES["embedding"], compression="gzip")
            manifest["stages"]["embedding"] = "completed"
            _checkpoint_manifest()
        else:
            embedded = sc.read_h5ad(output / STAGE_FILES["embedding"])

        if resume_index <= stage_order.index("clustering"):
            clustered = _run_clustering(embedded, resolved, sc)
            clustered.write_h5ad(output / STAGE_FILES["clustering"], compression="gzip")
            cluster_columns = [
                column
                for column in clustered.obs.columns
                if column == "cluster" or column.startswith("leiden_")
            ]
            cluster_assignments = clustered.obs[cluster_columns].copy()
            cluster_assignments.insert(0, "cell_id", clustered.obs_names.astype(str))
            cluster_assignments.to_csv(tables / "cluster_assignments.csv", index=False)
            _write_markers(clustered, tables / "cluster_markers.csv", pd, sc)
            # Only durable once every clustering output exists: marking the
            # stage completed before its tables would let a resume treat a
            # markerless run as finished.
            manifest["stages"]["clustering"] = "completed"
            _checkpoint_manifest()
        else:
            clustered = sc.read_h5ad(output / STAGE_FILES["clustering"])

        if resume_index <= stage_order.index("annotation"):
            annotated, annotation_status, annotation_warnings, evidence = _annotate(
                clustered, resolved, pd, np
            )
            stage_warnings["annotation"].extend(annotation_warnings)
            evidence_path = tables / "annotation_evidence.csv"
            # Remove first: a prior run's evidence table must not survive a
            # rerun that computes no evidence.
            evidence_path.unlink(missing_ok=True)
            if not evidence.empty:
                evidence.to_csv(evidence_path, index=False)
            annotation_columns = [
                column
                for column in (
                    "cluster",
                    "candidate_cell_type",
                    "confirmed_cell_type",
                    "cell_type",
                )
                if column in annotated.obs
            ]
            annotation_assignments = annotated.obs[annotation_columns].copy()
            annotation_assignments.insert(0, "cell_id", annotated.obs_names.astype(str))
            annotation_assignments.to_csv(
                tables / "annotation_assignments.csv", index=False
            )
            annotated.uns["openai4s_annotation_status"] = annotation_status
            annotated.write_h5ad(output / STAGE_FILES["annotation"], compression="gzip")
            manifest["stages"]["annotation"] = "completed"
            _checkpoint_manifest()
        else:
            annotated = sc.read_h5ad(output / STAGE_FILES["annotation"])
            annotation_status = str(
                annotated.uns.get("openai4s_annotation_status", "candidate_only")
            )
        manifest["annotation_status"] = annotation_status

        if resolved["analysis_mode"] == "descriptive":
            de_status = "not_applicable_descriptive"
            da_status = "not_applicable_descriptive"
            manifest["replication"] = {}
            manifest["statistics_group_key"] = None
        else:
            pseudobulk, metadata, group_key = _pseudobulk(annotated, resolved, pd, np)
            pseudobulk.to_csv(tables / "pseudobulk_counts.csv", index=False)
            metadata.to_csv(tables / "pseudobulk_metadata.csv", index=False)
            enough, donor_counts = _replication_status(metadata, resolved)
            manifest["replication"] = donor_counts
            manifest["statistics_group_key"] = group_key
            if enough:
                try:
                    de_status, de_notes = _run_deseq(
                        pseudobulk,
                        metadata,
                        resolved,
                        tables / "differential_expression.csv",
                        pd,
                    )
                    stage_warnings["statistics"].extend(de_notes)
                except Exception as exc:
                    de_status = "failed"
                    stage_warnings["statistics"].append(f"Pseudobulk DE failed: {exc}")
                da_status = _run_milo(
                    annotated, resolved, tables / "differential_abundance.csv"
                )
                if da_status == "failed":
                    stage_warnings["statistics"].append(
                        "Milo DA failed; see the adjacent error file."
                    )
            else:
                de_status = "skipped_insufficient_replicates"
                da_status = "skipped_insufficient_replicates"
                stage_warnings["statistics"].append(
                    "DE/DA skipped: each contrast level requires at least three independent donors."
                )
        manifest["statistics_status"] = {"de": de_status, "da": da_status}
        # A failed or dependency-missing inferential step must be visible in
        # the run status, not only in a structured field nobody reads.
        for kind, label in (
            ("de", "differential expression"),
            ("da", "differential abundance"),
        ):
            status = str(manifest["statistics_status"][kind])
            if status.startswith("failed"):
                stage_warnings["statistics"].append(
                    f"Requested {label} did not complete: {status}."
                )
        manifest["stages"]["statistics"] = "completed"

        annotated.uns["openai4s"] = {
            "schema_version": SCHEMA_VERSION,
            "analysis_mode": resolved["analysis_mode"],
            "config_sha256": manifest["config_sha256"],
            "input_fingerprint": manifest["input_fingerprint"],
            "annotation_status": annotation_status,
            "statistics_status": manifest["statistics_status"],
        }
        annotated.write_h5ad(output / "analysis.h5ad", compression="gzip")
        _plot_outputs(annotated, qc_summary, output, pd)
        _sync_warnings()
        manifest["status"] = (
            "completed_with_warnings" if manifest["warnings"] else "completed"
        )
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["errors"] = [str(exc)]
        manifest["traceback"] = traceback.format_exc()
        for name, state in manifest["stages"].items():
            if state == "not_started":
                manifest["stages"][name] = (
                    "failed" if name != "statistics" else "not_started"
                )
                break
    _sync_warnings()
    _write_report(output, manifest)
    _finalize_manifest(output, manifest)
    return _result(output, manifest)


def run(
    config: Mapping[str, Any] | str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
) -> dict[str, Any]:
    """Run the workflow and return a structured, Artifact-ready status."""
    return _run(config, output_dir)


def resume(run_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Validate provenance and return or rebuild an interrupted/stale run."""
    output = Path(run_dir).expanduser().resolve()
    config_path = output / "config.resolved.json"
    if not config_path.exists():
        raise ValueError(f"missing resolved configuration: {config_path}")
    config = _read_json(config_path)
    check = preflight(config)
    if check["status"] != "valid":
        # A run that cannot be validated right now (missing input, transient
        # mount failure, revoked path) must not be dismantled: report the
        # failure without touching any deliverable on disk.
        return {
            "status": "failed",
            "analysis_mode": (check.get("resolved_config") or {}).get(
                "analysis_mode", "comparative"
            ),
            "run_dir": str(output),
            "featured_files": [],
            "warnings": list(check.get("warnings", [])),
            "errors": list(check.get("errors", [])),
            "annotation_status": "not_started",
            "statistics_status": {"de": "not_started", "da": "not_started"},
            "manifest": str(output / "run_manifest.json"),
        }
    current_hashes = _stage_hashes(
        check["resolved_config"], check.get("input_fingerprint")
    )
    manifest_path = output / "run_manifest.json"
    prior_manifest: dict[str, Any] | None = None
    if manifest_path.exists():
        try:
            prior_manifest = _read_json(manifest_path)
        except ValueError:
            # A torn or tampered manifest means the prior state cannot be
            # trusted; rebuild instead of crashing.
            prior_manifest = None
    if prior_manifest is not None:
        same_config = prior_manifest.get("config_sha256") == _config_sha(
            check["resolved_config"]
        )
        same_input = prior_manifest.get("input_fingerprint") == check.get(
            "input_fingerprint"
        )
        # Stage hashes cover what config_sha/input_fingerprint cannot: the
        # contents of annotation side-input files named by path.
        same_stages = (prior_manifest.get("stage_hashes") or {}) == current_hashes
        deliverables_intact = all(
            path.exists()
            for path in (
                output / "preflight.json",
                output / STAGE_FILES["qc"],
                output / STAGE_FILES["embedding"],
                output / STAGE_FILES["clustering"],
                output / STAGE_FILES["annotation"],
                output / "analysis.h5ad",
            )
        )
        if (
            same_config
            and same_input
            and same_stages
            and deliverables_intact
            and prior_manifest.get("status")
            in {
                "completed",
                "completed_with_warnings",
            }
        ):
            return _result(output, prior_manifest)

    stage_order = [
        "preflight",
        "qc",
        "embedding",
        "clustering",
        "annotation",
        "statistics",
    ]
    earliest = "preflight"
    if prior_manifest and current_hashes:
        prior_hashes = prior_manifest.get("stage_hashes") or {}
        earliest = "statistics"
        for stage in stage_order:
            if prior_hashes.get(stage) != current_hashes.get(stage):
                earliest = stage
                break
        else:
            required = {
                "preflight": output / "preflight.json",
                "qc": output / STAGE_FILES["qc"],
                "embedding": output / STAGE_FILES["embedding"],
                "clustering": output / STAGE_FILES["clustering"],
                "annotation": output / STAGE_FILES["annotation"],
                "statistics": output / "analysis.h5ad",
            }
            for stage in stage_order:
                state = (prior_manifest.get("stages") or {}).get(stage)
                if state != "completed" or not required[stage].exists():
                    earliest = stage
                    break

    # Move only the earliest stale stage and its dependants aside. Upstream
    # checkpoints remain available for a provenance-safe resume.
    stale = output / ".invalidated"
    stale.mkdir(exist_ok=True)
    invalid_index = stage_order.index(earliest)
    stage_paths = {
        "preflight": ["preflight.json"],
        "qc": [STAGE_FILES["qc"], "tables/qc_summary.csv", "tables/cell_qc.csv"],
        "embedding": [STAGE_FILES["embedding"]],
        "clustering": [
            STAGE_FILES["clustering"],
            "tables/cluster_assignments.csv",
            "tables/cluster_markers.csv",
        ],
        "annotation": [
            STAGE_FILES["annotation"],
            "tables/annotation_assignments.csv",
            "tables/annotation_evidence.csv",
        ],
        "statistics": [
            "analysis.h5ad",
            "tables/pseudobulk_counts.csv",
            "tables/pseudobulk_metadata.csv",
            "tables/differential_expression.csv",
            "tables/differential_expression.error.txt",
            "tables/differential_abundance.csv",
            "tables/differential_abundance.error.txt",
            "report.md",
            "run_manifest.json",
        ],
    }
    relative_paths = [
        value for stage in stage_order[invalid_index:] for value in stage_paths[stage]
    ]
    for relative in relative_paths:
        source = output / relative
        if source.exists():
            destination = stale / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                destination.unlink()
            shutil.move(str(source), str(destination))
    source = output / "figures"
    destination = stale / "figures"
    if source.exists():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(source), str(destination))
    _write_json(
        stale / "invalidation.json",
        {
            "invalidated_at": _utc_now(),
            "earliest_stage": earliest,
            "previous_stage_hashes": (prior_manifest or {}).get("stage_hashes", {}),
            "current_stage_hashes": current_hashes,
        },
    )
    return _run(
        config,
        output,
        _resume_stage=earliest,
        _prior_manifest=prior_manifest,
    )
