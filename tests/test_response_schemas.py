"""The HTTP surface has to have a shape someone can check against.

The Contract scorecard asks for the external surface to be covered by schema.
The obvious way in -- generate it from the routing code -- has a prerequisite
nobody had paid: ``Handler._api`` is one method of ~2000 lines and ~150
branches with response bodies assembled inline, so nothing can be read off it
until it is decomposed. The clause stayed unmet not because it was hard to
agree with but because it was gated behind a refactor.

So the dependency is inverted. Every route already produces real responses
while the suite runs; those are generalised into shapes and frozen. Two
properties follow that a hand-written schema does not have:

  * it cannot describe a response the code does not produce, because it was
    derived from responses the code produced;
  * coverage is a measured number rather than an assertion. 50 of 143 routes
    are pinned today. The remaining 93 are ones no offline test reaches, which
    is a gap in the *tests*, and naming it is the first step to closing it.

What this file checks is that the frozen artifact stays honest: that it
describes real routes, that the shape algebra it is built on is right, and --
the part that earns its keep -- that a change which would break a client is
distinguishable from one that would not.
"""
from __future__ import annotations

import json

import pytest

from openai4s.server import contract
from openai4s.server.response_capture import (
    ARTIFACT,
    Recorder,
    check,
    check_compatible,
    load,
    route_for,
    specificity,
)
from openai4s.server.response_schema import infer, merge, type_of, validate


@pytest.fixture(scope="module")
def frozen():
    return load()


# --------------------------------------------------------------------------
# the shape algebra
# --------------------------------------------------------------------------


def test_a_field_seen_once_and_then_missing_becomes_optional():
    """The load-bearing rule of merging. If `required` were the union, the
    first response that happened to include an optional field would pin it
    forever and every later response would read as a violation."""
    schema = merge(infer({"id": "a", "note": "x"}), infer({"id": "b"}))

    assert schema["required"] == ["id"]
    assert validate({"id": "a", "note": "x"}, schema) == []
    assert validate({"id": "b"}, schema) == []


def test_a_field_present_in_every_observation_stays_guaranteed():
    """The other half: merging must not erode guarantees that hold."""
    schema = merge(infer({"id": "a"}), infer({"id": "b"}))
    assert schema["required"] == ["id"]
    assert validate({}, schema) == ["$.id: required key is missing"]


def test_null_makes_a_field_nullable_not_optional():
    """`{"v": null}` and a missing `v` are different contracts and clients
    break on the difference, so an observed null must not demote the key."""
    schema = merge(infer({"v": 1}), infer({"v": None}))

    assert schema["properties"]["v"]["type"] == ["integer", "null"]
    assert schema["required"] == ["v"]


def test_an_integer_satisfies_a_number_but_not_the_reverse():
    assert validate(3, {"type": "number"}) == []
    assert validate(3.5, {"type": "integer"}) != []


def test_int_and_float_observations_widen_to_number():
    """Otherwise a count that is sometimes 0 and sometimes 0.5 would be typed
    as two alternatives rather than as the one thing it is."""
    assert merge(infer(1), infer(1.5))["type"] == "number"


def test_a_booleans_type_is_not_integer():
    """`bool` is a subclass of `int` in Python, so the naive check types every
    `true` as an integer and a client generated from that expects a number."""
    assert type_of(True) == "boolean"
    assert merge(infer(True), infer(False))["type"] == "boolean"


def test_array_element_shapes_are_merged_not_taken_from_the_first():
    schema = infer([{"a": 1}, {"a": 2, "b": 3}])
    assert schema["items"]["required"] == ["a"]
    assert "b" in schema["items"]["properties"]


def test_an_empty_array_constrains_nothing():
    """Zero elements is not evidence that elements have a shape."""
    assert infer([]) == {"type": "array"}
    assert validate([{"anything": 1}], infer([])) == []


def test_validate_reports_every_problem_not_only_the_first():
    """A shape change usually breaks several fields at once; surfacing them one
    round-trip at a time is the slow way to learn that."""
    schema = infer({"a": 1, "b": "x"})
    problems = validate({"a": "wrong", "b": 2}, schema)
    assert len(problems) == 2


def test_a_new_key_is_reported_as_drift():
    """Additive and safe for existing clients, but still a signal that the
    frozen file is stale -- silence here is how a schema rots."""
    assert validate({"a": 1, "extra": 2}, infer({"a": 1})) == [
        "$.extra: not in the frozen shape"
    ]


# --------------------------------------------------------------------------
# breaking vs additive -- the distinction the gate exists to make
# --------------------------------------------------------------------------


def test_adding_an_optional_field_is_not_a_break():
    before = infer({"id": "a"})
    after = merge(infer({"id": "a"}), infer({"id": "a", "note": "x"}))
    assert check_compatible(before, after) == []


def test_dropping_a_guaranteed_field_is_a_break():
    before = infer({"id": "a", "name": "n"})
    after = infer({"id": "a"})
    assert any("name" in problem for problem in check_compatible(before, after))


def test_a_field_that_stops_being_guaranteed_is_a_break():
    """The field still appears sometimes, which is exactly why this is easy to
    ship by accident and hard for a client to survive."""
    before = infer({"id": "a", "name": "n"})
    after = merge(before, infer({"id": "a"}))
    assert any("name" in problem for problem in check_compatible(before, after))


def test_a_type_that_gains_null_is_a_break():
    before = infer({"count": 1})
    after = merge(before, infer({"count": None}))
    assert any("count" in problem for problem in check_compatible(before, after))


def test_a_nested_break_is_found_and_named_by_path():
    before = infer({"page": {"total": 1}})
    after = infer({"page": {}})
    problems = check_compatible(before, after)
    assert problems and problems[0].startswith("page.")


def test_a_break_inside_an_array_element_is_found():
    before = infer([{"id": "a", "name": "n"}])
    after = infer([{"id": "a"}])
    assert any("[]." in problem for problem in check_compatible(before, after))


def test_check_calls_a_shape_change_breaking_or_additive():
    """A reviewer reads this line to decide whether to look closer, so it must
    not describe a removed field and a new one with the same word."""
    frozen_doc = {"routes": {"GET /x [ok]": {"schema": infer({"id": "a"})}}}
    additive = {
        "routes": {
            "GET /x [ok]": {
                "schema": merge(infer({"id": "a"}), infer({"id": "a", "n": 1}))
            }
        }
    }
    breaking = {"routes": {"GET /x [ok]": {"schema": infer({"other": 1})}}}

    assert "additive" in check(additive, frozen_doc)[0]
    assert "BREAKING" in check(breaking, frozen_doc)[0]


def test_check_reports_a_route_that_lost_its_coverage():
    """A schema quietly disappearing because a test stopped exercising it reads
    as "nothing changed" unless it is named."""
    frozen_doc = {"routes": {"GET /x [ok]": {"schema": infer({"id": "a"})}}}
    assert check({"routes": {}}, frozen_doc) == [
        "GET /x [ok]: frozen but no longer observed"
    ]


# --------------------------------------------------------------------------
# attributing a response to the route that produced it
# --------------------------------------------------------------------------


def test_a_concrete_path_is_filed_under_its_route_pattern():
    """Otherwise the artifact grows with the fixtures instead of the surface."""
    assert route_for("/frames/f-abc123/kernel") == route_for("/frames/f-def456/kernel")


def test_a_specific_route_wins_over_the_catch_all_that_also_matches():
    """`/frames/([^/]+)(?:/.*)?` is the *longer* string and matches every
    sub-route, so ordering candidates by length files every frame sub-route's
    shape under the catch-all and produces a schema describing none of them."""
    # This exact path is the one that discriminates: the catch-all is 23
    # characters and `/frames/([^/]+)/kernel` is 22, so length ordering picks
    # the wrong one here while specificity ordering picks the right one. A
    # longer sub-route like `.../kernel/variables` proves nothing, because it
    # out-ranks the catch-all under either rule.
    assert route_for("/frames/f-1/kernel") == "/frames/([^/]+)/kernel"
    assert len("/frames/([^/]+)(?:/.*)?") > len("/frames/([^/]+)/kernel")
    assert specificity("/frames/([^/]+)/kernel") > specificity(
        "/frames/([^/]+)(?:/.*)?"
    )


def test_an_unrecognised_path_is_not_filed_under_the_root_route():
    """`/` prefixes every path. Accepting it in the prefix fallback would count
    every unknown path as covered, which is the one lie this file must not
    tell -- coverage is the number the whole exercise reports."""
    assert route_for("/definitely-not-a-route/at-all") is None


def test_a_prefix_match_has_to_end_on_a_segment_boundary():
    """`/frames` must not claim `/frameshift`."""
    assert route_for("/frameshift") is None


def test_a_query_string_does_not_create_a_second_route():
    assert route_for("/projects?limit=2") == route_for("/projects")


def test_success_and_failure_are_recorded_as_separate_contracts():
    """Merging them yields a schema in which everything is optional, which is
    indistinguishable from having no schema."""
    recorder = Recorder()
    recorder.observe("GET", "/projects", 200, {"projects": []})
    recorder.observe("GET", "/projects", 404, {"error": "nope"})

    assert set(recorder.shapes) == {"GET /projects [ok]", "GET /projects [error]"}


def test_a_response_from_an_unknown_path_is_flagged_not_silently_dropped():
    recorder = Recorder()
    recorder.observe("GET", "/not-a-route/x", 200, {"a": 1})

    assert recorder.shapes == {}
    assert "GET /not-a-route/x" in recorder.unmatched


def test_a_non_json_body_is_ignored():
    """Downloads and streams leave by another door and have no shape to freeze."""
    recorder = Recorder()
    recorder.observe("GET", "/projects", 200, b"raw bytes")
    assert recorder.shapes == {}


# --------------------------------------------------------------------------
# the committed artifact
# --------------------------------------------------------------------------


def test_the_frozen_artifact_exists_and_parses():
    assert ARTIFACT.is_file(), (
        "docs/response-schemas.json is missing; regenerate it with "
        "scripts/capture_response_schemas.py"
    )
    json.loads(ARTIFACT.read_text("utf-8"))


def test_every_frozen_route_is_a_route_the_server_actually_has(frozen):
    """A schema for a route that no longer exists is worse than no schema: it
    documents a surface the server does not serve."""
    known = contract.http_routes()
    for key in frozen["routes"]:
        route = key.split(" ", 1)[1].rsplit(" [", 1)[0]
        assert route in known, f"{key} is not in the route inventory"


def test_every_frozen_entry_carries_a_usable_shape(frozen):
    """A `type`-less entry validates everything, so it would sit in the file
    looking like coverage while checking nothing."""
    for key, entry in frozen["routes"].items():
        schema = entry.get("schema") or {}
        assert schema.get("type"), f"{key} has no type"
        if schema.get("type") == "object":
            assert "properties" in schema, f"{key} is an object with no fields"


def test_coverage_does_not_silently_regress(frozen):
    """The number this whole exercise reports. It is allowed to go up; it going
    down means a test stopped exercising a route and nobody noticed."""
    covered = {key.split(" ", 1)[1].rsplit(" [", 1)[0] for key in frozen["routes"]}
    assert len(covered) >= 50, (
        f"only {len(covered)} routes are pinned by a frozen shape, down from 50; "
        "regenerate with scripts/capture_response_schemas.py and explain the drop"
    )


def test_the_artifact_records_no_observation_counts(frozen):
    """Counts would make every unrelated new test that touches a route produce
    a diff, and the file is worth reading only when a shape moved."""
    for entry in frozen["routes"].values():
        assert set(entry) == {"schema"}
