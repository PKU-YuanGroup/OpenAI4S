"""The canary's one job is to tell an outage apart from a contract change.

A public API is not our dependency to gate a PR on, so the canary runs on a
schedule and its exit code is non-zero only on real drift. The value of the
whole thing rests on one distinction:

* the API is down or slow (a timeout, a 5xx, an HTML maintenance page) -- the
  API's weather, retried, reported as `unreachable`, never alarmed;
* the API answered 200 and a field the connector parses is gone -- the contract
  changing under us, which is `drift` and the reason the canary exists.

Getting that backwards in either direction is the failure mode. Alarm on an
outage and the canary gets muted; stay quiet on drift and it was pointless. So
these tests drive the classifier with an injected fetch -- no network -- through
both, and a couple of ways an outage can disguise itself as drift.
"""
from __future__ import annotations

import json

import pytest

from scripts import connector_canary as canary

_HEALTHY_UNIPROT = {
    "results": [
        {
            "primaryAccession": "P01308",
            "proteinDescription": {
                "recommendedName": {"fullName": {"value": "Insulin"}}
            },
            "organism": {"scientificName": "Homo sapiens"},
        }
    ]
}

# A 200 whose identifier field was renamed -- the exact shape of real drift.
_DRIFTED_UNIPROT = {
    "results": [
        {
            "accession": "P01308",  # was primaryAccession
            "proteinDescription": {
                "recommendedName": {"fullName": {"value": "Insulin"}}
            },
        }
    ]
}


def _fetch_returning(document):
    body = json.dumps(document)
    return lambda url, fmt, timeout, max_chars: body


def _no_sleep(_seconds):
    return None


# --------------------------------------------------------------------------
# the two outcomes that matter
# --------------------------------------------------------------------------


def test_a_healthy_api_reads_as_ok():
    outcome = canary.check_source(
        "uniprot", fetch=_fetch_returning(_HEALTHY_UNIPROT), sleep=_no_sleep
    )
    assert outcome["status"] == "ok"
    assert outcome["records"] == 1
    assert outcome["missing_expected"] == []


def test_a_renamed_required_field_reads_as_drift():
    """The case the canary exists for. A 200 body missing primaryAccession is
    the contract changing, not an outage."""
    outcome = canary.check_source(
        "uniprot", fetch=_fetch_returning(_DRIFTED_UNIPROT), sleep=_no_sleep
    )
    assert outcome["status"] == "drift"
    assert "results.[].primaryAccession" in outcome["missing_required"]


def test_a_missing_expected_field_is_noted_but_not_drift():
    """Losing a title degrades quality; it does not make the source drift, so it
    must not fail the run."""
    degraded = {"results": [{"primaryAccession": "P01308"}]}  # no proteinDescription
    outcome = canary.check_source(
        "uniprot", fetch=_fetch_returning(degraded), sleep=_no_sleep
    )
    assert outcome["status"] == "ok"
    assert outcome["missing_expected"]  # the title path is noted


# --------------------------------------------------------------------------
# an outage must never read as drift
# --------------------------------------------------------------------------


def test_a_transport_failure_reads_as_unreachable_not_drift():
    def boom(url, fmt, timeout, max_chars):
        raise TimeoutError("connection timed out")

    outcome = canary.check_source("uniprot", fetch=boom, sleep=_no_sleep)
    assert outcome["status"] == "unreachable"


def test_a_200_that_is_not_json_reads_as_unreachable():
    """An HTML maintenance page served with 200 is the API's weather, not a
    contract change, and must not be reported as drift."""

    def html(url, fmt, timeout, max_chars):
        return "<html>down for maintenance</html>"

    outcome = canary.check_source("uniprot", fetch=html, sleep=_no_sleep)
    assert outcome["status"] == "unreachable"


def test_a_transient_failure_is_retried_then_succeeds():
    calls = {"n": 0}

    def flaky(url, fmt, timeout, max_chars):
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("reset")
        return json.dumps(_HEALTHY_UNIPROT)

    outcome = canary.check_source("uniprot", fetch=flaky, sleep=_no_sleep)
    assert outcome["status"] == "ok"
    assert calls["n"] == 2


def test_persistent_failure_gives_up_as_unreachable():
    calls = {"n": 0}

    def always_down(url, fmt, timeout, max_chars):
        calls["n"] += 1
        raise ConnectionError("reset")

    outcome = canary.check_source("uniprot", fetch=always_down, sleep=_no_sleep)
    assert outcome["status"] == "unreachable"
    assert calls["n"] == canary._RETRIES  # it really retried, did not give up early


# --------------------------------------------------------------------------
# the run, and the exit contract a trend gate keys on
# --------------------------------------------------------------------------


def test_a_run_reports_each_source_and_separates_drift_from_outage():
    def fetch(url, fmt, timeout, max_chars):
        if "uniprot" in url:
            return json.dumps(_DRIFTED_UNIPROT)
        raise TimeoutError("down")

    report = canary.run(("uniprot", "pdb"), fetch=fetch, sleep=_no_sleep)

    assert report["drifted"] == ["uniprot"]
    assert report["unreachable"] == ["pdb"]


def test_only_drift_is_a_nonzero_exit(monkeypatch):
    """The property a scheduled trend gate depends on: an upstream outage does
    not fail the run, only a real contract change does."""
    # all unreachable -> exit 0
    monkeypatch.setattr(
        canary,
        "run",
        lambda *a, **k: {"results": {}, "drifted": [], "unreachable": ["uniprot"]},
    )
    monkeypatch.setattr("sys.argv", ["connector_canary.py"])
    assert canary.main() == 0

    # a drift -> exit 1
    monkeypatch.setattr(
        canary,
        "run",
        lambda *a, **k: {"results": {}, "drifted": ["uniprot"], "unreachable": []},
    )
    assert canary.main() == 1
