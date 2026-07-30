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

from openai4s.server.retrieval_source import (
    ALLOWED_FIELDS,
    MAX_VALUE_CHARS,
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
        assert SECRET not in _json.dumps(body)
        assert "internal_debug" not in _json.dumps(body)
        assert provenance["undisclosed_field_count"] == 1
    finally:
        runner.close()
