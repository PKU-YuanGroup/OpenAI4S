"""A capture split across xdist workers must equal the single-process one.

`docs/response-schemas.json` claims to have been captured from real responses,
and until the suite ran in parallel there was exactly one process to capture
them in. `tests/conftest.py` writes the capture once per session, so four
workers each writing `destination` would have left whichever finished last:
a fraction of the evidence in a file that still looked complete -- the
wrong-rather-than-absent provenance the artifact's own note warns about.

Workers now leave shares that `response_capture.assemble` merges after pytest
exits. The property that makes that sound is here: splitting the same
observations across processes and merging them must reach the schema one
process would have reached, including for a route both processes saw with
different optional fields. Every xdist share also states its run ID and the
number of workers expected, so a missing or stale share is rejected before a
document is written. Otherwise the file could silently describe whichever
workers happened to leave evidence behind.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.server import response_capture
from scripts import capture_response_schemas

#: One route seen twice with different optional fields -- the case the merge
#: exists for -- plus a second route only one side ever sees.
OBSERVATIONS = [
    ("GET", "/agents", 200, {"agents": [], "total": 0}),
    ("GET", "/agents", 200, {"agents": [{"id": "a"}], "total": 1, "extra": True}),
    ("GET", "/projects", 200, {"projects": []}),
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
    response_capture.save_partial(
        left, destination, "gw0", worker_count=2, run_id="run-one"
    )
    response_capture.save_partial(
        right, destination, "gw1", worker_count=2, run_id="run-one"
    )

    assert response_capture.assemble(destination, require_complete=True) == 2
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


def test_numeric_widening_is_the_same_across_worker_groupings(tmp_path):
    """A worker-local merge must not change the assembled schema.

    ``number`` admits integers, but the old merge only removed ``integer``
    when those were the *only* two types.  Serially observing null, integer,
    then float therefore retained a redundant integer; a worker that first
    combined integer and float did not.  The capture then described xdist's
    grouping rather than the responses.
    """
    observations = [
        ("GET", "/agents", 200, {"value": None}),
        ("GET", "/agents", 200, {"value": 1}),
        ("GET", "/agents", 200, {"value": 1.5}),
    ]
    single = _recorder(observations).document()
    destination = tmp_path / "numeric.json"
    response_capture.save_partial(_recorder(observations[:1]), destination, "gw0")
    response_capture.save_partial(_recorder(observations[1:]), destination, "gw1")

    assert response_capture.assemble(destination) == 2
    assert json.loads(destination.read_text("utf-8")) == single
    value = single["routes"]["GET /agents [ok]"]["schema"]["properties"]["value"]
    assert value["type"] == ["null", "number"]


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


def test_destination_filename_is_not_interpreted_as_a_glob(tmp_path):
    """Legal filename punctuation must not change which shares are found.

    ``Path.glob`` treats square brackets as a character class.  Interpolating
    the destination name into that pattern therefore missed the share written
    for a destination such as ``capture[1].json`` and returned zero.
    """
    destination = tmp_path / "capture[1].json"
    response_capture.save_partial(_recorder(OBSERVATIONS), destination, "gw0")

    assert response_capture.assemble(destination) == 1
    assert (
        json.loads(destination.read_text("utf-8")) == _recorder(OBSERVATIONS).document()
    )


def test_a_failed_share_write_never_publishes_truncated_json(tmp_path, monkeypatch):
    """A dead worker is a missing share, not a corrupt final share.

    Assembly runs before the capture script interprets pytest's exit code.  A
    direct write to the final name could therefore leave a truncated document
    whose JSON error hid the useful test failure and contradicted the stated
    missing-share behavior.
    """
    destination = tmp_path / "captured.json"
    target = response_capture.partial_path(destination, "gw0")
    temporary = tmp_path / ".interrupted-share.tmp"

    class _InterruptedFile:
        name = str(temporary)

        def __enter__(self):
            return self

        def __exit__(self, _kind, _value, _traceback):
            return False

        def write(self, payload):
            temporary.write_text(payload[:8], encoding="utf-8")
            raise OSError("worker interrupted")

    monkeypatch.setattr(
        response_capture.tempfile,
        "NamedTemporaryFile",
        lambda **_kwargs: _InterruptedFile(),
    )

    with pytest.raises(OSError, match="worker interrupted"):
        response_capture.save_partial(_recorder(OBSERVATIONS), destination, "gw0")

    assert not target.exists()
    assert not temporary.exists()


def test_a_share_publish_failure_makes_the_xdist_run_fail(tmp_path):
    """Worker success must include durable publication of its capture share."""
    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("occupied", encoding="utf-8")
    env = dict(os.environ)
    env["OPENAI4S_CAPTURE_SCHEMAS"] = str(not_a_directory / "captured.json")
    target = (
        "tests/test_response_capture_assembly.py::"
        "test_an_unsplit_run_has_nothing_to_assemble"
    )

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "-n",
            "1",
            "--dist",
            "loadfile",
            target,
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # This process starts its own xdist controller and worker while the
        # parent suite is already running four workers.  Startup alone can
        # exceed 30 seconds on a loaded CI host even though the child test has
        # completed, so keep the bound generous enough to test publication
        # failure rather than scheduler latency.
        timeout=90,
    )

    assert proc.returncode != 0, proc.stdout
    assert "response capture share for gw0 could not be published" in proc.stdout


def test_capture_runner_caps_xdist_fanout(tmp_path, monkeypatch):
    """A high-core host must not spawn an unbounded set of heavy workers."""
    called = {}

    def _run(argv, **kwargs):
        called["argv"] = argv
        called["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(capture_response_schemas.subprocess, "run", _run)
    destination = tmp_path / "capture.json"

    assert capture_response_schemas._run_suite(destination) == 0
    argv = called["argv"]
    cap = argv.index("--maxprocesses")
    assert argv[cap + 1] == str(capture_response_schemas._MAX_WORKERS) == "4"
    assert called["kwargs"]["env"]["OPENAI4S_CAPTURE_SCHEMAS"] == str(destination)


def test_a_missing_share_is_rejected_even_when_every_route_remains(tmp_path):
    """Compatibility drift cannot stand in for worker completeness.

    One worker drives every route, so another missing worker may contribute
    only the nullable state of an already-covered field.  Narrowing the field
    back to string is intentionally non-breaking/additive to ``check``; only
    xdist's expected worker count can prove the evidence is incomplete.
    """
    observations = [
        ("GET", "/agents", 200, {"value": "present"}),
        ("GET", "/agents", 200, {"value": None}),
    ]
    frozen = _recorder(observations).document()
    surviving = _recorder(observations[:1])
    problems = response_capture.check(surviving.document(), frozen)
    assert all("BREAKING" not in problem for problem in problems), problems

    destination = tmp_path / "captured.json"
    response_capture.save_partial(
        surviving,
        destination,
        "gw0",
        worker_count=2,
        run_id="incomplete-run",
    )

    with pytest.raises(ValueError, match="expected 2 worker shares, found 1"):
        response_capture.assemble(destination, require_complete=True)
    assert not destination.exists()


def test_shares_from_different_runs_are_never_mixed(tmp_path):
    destination = tmp_path / "captured.json"
    response_capture.save_partial(
        _recorder(OBSERVATIONS[:2]),
        destination,
        "gw0",
        worker_count=2,
        run_id="old-run",
    )
    response_capture.save_partial(
        _recorder(OBSERVATIONS[2:]),
        destination,
        "gw1",
        worker_count=2,
        run_id="new-run",
    )

    with pytest.raises(ValueError, match="different xdist runs"):
        response_capture.assemble(destination, require_complete=True)
    assert not destination.exists()


def test_successful_assembly_consumes_its_shares(tmp_path):
    """A later run must not inherit evidence from an earlier one."""
    destination = tmp_path / "captured.json"
    share = response_capture.save_partial(_recorder(OBSERVATIONS), destination, "gw0")

    assert response_capture.assemble(destination) == 1
    assert not share.exists()
    assert response_capture.assemble(destination) == 0
