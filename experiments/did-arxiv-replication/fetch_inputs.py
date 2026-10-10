"""Explicit online acquisition/conversion; normal experiment runs are offline."""

from __future__ import annotations

import hashlib
import json
import platform
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCES = {
    "mpdta.rda": ("bcallaway11/did", "74b88eb07f1faa644df1271055a0f77848f6550c"),
    "base_stagg.RData": ("lrberge/fixest", "789f888416e97673d154c840eec2d99101fc07b1"),
}


def main() -> None:
    try:
        import pandas
        import pyreadr
    except ImportError as exc:
        raise RuntimeError(
            "Acquisition needs pyreadr and pandas; analysis uses the saved CSVs."
        ) from exc
    inputs = ROOT / "inputs"
    inputs.mkdir(exist_ok=True)
    records = {}
    for filename, (repository, commit) in SOURCES.items():
        url = f"https://raw.githubusercontent.com/{repository}/{commit}/data/{filename}"
        binary = inputs / filename
        if not binary.exists():
            with urllib.request.urlopen(url, timeout=60) as response:
                binary.write_bytes(response.read())
        objects = pyreadr.read_r(str(binary))
        if len(objects) != 1:
            raise ValueError("Expected exactly one public data frame")
        name, frame = next(iter(objects.items()))
        csv_path = inputs / f"{name}.csv"
        csv_bytes = frame.to_csv(index=False, float_format="%.17g").encode("utf-8")
        if csv_path.exists() and csv_path.read_bytes() != csv_bytes:
            raise ValueError("Existing CSV differs from lossless 17-digit conversion")
        if not csv_path.exists():
            csv_path.write_bytes(csv_bytes)
        records[name] = {
            "url": url,
            "repository": repository,
            "commit": commit,
            "license": "GPL-3; upstream package DESCRIPTION",
            "binary_filename": filename,
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "csv_filename": csv_path.name,
            "csv_sha256": hashlib.sha256(csv_bytes).hexdigest(),
            "rows": len(frame),
            "columns": list(frame.columns),
            "conversion": "pyreadr.read_r -> pandas.to_csv(index=False,float_format='%.17g'); no filtering, rounding, imputation or row sorting",
        }
    license_path = inputs / "LICENSE-GPL-3.txt"
    if not license_path.exists():
        with urllib.request.urlopen(
            "https://www.gnu.org/licenses/gpl-3.0.txt", timeout=60
        ) as response:
            license_path.write_bytes(response.read())
    record = {
        "acquisition_date_utc": "2026-10-05",
        "datasets": records,
        "conversion_runtime": {
            "python": platform.python_version(),
            "pyreadr": pyreadr.__version__,
            "pandas": pandas.__version__,
        },
        "license_url": "https://www.gnu.org/licenses/gpl-3.0.txt",
        "papers": [
            {
                "title": "Difference-in-Differences with Multiple Time Periods",
                "authors": "Brantly Callaway; Pedro H. C. Sant'Anna",
                "arxiv": "https://arxiv.org/abs/1803.09015v4",
                "replication_scope": "Unconditional point estimates in authors' official did worked example on a subset, not the full paper's empirical application or bootstrap inference.",
            },
            {
                "title": "Estimating Dynamic Treatment Effects in Event Studies with Heterogeneous Treatment Effects",
                "authors": "Liyang Sun; Sarah Abraham",
                "arxiv": "https://arxiv.org/abs/1804.05785v2",
                "replication_scope": "Section 4 interaction-weighted estimator on fixest's official simulation example; not the paper's empirical application.",
            },
        ],
    }
    output = inputs / "sources.json"
    if output.exists():
        if json.loads(output.read_text()) != record:
            raise ValueError(
                "Provenance already exists with different metadata; preserve the original acquisition record"
            )
    else:
        output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
