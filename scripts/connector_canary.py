#!/usr/bin/env python3
"""Ask the three named public APIs whether they still return what we parse.

    python scripts/connector_canary.py            # check the live canary sources
    python scripts/connector_canary.py --json     # machine-readable result

This runs on a schedule, never on a pull request. A public API is not our
dependency to gate a contributor's PR on -- it can be down for reasons that have
nothing to do with the change under review, and a required check that fails for
that reason trains people to ignore it.

The one distinction the runner exists to make is between two failures that look
alike from a distance:

* **unreachable** -- the request timed out, the connection failed, or the API
  answered 5xx. That is the API's weather, not a change in its contract, so it
  is retried and, if it persists, reported as `unreachable` without alarm.
* **drift** -- the API answered 200 with a body, and a field the connector
  depends on is not in it. That is the contract changing under us, and it is the
  thing this whole exercise exists to catch.

A run's exit code is 0 when nothing drifted (including when a source was merely
unreachable), and non-zero only on real drift, so a trend gate can key on it
without flaking on an upstream outage.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openai4s.host.connector_manifest import CANARY_IDS, MANIFEST_BY_ID  # noqa: E402

#: A canary source id -> the science database id it queries.
_DATABASE = {"uniprot": "uniprot", "pdb": "pdb", "openalex": "openalex"}

_RETRIES = 3
_BACKOFF_S = 2.0


def _outcome(status: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, **extra}


def check_source(
    manifest_id: str,
    *,
    fetch: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run one source's probe and classify the result.

    `fetch` is injectable so the classification -- the part worth trusting -- is
    tested offline. Left None, it uses the connector's real web fetch, i.e. the
    exact path a user's query takes.
    """
    from openai4s.host.science import ScienceConnectorError, ScienceConnectorService

    manifest = MANIFEST_BY_ID[manifest_id]
    database = _DATABASE[manifest_id]

    # Capture the raw upstream body ourselves rather than reading the provenance
    # envelope: that envelope deliberately keeps only a hash of what came back,
    # not the bytes, so a canary that read `responses[].raw` would find nothing.
    # A wrapping fetch also means the canary parses exactly what the connector
    # was handed, and the wrapper is what makes this offline-testable.
    real_fetch = fetch

    def default_fetch(url, fmt, timeout, max_chars):
        return ScienceConnectorService()._default_fetch(url, fmt, timeout, max_chars)

    captured: dict[str, str] = {}

    def capturing(url, fmt, timeout, max_chars):
        body = (real_fetch or default_fetch)(url, fmt, timeout, max_chars)
        captured["last"] = body
        return body

    last_error = ""
    for attempt in range(_RETRIES):
        captured.clear()
        service = ScienceConnectorService(fetch=capturing)
        try:
            result = service.search(
                database,
                manifest.probe_query,
                limit=5,
                filters=manifest.probe_filters or None,
            )
        except ScienceConnectorError as exc:
            # The connector could not trust the response. If we captured a body,
            # the API answered 200 with something we could not parse -- inspect
            # it for real drift below. If we captured nothing, the request never
            # completed: an outage, retried.
            last_error = str(exc)
            if "last" not in captured:
                if attempt < _RETRIES - 1:
                    sleep(_BACKOFF_S * (attempt + 1))
                continue
        except Exception as exc:  # noqa: BLE001 - transport/timeout/DNS
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < _RETRIES - 1:
                sleep(_BACKOFF_S * (attempt + 1))
            continue

        raw = captured.get("last")
        if raw is None:
            last_error = last_error or "no upstream response captured"
            if attempt < _RETRIES - 1:
                sleep(_BACKOFF_S * (attempt + 1))
            continue
        try:
            upstream = json.loads(raw)
        except (TypeError, ValueError):
            # A 200 that is not JSON (an HTML error/maintenance page) is the
            # API's weather, not a contract change.
            return _outcome("unreachable", detail="upstream body was not JSON")

        drift = manifest.check(upstream)
        if drift["required"]:
            return _outcome(
                "drift",
                missing_required=drift["required"],
                missing_expected=drift["expected"],
            )
        records = len(result.get("results") or []) if isinstance(result, dict) else 0
        return _outcome("ok", records=records, missing_expected=drift["expected"])

    return _outcome("unreachable", detail=last_error)


def run(ids: tuple[str, ...] = CANARY_IDS, **kwargs: Any) -> dict[str, Any]:
    results = {source: check_source(source, **kwargs) for source in ids}
    drifted = sorted(s for s, r in results.items() if r["status"] == "drift")
    unreachable = sorted(s for s, r in results.items() if r["status"] == "unreachable")
    return {"results": results, "drifted": drifted, "unreachable": unreachable}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    report = run()

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for source, outcome in sorted(report["results"].items()):
            line = f"  {source:10} {outcome['status']}"
            if outcome["status"] == "drift":
                line += f"  missing required: {outcome['missing_required']}"
            elif outcome["status"] == "ok":
                line += f"  ({outcome['records']} records)"
                if outcome.get("missing_expected"):
                    line += f"  degraded: {outcome['missing_expected']}"
            elif outcome["status"] == "unreachable":
                line += f"  ({outcome.get('detail', '')})"
            print(line)
        if report["unreachable"]:
            print(
                f"\n{len(report['unreachable'])} source(s) unreachable — an "
                "upstream outage, not drift; not failing the run."
            )
        if report["drifted"]:
            print(
                f"\nDRIFT in {report['drifted']}: a field the connector depends "
                "on is gone from a 200 response. The parser needs updating."
            )

    # Non-zero only on real drift, so a scheduled trend gate does not flake on an
    # upstream outage.
    return 1 if report["drifted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
