"""A bound that is enforced and unreported is a wrong answer, not a missing one.

Seven buffers in this tree drop input to stay inside a budget. Two of them said
nothing at all, and the more damaging of the two is reachable from an agent
cell: `host.bash` cut stdout at 30,000 characters and stderr at 8,000, returned
the tail, and recorded in the durable audit `chars: 30000` beside a `sha256` of
that tail presented as the command's stdout digest. Nothing on either surface
distinguished that from a command that printed exactly 30,000 characters -- and
`len(stdout)` cannot distinguish them either, which is why the fact has to
travel from the drainer that watched the bytes go past.

`_safe_result` reported `workspace_diff.truncated` all along. The rule was
already stated in that function and applied only to the member that had not
been cut.
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from pathlib import Path

import pytest

from openai4s.execution.budget import channel_counters

# -- the shape itself ---------------------------------------------------------


def test_the_four_numbers_answer_the_question_without_being_recomputed():
    counters = channel_counters(seen=5_000_000, retained=30_000)

    assert counters == {
        "seen_bytes": 5_000_000,
        "retained_bytes": 30_000,
        "dropped_bytes": 4_970_000,
        "truncated": True,
    }


def test_a_channel_that_stopped_on_the_budget_is_not_called_truncated():
    """The ambiguous case, and the reason `retained` alone is not a derivation:
    a command that printed exactly the budget and one that printed a hundred
    times it are the same `len(stdout)`."""
    assert channel_counters(seen=30_000, retained=30_000)["truncated"] is False
    assert channel_counters(seen=30_001, retained=30_000)["truncated"] is True


def test_a_negative_loss_is_clamped_rather_than_published():
    """`seen` and `retained` are counted by different objects at different
    times. A negative drop is a bug in the caller, not a fact about a stream."""
    counters = channel_counters(seen=10, retained=40)

    assert counters["dropped_bytes"] == 0
    assert counters["truncated"] is False


def test_the_unit_is_named_by_what_was_actually_counted():
    """`sdk/bash.py` counts characters and `sink_drain.py` counts bytes; a
    record that says bytes while holding characters is the class of claim this
    module exists to stop."""
    chars = channel_counters(seen=2, retained=1, unit="chars", prefix="stdout_")

    assert set(chars) == {
        "stdout_seen_chars",
        "stdout_retained_chars",
        "stdout_dropped_chars",
        "stdout_truncated",
    }


# -- the consumers ------------------------------------------------------------


def test_the_reference_caller_still_emits_the_shape_that_was_on_the_wire():
    """`SinkCapture.counters` is pinned to the eight keys it published before
    the helper existed, not the other way round: the helper had to reproduce an
    established wire shape, or every consumer of a cell's `usage` moves."""
    from openai4s.kernel import sink_drain

    class _Sink:
        def __init__(self, seen: int, retained: int) -> None:
            self.seen = seen
            self.retained = retained
            self.dropped = max(0, seen - retained)

    capture = sink_drain.SinkCapture.__new__(sink_drain.SinkCapture)
    capture._out = _Sink(100, 40)  # type: ignore[attr-defined]
    capture._err = _Sink(7, 7)  # type: ignore[attr-defined]

    assert capture.counters() == {
        "stdout_seen_bytes": 100,
        "stdout_retained_bytes": 40,
        "stdout_dropped_bytes": 60,
        "stdout_truncated": True,
        "stderr_seen_bytes": 7,
        "stderr_retained_bytes": 7,
        "stderr_dropped_bytes": 0,
        "stderr_truncated": False,
    }


@pytest.mark.parametrize(
    "module, symbol",
    [
        ("openai4s.kernel.sink_drain", "SinkCapture.counters"),
        ("openai4s.jobs", "Job.to_dict"),
        ("openai4s.sdk.bash", "BashExecutor.run"),
    ],
)
def test_no_producer_spells_a_counter_key_of_its_own(module, symbol):
    """One definition, asserted as an absence rather than as a substring.

    "Does the source mention the helper?" is satisfied by a function that calls
    it for stdout and hand-writes stderr -- which is how four spellings of this
    accounting came to exist in the first place, each correct on the day it was
    written. What the contract actually forbids is a producer naming a counter
    key itself, so that is what is checked.
    """
    import importlib
    import re

    obj = importlib.import_module(module)
    for part in symbol.split("."):
        obj = getattr(obj, part)
    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))

    spelled = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.search(r"(?:^|_)(?:seen|retained|dropped)_(?:bytes|chars)$", node.value)
    }
    assert not spelled, sorted(spelled)


def test_a_bash_tail_counts_what_it_dropped_as_well_as_what_it_kept():
    from openai4s.sdk import bash

    sink = bash._BoundedTail(10)
    sink.feed("a" * 6)
    sink.feed("b" * 9)

    assert sink.value() == "b" * 9
    assert sink.truncated is True
    assert sink.seen == 15
    assert sink.retained == 9


def test_the_durable_bash_audit_says_the_command_printed_more_than_it_kept():
    """The record an operator reads back. Without this it asserted, in a field
    named `chars` beside a digest, that a five-million-character command
    printed thirty thousand characters."""
    from openai4s.host.bash import BashAuthorizationService

    capability = _capability()
    safe = BashAuthorizationService._safe_result(
        capability,
        {
            "status": "completed",
            "exit_code": 0,
            "stdout": "x" * 30_000,
            "stderr": "",
            "stdout_seen_chars": 5_000_000,
            "stderr_seen_chars": 0,
            "duration_ms": 12,
        },
    )

    assert safe["stdout"]["truncated"] is True
    assert safe["stdout"]["seen_chars"] == 5_000_000
    assert safe["stdout"]["dropped_chars"] == 4_970_000
    assert safe["stdout"]["retained_chars"] == 30_000
    # The stream that was not cut must not be described as though it were.
    assert safe["stderr"]["truncated"] is False


def test_the_audit_will_not_accept_a_claim_smaller_than_what_it_holds():
    """Clamped like `exit_code` and `status` beside it. The worker reports this
    number and the worker is the side the sandbox exists to distrust; a `seen`
    below the retained length would publish a negative loss as a fact."""
    from openai4s.host.bash import BashAuthorizationService

    safe = BashAuthorizationService._safe_result(
        _capability(),
        {
            "status": "completed",
            "exit_code": 0,
            "stdout": "y" * 500,
            "stderr": "",
            "stdout_seen_chars": 3,
            "duration_ms": 1,
        },
    )

    assert safe["stdout"]["seen_chars"] == 500
    assert safe["stdout"]["dropped_chars"] == 0
    assert safe["stdout"]["truncated"] is False


def _capability():
    from openai4s.host.bash import _IssuedCapability

    return _IssuedCapability(
        token="t" * 32,
        command_sha256="d" * 64,
        cwd="/tmp/ws",
        workspace="/tmp/ws",
        allowed_root="/tmp",
        frame_id="f-1",
        generation="3",
        challenge="c" * 16,
        category="read_only",
        domains=(),
        issued_at=0.0,
        expires_at=60.0,
    )


# -- the receipt contract, read from both ends --------------------------------


def test_every_field_adoption_reads_is_a_field_the_receipt_writes():
    """The two halves of one contract, separated by a daemon restart.

    A key the reader asks for and the writer never wrote is invisible to both
    sides: `dict.get` substitutes a default and the adopted job comes back
    subtly wrong rather than absent. Walking the reader's own source is the
    only way to assert this without a restart.
    """
    from openai4s import jobs

    source = Path(inspect.getfile(jobs)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    adopt = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_adopt_abandoned"
    )

    read: set[str] = set()
    for node in ast.walk(adopt):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in {
            "get",
            "__getitem__",
        }:
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != "data":
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            read.add(str(node.args[0].value))
    for node in ast.walk(adopt):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "data"
            and isinstance(node.slice, ast.Constant)
        ):
            read.add(str(node.slice.value))

    assert read, "the AST walk found no reads; it is asserting nothing"
    assert read <= set(jobs.RECEIPT_FIELDS), sorted(read - set(jobs.RECEIPT_FIELDS))


def test_the_receipt_writes_exactly_the_declared_fields_and_no_host_pid():
    """`pid` was written on every persist -- a host pid, on disk, under the data
    dir -- and read by nothing: an adopted job is unconditionally marked
    `abandoned` and never probed."""
    from openai4s import jobs

    job = jobs.Job("bash", "echo hi", "/tmp/ws")

    assert set(job.receipt()) == set(jobs.RECEIPT_FIELDS)
    assert "pid" not in job.receipt()


def test_a_dead_worker_says_the_stderr_it_shows_is_a_tail(tmp_path):
    """The counters T2 added, reaching a reader for the first time.

    `_StderrTail` computed `seen_bytes`/`dropped_bytes` correctly and the death
    path joined only the text, so an operator opening the diagnostic was handed
    64 KiB of a multi-megabyte stream with nothing saying so -- reading the end
    of a failure as though it were the whole of it. A real worker, killed after
    flooding its own fd2, because the message is assembled on the death path
    and a hand-built tail would assert nothing about it.

    Redacted from the user by `public_exception` before publication, exactly as
    before: this is the operator diagnostic.
    """
    from openai4s.kernel.manager import _STDERR_TAIL_BYTES, Kernel

    flood = _STDERR_TAIL_BYTES + 50_000
    kernel = Kernel(cwd=str(tmp_path))
    try:
        with pytest.raises(RuntimeError) as caught:
            kernel.execute(
                "import os\n" f"os.write(2, b'E' * {flood})\n" "os._exit(1)\n"
            )
    finally:
        kernel.shutdown()

    message = str(caught.value)
    assert "exited unexpectedly" in message
    assert re.search(
        r"stderr tail: \d+ of \d+ bytes kept, \d+ dropped", message
    ), message[-400:]


def test_the_jobs_panel_renders_the_drop(monkeypatch):
    """The UI consumer. `jobs.py` has carried these counters since T2 and the
    only trace of a cut anywhere in the interface was a notice prepended inside
    the output modal, which a reader has to open the modal to see."""
    app_js = Path("openai4s/server/webui/app.js").read_text(encoding="utf-8")

    assert "cust.jobs.dropped" in app_js
    assert re.search(
        r"j\.truncated\s*\?", app_js
    ), "the job row does not branch on the truncated flag"
    assert re.search(r"j\.dropped_bytes", app_js)
