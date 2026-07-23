"""Every route the router can match must have a checkable response contract.

Not "a schema for every route" — several routes legitimately answer with a
stream, a redirect, a file, or nothing at all, and inventing a JSON schema for
those would be a fabrication dressed as coverage. What each route must have is
an *observed* contract: the kind of thing it returns, the statuses it returns
it with, and — for JSON — a shape frozen in `docs/response-schemas.json`.

Two rules keep the number honest:

* **Derived, never declared.** The kind comes from what the handler actually
  sent: the status code and the content type on the way out. A hand-maintained
  list of "these routes stream" drifts the moment a route changes, and drifts
  silently.
* **An error is a contract.** A route driven with no parameters answers 400 or
  404 from the real handler, and that response is as much a promise as the
  happy path. It is *not* a way to tick a box: the assertions below require the
  status to be a real HTTP status and the body, when JSON, to have a shape.

An empty schema or `additionalProperties: true` would satisfy a coverage count
while promising nothing, so both are rejected outright.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import contract
from openai4s.server import gateway as gateway_mod
from openai4s.server import response_capture

#: Where the classification lives. Beside the schemas, because they answer two
#: halves of one question: "what kind of answer is this" and "what shape".
CONTRACT_ARTIFACT = (
    Path(__file__).resolve().parents[1] / "docs" / "response-contract.json"
)

#: Routes the offline suite cannot drive, each with the reason. An exemption is
#: a stated cost, not a shrug — it names what is not covered so the next person
#: can see the gap rather than read 144/144 and believe it.
EXEMPT: dict[str, str] = {}

#: Routes observed returning a bare `{}`. That is a real contract — an empty
#: JSON acknowledgement — but it is a thin one, so it is named here rather than
#: passing quietly among the schemas that actually describe something. Any
#: *other* empty-properties schema fails, because that is what a fabricated
#: coverage entry looks like.
EMPTY_ACK: dict[str, str] = {
    "/frames/([^/]+)": "a metadata patch with nothing to change acknowledges "
    "with an empty object",
    "/frames/([^/]+)(?:/.*)?": "the catch-all inherits the same acknowledgement",
    "/projects/([^/]+)": "a project patch with nothing to change acknowledges "
    "with an empty object",
}


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


@pytest.fixture(scope="module")
def driven(tmp_path_factory, request):
    """Drive every known route against a real handler and a real Store.

    Real, not stubbed: the whole claim of these artifacts is that they were
    captured from responses the code produced. A stub would let this file
    publish a shape nothing implements.

    When the schema capture is running (``capture_response_schemas.py`` sets a
    session recorder on the pytest config), the drive reports into *that*
    recorder as well — so the two artifacts are built from one body of
    evidence rather than from two drives that could disagree.
    """
    tmp_path = tmp_path_factory.mktemp("contract")
    config = Config(
        data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="test-key")
    )
    runner = gateway_mod.SessionRunner(config, _Hub(), start_idle_sweeper=False)
    recorder = response_capture.Recorder()
    session = getattr(request.config, "_openai4s_recorder", None)
    recorders = [recorder] + ([session[0]] if session else [])
    original = response_capture.install(gateway_mod, recorder)
    try:
        for target in recorders:
            response_capture.drive_all_routes(
                target, gateway_mod.make_handler, config, runner
            )
    finally:
        gateway_mod.make_handler = original
    return recorder


def _load_contract() -> dict:
    if not CONTRACT_ARTIFACT.is_file():
        return {"routes": {}}
    return json.loads(CONTRACT_ARTIFACT.read_text("utf-8"))


# --------------------------------------------------------------------------
# the gate
# --------------------------------------------------------------------------


def test_every_route_has_a_recorded_contract():
    """144/144, and the number is measured rather than asserted."""
    frozen = _load_contract()
    routes = contract.http_routes()
    recorded = set(frozen.get("routes") or {})
    missing = sorted(
        route for route in routes if route not in recorded and route not in EXEMPT
    )
    assert not missing, (
        f"{len(missing)} route(s) have no response contract. Regenerate with "
        f"`uv run python scripts/capture_response_contract.py`, or add an "
        f"exemption naming why the offline suite cannot drive them: {missing}"
    )


def test_the_contract_does_not_describe_routes_that_no_longer_exist():
    """A stale entry is worse than a missing one: it reads as coverage."""
    frozen = _load_contract()
    routes = contract.http_routes()
    stale = sorted(set(frozen.get("routes") or {}) - routes)
    assert not stale, f"the contract names routes the router cannot match: {stale}"


def test_every_contract_entry_names_a_kind_and_a_status():
    frozen = _load_contract()
    for route, record in (frozen.get("routes") or {}).items():
        assert record.get("kinds"), f"{route} has no response kind"
        assert record.get("statuses"), f"{route} records no status code"
        for status in record["statuses"]:
            assert 100 <= int(status) < 600, f"{route} recorded status {status}"
        for kind in record["kinds"]:
            assert kind in (
                response_capture.JSON,
                response_capture.STREAM,
                response_capture.REDIRECT,
                response_capture.EMPTY,
                response_capture.BINARY,
            ), f"{route} has an unknown response kind {kind!r}"


def test_a_json_route_has_a_shape_and_the_shape_promises_something():
    """The rule that stops the count being gamed.

    An empty `properties` or `additionalProperties: true` satisfies a coverage
    number while promising nothing at all, which is worse than an absent schema
    because it reads as a contract.
    """
    frozen = _load_contract()
    schemas = response_capture.load().get("routes") or {}
    unschematised = []
    for route, record in (frozen.get("routes") or {}).items():
        if response_capture.JSON not in record.get("kinds", []):
            continue
        # Schema keys are "METHOD /route [family]"; the contract is keyed by
        # route alone, because a route's kind does not vary by verb here.
        entries = [
            value["schema"]
            for key, value in schemas.items()
            if key.rsplit(" [", 1)[0].split(" ", 1)[-1] == route
        ]
        if not entries:
            unschematised.append(route)
            continue
        for schema in entries:
            assert (
                schema.get("additionalProperties") is not True
            ), f"{route}: additionalProperties:true promises nothing"
            if schema.get("type") == "object" and not schema.get("properties"):
                assert route in EMPTY_ACK, (
                    f"{route}: an object schema with no properties promises "
                    f"nothing. If the route really answers with a bare `{{}}`, "
                    f"name it in EMPTY_ACK with the reason; otherwise drive it "
                    f"with a request that produces a real body."
                )
    assert not unschematised, (
        f"{len(unschematised)} JSON route(s) have a contract but no shape: "
        f"{sorted(unschematised)}"
    )


def test_the_recorded_contract_matches_what_the_routes_actually_do(driven):
    """The artifact is regenerated from this same drive, so a route whose kind
    changed shows up as a diff rather than as a surprise in a client."""
    observed = driven.contracts()
    frozen = (_load_contract().get("routes") or {}).copy()
    drifted = []
    for key, record in observed.items():
        # `key` is "METHOD route"; the frozen artifact is keyed by route, since
        # a route's kind does not vary by verb in this surface.
        route = key.split(" ", 1)[1]
        known = frozen.get(route)
        if known is None:
            continue
        new_kinds = set(record["kinds"]) - set(known["kinds"])
        if new_kinds:
            drifted.append(f"{route}: new response kind(s) {sorted(new_kinds)}")
    assert not drifted, "\n".join(drifted)


def test_driving_every_route_reaches_most_of_the_surface(driven):
    """A guard on the driver itself: if `_concrete` stopped producing paths the
    router matches, every assertion above would pass against an empty capture."""
    reached = {key.split(" ", 1)[1] for key in driven.contracts()}
    routes = contract.http_routes()
    assert len(reached) >= len(routes) * 0.9, (
        f"the probe only reached {len(reached)} of {len(routes)} routes; "
        f"unmatched paths: {sorted(driven.unmatched)[:10]}"
    )


def test_an_exemption_states_a_reason():
    routes = contract.http_routes()
    for source in (EXEMPT, EMPTY_ACK):
        for route, reason in source.items():
            assert len(reason) > 20, f"{route}: an exemption needs a real reason"
            assert route in routes, f"{route} is not a route"


def test_an_empty_acknowledgement_is_still_an_observed_one():
    """The list is not a bypass: every route on it must actually have been
    observed answering with an empty object, or it is just a way to silence the
    rule it exists to qualify."""
    schemas = response_capture.load().get("routes") or {}
    for route in EMPTY_ACK:
        observed = [
            value["schema"]
            for key, value in schemas.items()
            if key.rsplit(" [", 1)[0].split(" ", 1)[-1] == route
        ]
        assert observed, f"{route} is on the empty-ack list but was never observed"
        assert any(
            item.get("type") == "object" and not item.get("properties")
            for item in observed
        ), f"{route} is on the empty-ack list but never answered with an empty object"
