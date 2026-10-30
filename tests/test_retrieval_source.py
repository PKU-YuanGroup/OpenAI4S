"""What a client may see of where retrieved data came from.

`artifact_versions.source` has always recorded the request URL, the query and
the response hashes, and has never been sent anywhere. It should be — "this
figure is built on data fetched at 14:02, here is the hash of what came back"
is the difference between a plot and a result — but not as it stands, because
the envelope is written by whatever code performed the retrieval, including a
skill nobody has audited.
"""

from __future__ import annotations

import json

import pytest

from openai4s.server.retrieval_source import (
    ALIAS_CONFLICTS_KEY,
    ALLOWED_FIELDS,
    LEGACY_FIELDS_KEY,
    MAX_VALUE_CHARS,
    RESPONSE_HASH_SHOWN,
    RESPONSE_HASH_STATE_KEY,
    public_source,
)

#: Credential-shaped enough for `_looks_opaque` to catch it, without being
#: shaped like any real provider's key. The obvious `sk-...` spelling is what I
#: reached for first, twice, and `source_secret_scan.py` refused it both times
#: — correctly: a scanner that made an exception for test files would be a
#: scanner with a hole exactly where people paste real keys "just to check".
SECRET = "Zx9Qw3Er7Ty1Ui5Op2As6Df4Gh8Jk0Lm"


def test_a_credential_in_the_query_string_never_reaches_the_client():
    """The attack this exists for.

    Plenty of scientific APIs take the key as a query parameter. Rendering the
    request URL raw would publish it into the UI and into every stored frame
    that quotes the panel. The parameter *name* is kept, because "which
    parameters were sent" is provenance; the value is the secret.
    """
    out = public_source(
        {
            "database": "UniProt",
            "request_url": f"https://api.example.org/search?q=NIF3&api_key={SECRET}",
        }
    )
    rendered = json.dumps(out)
    assert SECRET not in rendered
    assert "api_key" in out["request_url"], "the parameter name was dropped too"
    assert "redacted" in out["request_url"]


def test_a_credential_outside_a_query_parameter_is_still_caught():
    """A key can sit in the path or in userinfo, where no parameter name
    announces it. The whole URL goes through the text scan for that reason —
    the value-level check alone reads a long URL as "not opaque"."""
    for url in (
        f"https://api.example.org/v1/{SECRET}/records",
        f"https://user:{SECRET}@api.example.org/records",
    ):
        out = public_source({"request_url": url})
        assert SECRET not in json.dumps(out), url


def test_only_allowlisted_fields_are_rendered_and_the_rest_are_counted():
    """The envelope is free-form JSON written by retrieval code that is not
    required to know this panel exists. An allowlist is the only version of
    "show the provenance" that stays true as those callers change.

    What was dropped is *counted*, not listed: the key names themselves come
    from unaudited code and are not safe to render either.
    """
    out = public_source(
        {
            "database": "UniProt",
            "internal_cursor": "opaque",
            "debug_headers": {"Authorization": f"Bearer {SECRET}"},
        }
    )
    assert set(out) <= set(ALLOWED_FIELDS) | {
        "truncated_fields",
        "undisclosed_field_count",
    }
    assert out["undisclosed_field_count"] == 2
    assert "internal_cursor" not in json.dumps(out)
    assert SECRET not in json.dumps(out)


def test_a_long_value_is_clipped_and_says_which_field_was_clipped():
    """A query can be a large POST body. Cutting silently would render a
    shortened URL as if it were the request, which is worse than showing
    nothing at all."""
    out = public_source({"query": "x" * (MAX_VALUE_CHARS * 3)})
    assert len(out["query"]) == MAX_VALUE_CHARS
    assert out["truncated_fields"] == ["query"]


def test_nothing_to_show_is_none_rather_than_an_empty_panel():
    """Most artifacts are computed, not retrieved. An empty panel saying "no
    provenance" reads as a finding about the data; absence of a panel does
    not."""
    assert public_source({}) is None
    assert public_source(None) is None
    assert public_source("not json") is None
    assert public_source({"internal_only": "x"}) is None


def test_a_json_envelope_stored_as_text_is_accepted():
    """The column holds TEXT, so the value arrives as a string on some paths
    and as a dict on others. Both have to work, or the panel is empty exactly
    where the data is real."""
    out = public_source(json.dumps({"database": "RCSB", "record_count": 3}))
    assert out["database"] == "RCSB" and out["record_count"] == 3


def test_numeric_fields_survive_as_numbers():
    out = public_source({"record_count": 42})
    assert out["record_count"] == 42 and isinstance(out["record_count"], int)


def test_dataset_projection_preserves_unknowns_and_distinct_hash_scopes():
    import hashlib

    file_hash = hashlib.sha256(b"measured data").hexdigest()
    metadata_hash = hashlib.sha256(b"source record response").hexdigest()
    out = public_source(
        {
            "database": "zenodo",
            "response_sha256": metadata_hash,
            "dataset": {
                "record_id": "123",
                "record_doi": "10.5281/zenodo.123",
                "concept_doi": "10.5281/zenodo.122",
                "declared_license": None,
                "declared_size_bytes": 0,
                "downloaded_bytes": 0,
                "file_key": " spectrum.csv ",
                "local_sha256": file_hash,
            },
        },
        artifact_sha256=file_hash,
    )
    # The metadata-response digest is shown as written, and it stays a
    # different hash from the downloaded file's own local SHA-256.
    assert out["response_sha256"] == metadata_hash
    assert out["dataset"]["local_sha256"] == file_hash
    assert out["dataset"]["record_doi"] != out["dataset"]["concept_doi"]
    assert out["dataset"]["declared_license"] is None
    assert out["dataset"]["declared_size_bytes"] == 0
    assert out["dataset"]["file_key"] == " spectrum.csv "


def test_hash_shaped_source_value_needs_the_same_version_checksum():
    import hashlib

    claimed = hashlib.sha256(b"untrusted source field").hexdigest()
    actual = hashlib.sha256(b"actual artifact").hexdigest()
    for known in (None, actual):
        out = public_source(
            {"dataset": {"local_sha256": claimed}}, artifact_sha256=known
        )
        assert claimed not in json.dumps(out)
        assert "redacted" in out["dataset"]["local_sha256"]


def test_dataset_projection_never_serializes_private_or_complex_fields():
    out = public_source(
        {
            "dataset": {
                "record_id": "123",
                "record_url": f"https://example.org/123?api_key={SECRET}",
                "title": {"Authorization": f"Bearer {SECRET}"},
                "path": "/private/workspace/input.csv",
                "headers": {"Authorization": SECRET},
                "declared_size_bytes": True,
                "downloaded_bytes": float("inf"),
            }
        }
    )
    rendered = json.dumps(out)
    assert SECRET not in rendered and "/private/workspace" not in rendered
    assert out["dataset"]["redacted_fields"] == ["record_url"]
    assert "headers" not in rendered and "Authorization" not in rendered
    assert "declared_size_bytes" not in out["dataset"]
    assert "downloaded_bytes" not in out["dataset"]
    assert out["dataset"]["undisclosed_field_count"] == 5


@pytest.mark.stubbed_backend
def test_the_real_import_envelope_shows_every_declared_identity():
    """Project what `science_import_dataset` actually records, not a hand copy.

    Hand-written dataset dicts used short keys and no declared checksum, which
    is how every Zenodo `md5:<hex>` and six of the fourteen PaRoutes file keys
    (`uspto_rxn_n5_unique_templates.hdf5` …) shipped as `<redacted:…>`: the
    whole-value opacity rule reads a long, space-free, letter-and-digit string
    as a credential. This runs the captured record through the real resolver.
    """
    import hashlib
    from pathlib import Path

    from openai4s.host.datasets import resolve_selection, validate_selection
    from openai4s.host.science import ScienceConnectorService

    fixture = Path(__file__).parent / "fixtures/zenodo/record-6275421.json"
    row = json.loads(fixture.read_text(encoding="utf-8"))["hits"]["hits"][0]
    raw = json.dumps(row).encode()
    service = ScienceConnectorService(
        lambda *_args: {
            "content": raw.decode(),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_bytes": len(raw),
        }
    )
    assert len(row["files"]) == 14
    for item in row["files"]:
        source = resolve_selection(
            validate_selection("6275421", item["key"]),
            expected_size=item["size"],
            expected_checksum=item["checksum"],
            timeout=30,
            service=service,
        )
        # What the import tool adds once the bytes are verified.
        local = hashlib.sha256(item["key"].encode()).hexdigest()
        source["dataset"].update(
            file_verification="size_and_source_checksum_verified",
            local_sha256=local,
            downloaded_bytes=item["size"],
        )
        dataset = public_source(json.dumps(source), artifact_sha256=local)["dataset"]
        assert "redacted_fields" not in dataset, (item["key"], dataset)
        assert dataset["file_key"] == item["key"]
        assert dataset["declared_checksum"] == item["checksum"]
        assert dataset["record_doi"] == "10.5281/zenodo.6275421"
        assert dataset["concept_doi"] == "10.5281/zenodo.6275420"
        assert dataset["local_sha256"] == local
        assert dataset["declared_license"] == "cc-by-4.0"


@pytest.mark.parametrize(
    "field,value",
    [
        # No extension: a bare token is not a file name.
        ("file_key", SECRET),
        # A JWT ends in a signature, not a short extension.
        ("file_key", f"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.{SECRET}"),
        ("file_key", f"{SECRET}.{SECRET}"),
        # The algorithm's exact digest length or nothing.
        ("declared_checksum", f"md5:{SECRET}"),
        ("declared_checksum", "sha1:" + "a1" * 20),
        # A DOI prefix does not launder an opaque suffix.
        ("record_doi", f"10.5281/{SECRET}"),
        ("concept_doi", f"10.5281/zenodo.{SECRET}"),
    ],
)
def test_identifier_grammars_do_not_release_credential_shapes(field, value):
    dataset = public_source({"dataset": {field: value}})["dataset"]
    assert SECRET not in json.dumps(dataset)
    assert dataset["redacted_fields"] == [field]


def test_a_dataset_that_projects_to_nothing_is_still_counted():
    for dataset in (f"Bearer {SECRET}", {"path": "/private/x", "headers": {}}):
        out = public_source({"database": "zenodo", "dataset": dataset})
        assert "dataset" not in out
        assert out["undisclosed_field_count"] == 1
        assert SECRET not in json.dumps(out)
    assert "undisclosed_field_count" not in public_source(
        {"database": "zenodo", "dataset": {}}
    )


def test_dataset_projection_clips_with_an_explicit_field_name():
    out = public_source({"dataset": {"title": "x" * (MAX_VALUE_CHARS + 50)}})
    assert len(out["dataset"]["title"]) == MAX_VALUE_CHARS
    assert out["dataset"]["truncated_fields"] == ["title"]


def test_historical_dataset_claim_requires_a_local_receipt_after_reopen(tmp_path):
    """The real version route must not certify script-supplied historical JSON."""
    import hashlib

    from openai4s.config import Config, LLMConfig
    from openai4s.server.gateway import SessionRunner, WSHub, make_handler
    from openai4s.store import get_store

    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    frame = store.new_frame(kind="turn", project_id="recorded-input")
    path = tmp_path / "manual.csv"
    body = b"value\n1\n"
    path.write_bytes(body)
    source = json.dumps(
        {"dataset": {"file_verification": "size_and_source_checksum_verified"}}
    )
    artifact = store.save_artifact(
        path=str(path),
        filename="manual.csv",
        size_bytes=len(body),
        content_type="text/csv",
        checksum=hashlib.sha256(body).hexdigest(),
        frame_id=frame,
        root_frame_id=frame,
        project_id="recorded-input",
        source=source,
    )
    store.close()

    hub = WSHub()
    runner = SessionRunner(cfg, hub, start_idle_sweeper=False)
    try:
        handler = object.__new__(make_handler(cfg, hub, runner))
        handler.headers = {}
        artifact_id = artifact["artifact_id"]
        handler.path = f"/api/v1/artifacts/{artifact_id}/versions"
        seen = []
        handler._json = lambda obj, code=200: seen.append(obj)
        handler._body = lambda: {}
        handler._api("GET", f"/artifacts/{artifact_id}/versions")
        assert runner.store.version_meta(artifact["version_id"])["source"] == source
        row = seen[-1]["versions"][0]
        assert row["version_id"] == artifact["version_id"]
        dataset = row.get("retrieval_source", {}).get("dataset", {})
        assert "file_verification" in dataset
        assert dataset["file_verification"] is None
        assert dataset.get("file_verification") != "size_and_source_checksum_verified"
    finally:
        runner.close()
        runner.store.close()


def test_dataset_sources_remain_version_and_session_bound_after_reopen(tmp_path):
    """A later input must not change an earlier version's recorded selection."""
    import hashlib

    from openai4s.config import Config, LLMConfig
    from openai4s.server.gateway import SessionRunner, WSHub, make_handler
    from openai4s.store import get_store

    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    frame = store.new_frame(kind="turn", project_id="p")
    other_frame = store.new_frame(kind="turn", project_id="p")
    records = []
    sources = []
    for index, owner in enumerate((frame, frame, other_frame)):
        # Reimporting the same bytes with a fresh source declaration creates a
        # new immutable native version, not a rewrite of the first source.
        body = b"same input\n" if index < 2 else b"other input\n"
        snapshot = tmp_path / f"snapshot-{index}.txt"
        snapshot.write_bytes(body)
        checksum = hashlib.sha256(body).hexdigest()
        source = json.dumps(
            {
                "database": "zenodo",
                "dataset": {
                    "record_id": str(100 + index),
                    "record_doi": f"10.5281/zenodo.{100 + index}",
                    "concept_doi": "10.5281/zenodo.99",
                    "file_key": "input.txt",
                    "declared_license": None,
                    "downloaded_bytes": len(body),
                    "local_sha256": checksum,
                    "file_verification": "size_and_source_checksum_verified",
                    "private_debug": SECRET,
                },
            }
        )
        sources.append(source)
        records.append(
            store.record_cell_artifact(
                path=str(tmp_path / "input.txt"),
                filename="input.txt",
                content_type="text/plain",
                size_bytes=len(body),
                checksum=checksum,
                producing_cell_id=None,
                frame_id=owner,
                root_frame_id=owner,
                project_id="p",
                snapshot_path=str(snapshot),
                source=source,
            )
        )
    assert records[0]["artifact_id"] == records[1]["artifact_id"]
    assert records[0]["version_id"] != records[1]["version_id"]
    assert records[2]["artifact_id"] != records[0]["artifact_id"]
    store.close()

    hub = WSHub()
    runner = SessionRunner(cfg, hub, start_idle_sweeper=False)
    handler_class = make_handler(cfg, hub, runner)
    try:
        handler = object.__new__(handler_class)
        handler.headers = {}
        artifact_id = records[0]["artifact_id"]
        handler.path = f"/api/v1/artifacts/{artifact_id}/versions"
        seen = []
        handler._json = lambda obj, code=200: seen.append(obj)
        handler._body = lambda: {}
        handler._api("GET", f"/artifacts/{artifact_id}/versions")
        rows = seen[-1]["versions"]
        assert [row["version_id"] for row in rows] == [
            records[1]["version_id"],
            records[0]["version_id"],
        ]
        for row, index in zip(rows, (1, 0)):
            dataset = row["retrieval_source"]["dataset"]
            assert dataset["record_id"] == str(100 + index)
            assert dataset["record_doi"] == f"10.5281/zenodo.{100 + index}"
            assert dataset["local_sha256"] == row["checksum"]
            assert dataset["declared_license"] is None
            # These fixtures supply recorded JSON, not a native Host receipt.
            assert dataset["file_verification"] is None
            stored = runner.store.version_meta(row["version_id"])
            assert stored["source"] == sources[index]
        assert SECRET not in json.dumps(seen)
        assert "102" not in [
            row["retrieval_source"]["dataset"]["record_id"] for row in rows
        ]
    finally:
        runner.close()
        runner.store.close()


def _receipt_fixture(tmp_path, *, change=None):
    """Unit fixture for trusted Host evidence, not a live/native import claim."""
    import hashlib

    from openai4s.config import Config
    from openai4s.store import get_store

    store = get_store(Config(data_dir=tmp_path / "data").db_path)
    frame = store.new_frame(kind="turn", project_id="receipt-test")
    sibling = store.new_frame(kind="turn", project_id="receipt-test")
    body = b"value\n1\n"
    snapshot = tmp_path / "snapshot.csv"
    snapshot.write_bytes(body)
    checksum = hashlib.sha256(body).hexdigest()
    source = {
        "kind": "dataset_input",
        "database": "zenodo",
        "dataset": {
            "provider": "zenodo",
            "record_id": "1",
            "file_key": "input.csv",
            "access_right": "open",
            "declared_size_bytes": len(body),
            "declared_checksum": "md5:"
            + hashlib.md5(body, usedforsecurity=False).hexdigest(),
            "downloaded_bytes": len(body),
            "local_sha256": checksum,
            "file_verification": "size_and_source_checksum_verified",
        },
    }
    if change is not None:
        change(source)
    artifact = store.save_artifact(
        path=str(tmp_path / "input.csv"),
        snapshot_path=str(snapshot),
        filename="input.csv",
        size_bytes=len(body),
        content_type="text/csv",
        checksum=checksum,
        frame_id=frame,
        root_frame_id=frame,
        project_id="receipt-test",
        source=source,
    )
    return store, frame, sibling, artifact, source


def test_dataset_receipt_is_exact_scoped_durable_and_not_transferred_by_hash(tmp_path):
    from openai4s.store import get_store

    store, frame, sibling, artifact, source = _receipt_fixture(tmp_path)
    scope = {"root_frame_id": frame, "project_id": "receipt-test"}
    first_id = artifact["version_id"]
    db_path = store.db_path
    try:
        assert not store.dataset_input_is_verified(
            store.version_meta(first_id), **scope
        )
        store.record_dataset_verification(first_id, source, **scope)
        # An identical receipt is idempotent, not a rewrite of verification time.
        store.record_dataset_verification(first_id, source, **scope)
        assert store.dataset_input_is_verified(store.version_meta(first_id), **scope)
        assert not store.dataset_input_is_verified(
            store.version_meta(first_id),
            root_frame_id=sibling,
            project_id="receipt-test",
        )
        assert not store.dataset_input_is_verified(
            store.version_meta(first_id), root_frame_id=frame, project_id="foreign"
        )
        later_source = {**source, "retrieved_at": 2}
        metadata = store.version_meta(first_id)
        later = store.save_artifact(
            path=metadata["path"],
            snapshot_path=metadata["snapshot_path"],
            artifact_id=artifact["artifact_id"],
            filename="input.csv",
            size_bytes=metadata["size_bytes"],
            content_type="text/csv",
            checksum=metadata["checksum"],
            frame_id=frame,
            source=later_source,
            **scope,
        )
        assert not store.dataset_input_is_verified(
            store.version_meta(later["version_id"]), **scope
        )
        with pytest.raises(PermissionError):
            store.query("SELECT * FROM dataset_verification_receipts")
    finally:
        store.close()
    reopened = get_store(db_path)
    try:
        assert reopened.dataset_input_is_verified(
            reopened.version_meta(first_id), **scope
        )
        first = reopened.version_meta(first_id)
        projected = public_source(
            first["source"],
            artifact_sha256=first["checksum"],
            dataset_verified=reopened.dataset_input_is_verified(
                reopened.version_meta(first_id), **scope
            ),
        )
        assert (
            projected["dataset"]["file_verification"]
            == "size_and_source_checksum_verified"
        )
        assert not reopened.dataset_input_is_verified(
            reopened.version_meta(later["version_id"]), **scope
        )
    finally:
        reopened.close()


@pytest.mark.parametrize("failure_at", ["insert", "commit"])
def test_failed_receipt_cannot_be_committed_by_a_later_store_write(
    tmp_path, monkeypatch, failure_at
):
    import sqlite3

    store, frame, _sibling, artifact, source = _receipt_fixture(tmp_path)
    scope = {"root_frame_id": frame, "project_id": "receipt-test"}
    connection = store._conn

    class FailingConnection:
        receipt_pending = False

        def execute(self, sql, parameters=()):
            if sql.startswith("INSERT INTO dataset_verification_receipts"):
                if failure_at == "insert":
                    raise sqlite3.OperationalError("injected receipt insert failure")
                self.receipt_pending = True
            return connection.execute(sql, parameters)

        def commit(self):
            if self.receipt_pending:
                self.receipt_pending = False
                raise sqlite3.OperationalError("injected receipt commit failure")
            connection.commit()

        def rollback(self):
            connection.rollback()

    monkeypatch.setattr(store._artifacts, "_connection", FailingConnection())
    try:
        with pytest.raises(sqlite3.OperationalError, match="injected receipt"):
            store.record_dataset_verification(artifact["version_id"], source, **scope)
        assert not connection.in_transaction
        assert not store.dataset_input_is_verified(
            store.version_meta(artifact["version_id"]), **scope
        )
        # Ordinary audit/frame writes share this connection. They must not
        # accidentally commit authority from a previously failed import.
        store.new_frame(kind="turn", project_id="ordinary-write")
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM dataset_verification_receipts"
            ).fetchone()[0]
            == 0
        )
    finally:
        store.close()


@pytest.mark.parametrize("field", ["source", "checksum", "size_bytes", "snapshot_path"])
def test_dataset_receipt_refuses_changed_version_metadata(tmp_path, field):
    store, frame, _sibling, artifact, source = _receipt_fixture(tmp_path)
    scope = {"root_frame_id": frame, "project_id": "receipt-test"}
    try:
        store.record_dataset_verification(artifact["version_id"], source, **scope)
        replacement = {
            "source": json.dumps({**source, "retrieved_at": 99}),
            "checksum": "0" * 64,
            "size_bytes": 99,
            "snapshot_path": str(tmp_path / "another.csv"),
        }[field]
        # This controlled mutation models stale/corrupted persisted metadata.
        store._conn.execute(
            f"UPDATE artifact_versions SET {field}=? WHERE version_id=?",
            (replacement, artifact["version_id"]),
        )
        store._conn.commit()
        assert not store.dataset_input_is_verified(
            store.version_meta(artifact["version_id"]), **scope
        )
    finally:
        store.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("file_verification", "not_downloaded"),
        ("local_sha256", "0" * 64),
        ("downloaded_bytes", True),
        ("declared_size_bytes", 99),
        ("declared_checksum", "md5:invalid"),
        ("access_right", "restricted"),
        ("record_id", "concept-doi"),
        ("file_key", ""),
    ],
)
def test_dataset_receipt_rejects_incomplete_or_mismatched_evidence(
    tmp_path, field, value
):
    store, frame, _sibling, artifact, source = _receipt_fixture(
        tmp_path, change=lambda source: source["dataset"].update({field: value})
    )
    scope = {"root_frame_id": frame, "project_id": "receipt-test"}
    try:
        with pytest.raises(ValueError, match="native dataset receipt"):
            store.record_dataset_verification(artifact["version_id"], source, **scope)
        assert not store.dataset_input_is_verified(
            store.version_meta(artifact["version_id"]), **scope
        )
    finally:
        store.close()


@pytest.mark.parametrize("field", ["root_frame_id", "project_id"])
def test_dataset_receipt_does_not_follow_rescoped_ownership(tmp_path, field):
    store, frame, sibling, artifact, source = _receipt_fixture(tmp_path)
    scope = {"root_frame_id": frame, "project_id": "receipt-test"}
    try:
        store.record_dataset_verification(artifact["version_id"], source, **scope)
        changed_scope = {
            **scope,
            field: sibling if field == "root_frame_id" else "foreign",
        }
        store._conn.execute(
            f"UPDATE artifacts SET {field}=? WHERE artifact_id=?",
            (changed_scope[field], artifact["artifact_id"]),
        )
        store._conn.commit()
        assert not store.dataset_input_is_verified(
            store.version_meta(artifact["version_id"]), **changed_scope
        )
    finally:
        store.close()


def test_dataset_receipt_survives_real_version_route_and_store_reopen(tmp_path):
    from openai4s.config import Config, LLMConfig
    from openai4s.server.gateway import SessionRunner, WSHub, make_handler

    store, frame, _sibling, artifact, source = _receipt_fixture(tmp_path)
    store.record_dataset_verification(
        artifact["version_id"], source, root_frame_id=frame, project_id="receipt-test"
    )
    raw = store.version_meta(artifact["version_id"])["source"]
    store.close()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    hub = WSHub()
    runner = SessionRunner(cfg, hub, start_idle_sweeper=False)
    try:
        handler = object.__new__(make_handler(cfg, hub, runner))
        handler.headers = {}
        handler.path = f"/api/v1/artifacts/{artifact['artifact_id']}/versions"
        seen = []
        handler._json = lambda obj, code=200: seen.append(obj)
        handler._body = lambda: {}
        handler._api("GET", f"/artifacts/{artifact['artifact_id']}/versions")
        row = seen[-1]["versions"][0]
        assert (
            row["retrieval_source"]["dataset"]["file_verification"]
            == "size_and_source_checksum_verified"
        )
        assert runner.store.version_meta(artifact["version_id"])["source"] == raw
    finally:
        runner.close()
        runner.store.close()


@pytest.mark.parametrize("field", ["source", "checksum", "size_bytes"])
def test_version_route_does_not_verify_a_stale_projection(tmp_path, monkeypatch, field):
    from openai4s.config import Config, LLMConfig
    from openai4s.server.gateway import SessionRunner, WSHub, make_handler

    store, frame, _sibling, artifact, source = _receipt_fixture(tmp_path)
    scope = {"root_frame_id": frame, "project_id": "receipt-test"}
    store.record_dataset_verification(artifact["version_id"], source, **scope)
    original = store.version_meta(artifact["version_id"])
    stale_source = {**source, "dataset": {**source["dataset"], "record_id": "999"}}
    stale_value = {
        "source": json.dumps(stale_source),
        "checksum": "0" * 64,
        "size_bytes": 999,
    }[field]
    store._conn.execute(
        f"UPDATE artifact_versions SET {field}=? WHERE version_id=?",
        (stale_value, artifact["version_id"]),
    )
    store._conn.commit()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    hub = WSHub()
    runner = SessionRunner(cfg, hub, start_idle_sweeper=False)
    list_versions = store.list_versions

    def read_then_restore(artifact_id):
        rows = list_versions(artifact_id)
        store._conn.execute(
            f"UPDATE artifact_versions SET {field}=? WHERE version_id=?",
            (original[field], artifact["version_id"]),
        )
        store._conn.commit()
        return rows

    monkeypatch.setattr(store, "list_versions", read_then_restore)
    try:
        handler = object.__new__(make_handler(cfg, hub, runner))
        handler.headers = {}
        handler.path = f"/api/v1/artifacts/{artifact['artifact_id']}/versions"
        seen = []
        handler._json = lambda obj, code=200: seen.append(obj)
        handler._body = lambda: {}
        handler._api("GET", f"/artifacts/{artifact['artifact_id']}/versions")
        projected = seen[-1]["versions"][0]
        dataset = projected["retrieval_source"]["dataset"]
        assert dataset["file_verification"] is None
        assert store.version_meta(artifact["version_id"])[field] == original[field]
    finally:
        runner.close()
        runner.store.close()


def test_package_artifact_import_cannot_inherit_local_verification(tmp_path):
    import hashlib

    from openai4s.server.session_package import SessionPackageService

    store, frame, _sibling, artifact, source = _receipt_fixture(tmp_path)
    scope = {"root_frame_id": frame, "project_id": "receipt-test"}
    try:
        store.record_dataset_verification(artifact["version_id"], source, **scope)
        original = store.version_meta(artifact["version_id"])
        body = b"value\n1\n"
        digest = hashlib.sha256(body).hexdigest()
        imported_root = store.new_frame(kind="turn", project_id="package-import")
        service = object.__new__(SessionPackageService)
        service.store = store
        # A package may carry source declarations, but not this installation's
        # authoritative receipt, even when its bytes genuinely match.
        manifest = {
            "dataset_verification_receipts": [
                {"version_id": artifact["version_id"], "verified": True}
            ],
            "artifacts": [
                {
                    "artifact_id": artifact["artifact_id"],
                    "filename": "input.csv",
                    "latest_version_id": artifact["version_id"],
                    "versions": [
                        {
                            "version_id": artifact["version_id"],
                            "available": True,
                            "snapshot_sha256": digest,
                            "source": original["source"],
                        }
                    ],
                }
            ],
        }
        _artifacts, versions, _live = service._import_artifacts(
            imported_root,
            "package-import",
            manifest,
            {f"artifact-data/{digest}": body},
            import_root=tmp_path / "import-data",
            active_workspace=tmp_path / "import-workspace",
            cell_map={},
            env_map={},
        )
        imported_id = versions[artifact["version_id"]]
        assert imported_id != artifact["version_id"]
        assert store.version_meta(imported_id)["source"] == original["source"]
        assert not store.dataset_input_is_verified(
            store.version_meta(imported_id),
            root_frame_id=imported_root,
            project_id="package-import",
        )
        assert store.dataset_input_is_verified(
            store.version_meta(artifact["version_id"]), **scope
        )
    finally:
        store.close()


def test_source_json_cannot_supply_verification_authority():
    out = public_source(
        {
            "database": "zenodo",
            "dataset_verified": True,
            "dataset": {
                "dataset_verified": True,
                "file_verification": "size_and_source_checksum_verified",
            },
        }
    )
    assert out["dataset"]["file_verification"] is None


def test_unattested_dataset_reports_an_explicit_unknown_check():
    out = public_source({"database": "zenodo", "dataset": {"record_id": "123"}})
    assert "file_verification" in out["dataset"]
    assert out["dataset"]["file_verification"] is None


def test_the_route_sends_provenance_and_never_the_credential(tmp_path):
    """End to end, because the unit test alone proved less than it looked like.

    A first attempt at this check passed with `retrieval_source: null` — the
    secret was absent from the response for the boring reason that the field
    was absent too: `list_versions` did not select the `source` column, so the
    envelope had been written on every retrieved version since retrieval
    provenance was added and read by nothing. "The secret is not in the
    response" is worthless unless the provenance *is*.
    """
    import hashlib
    import json as _json

    from openai4s.config import Config, LLMConfig
    from openai4s.server import gateway as gateway_mod

    DECLARED_MD5 = "md5:" + hashlib.md5(b"a\n1\n", usedforsecurity=False).hexdigest()
    from openai4s.store import get_store

    class _Hub:
        def emitter(self, root_frame_id):
            return lambda event: None

        def broadcast(self, root_frame_id, event):
            pass

    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    frame_id = store.new_frame(kind="turn", project_id="p")
    versions = tmp_path / "data" / "artifact-versions"
    versions.mkdir(parents=True, exist_ok=True)
    snapshot = versions / "v1__data.csv"
    snapshot.write_bytes(b"a\n1\n")
    row = store.record_cell_artifact(
        path=str(snapshot),
        filename="data.csv",
        content_type="text/csv",
        size_bytes=4,
        checksum=hashlib.sha256(b"a\n1\n").hexdigest(),
        producing_cell_id=None,
        frame_id=frame_id,
        root_frame_id=frame_id,
        project_id="p",
        snapshot_path=str(snapshot),
        source=_json.dumps(
            {
                "database": "UniProt",
                "request_url": f"https://rest.uniprot.org/search?q=NIF3&api_key={SECRET}",
                "retrieved_at": "2026-07-27T14:02:00Z",
                "record_count": 42,
                "internal_debug": {"Authorization": f"Bearer {SECRET}"},
                "dataset": {
                    "provider": "zenodo",
                    "record_id": "123",
                    "record_doi": "10.5281/zenodo.123",
                    "concept_doi": "10.5281/zenodo.122",
                    # A real PaRoutes key and Zenodo's own checksum shape:
                    # both read as credentials to a whole-value opacity check.
                    "file_key": "uspto_rxn_n5_unique_templates.hdf5",
                    "declared_license": "cc-by-4.0",
                    "declared_size_bytes": 4,
                    "declared_checksum": DECLARED_MD5,
                    "local_sha256": hashlib.sha256(b"a\n1\n").hexdigest(),
                    "private_debug": SECRET,
                },
            }
        ),
    )

    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)
    try:
        handler = object.__new__(handler_class)
        handler.headers = {}
        handler.path = f"/api/v1/artifacts/{row['artifact_id']}/versions"
        seen: list[dict] = []
        handler._json = lambda obj, code=200: seen.append(obj)
        handler._body = lambda: {}
        handler._api("GET", f"/artifacts/{row['artifact_id']}/versions")

        body = seen[-1]
        provenance = body["versions"][0].get("retrieval_source")
        # Both halves. Either one alone can pass while the feature is broken.
        assert provenance is not None, "the provenance never reached the client"
        assert provenance["database"] == "UniProt"
        assert provenance["record_count"] == 42
        assert provenance["dataset"]["record_id"] == "123"
        assert provenance["dataset"]["declared_license"] == "cc-by-4.0"
        assert provenance["dataset"]["declared_checksum"] == DECLARED_MD5
        assert provenance["dataset"]["file_key"] == "uspto_rxn_n5_unique_templates.hdf5"
        assert "redacted_fields" not in provenance["dataset"]
        assert (
            provenance["dataset"]["local_sha256"]
            == hashlib.sha256(b"a\n1\n").hexdigest()
        )
        assert SECRET not in _json.dumps(body)
        assert "internal_debug" not in _json.dumps(body)
        assert provenance["undisclosed_field_count"] == 1
    finally:
        runner.close()


# --------------------------------------------------------------------------
# historical names, and the response digest's public state
# --------------------------------------------------------------------------


def test_the_canonical_response_digest_says_whether_it_is_shown_or_hidden():
    """A real SHA-256 is the hash of what came back and is shown as written.
    Judged whole it is an opaque 64-hex run, so it used to project as
    `<redacted:…>` with state "hidden" for every real search. Any other value
    is text like the rest, and when it is hidden the client is told so instead
    of having to guess from the redaction marker."""
    import hashlib

    digest = hashlib.sha256(b"upstream response bytes").hexdigest()
    out = public_source({"response_sha256": digest})
    assert out["response_sha256"] == digest
    assert out[RESPONSE_HASH_STATE_KEY] == RESPONSE_HASH_SHOWN

    # Not a digest: a credential-shaped value, or a digest with extra text.
    for opaque in ("sk-live-" + SECRET, "sha256:" + digest, digest[:40] + "ZZZZ"):
        out = public_source({"response_sha256": opaque})
        assert opaque not in json.dumps(out)
        assert "redacted" in out["response_sha256"]
        assert out[RESPONSE_HASH_STATE_KEY] == "hidden"


def test_a_legacy_science_search_source_is_readable():
    """`write_search_artifact` used to record `endpoint` / `source_checksum`.
    Those artifacts are immutable and still have to be readable, but only for
    the one known kind that ever wrote them."""
    out = public_source(
        {
            "kind": "science_search",
            "database": "clinvar",
            "query": "VCV000012345",
            "endpoint": "https://eutils.ncbi.nlm.nih.gov/example",
            "retrieved_at": 123456789,
            "source_checksum": "ab" * 32,
            "accessions": ["VCV000012345"],
        }
    )
    assert out["request_url"] == "https://eutils.ncbi.nlm.nih.gov/example"
    assert out["response_sha256"] == "ab" * 32
    assert out[LEGACY_FIELDS_KEY] == ["request_url", "response_sha256"]
    assert "endpoint" not in out and "source_checksum" not in out
    # `kind` and `accessions` are still fields the projection does not render;
    # the consumed aliases are not counted as unknown.
    assert out["undisclosed_field_count"] == 2


def test_a_legacy_response_checksum_is_judged_by_the_same_digest_grammar():
    import hashlib

    digest = hashlib.sha256(b"legacy response bytes").hexdigest()
    out = public_source({"kind": "science_search", "source_checksum": digest})
    assert out["response_sha256"] == digest
    assert out[RESPONSE_HASH_STATE_KEY] == RESPONSE_HASH_SHOWN
    assert out[LEGACY_FIELDS_KEY] == ["response_sha256"]

    opaque = "sk-live-" + SECRET
    out = public_source({"kind": "science_search", "source_checksum": opaque})
    assert opaque not in json.dumps(out)
    assert "redacted" in out["response_sha256"]
    assert out[RESPONSE_HASH_STATE_KEY] == "hidden"
    assert out[LEGACY_FIELDS_KEY] == ["response_sha256"]


def test_alias_and_canonical_names_that_agree_merge_without_a_conflict():
    url = "https://eutils.ncbi.nlm.nih.gov/example"
    out = public_source(
        {
            "kind": "science_search",
            "request_url": url,
            "endpoint": url,
            "response_sha256": "ab" * 32,
            "source_checksum": "ab" * 32,
        }
    )
    assert out["request_url"] == url
    assert out["response_sha256"] == "ab" * 32
    assert LEGACY_FIELDS_KEY not in out
    assert ALIAS_CONFLICTS_KEY not in out
    assert out["undisclosed_field_count"] == 1


def test_conflicting_alias_and_canonical_names_keep_the_canonical_value():
    canonical = "https://eutils.ncbi.nlm.nih.gov/canonical"
    alias = "https://legacy.example.org/other"
    out = public_source(
        {
            "kind": "science_search",
            "request_url": canonical,
            "endpoint": alias,
            "response_sha256": "ab" * 32,
            "source_checksum": "cd" * 32,
        }
    )
    assert out["request_url"] == canonical
    assert out["response_sha256"] == "ab" * 32
    # F13's frozen field name; the pre-S1 `source_conflicts` spelling is gone.
    assert out[ALIAS_CONFLICTS_KEY] == ["request_url", "response_sha256"]
    assert "alias_conflicts" in out and "source_conflicts" not in out
    assert LEGACY_FIELDS_KEY not in out
    rendered = json.dumps(out)
    assert alias not in rendered
    assert "cd" * 32 not in rendered


def test_a_conflicting_alias_value_is_not_rendered_even_if_it_carries_a_credential():
    out = public_source(
        {
            "kind": "science_search",
            "request_url": "https://eutils.ncbi.nlm.nih.gov/example",
            "endpoint": f"https://legacy.example.org/search?api_key={SECRET}",
        }
    )
    assert out[ALIAS_CONFLICTS_KEY] == ["request_url"]
    assert SECRET not in json.dumps(out)


def test_the_legacy_names_are_only_understood_on_the_known_kind():
    alias_url = "https://legacy.example.org/data"
    out = public_source(
        {
            "kind": "something_else",
            "database": "RCSB",
            "endpoint": alias_url,
            "source_checksum": "ab" * 32,
        }
    )
    assert "request_url" not in out
    assert "response_sha256" not in out
    assert LEGACY_FIELDS_KEY not in out
    assert alias_url not in json.dumps(out)
    # kind + both aliases are fields the projection does not render.
    assert out["undisclosed_field_count"] == 3


def test_a_legacy_request_url_is_still_redacted():
    out = public_source(
        {
            "kind": "science_search",
            "endpoint": f"https://api.example.org/search?q=NIF3&api_key={SECRET}",
        }
    )
    assert out[LEGACY_FIELDS_KEY] == ["request_url"]
    assert SECRET not in json.dumps(out)
    assert "api_key" in out["request_url"]


def test_a_legacy_value_is_clipped_like_the_canonical_one():
    out = public_source(
        {"kind": "science_search", "endpoint": "x" * (MAX_VALUE_CHARS * 2)}
    )
    assert len(out["request_url"]) == MAX_VALUE_CHARS
    assert out["truncated_fields"] == ["request_url"]
    assert out[LEGACY_FIELDS_KEY] == ["request_url"]


def test_empty_results_and_empty_legacy_values_stay_absent():
    assert public_source({"kind": "science_search"}) is None
    assert (
        public_source(
            {
                "kind": "science_search",
                "endpoint": "",
                "source_checksum": None,
                "query": "",
            }
        )
        is None
    )
