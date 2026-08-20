"""A capture split across xdist workers must equal the single-process one.

`docs/response-schemas.json` claims to have been captured from real responses,
and until the suite ran in parallel there was exactly one process to capture
them in. `tests/conftest.py` writes the capture once per session, so four
workers each writing `destination` would have left whichever finished last:
a quarter of the routes in a file that still looked complete -- the
wrong-rather-than-absent provenance the artifact's own note warns about.

Workers now leave shares that `response_capture.assemble` merges after pytest
exits. The property that makes that sound is here: splitting the same
observations across processes and merging them must reach the schema one
process would have reached, including for a route both processes saw with
different optional fields. If it did not, the file would silently start
describing whichever worker happened to observe a route first.
"""

from __future__ import annotations

import json

from openai4s.server import response_capture

#: One route seen twice with different optional fields -- the case the merge
#: exists for -- plus a second route only one side ever sees.
OBSERVATIONS = [
    ("GET", "/agents", 200, {"agents": [], "total": 0}),
    ("GET", "/agents", 200, {"agents": [{"id": "a"}], "total": 1, "extra": True}),
    ("GET", "/settings", 200, {"model": "m"}),
    ("GET", "/agents", 404, {"error": "no such agent"}),
]


def _recorder(observations):
    recorder = response_capture.Recorder()
    for method, path, code, body in observations:
        recorder.observe(method, path, code, body)
    return recorder


def test_a_split_capture_assembles_to_the_single_process_capture(tmp_path):
    """The whole claim, stated as an equality.

    The split is deliberately unkind: the two halves interleave the same route,
    so neither share alone holds the shape and the merge has to widen one into
    the other rather than pick a winner.
    """
    single = _recorder(OBSERVATIONS).document()

    destination = tmp_path / "captured.json"
    left = _recorder(OBSERVATIONS[0::2])
    right = _recorder(OBSERVATIONS[1::2])
    response_capture.save_partial(left, destination, "gw0")
    response_capture.save_partial(right, destination, "gw1")

    assert response_capture.assemble(destination) == 2
    assembled = json.loads(destination.read_text("utf-8"))

    assert assembled == single
    # And say out loud what the equality is protecting: the optional field that
    # only one share saw survived, and did not become required.
    schema = assembled["routes"]["GET /agents [ok]"]["schema"]
    assert "extra" in schema["properties"]
    assert "extra" not in schema["required"]


def test_the_merge_does_not_depend_on_which_share_is_read_first(tmp_path):
    """Workers finish in whatever order the runner gives them.

    `assemble` sorts the shares by name so the result is a property of the
    observations rather than of the scheduling, but the merge itself has to be
    order-independent too -- otherwise the sort would only be hiding a
    document that changes run to run.
    """
    forward = tmp_path / "forward.json"
    reverse = tmp_path / "reverse.json"
    response_capture.save_partial(_recorder(OBSERVATIONS[0::2]), forward, "gw0")
    response_capture.save_partial(_recorder(OBSERVATIONS[1::2]), forward, "gw1")
    # Same two shares, names swapped, so sorting reads them the other way round.
    response_capture.save_partial(_recorder(OBSERVATIONS[1::2]), reverse, "gw0")
    response_capture.save_partial(_recorder(OBSERVATIONS[0::2]), reverse, "gw1")

    response_capture.assemble(forward)
    response_capture.assemble(reverse)
    assert json.loads(forward.read_text("utf-8")) == json.loads(
        reverse.read_text("utf-8")
    )


def test_an_unsplit_run_has_nothing_to_assemble(tmp_path):
    """The single-process path still writes `destination` itself.

    `assemble` must leave that file exactly as it found it; a version that
    wrote an empty document when it found no shares would erase every serial
    capture on the way past.
    """
    destination = tmp_path / "captured.json"
    response_capture.save(_recorder(OBSERVATIONS).document(), destination)
    before = destination.read_text("utf-8")

    assert response_capture.assemble(destination) == 0
    assert destination.read_text("utf-8") == before


def test_a_share_that_never_arrived_leaves_the_route_visibly_missing(tmp_path):
    """The failure mode has to be loud, because it is the one that matters.

    A worker that dies without writing its share takes its routes out of the
    merge. That must read as a gap in the capture -- which `check` reports and
    the gate fails on -- and never as a route that quietly kept its old frozen
    shape.
    """
    frozen = _recorder(OBSERVATIONS).document()

    destination = tmp_path / "captured.json"
    response_capture.save_partial(_recorder(OBSERVATIONS[0::2]), destination, "gw0")
    assert response_capture.assemble(destination) == 1
    observed = json.loads(destination.read_text("utf-8"))

    problems = response_capture.check(observed, frozen)
    assert any("no longer observed" in problem for problem in problems), problems
