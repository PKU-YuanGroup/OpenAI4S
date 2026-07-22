"""Retrieved data has to say where it came from and when.

The Evidence scorecard row asks every release-grade artifact to carry a
`source`. There was no such column, and the retrieval layer produced nothing
to put in one: a `science_search` result named the database and the request
URL but not when it was fetched, nor what the upstream actually returned. The
clause was structurally unmeetable rather than sparsely met.

The two questions an envelope has to answer, and neither could be:

  * **When was this true?** A database is a moving target. A result with no
    timestamp cannot be compared against the same query run later, so
    "the data changed" and "the analysis changed" are indistinguishable.
  * **Was it the same bytes?** Without a hash of what came back, a rerun that
    silently returns something different reads exactly like a rerun that
    returns the same thing.
"""
from __future__ import annotations

import json

import pytest

from openai4s.config import Config
from openai4s.host.science import NORMALIZATION_VERSION, ScienceConnectorService
from openai4s.store import get_store

_UNIPROT = {
    "results": [
        {
            "primaryAccession": "P69905",
            "proteinDescription": {
                "recommendedName": {"fullName": {"value": "Hemoglobin subunit alpha"}}
            },
            "organism": {"scientificName": "Homo sapiens", "taxonId": 9606},
            "sequence": {"length": 142},
        }
    ]
}


def _service(payload=None, *, calls=None):
    """A connector over a deterministic upstream, so this stays offline."""
    body = json.dumps(payload if payload is not None else _UNIPROT)

    def fetch(url, _fmt, _timeout, _max_chars):
        if calls is not None:
            calls.append(url)
        return body

    return ScienceConnectorService(fetch=fetch)


@pytest.fixture
def store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


# --------------------------------------------------------------------------
# the envelope
# --------------------------------------------------------------------------


def test_a_search_reports_when_it_was_retrieved():
    """A database is a moving target; without this, a result that changed and
    an analysis that changed look the same."""
    provenance = _service().search("uniprot", "hemoglobin", limit=1)["provenance"]

    assert provenance["retrieved_at"] > 0
    assert provenance["database"] == "uniprot"
    assert provenance["source"] == "UniProtKB"
    assert provenance["query"] == "hemoglobin"
    assert provenance["request_url"].startswith("https://")


def test_the_envelope_hashes_what_upstream_actually_returned():
    provenance = _service().search("uniprot", "hemoglobin", limit=1)["provenance"]

    assert len(provenance["response_sha256"]) == 64
    assert provenance["responses"], "the individual responses are kept too"
    assert provenance["responses"][0]["bytes"] > 0


def test_identical_upstream_bytes_hash_identically():
    """Or the hash cannot answer "is this the same data"."""
    first = _service().search("uniprot", "x", limit=1)["provenance"]
    second = _service().search("uniprot", "x", limit=1)["provenance"]
    assert first["response_sha256"] == second["response_sha256"]


def test_changed_upstream_bytes_change_the_hash():
    """The case the hash exists for: a rerun that quietly returns something
    else must not look like a rerun that returns the same thing."""
    before = _service().search("uniprot", "x", limit=1)["provenance"]
    after = _service({"results": [{"primaryAccession": "P68871"}]}).search(
        "uniprot", "x", limit=1
    )["provenance"]
    assert before["response_sha256"] != after["response_sha256"]


def test_the_normalization_version_travels_with_the_record():
    """Same upstream bytes shaped by a different normalizer is not the same
    evidence, and a reader has no other way to tell."""
    provenance = _service().search("uniprot", "x", limit=1)["provenance"]
    assert provenance["normalization_version"] == NORMALIZATION_VERSION


def test_every_upstream_request_is_recorded_not_only_the_last():
    """A result assembled from more than one request is reproducible only if
    all of them are named, so the envelope keeps each in the order made."""
    calls: list[str] = []
    service = _service(calls=calls)
    envelope = service.search("uniprot", "hemoglobin", limit=1)["provenance"]

    assert len(envelope["responses"]) == len(
        calls
    ), "every request the search made must appear in its provenance"
    assert [item["url"] for item in envelope["responses"]] == calls


def test_a_multi_response_digest_depends_on_their_order():
    """Two requests made the other way round is a different retrieval, and
    hashing them the same would call it identical evidence."""
    from openai4s.host.science import _combined_digest

    a = {"sha256": "aaa"}
    b = {"sha256": "bbb"}
    assert _combined_digest([a, b]) != _combined_digest([b, a])
    assert _combined_digest([a]) == "aaa"
    assert _combined_digest([]) is None


def test_one_search_never_inherits_another_s_provenance():
    """The buffer is per-call; leaking across searches would attribute one
    query's evidence to another."""
    service = _service()
    first = service.search("uniprot", "a", limit=1)["provenance"]
    second = service.search("uniprot", "b", limit=1)["provenance"]

    assert len(first["responses"]) == len(second["responses"])
    assert second["query"] == "b"


def test_the_filters_that_shaped_the_query_are_recorded():
    """`organism_id=9606` is part of what was asked; a request URL alone does
    not survive a normalizer change."""
    provenance = _service().search(
        "uniprot", "hemoglobin", limit=1, filters={"organism_id": "9606"}
    )["provenance"]
    assert provenance["filters"] == {"organism_id": "9606"}


# --------------------------------------------------------------------------
# it reaches the artifact
# --------------------------------------------------------------------------


def test_an_artifact_records_the_retrieval_it_was_derived_from(store, tmp_path):
    """The scorecard clause. Before this there was no column to put it in."""
    result = _service().search("uniprot", "hemoglobin", limit=1)
    project = store.create_project(name="p")["project_id"]
    root = store.new_frame(project_id=project, kind="turn", status="done")
    path = tmp_path / "analysis.csv"
    path.write_text("accession\nP69905\n", encoding="utf-8")

    meta = store.save_artifact(
        path=str(path),
        filename="analysis.csv",
        content_type="text/csv",
        size_bytes=path.stat().st_size,
        checksum="abc",
        frame_id=root,
        root_frame_id=root,
        project_id=project,
        source=result["provenance"],
    )

    stored = json.loads(store.version_meta(meta["version_id"])["source"])
    assert stored["source"] == "UniProtKB"
    assert stored["retrieved_at"] == result["provenance"]["retrieved_at"]
    assert stored["response_sha256"] == result["provenance"]["response_sha256"]


def test_an_artifact_without_a_retrieval_stores_nothing_rather_than_empty(
    store, tmp_path
):
    """A computed-from-nothing artifact has no source, and an empty envelope
    would read as "retrieved from somewhere unrecorded"."""
    project = store.create_project(name="p")["project_id"]
    root = store.new_frame(project_id=project, kind="turn", status="done")
    path = tmp_path / "plot.png"
    path.write_bytes(b"png")

    meta = store.save_artifact(
        path=str(path),
        filename="plot.png",
        content_type="image/png",
        size_bytes=3,
        checksum="abc",
        frame_id=root,
        root_frame_id=root,
        project_id=project,
    )
    assert store.version_meta(meta["version_id"])["source"] is None


def test_the_stored_envelope_is_canonical_so_two_can_be_compared(store, tmp_path):
    """Two versions derived from the same retrieval must compare equal as
    text; key ordering is not a difference in evidence."""
    result = _service().search("uniprot", "x", limit=1)
    project = store.create_project(name="p")["project_id"]
    root = store.new_frame(project_id=project, kind="turn", status="done")

    stored = []
    for index, envelope in enumerate(
        (result["provenance"], dict(reversed(list(result["provenance"].items()))))
    ):
        path = tmp_path / f"out{index}.csv"
        path.write_text("x\n", encoding="utf-8")
        meta = store.save_artifact(
            path=str(path),
            filename=f"out{index}.csv",
            content_type="text/csv",
            size_bytes=2,
            checksum=f"c{index}",
            frame_id=root,
            root_frame_id=root,
            project_id=project,
            source=envelope,
        )
        stored.append(store.version_meta(meta["version_id"])["source"])

    assert stored[0] == stored[1]
