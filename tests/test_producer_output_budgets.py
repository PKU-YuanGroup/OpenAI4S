"""Producer-side byte budgets for everything a cell can emit.

Three of the worker's output paths were bounded only when the response frame
was assembled: kernel stderr, the formatted traceback, and — on the R side —
the error string. `_cap` produced exactly the right string and exactly the
wrong allocation, because by the time it ran the whole payload was already in
worker RAM.

Measured on the tree before the fix, with the same code these tests drive:

  * a cell doing 200 x `sys.stderr.write('x' * 1_000_000)` peaked at **452 MB**
    of traced allocation to retain 1 MB of it;
  * formatting a sixty-deep exception chain whose links share one 1 MB message
    peaked at **122 MB** to produce 61 MB of text that was then cut to 1 MB.

Both returned the correct, capped string. That is why nothing caught this, and
it is why every assertion below is about ALLOCATION rather than about the
returned length: `tracemalloc` peak is the observable, and it does not depend
on timing, machine speed, or when a thread happens to be scheduled.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import tracemalloc
from pathlib import Path

import pytest

from openai4s.kernel import worker
from openai4s.kernel.background import BackgroundExecutor
from openai4s.kernel.r_kernel import resolve_r_interpreter

_R_WORKER = Path(__file__).parents[1] / "openai4s" / "kernel" / "r_worker.R"
_REAL_R = resolve_r_interpreter()
_MARKER_LEN = len(worker._TRUNCATION_MARKER)


@pytest.fixture()
def offline_worker(monkeypatch):
    """Run `_run_cell` in-process with no protocol channel underneath it.

    `_run_cell` is the production entry point — the same function `main()`
    calls for an `execute` frame — so this drives the real path. Only the two
    things that need a live subprocess around them are stubbed: the frame
    writer (there is no protocol fd here) and the lazy `host` install.
    """
    frames: list[dict] = []
    monkeypatch.setattr(worker, "_write_frame", frames.append)
    monkeypatch.setitem(worker._NS, "host", object())
    # Warm every import `_run_cell` performs (guards, provenance, linecache)
    # so a measured peak is the cell's output and not a first module import.
    worker._run_cell("1", "warm-up")
    frames.clear()
    return frames


def test_a_cell_writing_200mb_to_stderr_does_not_allocate_200mb(offline_worker):
    """The defect, and the only assertion that can see it.

    `err_buf` was a plain `io.StringIO`, capped in the response builder — the
    same mistake `_StreamingStdout` was written to fix on the other stream,
    left standing on this one. The returned string was already correct, so the
    length assertions below pass either way; the peak is what changes, from
    452 MB to roughly 3 MB.
    """
    code = (
        "import sys\n"
        "for _ in range(200):\n"
        "    sys.stderr.write('x' * 1_000_000)\n"
    )
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        response = worker._run_cell(code, "cell-stderr")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert response["error"] is None
    assert len(response["stderr"]) == worker.MAX_OUTPUT + _MARKER_LEN
    assert response["stderr"].count("...(truncated at") == 1
    assert peak < 24_000_000, (
        f"{peak} bytes peak to retain {worker.MAX_OUTPUT} characters — "
        "stderr is being accumulated whole and capped afterwards"
    )


def _chained_failure(depth: int, message: str) -> None:
    """Raise from inside `except`, so every level chains onto the last."""
    if depth == 0:
        raise ValueError(message)
    try:
        _chained_failure(depth - 1, message)
    except ValueError:
        raise ValueError(message)


def test_a_large_traceback_is_bounded_while_it_is_formatted():
    """Built outside the measurement, formatted inside it.

    The chain is deliberately made of ONE message object shared by sixty links,
    so the exception graph itself costs 1 MB and the peak measured below is the
    formatter's, not the fixture's. `traceback.format_exc()` joins all sixty
    renderings before anything caps the result (122 MB); pulling the generator
    and stopping at the cap never formats the links past the first (3 MB).
    """
    message = "y" * 1_000_000
    caught: BaseException | None = None
    try:
        _chained_failure(60, message)
    except ValueError as exc:
        caught = exc
    assert caught is not None

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        text = worker._bounded_format_exc(caught)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(text) == worker.MAX_OUTPUT + _MARKER_LEN
    assert text.count("...(truncated at") == 1
    assert peak < 16_000_000, (
        f"{peak} bytes peak to keep {worker.MAX_OUTPUT} characters of "
        "traceback — the whole chain is being formatted before it is capped"
    )


def test_a_traceback_that_fits_carries_no_marker():
    """The cap must not claim a cut that did not happen.

    Stopping the generator at the cap is the easy way to attach a marker to
    output that merely ended there, which is the same silent lie the buffers
    guard against from the other direction.
    """
    try:
        1 / 0
    except ZeroDivisionError as exc:
        text = worker._bounded_format_exc(exc)

    assert "truncated" not in text
    assert text.rstrip().endswith("ZeroDivisionError: division by zero")


def test_a_huge_exception_message_still_produces_a_response_frame(offline_worker):
    """The failure the late cap left behind, asserted on the wire size.

    An uncapped `error` pushed the whole response past `_MAX_FRAME_BYTES`,
    `_write_frame` refused it and sent a `log` instead, and `Kernel.execute`
    then blocked on an id that never arrived until the watchdog killed the
    kernel. The frame has to fit, with the line number intact rather than lost
    with the rest of the payload.
    """
    response = worker._run_cell("raise ValueError('e' * 12_000_000)", "cell-error")

    assert response["error"].count("...(truncated at") == 1
    assert len(response["error"]) == worker.MAX_OUTPUT + _MARKER_LEN
    assert response["trace"]["error_lineno"] == 1
    encoded = json.dumps(response, ensure_ascii=False).encode("utf-8")
    assert len(encoded) <= worker._MAX_FRAME_BYTES


def test_a_trapped_system_exit_message_is_bounded_too(offline_worker):
    """`SystemExit('x' * N)` never reaches the traceback formatter.

    It is trapped and reported as a sentence, and that sentence used to
    interpolate the message whole. One marker, not two: the response builder
    no longer caps a string that has already been cut.
    """
    response = worker._run_cell("raise SystemExit('z' * 3_000_000)", "cell-exit")

    assert response["error"].startswith("SystemExit trapped (worker kept alive): ")
    assert response["error"].count("...(truncated at") == 1
    assert len(response["error"]) < 1_100_000


def test_the_bounded_buffer_reports_the_length_it_was_handed():
    """The returned counter is the producer's, never the retained one.

    A short return means "partial write" to every stdlib caller, so reporting
    the retained length would make a bounded stream look like a failing one and
    invite the caller to send the remainder again.
    """
    buf = worker._BoundedBuffer()

    assert buf.write("a" * 10) == 10
    assert not buf.truncated and not buf.full
    assert buf.write("b" * (worker.MAX_OUTPUT * 2)) == worker.MAX_OUTPUT * 2
    assert buf.full and buf.truncated
    # Writes after the cap are still acknowledged in full, and still dropped.
    assert buf.write("c" * 5) == 5

    captured = buf.captured()
    assert len(captured) == worker.MAX_OUTPUT + _MARKER_LEN
    assert captured.count("...(truncated at") == 1
    assert captured.startswith("a" * 10 + "b")


def test_a_buffer_that_was_never_cut_says_nothing():
    buf = worker._BoundedBuffer()
    buf.write("x" * worker.MAX_OUTPUT)  # exactly the cap, nothing dropped
    assert buf.full and not buf.truncated
    assert buf.captured() == "x" * worker.MAX_OUTPUT


def test_stdout_is_still_streamed_and_bounded_through_the_shared_base(monkeypatch):
    """`_StreamingStdout` now inherits its retention bound; prove it kept it.

    Sharing the bound with stderr is the point — two near-identical writers is
    how the two streams drifted apart in the first place — but the streaming
    half is `_StreamingStdout`'s alone and must survive the move.
    """
    frames: list[dict] = []
    monkeypatch.setattr(worker, "_write_frame", frames.append)

    buf = worker._StreamingStdout("cell-1")
    assert buf.write("hello") == 5
    assert buf.write("x" * (worker.MAX_OUTPUT * 2)) == worker.MAX_OUTPUT * 2
    assert buf.write("later") == 5

    texts = [f["text"] for f in frames if f["type"] == "stdout_chunk"]
    assert texts, "the buffer streamed nothing at all"
    assert max(len(t) for t in texts) <= worker._MAX_CHUNK_CHARS
    streamed = "".join(texts)
    assert streamed.count("...(truncated at") == 1
    assert len(streamed) <= worker.MAX_OUTPUT + _MARKER_LEN
    # The streamed tail and the captured result agree about what happened.
    assert buf.captured().count("...(truncated at") == 1
    assert len(buf.captured()) == worker.MAX_OUTPUT + _MARKER_LEN


# --- background capacity ----------------------------------------------------


class _BlockedKernel:
    """A kernel whose cell runs until something stops it."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def execute(self, code, origin="agent", on_chunk=None):
        del code, origin, on_chunk
        self.release.wait(5)
        return {"stdout": "", "error": None}

    def interrupt(self) -> None:
        self.release.set()

    def kill_worker(self) -> None:
        self.release.set()

    def shutdown(self) -> None:
        self.release.set()


def test_a_background_slot_is_claimed_before_the_kernel_is_created():
    """The ordering IS the fix, so the factory asserts on it from inside.

    `launch()` used to check `_closed`, release the lock, spawn a real worker
    process, and register the job afterwards — so concurrent launches all
    passed the check together and all spawned, and any cap tested there would
    have been tested against processes that already existed. The refusal below
    has to cost nothing: no worker for the job that was turned away.
    """
    created: list[_BlockedKernel] = []
    registered_at_spawn: list[int] = []

    def factory() -> _BlockedKernel:
        # At the instant a worker would be spawned, this job is already in the
        # registry — the slot is claimed, not merely intended.
        registered_at_spawn.append(len(executor.list_jobs()))
        kernel = _BlockedKernel()
        created.append(kernel)
        return kernel

    executor = BackgroundExecutor(kernel_factory=factory, dispatcher=None)
    try:
        for _ in range(BackgroundExecutor.MAX_ACTIVE_JOBS):
            executor.launch("hang()")
        assert registered_at_spawn == list(
            range(1, BackgroundExecutor.MAX_ACTIVE_JOBS + 1)
        )
        assert len(created) == BackgroundExecutor.MAX_ACTIVE_JOBS

        with pytest.raises(RuntimeError, match="already running"):
            executor.launch("one too many")
        assert len(created) == BackgroundExecutor.MAX_ACTIVE_JOBS, (
            "the rejected launch spawned a worker anyway — capacity is being "
            "checked after the process exists"
        )
    finally:
        executor.shutdown(timeout_per_job=1.0)


def test_a_spawn_failure_returns_the_slot_it_claimed():
    """Claiming first means the claim has to be given back.

    Without the rollback the cap leaks one slot per failed spawn, and after
    `MAX_ACTIVE_JOBS` failures the executor refuses every launch on a machine
    that is running nothing at all — the error even changes, from the real
    cause to a capacity message that is false.
    """

    def factory():
        raise RuntimeError("no pids left")

    executor = BackgroundExecutor(kernel_factory=factory, dispatcher=None)
    for _ in range(BackgroundExecutor.MAX_ACTIVE_JOBS + 4):
        with pytest.raises(RuntimeError, match="no pids left"):
            executor.launch("boom()")
    assert executor.list_jobs() == []


# --- the R sibling ----------------------------------------------------------


def test_the_r_worker_bounds_are_derived_from_the_python_ones():
    """Both workers answer the same manager, so both owe the same contract.

    The R frame cap is read as the arithmetic it is rather than as a literal:
    a hand-typed number that happens to match today is exactly how the python
    side drifted from `MAX_OUTPUT` before it was derived. Needs no R.
    """
    source = _R_WORKER.read_text(encoding="utf-8")

    cap = re.search(r"\.oai4s_MAX_OUTPUT <- (\d+)L", source)
    assert cap, "the R worker no longer declares a stream cap"
    assert int(cap.group(1)) == worker.MAX_OUTPUT

    frame = re.search(r"\.oai4s_MAX_FRAME_BYTES <- ([^\n]+)", source)
    assert frame, "the R worker has no outbound frame bound"
    expression = frame.group(1).strip()
    expression = expression.replace(".oai4s_MAX_OUTPUT", str(worker.MAX_OUTPUT))
    value = eval(  # noqa: S307 - arithmetic lifted from the R source, no names
        expression.replace("L", ""), {"__builtins__": {}}, {}
    )
    assert value == worker._MAX_FRAME_BYTES


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_the_r_error_message_is_capped_at_the_producer():
    """Drive `.oai4s_cap_message` itself, lifted out of the shipped worker.

    Lifting keeps the code under test the code that ships; sourcing the whole
    file would start the protocol loop, and a hand-written stand-in would be
    the thing that drifts. The helper is what the error path calls before the
    message is pasted into a bigger string and escaped character by character.
    """
    source = _R_WORKER.read_text(encoding="utf-8")
    match = re.search(r"\.oai4s_cap_message <- function.*?\n\}", source, re.S)
    assert match, "the helper this test drives is no longer in the worker"

    script = (
        ".oai4s_MAX_OUTPUT <- 1000000L\n"
        + match.group(0)
        + "\nout <- .oai4s_cap_message(strrep('x', 5000000L))\n"
        "short <- .oai4s_cap_message('boom')\n"
        "cat(nchar(out, type='chars'), nchar(short, type='chars'), '\\n')\n"
    )
    result = subprocess.run(
        [_REAL_R, "--vanilla", "-e", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[:400]
    capped, short = (int(n) for n in result.stdout.split()[-2:])
    assert capped == 1_000_000 + len("\n...(truncated at 1000000 characters)")
    assert short == len("boom")  # nothing to cut, nothing said


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_real_r_caps_a_giant_error_instead_of_wiring_it(tmp_path):
    """End to end: `stop()` with a 50 MB message must not become a 50 MB field.

    This is the assertion that fails on the unfixed worker — the error string
    was the one field in the R response with no cap on it at all, and it went
    through `.oai4s_esc` a character at a time on its way to the pipe.
    """
    from openai4s.kernel.r_kernel import spawn_r_kernel

    kernel = spawn_r_kernel(cwd=str(tmp_path), rscript=_REAL_R)
    try:
        result = kernel.execute("stop(strrep('x', 50000000L))")
        assert result["error"] is not None
        # Bounded is the property. The marker is asserted only when OUR cap is
        # what bit: R truncates `conditionMessage` itself (measured here: a 50 MB
        # `stop()` arrives as ~8 KB, so `.oai4s_cap_message` returns it
        # untouched), and which of the two limits applies depends on the R build.
        # Requiring the marker unconditionally would make this test assert the
        # local R version rather than the fix.
        assert len(result["error"]) <= 1_000_000 + 80
        if len(result["error"]) > 1_000_000:
            assert result["error"].count("...(truncated at") == 1
        # A short error is still reported verbatim.
        small = kernel.execute("stop('nope')")
        assert "nope" in small["error"] and "truncated" not in small["error"]
    finally:
        kernel.shutdown()


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_the_r_capture_file_stops_growing_once_the_cap_is_passed(tmp_path):
    """The R side bounded the *read* and left the *write* unbounded.

    `.oai4s_slurp` reads at most one byte past the cap and says why: reading the
    whole file "allocated 300 MB in this worker to keep 1 MB of it". But the
    file it declines to read is still written in full — `sink()` sends every
    byte the cell prints to a tempfile, so a runaway cell fills the disk (a
    tmpfs `/tmp`, on much of Linux, meaning RAM) with output that has already
    been decided against. worker.py's equivalent buffer drops those bytes at
    `write` time; this one kept them and then ignored them.

    Probed from inside the cell rather than by sampling from the test thread,
    because a sampler races the writer and would pass or fail on scheduling.
    The cell finds its own sink file through `showConnections`, records the
    size after an expression that crosses the cap, prints 20 MB more, and
    records the size again. The second number is the whole test: frozen means
    the producer stopped, and growing means it did not.
    """
    from openai4s.kernel.r_kernel import spawn_r_kernel

    probe = tmp_path / "probe"
    code = "\n".join(
        (
            "cons <- showConnections(all = TRUE)",
            'hit <- grepl("oai4s-out-", cons[, "description"], fixed = TRUE)',
            'path <- cons[hit, "description"][1]',
            f'writeLines(path, {str(probe) + ".path"!r})',
            "cat(strrep('a', 1200000L))",
            f'writeLines(as.character(file.info(path)$size), {str(probe) + ".1"!r})',
            "cat(strrep('b', 20000000L))",
            f'writeLines(as.character(file.info(path)$size), {str(probe) + ".2"!r})',
        )
    )

    kernel = spawn_r_kernel(cwd=str(tmp_path), rscript=_REAL_R)
    try:
        result = kernel.execute(code)
    finally:
        kernel.shutdown()

    assert result.get("error") is None, result.get("error")
    assert (
        probe.parent / "probe.path"
    ).exists(), "the cell could not find its own capture file; the probe proves nothing"
    crossed = int(float((probe.parent / "probe.1").read_text().strip()))
    after = int(float((probe.parent / "probe.2").read_text().strip()))

    assert crossed > worker.MAX_OUTPUT, (
        "the first expression did not cross the cap, so the test never reached "
        f"the condition it is about: {crossed}"
    )
    # 20 MB was printed after the cap was already passed. Every one of those
    # bytes is discarded by `.oai4s_cap` later, so none of them should have
    # reached the disk.
    assert after <= 4 * worker.MAX_OUTPUT, (
        f"the capture file grew from {crossed} to {after} bytes writing output "
        "that was already destined to be thrown away"
    )

    # And the answer is still correct: head retained, cut announced once.
    assert result["stdout"].startswith("a")
    assert result["stdout"].count("...(truncated at") == 1
    # The retained part is the head, not a window that slid into the second
    # expression's output. Sliced before the marker because the marker's own
    # wording contains a "b".
    kept = result["stdout"].split("\n...(truncated at")[0]
    assert set(kept) == {"a"}, sorted(set(kept))[:5]


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_the_r_message_capture_file_stops_growing_too(tmp_path):
    """The same bound on the other stream.

    `message()` has its own sink and its own tempfile, and a cell that warns in
    a loop reaches the cap the same way a chatty one does. Asserted separately
    rather than assumed from the stdout test: the two are one helper called
    with different arguments, and "called with the wrong connection" is a defect
    that looks identical to "not called".
    """
    from openai4s.kernel.r_kernel import spawn_r_kernel

    probe = tmp_path / "msg"
    code = "\n".join(
        (
            "cons <- showConnections(all = TRUE)",
            'hit <- grepl("oai4s-msg-", cons[, "description"], fixed = TRUE)',
            'path <- cons[hit, "description"][1]',
            "message(strrep('a', 1200000L))",
            f'writeLines(as.character(file.info(path)$size), {str(probe) + ".1"!r})',
            "message(strrep('b', 20000000L))",
            f'writeLines(as.character(file.info(path)$size), {str(probe) + ".2"!r})',
        )
    )

    kernel = spawn_r_kernel(cwd=str(tmp_path), rscript=_REAL_R)
    try:
        result = kernel.execute(code)
    finally:
        kernel.shutdown()

    assert result.get("error") is None, result.get("error")
    crossed = int(float((probe.parent / "msg.1").read_text().strip()))
    after = int(float((probe.parent / "msg.2").read_text().strip()))

    assert crossed > worker.MAX_OUTPUT, crossed
    assert (
        after <= 4 * worker.MAX_OUTPUT
    ), f"the message capture file grew from {crossed} to {after} bytes"
    assert result["stderr"].count("...(truncated at") == 1
