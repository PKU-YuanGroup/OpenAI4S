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

There is a third case, and leaving it unnamed is what made the runner crash: the
API answered 200, every *required* path is still there, and the connector still
refused the body -- an optional field changed type, a nested shape moved. That
is neither weather nor a missing field, so it gets its own status,
**parse_error**, rather than falling through code that assumed a parsed result
existed.

There is a fourth: the API answered with a **permanent** client error — a 404
or 410 for a removed route, a 401/403 for an auth or contract change. That is
the contract breaking, not the weather, so it is `http_error` and fails the
run rather than being retried into `unreachable`. 408 and 429 stay transient.

A run's exit code is 0 when nothing drifted (including when a source was merely
unreachable), and non-zero on real drift, a parse error, or a permanent client
error, so a trend gate can key on it without flaking on an upstream outage.
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


#: Client-error statuses that are the *contract* breaking rather than the API's
#: weather. 408 (timeout) and 429 (rate limit) are deliberately excluded —
#: those are transient and belong on the retry path.
_PERMANENT_CLIENT_ERRORS = frozenset({400, 401, 403, 404, 405, 406, 410, 422})


def _permanent_http_status(exc: BaseException) -> int | None:
    """The HTTP status of a permanent client error, or None.

    An upstream route removal reaches here as a ``urllib.error.HTTPError`` (or
    a ``ScienceConnectorError`` wrapping one) whose ``code`` is a 4xx that will
    not resolve on retry. Anything else — a timeout, a DNS failure, a 5xx — is
    transient and returns None so the caller keeps retrying.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "code", None)
        try:
            code_int = int(code)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            code_int = None
        if code_int in _PERMANENT_CLIENT_ERRORS:
            return code_int
        # Walk the whole chain: the connector wraps the transport error, and a
        # transport layer may itself have wrapped the HTTPError, so the code can
        # be more than one `__cause__` deep.
        current = current.__cause__ or current.__context__
    return None


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
        payload = (real_fetch or default_fetch)(url, fmt, timeout, max_chars)
        # A fetch may answer with the content alone or with a mapping that also
        # describes the raw response bytes. Capture the *content*, since that is
        # what the connector parses and therefore what a drift check must look
        # at, but hand the whole payload back so the provenance envelope still
        # gets its raw digest.
        captured["last"] = (
            str(payload.get("content") or "")
            if isinstance(payload, dict)
            else str(payload or "")
        )
        return payload

    last_error = ""
    for attempt in range(_RETRIES):
        captured.clear()
        result: Any = None
        service = ScienceConnectorService(fetch=capturing)
        try:
            result = service.search(
                database,
                manifest.probe_query,
                limit=5,
                filters=manifest.probe_filters or None,
            )
        except ScienceConnectorError as exc:
            # The connector normalises every transport failure into this type,
            # so a permanent client error (a removed route, an auth change)
            # arrives here wrapping the original HTTPError. Classify it before
            # treating an empty capture as a retryable outage.
            code = _permanent_http_status(exc)
            if code is not None:
                return _outcome("http_error", code=code, detail=f"HTTP {code}: {exc}")
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
            code = _permanent_http_status(exc)
            if code is not None:
                # A route that was removed answers 404/410, an auth or contract
                # change 401/403. These are the API's *contract* breaking, not
                # its weather — retrying them and then reporting `unreachable`
                # let the scheduled canary exit 0 while the connector was
                # already broken. Fail the run instead.
                return _outcome("http_error", code=code, detail=f"HTTP {code}: {exc}")
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
        if result is None:
            # 200, every required path present, and the connector still refused
            # the body. Reaching here used to read `result`, which the failure
            # path had never assigned — so the nightly run died with an
            # UnboundLocalError instead of reporting anything at all. This is a
            # real finding and it needs a name of its own: the contract the
            # manifest declares is intact, and something outside it changed.
            return _outcome(
                "parse_error",
                detail=last_error or "the connector rejected a 200 response",
                missing_expected=drift["expected"],
            )
        records = len(result.get("results") or []) if isinstance(result, dict) else 0
        return _outcome("ok", records=records, missing_expected=drift["expected"])

    return _outcome("unreachable", detail=last_error)


def run(ids: tuple[str, ...] = CANARY_IDS, **kwargs: Any) -> dict[str, Any]:
    results = {source: check_source(source, **kwargs) for source in ids}
    drifted = sorted(s for s, r in results.items() if r["status"] == "drift")
    unreachable = sorted(s for s, r in results.items() if r["status"] == "unreachable")
    parse_errors = sorted(s for s, r in results.items() if r["status"] == "parse_error")
    http_errors = sorted(s for s, r in results.items() if r["status"] == "http_error")
    return {
        "results": results,
        "drifted": drifted,
        "unreachable": unreachable,
        "parse_errors": parse_errors,
        "http_errors": http_errors,
    }


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
            elif outcome["status"] in ("unreachable", "parse_error"):
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
        if report["parse_errors"]:
            print(
                f"\nPARSE ERROR in {report['parse_errors']}: a 200 response "
                "carrying every required field that the connector still could "
                "not read. Something outside the manifest changed shape."
            )

        if report.get("http_errors"):
            print(
                f"\nHTTP ERROR in {report['http_errors']}: a permanent client "
                "error (a removed route, an auth/contract change). The "
                "connector's endpoint is broken, not merely unreachable."
            )

    # Non-zero on a real contract problem — drift, a 200 the connector could
    # not read, or a permanent client error like a removed route — so a
    # scheduled trend gate does not flake on an upstream outage but does not
    # stay silent about a break either.
    return (
        1
        if (report["drifted"] or report["parse_errors"] or report.get("http_errors"))
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
