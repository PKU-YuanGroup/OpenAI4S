"""A connector manifest has to describe the connector, not decorate it.

The manifest declares the upstream field paths each science connector depends
on. That declaration is only worth having if it cannot drift from what the
connector actually needs, so two properties are pinned here, both against the
connector's own offline fixtures:

* every required path is really present in the fixture the adapter parses, so
  the manifest cannot claim a field the API does not provide;
* every required path is load-bearing -- delete it from the fixture and the
  adapter stops returning the record. A path that can be removed without
  breaking anything is not required, and this test refuses to let the manifest
  say it is.

The second is the one that matters. Without it, a manifest drifts toward listing
every field anyone might like to have, and a canary built on it alarms on things
that do not matter until someone mutes the canary.
"""
from __future__ import annotations

import copy
import json
import urllib.parse

import pytest

from openai4s.host.connector_manifest import (
    CANARY_IDS,
    EACH,
    MANIFEST_BY_ID,
    MANIFESTS,
    resolve,
)
from openai4s.host.science import DATABASES, ScienceConnectorService

# The same offline fixtures the connector tests use, keyed by host.
from tests.test_science_connectors import ARXIV_XML, RESPONSES

# manifest id -> (database id, probe host, a query that hits the fixture)
_PROBE = {
    "uniprot": ("uniprot", "rest.uniprot.org", "insulin"),
    "pdb": ("pdb", "search.rcsb.org", "hemoglobin"),
    "openalex": ("openalex", "api.openalex.org", "CRISPR"),
    "ensembl": ("ensembl", "rest.ensembl.org", "BRCA2"),
    "chembl": ("chembl", "www.ebi.ac.uk", "aspirin"),
    "pubchem": ("pubchem", "pubchem.ncbi.nlm.nih.gov", "aspirin"),
    "arxiv": ("arxiv", "export.arxiv.org", "electron"),
}


def _fixture(host):
    return copy.deepcopy(RESPONSES[host]) if host in RESPONSES else None


def _service_returning(document):
    body = json.dumps(document)

    def fetch(url, fmt, timeout, max_chars):
        return body

    return ScienceConnectorService(fetch=fetch)


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------


def test_every_science_database_has_a_manifest():
    """A source without a manifest is a source whose upstream can change
    unwatched. The frozen decision asks for all seven."""
    database_ids = {db.id for db in DATABASES}
    manifest_ids = set(MANIFEST_BY_ID)
    assert database_ids == manifest_ids, (
        f"databases without a manifest: {database_ids - manifest_ids}; "
        f"manifests without a database: {manifest_ids - database_ids}"
    )


def test_the_three_named_sources_are_the_canary_set():
    """The frozen decision names UniProt, RCSB PDB and OpenAlex for live
    canaries; nothing else should be quietly reaching the network on a
    schedule."""
    assert set(CANARY_IDS) == {"uniprot", "pdb", "openalex"}


def test_every_probe_query_is_non_empty():
    for manifest in MANIFESTS:
        assert manifest.probe_query.strip(), f"{manifest.id} has no probe query"


# --------------------------------------------------------------------------
# the required paths are present in the fixture
# --------------------------------------------------------------------------


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda m: m.id)
def test_required_paths_exist_in_the_connector_fixture(manifest):
    """The manifest cannot claim a field the upstream (as the connector's own
    fixture represents it) does not provide."""
    host = _PROBE[manifest.id][1]
    fixture = _fixture(host)
    if fixture is None:  # arxiv is XML; no JSON manifest paths to check
        assert manifest.required == ()
        return
    for path in manifest.required:
        assert resolve(
            fixture, path
        ), f"{manifest.id}: required path {path} is absent from its own fixture"


# --------------------------------------------------------------------------
# the required paths are load-bearing -- the property that keeps the manifest honest
# --------------------------------------------------------------------------


def _delete_first(document, path):
    """Remove the leaf reached by ``path`` from the first array element along
    it, mirroring how a real API would drop a renamed field."""
    node = document
    for i, step in enumerate(path):
        if step is EACH:
            if not isinstance(node, list) or not node:
                return document
            node = node[0]
        elif i == len(path) - 1:
            if isinstance(node, dict):
                node.pop(step, None)
        else:
            node = node.get(step) if isinstance(node, dict) else None
            if node is None:
                return document
    return document


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda m: m.id)
def test_every_required_path_is_load_bearing(manifest):
    """Delete each required path from the fixture and the connector must stop
    returning the record. If it still returns one, the path was not required and
    the manifest is over-claiming."""
    database_id, host, query = _PROBE[manifest.id]
    fixture = _fixture(host)
    if fixture is None or not manifest.required:
        pytest.skip("no JSON required paths (arxiv is XML)")

    healthy = _service_returning(fixture).search(database_id, query, limit=5)
    assert healthy[
        "results"
    ], f"{manifest.id} fixture should yield a record to begin with"

    for path in manifest.required:
        broken_doc = _delete_first(copy.deepcopy(fixture), path)
        result = _service_returning(broken_doc).search(database_id, query, limit=5)
        assert not result["results"], (
            f"{manifest.id}: deleting required path {path} still returned a record, "
            "so the path is not actually required"
        )


# --------------------------------------------------------------------------
# resolve()
# --------------------------------------------------------------------------


def test_resolve_fans_out_over_array_elements():
    doc = {"results": [{"id": "a"}, {"id": "b"}, {"no_id": "c"}]}
    assert resolve(doc, ("results", EACH, "id")) == ["a", "b"]


def test_resolve_returns_empty_for_an_absent_path():
    assert resolve({"results": [{"x": 1}]}, ("results", EACH, "id")) == []
    assert resolve({}, ("results",)) == []


def test_a_manifest_reports_a_missing_required_path():
    manifest = MANIFEST_BY_ID["uniprot"]
    healthy = {"results": [{"primaryAccession": "P01308"}]}
    drifted = {"results": [{"accession": "P01308"}]}  # renamed field

    assert manifest.check(healthy)["required"] == []
    assert manifest.check(drifted)["required"] == ["results.[].primaryAccession"]


# --------------------------------------------------------------------------
# a key that survived with a null in it is still a break
# --------------------------------------------------------------------------


def test_a_required_field_emptied_to_null_is_drift():
    """The regression.

    `resolve` returns `[None]` for a key present with a null value, and a
    truthy list read as "present" meant the check passed while every adapter
    dropped every record. It is also the *likelier* shape of a real API change:
    a renamed field usually leaves the old one in place and empty for a
    deprecation window, which is precisely when a canary needs to fire.
    """
    manifest = MANIFEST_BY_ID["uniprot"]
    drift = manifest.check({"results": [{"primaryAccession": None}]})
    assert drift["required"] == ["results.[].primaryAccession"]


def test_a_required_field_emptied_to_an_empty_string_is_drift():
    manifest = MANIFEST_BY_ID["uniprot"]
    drift = manifest.check({"results": [{"primaryAccession": ""}]})
    assert drift["required"] == ["results.[].primaryAccession"]


def test_one_usable_value_among_nulls_is_still_present():
    """Partial nulls are normal in real data; the connector still works."""
    manifest = MANIFEST_BY_ID["uniprot"]
    drift = manifest.check(
        {"results": [{"primaryAccession": None}, {"primaryAccession": "P01308"}]}
    )
    assert drift["required"] == []


def test_a_falsy_but_real_value_is_not_drift():
    """`0` is a score, not an absence. Treating every falsy value as missing
    would alarm on correct data, which is how a canary gets muted."""
    from openai4s.host.connector_manifest import present

    assert present({"result_set": [{"score": 0}]}, ("result_set", EACH, "score"))
    assert present({"result_set": [{"ok": False}]}, ("result_set", EACH, "ok"))


def test_resolve_still_reports_what_the_document_literally_contains():
    """The accessor is not the judge: `[None]` is a true statement about a
    document that has the key with a null in it, and other callers may need
    that distinction."""
    assert resolve(
        {"results": [{"primaryAccession": None}]}, ("results", EACH, "primaryAccession")
    ) == [None]
