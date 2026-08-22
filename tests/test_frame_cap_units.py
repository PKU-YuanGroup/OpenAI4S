"""Two caps that measured different things, and a refusal one tool skipped.

**Bytes against characters.** `MAX_OUTPUT` caps captured stdout and stderr at
1,000,000 *characters*. `_MAX_FRAME_BYTES` capped the whole outbound frame at
8,000,000 *bytes*, with a comment claiming it sat "above the largest legitimate
frame (a response carries stdout and stderr, each capped at MAX_OUTPUT)". That
holds for ASCII and nothing else: a character is up to 4 bytes in UTF-8 and up
to 6 in JSON's `\\uXXXX` escape.

Measured — both streams filled to the documented cap:

    ascii     2,000,059 bytes   ok
    CJK      12,000,059 bytes   DROPPED
    control  12,000,059 bytes   DROPPED

So a cell obeying every documented limit had its entire response frame replaced
by a drop note, losing stderr, the exception text, `error_lineno`, `guards` and
`usage`. Only stdout survived, and only because the manager backfills it from
the streamed chunks. Chinese output at the cap is not a corner case in this
project.

The comment is what made it look handled, which is why the fix derives the
number from `MAX_OUTPUT` instead of choosing a bigger one.

**The refusal `web_download` skipped.** `secret_path_key` is not the permission
target — that is deliberately the URL, since the interesting question is which
host is contacted. It is the hard refusal that stops a tool overwriting a
credential file. `write_file` and `edit_file` both declared it; `web_download`,
the third and only other tool with `writes_files = True`, did not. So
`write_file(".env", ...)` was refused and `web_download(url, ".env")` was not,
on the same workspace — which under the CLI is the user's own cwd.
"""

from __future__ import annotations

import json

import pytest

from openai4s.kernel import worker as worker_mod
from openai4s.tools.registry import TOOL_TYPES

# --------------------------------------------------------------------------
# the units
# --------------------------------------------------------------------------


def _frame_bytes(char: str) -> int:
    frame = {
        "type": "response",
        "id": "1",
        "stdout": char * worker_mod.MAX_OUTPUT,
        "stderr": char * worker_mod.MAX_OUTPUT,
    }
    return len(json.dumps(frame).encode("utf-8"))


def test_output_within_the_documented_caps_is_never_dropped():
    """The defect, in the three encodings that behave differently. Anything at
    or under the character caps must survive whatever alphabet it is in."""
    for label, char in (("ascii", "x"), ("CJK", "中"), ("control", "\x01")):
        size = _frame_bytes(char)
        assert size <= worker_mod._MAX_FRAME_BYTES, (
            f"{label} output at the documented cap serialises to {size:,} bytes, "
            f"over the {worker_mod._MAX_FRAME_BYTES:,} backstop"
        )


def test_the_backstop_is_derived_from_the_character_cap():
    """A second hand-picked number would drift apart from `MAX_OUTPUT` again the
    next time either moves. This is the actual fix — the larger value is only
    its consequence."""
    assert worker_mod._MAX_FRAME_BYTES == (
        worker_mod._JSON_WORST_BYTES_PER_CHAR * 3 * worker_mod.MAX_OUTPUT + 2_000_000
    )


def test_six_bytes_is_really_the_worst_json_can_do():
    """The whole derivation rests on this constant. If JSON could emit more per
    character, the cap would be back under a legitimate frame."""
    worst = max(
        len(json.dumps(chr(code), ensure_ascii=False).encode("utf-8")) - 2
        # minus the surrounding ASCII quotes
        for code in list(range(0, 128)) + [0x4E2D, 0x1F600]
    )
    assert worst <= worker_mod._JSON_WORST_BYTES_PER_CHAR


def test_the_backstop_still_stops_a_runaway():
    """It exists to bound the allocation. Raising it must not mean removing it
    — `print("x" * 200_000_000)` is what it was added for."""
    runaway = {"type": "response", "id": "1", "stdout": "x" * 200_000_000}
    assert len(json.dumps(runaway).encode("utf-8")) > worker_mod._MAX_FRAME_BYTES


def test_an_oversized_frame_still_answers_the_caller(monkeypatch):
    """Dropping the frame outright would hang the cell waiting on it —
    `Kernel.execute` blocks until the watchdog kills the worker, which reads as
    a hang rather than a refusal. So the replacement keeps the same type and
    the same id.

    Driven, not grepped: the first version of this asserted `"id" in source`,
    which is true of almost any Python source and would have passed against a
    replacement that dropped the id entirely.
    """
    written: list[str] = []

    class _Sink:
        def write(self, text):
            written.append(text)

        def flush(self):
            return None

    # The worker resolves its protocol channel through `_proto_out()`, so that
    # is what has to be replaced. Patching a module-level name that does not
    # exist would leave this skipping forever on its own except clause —
    # a test that reports success by never running.
    sink = _Sink()
    monkeypatch.setattr(worker_mod, "_proto_out", lambda: sink)
    # `_write_frame` serialises on a lock the worker installs on `sys` at
    # startup, which no test process has. Supplying it is what makes this run
    # instead of skip — and a skipping test reports success without asserting
    # anything, which is the failure mode this whole file exists to catch.
    import sys as _sys
    import threading as _threading

    if not hasattr(_sys, "_openai4s_protocol_lock"):
        monkeypatch.setattr(
            _sys, "_openai4s_protocol_lock", _threading.Lock(), raising=False
        )
    oversized = {
        "type": "response",
        "id": "req-42",
        "stdout": "x" * (worker_mod._MAX_FRAME_BYTES + 10),
    }
    try:
        worker_mod._write_frame(oversized)
    except Exception as error:  # noqa: BLE001
        raise AssertionError(f"the frame writer could not run: {error}") from error

    assert written, "nothing was written at all"
    frame = json.loads(written[-1])
    assert frame["type"] == "response", "the caller cannot match this to its request"
    assert frame["id"] == "req-42", "the id is gone; the cell waits forever"
    assert frame["error"], "no reason given for the drop"
    assert len(json.dumps(frame).encode("utf-8")) <= worker_mod._MAX_FRAME_BYTES


# --------------------------------------------------------------------------
# the refusal
# --------------------------------------------------------------------------


def test_every_file_writing_tool_refuses_secret_paths():
    """The defect, as the asymmetry it was. Written as "every tool that writes"
    rather than naming `web_download`, so a fourth one cannot be added without
    this failing.

    `derived_write_path` is the one exemption, and it is narrow: it means the
    caller names no destination, so `secret_path_key` has nothing to refuse.
    `compute_result` is the case — its harvest lands under
    `<workspace>/hpc/<job_id>/`, where `job_id` is regex-sanitised and
    containment-checked. The exemption is not taken on trust:
    `tests/test_compute_owner_and_harvest.py` drives `_safe_harvest_dest` with a
    traversing id and a symlinked directory, so a tool cannot claim it and skip
    the confinement.
    """
    missing = [
        tool.name
        for tool in (cls() for cls in TOOL_TYPES)
        if tool.writes_files
        and not tool.secret_path_key
        and not tool.derived_write_path
    ]
    assert missing == [], f"these write files with no secret-path refusal: {missing}"


def test_the_derived_path_exemption_is_not_a_way_out_of_the_refusal():
    """A tool may not declare both: naming a destination argument *and* claiming
    the destination is host-derived is a contradiction, and the pair would read as
    protection while the invariant above skipped it."""
    both = [
        tool.name
        for tool in (cls() for cls in TOOL_TYPES)
        if tool.derived_write_path and tool.secret_path_key
    ]
    assert both == [], f"these claim a derived path and also name one: {both}"

    # And the exemption is only meaningful for tools that write at all.
    idle = [
        tool.name
        for tool in (cls() for cls in TOOL_TYPES)
        if tool.derived_write_path and not tool.writes_files
    ]
    assert idle == [], f"these declare a derived write path but write nothing: {idle}"


def test_web_download_names_its_destination_argument():
    """`secret_path_key` has to name the argument that becomes a filename. The
    wrong key is a declaration that reads as protection and gates nothing."""
    tool = next(cls() for cls in TOOL_TYPES if cls.name == "web_download")
    assert tool.secret_path_key == "path"
    assert tool.secret_path_key in tool.parameters["properties"]


def test_the_permission_target_is_still_the_url():
    """These are different controls and the fix must not have swapped one for
    the other: the approval asks which host is contacted, and the secret-path
    check is a refusal nobody is asked about."""
    tool = next(cls() for cls in TOOL_TYPES if cls.name == "web_download")
    assert tool.permission_target_key == "url"


@pytest.mark.parametrize("filename", [".env", "id_rsa", "credentials.json"])
def test_the_declared_key_resolves_to_the_path_argument(filename):
    """Reads the value the dispatcher would test, so a renamed parameter fails
    here rather than silently disarming the refusal."""
    tool = next(cls() for cls in TOOL_TYPES if cls.name == "web_download")
    assert (
        tool.secret_path({"url": "https://example.com/x", "path": filename}) == filename
    )
