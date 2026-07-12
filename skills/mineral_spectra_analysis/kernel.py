"""Mixed-mineral Raman spectra analysis helpers for OpenAI4S.

This sidecar packages the workflow from ``spectra-pipeline/``:

    load spectrum -> preprocess once -> save/load clean spectrum ->
    iterative residual peak matching -> NNLS unmixing -> diagnosis/report

Imports are intentionally stdlib-only. Scientific dependencies are imported at
runtime so the skill can still be discovered and compile-checked without numpy,
scipy, pybaselines, or matplotlib installed.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.request import urlopen
from zipfile import ZipFile

GRID_MIN = 150.0
GRID_MAX = 1400.0
GRID_STEP = 2.0

RRUFF_URL = "https://www.rruff.net/zipped_data_files/raman/{dataset}.zip"

DEFAULT_CONFIG = {
    "despike_enabled": True,
    "despike_threshold": 7.0,
    "despike_window": 5,
    "denoise_method": "savgol",
    "denoise_window": 7,
    "savgol_poly": 3,
    "baseline_method": "asls",
    "baseline_lam": 1e5,
    "baseline_p": 0.01,
    "baseline_poly_order": 5,
    "normalise_method": "area",
    "peak_smooth_window": 11,
    "peak_savgol_poly": 3,
    "peak_prominence_sigma": 3.0,
    "peak_min_distance_cm": 8.0,
    "peak_prefilter_enabled": True,
    "peak_match_tol_cm": 8.0,
    "peak_min_matches": 1,
    "match_metric": "pearson",
    "top_k": 8,
    "corr_threshold": 0.3,
    "greedy_min_gain": 0.01,
    "fraction_threshold": 0.05,
}


def _import_module(name: str, install_hint: str):
    try:
        return __import__(name, fromlist=["*"])
    except ImportError as exc:
        raise ImportError(
            f"mineral_spectra_analysis requires {name}. Install with: {install_hint}"
        ) from exc


def _np():
    return _import_module(
        "numpy", "python -m pip install numpy scipy pybaselines matplotlib"
    )


def _signal():
    return _import_module("scipy.signal", "python -m pip install scipy")


def _ndimage():
    return _import_module("scipy.ndimage", "python -m pip install scipy")


def _optimize():
    return _import_module("scipy.optimize", "python -m pip install scipy")


def _baseline_cls():
    mod = _import_module("pybaselines", "python -m pip install pybaselines")
    return mod.Baseline


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def available_dependencies() -> dict[str, bool]:
    """Return whether optional runtime dependencies are importable."""
    import importlib.util

    return {
        "numpy": importlib.util.find_spec("numpy") is not None,
        "scipy": importlib.util.find_spec("scipy") is not None,
        "pybaselines": importlib.util.find_spec("pybaselines") is not None,
        "matplotlib": importlib.util.find_spec("matplotlib") is not None,
    }


def default_config() -> dict[str, Any]:
    """Return a fresh copy of the fixed pipeline configuration."""
    return copy.deepcopy(DEFAULT_CONFIG)


@dataclass
class Library:
    """Aligned reference spectral library.

    ``grid`` is shape ``(n_grid,)``. ``A`` is shape ``(n_grid, n_minerals)`` and
    each column is an area-normalized reference spectrum.
    """

    grid: Any
    names: list[str]
    A: Any

    def index(self, name: str) -> int:
        return self.names.index(name)


@dataclass
class PipelineResult:
    processed: Any
    recon: Any
    candidate_names: list[str]
    fractions: dict[str, float]
    used_idx: Any
    used_coef: Any
    diagnostics: dict[str, Any]
    peaks: Any = field(default_factory=list)
    support: dict[str, list[float]] = field(default_factory=dict)


@dataclass
class LoopOutcome:
    best_result: PipelineResult
    history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SynthCase:
    grid: Any
    spectrum: Any
    true_names: list[str]
    true_fractions: dict[str, float] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


def parse_rruff_text(txt: str):
    """Parse a RRUFF text file while ignoring metadata and non-data lines."""
    np = _np()
    meta, ax, iv = {}, [], []
    for line in txt.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("##"):
            if "=" in line:
                key, value = line[2:].split("=", 1)
                meta[key.strip()] = value.strip()
            continue
        parts = line.split(",")
        if len(parts) != 2:
            continue
        try:
            axis, intensity = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        ax.append(axis)
        iv.append(intensity)
    return meta, np.asarray(ax), np.asarray(iv)


def mineral_from_filename(fname: str) -> str:
    """Return the mineral name token before the first ``__`` in a RRUFF file."""
    return os.path.basename(fname).split("__", 1)[0]


def ensure_rruff_dataset(
    dataset: str = "excellent_oriented",
    cache_dir: str | os.PathLike[str] | None = None,
    allow_download: bool = True,
) -> str:
    """Return a cached RRUFF zip path, downloading it when allowed and missing."""
    root = Path(cache_dir) if cache_dir else Path.cwd() / "spectra_cache"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{dataset}.zip"
    if path.exists() and path.stat().st_size > 1000:
        return str(path)
    if not allow_download:
        raise FileNotFoundError(
            f"RRUFF cache not found: {path}. Provide zip_path or allow download."
        )
    with urlopen(RRUFF_URL.format(dataset=dataset)) as response:
        data = response.read()
    path.write_bytes(data)
    return str(path)


def common_grid():
    """Return the fixed Raman-shift grid used by the prototype pipeline."""
    np = _np()
    return np.arange(GRID_MIN, GRID_MAX + GRID_STEP / 2, GRID_STEP)


def resample(axis: Any, intensity: Any, grid: Any):
    """Linearly interpolate a spectrum onto ``grid``; outside coverage is zero."""
    np = _np()
    axis = np.asarray(axis, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    order = np.argsort(axis)
    axis, intensity = axis[order], intensity[order]
    return np.interp(grid, axis, intensity, left=0.0, right=0.0)


def _grid_coverage(axis: Any, grid: Any) -> float:
    np = _np()
    axis = np.asarray(axis, dtype=float)
    inside = (grid >= axis.min()) & (grid <= axis.max())
    return float(inside.mean())


def build_rruff_library(
    dataset: str = "excellent_oriented",
    max_minerals: int | None = None,
    cache_dir: str | os.PathLike[str] | None = None,
    zip_path: str | os.PathLike[str] | None = None,
    allow_download: bool = True,
) -> Library:
    """Build the RRUFF representative-spectrum library used by the prototype."""
    np = _np()
    path = (
        str(zip_path)
        if zip_path
        else ensure_rruff_dataset(dataset, cache_dir, allow_download)
    )
    grid = common_grid()
    per_mineral: dict[str, list[tuple[str, Any, Any]]] = {}
    with ZipFile(path) as archive:
        for filename in archive.namelist():
            if not filename.endswith(".txt"):
                continue
            mineral = mineral_from_filename(filename)
            text = archive.read(filename).decode("utf-8", "ignore")
            _, axis, intensity = parse_rruff_text(text)
            if axis.size < 100:
                continue
            per_mineral.setdefault(mineral, []).append((filename, axis, intensity))

    names, cols = [], []
    for mineral in sorted(per_mineral):
        candidates = per_mineral[mineral]

        def score(item):
            filename, axis, _ = item
            processed = 1 if "Processed" in filename else 0
            return (processed, _grid_coverage(axis, grid))

        _, axis, intensity = max(candidates, key=score)
        col = resample(axis, intensity, grid)
        col = np.clip(col, 0, None)
        if col.max() <= 0:
            continue
        col = normalise(col, method="area")
        names.append(mineral)
        cols.append(col)

    A = np.array(cols).T
    if max_minerals is not None and len(names) > max_minerals:
        names = names[:max_minerals]
        A = A[:, :max_minerals]
    return Library(grid=grid, names=names, A=A)


def load_spectrum_csv(path: str | os.PathLike[str]):
    """Load a two-column ``raman_shift,intensity`` CSV."""
    np = _np()
    arr = np.loadtxt(path, delimiter=",", skiprows=1)
    if arr.ndim != 2 or arr.shape[1] < 2:
        raise ValueError(f"spectrum CSV must have at least two columns: {path}")
    return arr[:, 0], arr[:, 1]


def save_spectrum_csv(path: str | os.PathLike[str], grid: Any, spectrum: Any) -> None:
    """Write a two-column ``raman_shift,intensity`` CSV."""
    np = _np()
    arr = np.column_stack(
        [np.asarray(grid, dtype=float), np.asarray(spectrum, dtype=float)]
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    np.savetxt(
        path,
        arr,
        delimiter=",",
        header="raman_shift,intensity",
        comments="",
        fmt="%.6g",
    )


def despike(y: Any, threshold: float = 7.0, window: int = 5):
    """Remove cosmic-ray-like spikes with a modified z-score filter."""
    np = _np()
    y = np.asarray(y, dtype=float)
    diff = np.diff(y, prepend=y[0])
    med = np.median(diff)
    mad = np.median(np.abs(diff - med)) or 1e-9
    mod_z = 0.6745 * (diff - med) / mad
    spikes = np.abs(mod_z) > threshold
    out = y.copy()
    n = len(out)
    for idx in np.where(spikes)[0]:
        lo, hi = max(0, idx - window), min(n, idx + window + 1)
        neigh = [j for j in range(lo, hi) if not spikes[j]]
        if neigh:
            out[idx] = np.mean(out[neigh])
    return out


def denoise(y: Any, method: str = "savgol", window: int = 7, poly: int = 3):
    """Smooth a spectrum using the configured denoising method."""
    np = _np()
    y = np.asarray(y, dtype=float)
    if method == "none":
        return y
    if method == "gaussian":
        return _ndimage().gaussian_filter1d(y, sigma=max(window / 3.0, 0.5))
    window = int(window)
    if window % 2 == 0:
        window += 1
    window = max(window, poly + 2)
    if window % 2 == 0:
        window += 1
    return _signal().savgol_filter(y, window_length=window, polyorder=poly)


def baseline_correct(
    y: Any,
    method: str = "asls",
    lam: float = 1e5,
    p: float = 0.01,
    poly_order: int = 5,
):
    """Apply the configured baseline correction."""
    np = _np()
    y = np.asarray(y, dtype=float)
    if method == "none":
        return y
    if method == "poly":
        x = np.arange(len(y))
        coeffs = np.polyfit(x, y, poly_order)
        base = np.polyval(coeffs, x)
        return y - base
    fitter = _baseline_cls()(x_data=np.arange(len(y)))
    if method == "airpls":
        base, _ = fitter.airpls(y, lam=lam)
    else:
        base, _ = fitter.asls(y, lam=lam, p=p)
    return y - base


def normalise(y: Any, method: str = "area"):
    """Clip negative values and normalize by area, max, or L2 norm."""
    np = _np()
    y = np.clip(np.asarray(y, dtype=float), 0, None)
    if method == "max":
        denom = y.max()
    elif method == "l2":
        denom = np.linalg.norm(y)
    else:
        denom = y.sum()
    return y / denom if denom > 0 else y


def detect_peaks_2nd_deriv(y: Any, grid: Any, config: dict[str, Any]):
    """Detect Raman peak positions with the second-derivative method."""
    np = _np()
    signal = _signal()
    y = np.asarray(y, dtype=float)
    grid = np.asarray(grid, dtype=float)
    n = len(y)
    if n < 5:
        return np.array([])

    window = int(config.get("peak_smooth_window", 11))
    poly = int(config.get("peak_savgol_poly", 3))
    if window % 2 == 0:
        window += 1
    window = min(window, n if n % 2 == 1 else n - 1)
    window = max(window, poly + 2)
    if window % 2 == 0:
        window += 1
    if window > n:
        return np.array([])

    d2 = signal.savgol_filter(y, window_length=window, polyorder=poly, deriv=2)
    neg = -d2
    noise = np.median(np.abs(np.diff(neg))) * 1.4826 or 1e-9
    prominence = config.get("peak_prominence_sigma", 3.0) * noise
    distance = max(1, int(round(config.get("peak_min_distance_cm", 8.0) / GRID_STEP)))
    idx, _ = signal.find_peaks(neg, prominence=prominence, distance=distance)
    idx = idx[y[idx] > 0]
    return grid[idx]


def preprocess(y: Any, config: dict[str, Any]):
    """Run despike -> denoise -> baseline correction -> normalization once."""
    np = _np()
    out = np.asarray(y, dtype=float)
    if config.get("despike_enabled", True):
        out = despike(out, config["despike_threshold"], config["despike_window"])
    out = denoise(
        out, config["denoise_method"], config["denoise_window"], config["savgol_poly"]
    )
    out = baseline_correct(
        out,
        config["baseline_method"],
        config["baseline_lam"],
        config["baseline_p"],
        config["baseline_poly_order"],
    )
    out = np.clip(out, 0, None)
    return normalise(out, config["normalise_method"])


def save_clean_spectrum(path: str | os.PathLike[str], grid: Any, y: Any) -> None:
    """Persist the one-time cleaned spectrum in the same CSV format as input."""
    save_spectrum_csv(path, grid, y)


def load_clean_spectrum(path: str | os.PathLike[str]):
    """Read back a cleaned spectrum written by ``save_clean_spectrum``."""
    return load_spectrum_csv(path)


def pearson(a: Any, b: Any) -> float:
    np = _np()
    a = np.asarray(a, dtype=float) - np.asarray(a, dtype=float).mean()
    b = np.asarray(b, dtype=float) - np.asarray(b, dtype=float).mean()
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0


def cosine(a: Any, b: Any) -> float:
    np = _np()
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0


def rmse(a: Any, b: Any) -> float:
    np = _np()
    return float(np.sqrt(np.mean((np.asarray(a) - np.asarray(b)) ** 2)))


def sid(a: Any, b: Any) -> float:
    np = _np()
    eps = 1e-12
    p = np.clip(np.asarray(a, dtype=float), eps, None)
    q = np.clip(np.asarray(b, dtype=float), eps, None)
    p = p / p.sum()
    q = q / q.sum()
    return float(np.sum(p * np.log(p / q) + q * np.log(q / p)))


def rank_library(processed: Any, lib: Library, metric: str = "pearson"):
    """Return similarity scores against every reference column."""
    np = _np()
    metric_fn = pearson if metric == "pearson" else cosine
    return np.array(
        [metric_fn(processed, lib.A[:, idx]) for idx in range(len(lib.names))]
    )


def _reference_peaks(lib: Library, config: dict[str, Any]) -> list[Any]:
    key = (
        config.get("peak_smooth_window", 11),
        config.get("peak_savgol_poly", 3),
        config.get("peak_prominence_sigma", 3.0),
        config.get("peak_min_distance_cm", 8.0),
    )
    cache = getattr(lib, "_ref_peak_cache", None)
    if cache is None:
        cache = {}
        setattr(lib, "_ref_peak_cache", cache)
    if key not in cache:
        cache[key] = [
            detect_peaks_2nd_deriv(lib.A[:, idx], lib.grid, config)
            for idx in range(len(lib.names))
        ]
    return cache[key]


def _peak_prefilter(peaks: Any, lib: Library, config: dict[str, Any]):
    np = _np()
    tol = config.get("peak_match_tol_cm", 8.0)
    min_matches = config.get("peak_min_matches", 1)
    ref_peaks = _reference_peaks(lib, config)
    target = np.asarray(peaks, dtype=float)
    allowed = []
    for idx, ref in enumerate(ref_peaks):
        if len(ref) == 0:
            continue
        coincident = int(np.sum([np.min(np.abs(target - peak)) <= tol for peak in ref]))
        if coincident >= min_matches:
            allowed.append(idx)
    return np.array(allowed, dtype=int)


def best_next_component(
    residual: Any,
    peaks: Any,
    lib: Library,
    config: dict[str, Any],
    exclude: Iterable[int] = (),
):
    """Pick the best library component for the current residual."""
    metric_fn = pearson if config["match_metric"] == "pearson" else cosine
    exclude_set = {int(item) for item in exclude}
    pool = None
    if config.get("peak_prefilter_enabled", True) and peaks is not None and len(peaks):
        allowed = _peak_prefilter(peaks, lib, config)
        if len(allowed):
            pool = [int(item) for item in allowed]
    if pool is None:
        pool = list(range(len(lib.names)))
    pool = [idx for idx in pool if idx not in exclude_set]
    if not pool:
        return None, 0.0
    scores = {idx: metric_fn(residual, lib.A[:, idx]) for idx in pool}
    best = max(scores, key=scores.get)
    return int(best), float(scores[best])


def unmix(processed: Any, lib: Library, candidate_idx: Any, config: dict[str, Any]):
    """NNLS fit on selected columns; drop low fractions and refit."""
    np = _np()
    idx = np.asarray(candidate_idx, dtype=int)
    if len(idx) == 0:
        return {}, idx, np.array([])
    coef, _ = _optimize().nnls(lib.A[:, idx], processed)
    total = coef.sum()
    if total <= 0:
        return {}, idx, np.zeros_like(coef)
    fractions = coef / total
    keep = fractions >= config["fraction_threshold"]
    if keep.sum() == 0:
        keep[np.argmax(fractions)] = True
    idx2 = idx[keep]
    coef2, _ = _optimize().nnls(lib.A[:, idx2], processed)
    total2 = coef2.sum() or 1.0
    frac2 = coef2 / total2
    names = [lib.names[int(item)] for item in idx2]
    return {names[i]: float(frac2[i]) for i in range(len(names))}, idx2, coef2


def reconstruct(lib: Library, idx: Any, coef: Any):
    """Reconstruct an area-normalized spectrum from fitted coefficients."""
    np = _np()
    idx = np.asarray(idx, dtype=int)
    if len(idx) == 0:
        return np.zeros(lib.A.shape[0])
    recon = lib.A[:, idx] @ np.asarray(coef, dtype=float)
    total = recon.sum()
    return recon / total if total > 0 else recon


def find_significant_peaks(
    y: Any,
    grid: Any,
    prominence_sigma: float = 4.0,
    abs_floor: float = 0.0,
):
    """Find peaks whose prominence exceeds a robust noise-derived threshold."""
    np = _np()
    y = np.asarray(y, dtype=float)
    noise = np.median(np.abs(np.diff(y))) * 1.4826 or 1e-9
    prominence = max(prominence_sigma * noise, abs_floor)
    peaks, props = _signal().find_peaks(y, prominence=prominence)
    return np.asarray(grid)[peaks], peaks, props.get("prominences", np.array([]))


def supporting_peaks(
    processed_target: Any,
    lib: Library,
    name: str,
    grid: Any,
    tol_cm: float = 10.0,
    max_peaks: int = 8,
) -> list[float]:
    """Return reference peaks for ``name`` that coincide with target peaks."""
    np = _np()
    col = lib.A[:, lib.index(name)]
    ref_pos, _, ref_prom = find_significant_peaks(col, grid, prominence_sigma=3.0)
    tgt_pos, _, _ = find_significant_peaks(processed_target, grid, prominence_sigma=3.0)
    if len(ref_pos) == 0 or len(tgt_pos) == 0:
        return []
    order = np.argsort(ref_prom)[::-1]
    matched = []
    for idx in order:
        peak = ref_pos[idx]
        if np.min(np.abs(tgt_pos - peak)) <= tol_cm:
            matched.append(round(float(peak), 1))
        if len(matched) >= max_peaks:
            break
    return sorted(matched)


def diagnose(processed_target: Any, recon: Any, grid: Any, config: dict[str, Any]):
    """Compute residual diagnostics and reliability labels."""
    np = _np()
    processed_target = np.asarray(processed_target, dtype=float)
    recon = np.asarray(recon, dtype=float)
    residual = processed_target - recon
    residual_rmse = rmse(processed_target, recon)
    fit_corr = pearson(processed_target, recon)
    target_norm = np.linalg.norm(processed_target) or 1e-12
    rel_residual = float(np.linalg.norm(residual) / target_norm)
    pos_res = np.clip(residual, 0, None)
    abs_floor = 0.10 * float(processed_target.max())
    res_peak_pos, _, _ = find_significant_peaks(
        pos_res, grid, prominence_sigma=5.0, abs_floor=abs_floor
    )
    n_res_peaks = len(res_peak_pos)
    denom = np.sum(processed_target**2) or 1e-12
    explained = 1.0 - np.sum(residual**2) / denom
    if fit_corr >= 0.98 and explained >= 0.95 and n_res_peaks <= 1:
        reliability = "high"
    elif fit_corr >= 0.93 and explained >= 0.88 and n_res_peaks <= 3:
        reliability = "moderate"
    else:
        reliability = "low"
    hints = []
    if n_res_peaks >= 1 and fit_corr < 0.97:
        hints.append("possible_missing_component")
    if fit_corr < 0.85:
        hints.append("poor_baseline_or_denoise")
    if explained < 0.7:
        hints.append("low_explained_energy")
    return {
        "residual_rmse": residual_rmse,
        "rel_residual": rel_residual,
        "fit_corr": fit_corr,
        "explained_energy": float(explained),
        "n_residual_peaks": int(n_res_peaks),
        "residual_peak_positions": [
            round(float(peak), 1) for peak in res_peak_pos[:10]
        ],
        "reliability": reliability,
        "hints": hints,
    }


def run_blind_loop(
    clean: Any,
    lib: Library,
    config: dict[str, Any],
    log_path: str | os.PathLike[str] | None = None,
    verbose: bool = True,
) -> LoopOutcome:
    """Run the residual peak-find -> match -> subtract loop on a clean spectrum."""
    np = _np()
    clean = np.asarray(clean, dtype=float)
    grid = lib.grid
    max_components = config["top_k"]
    corr_threshold = config["corr_threshold"]
    min_gain = config.get("greedy_min_gain", 0.01)
    target_norm = float(np.linalg.norm(clean)) or 1e-12

    selected: list[int] = []
    residual = clean.copy()
    prev_relres = 1.0
    history: list[dict[str, Any]] = []
    log_handle = None
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log_handle = Path(log_path).open("w", encoding="utf-8")

    try:
        for step in range(max_components):
            peaks = detect_peaks_2nd_deriv(residual, grid, config)
            if len(peaks) == 0:
                if verbose:
                    print(f"[{step:02d}] no significant residual peaks -> stop")
                break

            best, corr = best_next_component(
                residual, peaks, lib, config, exclude=selected
            )
            if best is None or (corr < corr_threshold and selected):
                if verbose:
                    print(
                        f"[{step:02d}] best corr {corr:.3f} < {corr_threshold} -> stop"
                    )
                break

            trial = selected + [best]
            coef, _ = _optimize().nnls(lib.A[:, trial], clean)
            recon = lib.A[:, trial] @ coef
            relres = float(np.linalg.norm(clean - recon) / target_norm)

            if selected and (prev_relres - relres) < min_gain:
                if verbose:
                    print(
                        f"[{step:02d}] gain {prev_relres - relres:.4f} < {min_gain} -> stop"
                    )
                break

            selected = trial
            residual = clean - recon
            prev_relres = relres
            res_rmse = float(np.sqrt(np.mean((clean - recon) ** 2)))
            record = {
                "step": step,
                "added_component": lib.names[best],
                "match_corr": round(corr, 4),
                "rel_residual": round(relres, 5),
                "residual_rmse": round(res_rmse, 6),
                "n_residual_peaks": int(len(peaks)),
                "residual_peak_positions": [
                    round(float(peak), 1) for peak in peaks[:12]
                ],
                "cumulative_components": [lib.names[idx] for idx in selected],
            }
            history.append(record)
            if log_handle:
                log_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                log_handle.flush()
            if verbose:
                print(
                    f"[{step:02d}]+ add {lib.names[best]:<22} corr={corr:.3f} "
                    f"relres={relres:.4f} peaks={len(peaks)} -> {record['cumulative_components']}"
                )
    finally:
        if log_handle:
            log_handle.close()

    if selected:
        fractions, used_idx, used_coef = unmix(clean, lib, np.array(selected), config)
        recon = reconstruct(lib, used_idx, used_coef)
        candidate_names = [lib.names[idx] for idx in selected]
    else:
        fractions, used_idx, used_coef = {}, np.array([], dtype=int), np.array([])
        recon = np.zeros_like(clean)
        candidate_names = []

    diagnostics = diagnose(clean, recon, grid, config)
    peaks_clean = detect_peaks_2nd_deriv(clean, grid, config)
    support = {name: supporting_peaks(clean, lib, name, grid) for name in fractions}
    result = PipelineResult(
        processed=clean,
        recon=recon,
        candidate_names=candidate_names,
        fractions=fractions,
        used_idx=used_idx,
        used_coef=used_coef,
        diagnostics=diagnostics,
        peaks=peaks_clean,
        support=support,
    )
    return LoopOutcome(best_result=result, history=history)


def make_figures(
    spectrum: Any, clean: Any, lib: Library, outcome: LoopOutcome, fig_dir
):
    """Write the same diagnostic figures as the prototype pipeline."""
    np = _np()
    plt = _plt()
    fig_dir = Path(fig_dir)
    fig_dir.mkdir(parents=True, exist_ok=True)
    result = outcome.best_result
    grid = lib.grid

    fig, (axa, axb) = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
    axa.plot(grid, spectrum, color="gray", lw=0.9)
    axa.set_ylabel("raw intensity (a.u.)")
    axa.set_title("Global preprocessing (done once)")
    axb.plot(grid, clean, color="tab:green", lw=1.0)
    axb.set_ylabel("clean (norm.)")
    axb.set_xlabel("Raman shift (cm$^{-1}$)")
    fig.tight_layout()
    fig.savefig(fig_dir / "preprocess.png", dpi=120)
    plt.close(fig)

    plt.figure(figsize=(10, 4))
    plt.plot(grid, result.processed, label="clean (preprocessed)", lw=1.2)
    plt.plot(grid, result.recon, label="NNLS reconstruction", lw=1.2, alpha=0.8)
    plt.xlabel("Raman shift (cm$^{-1}$)")
    plt.ylabel("norm. intensity")
    plt.title("Clean spectrum vs reconstruction")
    plt.legend()
    plt.tight_layout()
    plt.savefig(fig_dir / "overlay.png", dpi=120)
    plt.close()

    plt.figure(figsize=(10, 3))
    plt.plot(grid, result.processed - result.recon, color="crimson", lw=1.0)
    plt.axhline(0, color="k", lw=0.5)
    plt.xlabel("Raman shift (cm$^{-1}$)")
    plt.ylabel("residual")
    plt.title("Final residual (clean - reconstruction)")
    plt.tight_layout()
    plt.savefig(fig_dir / "residual.png", dpi=120)
    plt.close()

    if outcome.history:
        steps = [item["step"] for item in outcome.history]
        relres = [item["rel_residual"] for item in outcome.history]
        labels = [item["added_component"] for item in outcome.history]
        plt.figure(figsize=(8, 4))
        plt.plot(steps, relres, "o-", color="tab:blue")
        for step, residual, name in zip(steps, relres, labels):
            plt.annotate(
                name,
                (step, residual),
                fontsize=7,
                rotation=30,
                textcoords="offset points",
                xytext=(4, 4),
            )
        plt.xlabel("subtraction step")
        plt.ylabel("relative residual")
        plt.title("Iterative subtraction progress")
        plt.tight_layout()
        plt.savefig(fig_dir / "iterations.png", dpi=120)
        plt.close()

    plt.figure(figsize=(10, 3))
    plt.plot(grid, np.asarray(spectrum, dtype=float), color="gray", lw=0.9)
    plt.xlabel("Raman shift (cm$^{-1}$)")
    plt.ylabel("intensity (a.u.)")
    plt.title("Dirty spectrum (input)")
    plt.tight_layout()
    plt.savefig(fig_dir / "input.png", dpi=120)
    plt.close()


def build_markdown_report(
    outcome: LoopOutcome,
    config: dict[str, Any],
    truth: dict[str, Any] | None = None,
    evaluation: dict[str, float] | None = None,
    include_figures: bool = True,
) -> str:
    """Build the diagnostic Markdown report."""
    result = outcome.best_result
    diagnostics = result.diagnostics
    lines = [
        "# 光谱成分识别诊断报告",
        "",
        "- 流程: 全局预处理(一次) -> 迭代寻峰-匹配-相减 -> 结论诊断"
        + (" -> 真值评估(一次)" if truth is not None else ""),
        "",
        "## 1. 结论：识别到的成分与比例",
        "",
        "| 成分 | 估计比例 | 支持特征峰 (cm^-1) |",
        "|---|---|---|",
    ]
    if result.fractions:
        for name, frac in sorted(result.fractions.items(), key=lambda item: -item[1]):
            peaks = ", ".join(str(peak) for peak in result.support.get(name, [])) or "-"
            lines.append(f"| {name} | {frac * 100:.1f}% | {peaks} |")
    else:
        lines.append("| 未识别到可靠成分 | - | - |")
    lines.extend(
        [
            "",
            "## 2. 可信性诊断",
            "",
            f"- 二阶导数法在干净谱上检出峰: **{len(result.peaks)}** 个"
            + (
                f"（位置 cm^-1: {[round(float(peak), 1) for peak in list(result.peaks)[:20]]}）"
                if len(result.peaks)
                else ""
            ),
            "- 说明: 全局预处理只做一次，干净谱已保存为 `clean_spectrum.csv`；循环从该文件读回后在残差上逐步寻峰-相减。",
            f"- 重构拟合相关 (Pearson): **{diagnostics['fit_corr']:.3f}**",
            f"- 残差 RMSE: **{diagnostics['residual_rmse']:.4f}**",
            f"- 相对残差: **{diagnostics['rel_residual']:.4f}**",
            f"- 解释能量占比: **{diagnostics['explained_energy'] * 100:.1f}%**",
            f"- 残差残留显著峰数: **{diagnostics['n_residual_peaks']}** "
            + (
                "(提示可能漏成分)"
                if diagnostics["n_residual_peaks"]
                else "(无明显未解释峰)"
            ),
        ]
    )
    if diagnostics["residual_peak_positions"]:
        lines.append(f"  - 位置: {diagnostics['residual_peak_positions']}")
    if diagnostics.get("hints"):
        lines.append(f"- 诊断提示: {diagnostics['hints']}")
    lines.extend([f"- 综合可信度: **{diagnostics['reliability'].upper()}**", ""])

    if truth is not None and evaluation is not None:
        lines.extend(
            [
                "## 3. 与真值对比（循环结束后一次性评估）",
                "",
                "> 说明: 以下真值指标仅在迭代相减循环结束后计算一次，循环过程中未使用。",
                "",
                f"- 真实成分: {truth['true_names']}",
                "- 真实比例: {"
                + ", ".join(
                    f"{name!r}: {value:.3f}"
                    for name, value in truth["true_fractions"].items()
                )
                + "}",
                f"- 成分识别 Precision/Recall/F1: {evaluation['precision']:.2f} / "
                f"{evaluation['recall']:.2f} / **{evaluation['f1']:.2f}**",
                f"- 比例估计 MAE: **{evaluation['fraction_mae']:.3f}**",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## 3. 评测状态",
                "",
                "- 未提供 ground truth；本报告只给出盲目谱图库匹配、NNLS 解混和残差诊断。",
                "",
            ]
        )

    lines.extend(
        [
            "## 4. 使用的固定配置",
            "",
            "```json",
            json.dumps(config, ensure_ascii=False, indent=2),
            "```",
            "",
            f"- 相减步数: {len(outcome.history)} | 最大成分数上限: {config['top_k']}",
            "",
            "## 5. 迭代相减过程（每步：残差寻峰 -> 匹配 -> 相减）",
            "",
            "| 步 | 新增成分 | 匹配相关 | rel_residual | 残差峰数 | 累计成分 |",
            "|---|---|---|---|---|---|",
        ]
    )
    for item in outcome.history:
        lines.append(
            f"| {item['step']} | {item['added_component']} | {item['match_corr']:.3f} | "
            f"{item['rel_residual']:.4f} | {item['n_residual_peaks']} | "
            f"{item['cumulative_components']} |"
        )
    if not outcome.history:
        lines.append("| - | 未加入成分 | - | - | - | [] |")
    if include_figures:
        lines.extend(
            [
                "",
                "## 6. 图",
                "",
                "![input](figures/input.png)",
                "",
                "![preprocess](figures/preprocess.png)",
                "",
                "![overlay](figures/overlay.png)",
                "",
                "![residual](figures/residual.png)",
                "",
                "![iterations](figures/iterations.png)",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def write_report(path: str | os.PathLike[str], report: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(report, encoding="utf-8")


def analyze_spectrum_file(
    spectrum_csv_path: str | os.PathLike[str],
    library: Library,
    output_dir: str | os.PathLike[str],
    config: dict[str, Any] | None = None,
    truth_path: str | os.PathLike[str] | None = None,
    make_figure_files: bool = True,
    make_figures: bool | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """Run the full blind analysis pipeline on a spectrum CSV.

    ``truth_path`` is optional and is loaded only after ``run_blind_loop``
    completes. The loop itself receives only the clean spectrum and library.
    """
    np = _np()
    if make_figures is not None:
        make_figure_files = make_figures
    cfg = default_config()
    if config:
        cfg.update(copy.deepcopy(config))

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    grid, spectrum = load_spectrum_csv(spectrum_csv_path)
    if len(spectrum) != len(library.grid) or not np.allclose(grid, library.grid):
        spectrum = resample(grid, spectrum, library.grid)

    clean = preprocess(spectrum, cfg)
    clean_path = output_root / "clean_spectrum.csv"
    save_clean_spectrum(clean_path, library.grid, clean)
    _, clean = load_clean_spectrum(clean_path)

    log_path = output_root / "iterations.jsonl"
    outcome = run_blind_loop(clean, library, cfg, log_path=log_path, verbose=verbose)

    truth = load_truth(truth_path) if truth_path else None
    evaluation = evaluate_against_truth(outcome.best_result, truth) if truth else None

    if make_figure_files:
        make_figures_fn = globals()["make_figures"]
        make_figures_fn(spectrum, clean, library, outcome, output_root / "figures")

    report = build_markdown_report(
        outcome,
        cfg,
        truth=truth,
        evaluation=evaluation,
        include_figures=make_figure_files,
    )
    report_path = output_root / "report.md"
    write_report(report_path, report)
    return {
        "outcome": outcome,
        "config": cfg,
        "truth": truth,
        "evaluation": evaluation,
        "output_dir": str(output_root),
        "clean_spectrum_path": str(clean_path),
        "iterations_path": str(log_path),
        "report_path": str(report_path),
        "report": report,
    }


def component_prf(
    true_names: Iterable[str], pred_names: Iterable[str]
) -> dict[str, float]:
    """Precision/recall/F1 for predicted component names."""
    true_set = set(true_names)
    pred_set = set(pred_names)
    tp = len(true_set & pred_set)
    precision = tp / len(pred_set) if pred_set else 0.0
    recall = tp / len(true_set) if true_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def fraction_mae(true_map: dict[str, float], pred_map: dict[str, float]) -> float:
    """Mean absolute fraction error over the union of true and predicted names."""
    keys = set(true_map) | set(pred_map)
    if not keys:
        return 0.0
    return sum(
        abs(true_map.get(key, 0.0) - pred_map.get(key, 0.0)) for key in keys
    ) / len(keys)


def evaluate_against_truth(
    result: PipelineResult, truth: dict[str, Any]
) -> dict[str, float]:
    """Evaluate a completed blind-loop result against a hidden truth dict."""
    prf = component_prf(truth["true_names"], list(result.fractions))
    mae = fraction_mae(truth["true_fractions"], result.fractions)
    return {**prf, "fraction_mae": mae}


def synth_mixture(
    lib: Library,
    rng: Any,
    n_components: int | None = None,
    noise_level: float = 0.02,
    n_spikes: int = 3,
    baseline_strength: float = 0.5,
    min_fraction: float = 0.15,
) -> SynthCase:
    """Synthesize a dirty mixed-mineral spectrum with hidden ground truth."""
    np = _np()
    grid = lib.grid
    n_min = len(lib.names)
    if n_components is None:
        n_components = int(rng.integers(2, 4))
    idx = rng.choice(n_min, size=n_components, replace=False)
    names = [lib.names[int(item)] for item in idx]
    for _ in range(1000):
        fractions = rng.dirichlet(np.ones(n_components) * 2.0)
        if fractions.min() >= min_fraction:
            break
    true_fractions = {names[i]: float(fractions[i]) for i in range(n_components)}
    clean = lib.A[:, idx] @ fractions
    clean = clean / (clean.max() or 1.0)
    y = clean.copy()
    x = np.linspace(0, 1, len(grid))
    poly = (baseline_strength * (0.3 + 0.7 * rng.random())) * (
        rng.uniform(-1, 1) * x + rng.uniform(-1, 1) * x**2 + 0.5
    )
    center = rng.uniform(0.2, 0.8)
    gauss = baseline_strength * 0.4 * np.exp(-((x - center) ** 2) / (2 * 0.15**2))
    y = y + np.clip(poly, 0, None) + gauss
    y = y + rng.normal(0, noise_level, size=len(grid))
    for _ in range(n_spikes):
        pos = int(rng.integers(0, len(grid)))
        y[pos] += rng.uniform(0.3, 1.0) * y.max()
    y = y * rng.uniform(500, 2000)
    y = np.clip(y, 0, None)
    return SynthCase(
        grid=grid,
        spectrum=y,
        true_names=names,
        true_fractions=true_fractions,
        meta={
            "n_components": n_components,
            "noise_level": noise_level,
            "n_spikes": n_spikes,
            "baseline_strength": baseline_strength,
        },
    )


def save_case(case: SynthCase, case_dir: str | os.PathLike[str]) -> None:
    """Write ``spectrum.csv`` and hidden ``truth.json`` for a synthetic case."""
    case_root = Path(case_dir)
    case_root.mkdir(parents=True, exist_ok=True)
    save_spectrum_csv(case_root / "spectrum.csv", case.grid, case.spectrum)
    truth = {
        "true_names": list(case.true_names),
        "true_fractions": {
            name: float(value) for name, value in case.true_fractions.items()
        },
        "meta": case.meta,
    }
    (case_root / "truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_truth(case_dir_or_path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load a hidden truth file from ``truth.json`` or an explicit JSON path."""
    path = Path(case_dir_or_path)
    if path.is_dir():
        path = path / "truth.json"
    return json.loads(path.read_text(encoding="utf-8"))


def plot_input(case: SynthCase, path: str | os.PathLike[str]) -> None:
    """Save the dirty input spectrum plot for a synthetic case."""
    plt = _plt()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(10, 3))
    plt.plot(case.grid, case.spectrum, color="gray", lw=0.9)
    plt.xlabel("Raman shift (cm$^{-1}$)")
    plt.ylabel("intensity (a.u.)")
    plt.title("Synthetic dirty spectrum (input)")
    plt.tight_layout()
    plt.savefig(path, dpi=120)
    plt.close()


def generate_synthetic_cases(
    lib: Library,
    out_dir: str | os.PathLike[str],
    n: int = 5,
    seed: int = 0,
    n_components: int | None = None,
    noise: float = 0.02,
    make_input_figures: bool = True,
) -> list[str]:
    """Generate optional benchmark cases, separated from blind inference."""
    np = _np()
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    case_dirs = []
    for idx in range(n):
        rng = np.random.default_rng(seed + idx)
        case = synth_mixture(lib, rng, n_components=n_components, noise_level=noise)
        case_dir = root / f"case{idx + 1}"
        save_case(case, case_dir)
        if make_input_figures:
            plot_input(case, case_dir / "input.png")
        case_dirs.append(str(case_dir))
    return case_dirs


def summarize_outcome(outcome: LoopOutcome) -> dict[str, Any]:
    """Return a compact serializable summary of a completed analysis."""
    result = outcome.best_result
    return {
        "fractions": dict(sorted(result.fractions.items(), key=lambda item: -item[1])),
        "candidate_names": list(result.candidate_names),
        "support": result.support,
        "diagnostics": result.diagnostics,
        "history": outcome.history,
    }
