"""One artifact, four retrievals, four envelopes.

The reference walkthrough writes the responses of four independent UniProt
searches into a single raw artifact and attached ``records[0]["provenance"]``
to it. That envelope describes exactly one request — so three of the four
accessions sat inside a file whose whole purpose is to preserve their evidence
while carrying no request URL, no retrieval time and no response hash for them.
The artifact *looked* provenanced, which is worse than carrying nothing: a
reader has no way to tell which quarter is accounted for.

The round-trip below goes through the real repository rather than asserting on
the document, because "the skill says to pass all four" and "all four survive
into the record" are different claims.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from openai4s.config import Config
from openai4s.store import get_store

SKILL = (
    Path(__file__).resolve().parents[1] / "skills" / "evidence-walkthrough" / "SKILL.md"
)

ACCESSIONS = ["P69905", "P68871", "P02042", "P02100"]


def _envelope(accession: str) -> dict:
    """The shape `host.science.search` really returns, minus the payload."""
    return {
        "database": "uniprot",
        "source": "UniProtKB",
        "retrieved_at": "2026-07-23T00:00:00Z",
        "request_url": ("https://rest.uniprot.org/uniprotkb/search?query=" + accession),
        "query": accession,
        "filters": {},
        "normalization_version": 1,
        "response_sha256": "0" * 64,
    }


@pytest.fixture
def store(tmp_path):
    return get_store(Config(data_dir=tmp_path).db_path)


# --------------------------------------------------------------------------
# the walkthrough itself
# --------------------------------------------------------------------------


def test_the_walkthrough_does_not_attach_one_envelope_for_four_requests():
    text = SKILL.read_text("utf-8")
    assert 'source=records[0]["provenance"]' not in text, (
        "the raw artifact holds four retrievals; one envelope leaves three "
        "accessions unaccounted for inside a file that claims to be their "
        "evidence"
    )
    assert "for record in records" in text
    assert '"sources"' in text


def test_the_walkthrough_checks_every_accession_before_it_saves():
    text = SKILL.read_text("utf-8")
    assert "request_url" in text and "response_sha256" in text
    assert "retrieved_at" in text
    assert re.search(
        r"assert .*missing", text
    ), "the walkthrough must verify the coverage it claims, not describe it"


# --------------------------------------------------------------------------
# and the record really holds all four
# --------------------------------------------------------------------------


def test_every_accession_is_traceable_from_the_stored_artifact(store, tmp_path):
    raw = tmp_path / "raw_uniprot.json"
    raw.write_text("[]", encoding="utf-8")
    frame_id = store.new_frame(kind="turn")

    aggregate = {
        "kind": "aggregate",
        "database": "uniprot",
        "queries": ACCESSIONS,
        "sources": [_envelope(accession) for accession in ACCESSIONS],
    }
    record = store.record_cell_artifact(
        path=str(raw),
        filename="raw_uniprot.json",
        content_type="application/json",
        size_bytes=2,
        checksum="a" * 64,
        producing_cell_id=None,
        frame_id=frame_id,
        snapshot_path=str(raw),
        input_version_ids=[],
        source=aggregate,
    )

    stored = store.version_meta(record["version_id"]) or {}
    attached = stored.get("source")
    assert attached, "the aggregate envelope did not survive into the record"
    if isinstance(attached, str):
        attached = json.loads(attached)

    envelopes = attached["sources"]
    assert len(envelopes) == len(ACCESSIONS)
    for accession in ACCESSIONS:
        matches = [e for e in envelopes if accession in e["request_url"]]
        assert matches, f"{accession} has no retrieval provenance attached"
        envelope = matches[0]
        assert envelope["retrieved_at"], accession
        assert envelope["response_sha256"], accession


def test_a_single_envelope_cannot_account_for_four_accessions(store, tmp_path):
    """The control. Without it, the test above passes on the broken form too if
    the loop that builds the list is ever quietly reduced to one element."""
    raw = tmp_path / "raw_uniprot.json"
    raw.write_text("[]", encoding="utf-8")
    frame_id = store.new_frame(kind="turn")

    record = store.record_cell_artifact(
        path=str(raw),
        filename="raw_uniprot.json",
        content_type="application/json",
        size_bytes=2,
        checksum="b" * 64,
        producing_cell_id=None,
        frame_id=frame_id,
        snapshot_path=str(raw),
        input_version_ids=[],
        source=_envelope(ACCESSIONS[0]),
    )
    stored = store.version_meta(record["version_id"]) or {}
    attached = stored.get("source")
    if isinstance(attached, str):
        attached = json.loads(attached)

    covered = [a for a in ACCESSIONS if a in json.dumps(attached)]
    assert covered == [
        ACCESSIONS[0]
    ], "the old form really does account for exactly one of the four"
