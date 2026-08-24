"""R kernel contracts — kernel/r_kernel.py + kernel/r_worker.R.

The host executes exactly two kinds of instructions: python cells on worker.py
and R cells on r_worker.R, both driven by the SAME manager over the same
JSON-per-line frame protocol. The offline tests here exercise the host-side
plumbing (argv fd discipline, execute, persistence, restart/generation, death,
interrupt) against tests/fixtures/fake_rscript.py — a python stand-in that
speaks the exact protocol — so they need no R installation. The real-R
integration tests at the bottom run only when an Rscript is resolvable.
"""

import os
import signal
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

from openai4s.kernel.r_kernel import r_argv, resolve_r_interpreter, spawn_r_kernel

_FAKE = Path(__file__).parent / "fixtures" / "fake_rscript.py"
_R_WORKER = Path(__file__).parents[1] / "openai4s" / "kernel" / "r_worker.R"


@pytest.fixture()
def fake_rscript():
    _FAKE.chmod(_FAKE.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(_FAKE)


# --- argv / fd discipline ---------------------------------------------------


def test_r_argv_shape_carries_the_fd_swap(fake_rscript):
    argv = r_argv(fake_rscript)
    assert argv[0] == "/bin/sh" and argv[1] == "-c"
    # the shell line must alias protocol OUT to fd3, protocol IN to fd4, blank
    # user stdin, and dump stray fd-1 writes on stderr — worker.py's dup2
    # discipline expressed as redirections. `exec` keeps pid == R's pid so
    # SIGINT interrupts reach R.
    for token in ("exec", "3>&1", "4<&0", "</dev/null", "1>&2"):
        assert token in argv[2]
    assert argv[3] == fake_rscript
    assert argv[4].endswith("r_worker.R")


# --- protocol against the fake interpreter -----------------------------------


def test_execute_persistence_and_stray_stdout(fake_rscript, tmp_path):
    k = spawn_r_kernel(cwd=str(tmp_path), rscript=fake_rscript)
    try:
        assert k.is_alive()
        # one live process serves every cell (persistent counter)
        assert k.execute("COUNT")["stdout"] == "1"
        assert k.execute("COUNT")["stdout"] == "2"
        # a stray print to real stdout lands on stderr, never the wire
        r = k.execute("NOISE")
        assert r["stdout"] == "quiet" and r["error"] is None
    finally:
        k.shutdown()
    assert not k.is_alive()


def test_r_variable_inspector_uses_dedicated_idle_protocol(fake_rscript, tmp_path):
    kernel = spawn_r_kernel(cwd=str(tmp_path), rscript=fake_rscript)
    try:
        assert kernel.execute("COUNT")["stdout"] == "1"
        inspected = kernel.inspect_variables()
        assert inspected["type"] == "variables_response"
        assert inspected["variables"] == [
            {
                "name": "counter",
                "type": "integer",
                "kind": "vector",
                "length": 1,
                "preview": "[1]",
                "fingerprint": "01234567",
            }
        ]
        # Inspection is not an execute frame and does not advance namespace.
        assert kernel.execute("COUNT")["stdout"] == "2"
    finally:
        kernel.shutdown()


def test_r_variable_inspector_source_fails_closed_on_dynamic_bindings():
    source = _R_WORKER.read_text("utf-8")
    inspector = source.split("# --- read-only variable inspection", 1)[1].split(
        "# --- protocol channels", 1
    )[0]

    assert "bindingIsActive" in inspector
    assert "substitute" in inspector
    assert "lockEnvironment(.oai4s_inspector, bindings = TRUE)" in inspector
    assert 'lockBinding(".oai4s_inspector", globalenv())' in inspector
    for forbidden in (
        "serialize(",
        "saveRDS(",
        "object.size(",
        "deparse(",
        "capture.output(",
        " get(",
    ):
        assert forbidden not in inspector


def test_r_kernel_cannot_inherit_host_api_keys_or_loader_injection(
    fake_rscript, monkeypatch, tmp_path
):
    marker = "synthetic-host-r-secret-never-in-kernel"
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", marker)
    monkeypatch.setenv("OPENAI4S_ARK_API_KEY", marker)
    monkeypatch.setenv("OPENAI_API_KEY", marker)
    monkeypatch.setenv("HF_TOKEN", marker)
    monkeypatch.setenv("LD_PRELOAD", "/tmp/openai4s-never-load.so")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/tmp/openai4s-never-load.dylib")
    monkeypatch.setenv("OPENAI4S_PROVENANCE_OFF", "1")

    kernel = spawn_r_kernel(cwd=str(tmp_path), rscript=fake_rscript)
    try:
        for name in (
            "OPENAI4S_LLM_API_KEY",
            "OPENAI4S_ARK_API_KEY",
            "OPENAI_API_KEY",
            "HF_TOKEN",
            "LD_PRELOAD",
            "DYLD_INSERT_LIBRARIES",
        ):
            assert kernel.execute(f"ENV:{name}")["stdout"] == "<missing>"
        assert kernel.execute("ENV:OPENAI4S_PROVENANCE_OFF")["stdout"] == "1"
    finally:
        kernel.shutdown()


def test_child_stderr_flood_does_not_deadlock(fake_rscript, tmp_path):
    """A cell whose process writes >64KB to inherited fd2 must not wedge the
    kernel: the manager's drain thread keeps the stderr pipe empty (this used
    to deadlock forever — nothing read stderr until worker death)."""
    k = spawn_r_kernel(cwd=str(tmp_path), rscript=fake_rscript)
    try:
        box = {}
        t = threading.Thread(target=lambda: box.update(r=k.execute("FLOOD")))
        t.start()
        t.join(15)
        assert not t.is_alive(), "stderr flood deadlocked the cell"
        assert box["r"]["stdout"] == "flooded"
    finally:
        k.shutdown()


def test_worker_death_raises_with_stderr(fake_rscript, tmp_path):
    k = spawn_r_kernel(cwd=str(tmp_path), rscript=fake_rscript)
    try:
        with pytest.raises(RuntimeError, match="exited unexpectedly"):
            k.execute("DIE")
    finally:
        k.shutdown()


def test_restart_resets_state_and_bumps_generation(fake_rscript, tmp_path):
    k = spawn_r_kernel(cwd=str(tmp_path), rscript=fake_rscript)
    try:
        assert k.execute("COUNT")["stdout"] == "1"
        gen = k.generation
        k.restart()
        assert k.generation == gen + 1
        # a fresh process: the counter starts over — and the argv override
        # survived the respawn (still the fake R, not the python worker)
        assert k.execute("COUNT")["stdout"] == "1"
    finally:
        k.shutdown()


def test_interrupt_returns_interrupted_result_and_keeps_worker(fake_rscript, tmp_path):
    k = spawn_r_kernel(cwd=str(tmp_path), rscript=fake_rscript)
    try:
        box = {}
        t = threading.Thread(target=lambda: box.update(r=k.execute("SLEEP")))
        t.start()
        time.sleep(0.5)
        k.interrupt()
        t.join(10)
        assert box["r"]["interrupted"] is True
        assert box["r"]["error"] == "Interrupted"
        # the worker survives an interrupt (namespace-preserving contract)
        assert k.is_alive()
        assert k.execute("COUNT")["stdout"] == "1"
    finally:
        k.shutdown()


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal dispositions")
def test_interrupt_reaches_r_even_when_the_spawning_process_ignores_sigint(
    fake_rscript, tmp_path
):
    """A backgrounded daemon (`./start.sh &`, nohup) runs with SIGINT set to
    SIG_IGN; SIG_IGN survives the sh wrapper's exec, and R — exactly like the
    CPython running this fake — installs its interrupt handler only when the
    inherited disposition is not SIG_IGN. Before the spawn-boundary reset in
    kernel/transport.py the worker was therefore born uninterruptible:
    Kernel.interrupt()'s SIGINT was discarded in the child and the cell ran to
    completion while the caller reported a delivered stop. The fake reproduces
    that faithfully (revert the reset and this test fails by sleeping out the
    full 30s), so the regression stays covered on machines without R.

    The interrupt is armed by the cell's first drained chunk, not a delay —
    the same pattern as the real-R interrupt tests — so the signal cannot land
    before the fake has entered its interruptible sleep.
    """
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        k = spawn_r_kernel(cwd=str(tmp_path), rscript=fake_rscript)
        try:
            fired = threading.Event()

            def interrupt_once_streaming(_text: str) -> None:
                if not fired.is_set():
                    fired.set()
                    k.interrupt()

            r = k.execute("SLEEP", on_chunk=interrupt_once_streaming)
            assert fired.is_set(), "the cell finished without streaming a chunk"
            assert r["interrupted"] is True, r
            assert r["error"] == "Interrupted"
            # and the worker survived, as the interrupt contract promises
            assert k.is_alive()
            assert k.execute("COUNT")["stdout"] == "1"
        finally:
            k.shutdown()
    finally:
        signal.signal(signal.SIGINT, old)


# --- interpreter resolution ---------------------------------------------------


def test_resolver_prefers_env_then_named_r_then_path(monkeypatch, tmp_path):
    from openai4s.kernel import environments as envmod
    from openai4s.kernel import r_kernel as rk_mod

    class _E:
        def __init__(self, name, rscript):
            self.name = name
            self.rscript = rscript

    # 1) an explicitly selected env wins outright
    env = _E("custom", str(tmp_path / "custom-Rscript"))
    assert resolve_r_interpreter(env) == str(tmp_path / "custom-Rscript")

    # 2) else the discovered env literally named "r"
    monkeypatch.setattr(
        envmod,
        "discover_environments",
        lambda force=False: [_E("other", "/x/Rscript"), _E("r", "/r/Rscript")],
    )
    assert resolve_r_interpreter() == "/r/Rscript"

    # 3) else any discovered R-carrying env
    monkeypatch.setattr(
        envmod,
        "discover_environments",
        lambda force=False: [_E("py", None), _E("other", "/x/Rscript")],
    )
    assert resolve_r_interpreter() == "/x/Rscript"

    # 4) else Rscript on PATH; and None when nowhere
    monkeypatch.setattr(envmod, "discover_environments", lambda force=False: [])
    monkeypatch.setattr(rk_mod.shutil, "which", lambda name: "/usr/bin/Rscript")
    assert resolve_r_interpreter() == "/usr/bin/Rscript"
    monkeypatch.setattr(rk_mod.shutil, "which", lambda name: None)
    assert resolve_r_interpreter() is None


def test_spawn_without_any_r_raises(monkeypatch):
    from openai4s.kernel import environments as envmod
    from openai4s.kernel import r_kernel as rk_mod

    monkeypatch.setattr(envmod, "discover_environments", lambda force=False: [])
    monkeypatch.setattr(rk_mod.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="no R interpreter"):
        spawn_r_kernel()


# --- real R integration (skipped when no R is installed) ----------------------

_REAL_R = resolve_r_interpreter()


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_real_r_file_reads_are_executed_observations_without_global_masks(tmp_path):
    """The R observer sees calls that ran, never paths merely in source.

    This exercises the observer without the fd-3/fd-4 transport because some
    macOS R builds refuse to reopen a pipe through ``/dev/fd``.  The ordinary
    real-worker tests below cover that transport independently.
    """

    source_literal = str(_R_WORKER).replace("\\", "\\\\").replace('"', '\\"')
    tmp_literal = str(tmp_path).replace("\\", "\\\\").replace('"', '\\"')
    script = f"""
source_lines <- readLines("{source_literal}", warn = FALSE)
cut <- grep("# --- one cell", source_lines, fixed = TRUE)[1] - 1L
setwd("{tmp_literal}")
eval(parse(text = source_lines[seq_len(cut)]), envir = .GlobalEnv)
.oai4s_lineage$install()
dir.create("nested")
writeLines(c("value", "7"), "nested/live.csv")
writeLines(c("value", "99"), "dead.csv")
.oai4s_lineage$begin()
if (FALSE) read.csv("dead.csv")
table_value <- utils::read.csv(file.path("nested", "live.csv"))
write.csv(table_value, "write-only.csv", row.names = FALSE)
observed <- .oai4s_lineage$finish()
cat(paste(observed, collapse = "|"), "\\n", sep = "")
cat(table_value$value[[1]], "\\n", sep = "")
cat(exists("read.csv", envir = .GlobalEnv, inherits = FALSE), "\\n", sep = "")
"""
    completed = subprocess.run(
        [_REAL_R, "--vanilla", "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == ["nested/live.csv", "7", "FALSE"]
    assert (tmp_path / "write-only.csv").is_file()


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_real_r_persistent_namespace_error_lineno_and_quit_guard(tmp_path):
    k = spawn_r_kernel(cwd=str(tmp_path), rscript=_REAL_R)
    try:
        r1 = k.execute('x <- 40; cat("hi\n"); x + 2')
        assert r1["error"] is None
        assert "hi" in r1["stdout"] and "[1] 42" in r1["stdout"]
        # namespace persists across cells
        assert "[1] 400" in k.execute("x * 10")["stdout"]
        # error carries the failing expression's line + call name
        r3 = k.execute('f <- function() stop("boom")\nf()')
        assert "boom" in (r3["error"] or "")
        assert r3["trace"]["error_lineno"] == 2
        assert r3["trace"]["error_call"] == "f"
        # quit() cannot kill the worker
        r4 = k.execute("quit()")
        assert "disabled" in (r4["error"] or "")
        assert k.is_alive()
        # files written land in cwd (the workspace) for artifact capture
        k.execute('writeLines("data", "out.txt")')
        assert (tmp_path / "out.txt").read_text().strip() == "data"
        assert r1["usage"]["wall_s"] >= 0
    finally:
        k.shutdown()


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_real_r_variable_inspector_reports_values_without_invoking_bindings(tmp_path):
    """What the R inspector may and may not do to a session to describe it.

    It used to describe nothing. `substitute()` does not substitute bindings
    from `.GlobalEnv` — the only environment this inspector reads — so every
    ordinary variable came back as an opaque `symbol` with no type, length or
    preview. The feature was a list of names, and the previous version of this
    test wrote that outcome down as the contract ("the safe fallback is an
    opaque symbol for every ordinary binding").

    Safe it was, and the safety is real: inspection must not run user code.
    But the two things were conflated. Reading an ordinary binding runs
    nothing; only a promise has anything to force. So the inspector probes with
    `substitute` first — the one tool that can see an unforced promise without
    running it — and reads the binding for real when the probe says nothing.

    The cost, stated rather than hidden: on builds where the probe cannot
    distinguish a promise (current R, in `.GlobalEnv`), inspecting a
    `delayedAssign` binding forces it. Only that binding, and only when
    inspected. A promise whose body raises degrades to the same opaque answer
    instead of failing the whole inspection — which is how this first showed
    up, with one `stop()` in a promise turning the entire variable list into
    "failed closed".
    """
    kernel = spawn_r_kernel(cwd=str(tmp_path), rscript=_REAL_R)
    try:
        setup = kernel.execute(
            "forced <- FALSE\n"
            "delayedAssign('later', { forced <<- TRUE; stop('promise forced') })\n"
            "makeActiveBinding('active_trap', function(value) stop('active binding called'), .GlobalEnv)\n"
            "score <- 0.93\n"
            "samples <- list(1L, 'x')\n"
            "custom <- structure(list(1L), class='custom_class')"
        )
        assert setup["error"] is None

        inspected = kernel.inspect_variables()
        variables = {item["name"]: item for item in inspected["variables"]}

        # The hard guarantee, unchanged: an active binding is never invoked.
        # Its accessor is arbitrary user code and calling it would be running
        # the session rather than describing it.
        assert variables["active_trap"] == {
            "name": "active_trap",
            "type": "active_binding",
        }

        # The fix: ordinary variables describe themselves.
        assert variables["score"]["type"] == "double"
        assert variables["score"]["length"] == 1
        assert variables["samples"]["type"] == "list"
        assert variables["samples"]["length"] == 2
        assert variables["custom"] == {"name": "custom", "type": "list"}

        # The promise stays opaque either way — exposed by the probe on builds
        # that can, or degraded after a failed read on builds that cannot.
        assert variables["later"]["type"] in {"language", "symbol"}
        assert "preview" not in variables["later"]

        # And one raising promise does not take the rest of the list with it:
        # every variable above was still reported.
        assert {"score", "samples", "custom", "active_trap"} <= set(variables)
    finally:
        kernel.shutdown()


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_real_r_worker_survives_hostile_cells(tmp_path):
    """The adversarially-verified kill scenarios: each of these used to halt
    the non-interactive interpreter (fatal uncaught condition) or poison the
    wire — one frame may fail, the worker must survive them all."""
    k = spawn_r_kernel(cwd=str(tmp_path), rscript=_REAL_R)
    try:
        # non-UTF-8 bytes in output (latin-1 'café') — .oai4s_esc iconv repair
        r = k.execute("cat(rawToChar(as.raw(c(0x63, 0x61, 0x66, 0xe9))))")
        assert r["error"] is None and k.is_alive()
        # options(OutDec=",") must not corrupt usage numbers on the wire
        k.execute('options(OutDec=",")')
        assert isinstance(k.execute("1.5 * 2")["usage"]["wall_s"], float)
        # closeAllConnections() closes the protocol connections — reopen path
        assert k.execute("closeAllConnections(); 1 + 1")["error"] is None
        assert "[1] 4" in k.execute("2 + 2")["stdout"]
        # idle SIGINT is latched by R and fires at the next checkpoint — the
        # per-frame guard absorbs it as an Interrupted response
        k.interrupt()
        time.sleep(0.3)
        r = k.execute("41 + 1")
        assert k.is_alive()
        if r["interrupted"]:
            r = k.execute("41 + 1")
        assert "[1] 42" in r["stdout"]
        # byte-true output cap: multibyte output must not leak 3-4x the cap
        big = k.execute('cat(strrep("中", 600000))')  # 3 bytes/char ≈ 1.8MB
        assert len(big["stdout"].encode("utf-8")) < 1_100_000
        assert "truncated" in big["stdout"]
    finally:
        k.shutdown()


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_real_r_bounds_captured_output_without_reading_it_whole(tmp_path):
    """A chatty R cell must not cost its own size in worker memory.

    `.oai4s_slurp` read the entire capture file and handed it to `.oai4s_cap`,
    which threw all but the first megabyte away — so a cell printing 300 MB
    allocated 300 MB to keep 1 MB. `worker.py` has bounded the same output at
    write time since the streaming buffer landed; the R half never did.

    The visible behaviour is unchanged by design, which is exactly why this
    test alone cannot check the fix: reverting to the whole-file read leaves
    every assertion below green. `test_real_r_slurp_reads_at_most_the_cap`
    drives `.oai4s_slurp` directly and is the one that fails. Mutation testing
    is what exposed that, and this docstring says so rather than leaving a
    reader to assume the pair is redundant.
    """
    kernel = spawn_r_kernel(cwd=str(tmp_path), rscript=_REAL_R)
    try:
        result = kernel.execute("for (i in 1:400000) cat('line', i, '\n')")
        assert result["error"] is None
        stdout = result["stdout"]
        # Capped, and it says so.
        assert 1_000_000 <= len(stdout) <= 1_000_100
        assert "truncated" in stdout[-80:]

        # Short output is untouched — no marker on something that ended.
        short = kernel.execute("cat('brief\n')")
        assert short["stdout"] == "brief\n"
        assert "truncated" not in short["stdout"]
    finally:
        kernel.shutdown()


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_real_r_reports_no_peak_rss_rather_than_zero(tmp_path):
    """`0` is a measurement this worker cannot make.

    Peak RSS comes from `/proc/self/status`, which does not exist on macOS —
    the platform this project is developed on — so every R cell reported a
    peak RSS of zero and the usage row recorded it as fact. Absent is the true
    answer, and the column is nullable so that it can be given.

    On Linux the value is `VmHWM`, a process-lifetime high-water mark rather
    than a per-cell peak; that is recorded in the worker rather than silently
    presented as a per-cell number.
    """
    import sys

    kernel = spawn_r_kernel(cwd=str(tmp_path), rscript=_REAL_R)
    try:
        result = kernel.execute("x <- 1")
        usage = result["usage"]
        assert "wall_s" in usage and "cpu_s" in usage
        if sys.platform == "linux":
            assert usage["peak_rss_kb"] is None or usage["peak_rss_kb"] > 0
        else:
            assert usage["peak_rss_kb"] is None, (
                f"a platform with no /proc reported {usage['peak_rss_kb']!r} "
                "as a measured peak RSS"
            )
    finally:
        kernel.shutdown()


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_real_r_refuses_an_execute_frame_with_no_host_sinks(tmp_path):
    """No sinks means no capture, and the worker must refuse before it runs.

    The R worker's output is bounded by the host draining two fifos, so a cell
    started without them would execute for real and then report nothing at all
    -- which reads to a user as "the code printed nothing" rather than as the
    protocol break it is. The refusal has to land BEFORE the side effect, so
    the cell offered here writes a file: the file's absence is the assertion.

    Driven through a Kernel built without sink capture rather than by hand-
    writing a frame, because what is under test is the worker's response to the
    frame the manager actually sends when capture is off.
    """
    from openai4s.kernel.manager import Kernel
    from openai4s.kernel.r_kernel import r_argv

    evidence = tmp_path / "the-cell-ran"
    kernel = Kernel(
        dispatcher=None,
        cwd=str(tmp_path),
        mode="r",
        argv=r_argv(_REAL_R),
        capture_sinks=False,
    )
    try:
        result = kernel.execute(f"writeLines('x', {str(evidence)!r})")
    finally:
        kernel.shutdown()

    assert result["error"] is not None
    assert "capture sinks" in result["error"], result["error"]
    assert (
        not evidence.exists()
    ), "the cell ran anyway; the refusal is after the side effect"


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_real_r_streams_stdout_while_the_cell_runs(tmp_path):
    """Live output, which the R half of the product did not have.

    The host has always accepted `stdout_chunk` frames — `manager.py` collects
    them and the Notebook renders them — and the R worker never emitted one.
    A long R cell showed nothing at all until it finished, while the same cell
    in Python reported as it went.

    This used to say a chatty `for` loop "still arrives in one piece", because
    the worker could only flush between top-level expressions. That stopped
    being true when the host began draining the fifo: the drain sees bytes as
    R's connection buffer empties, which happens *inside* an expression too.
    The worker-side flush remains, for the expression that ends without
    filling that buffer.
    """
    kernel = spawn_r_kernel(cwd=str(tmp_path), rscript=_REAL_R)
    try:
        chunks: list[str] = []
        result = kernel.execute(
            "cat('first\n')\nSys.sleep(0.05)\ncat('second\n')\ncat('third\n')",
            on_chunk=chunks.append,
        )
        assert result["error"] is None
        assert len(chunks) >= 2, f"no live output: {chunks}"
        assert "first" in chunks[0]

        # ...and the final response is the output once, not twice. The manager
        # falls back to the accumulated chunks only when the frame carries no
        # stdout, so a worker that sent both would double it.
        assert result["stdout"] == "first\nsecond\nthird\n"
    finally:
        kernel.shutdown()


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_real_r_streaming_is_bounded_like_the_capture(tmp_path):
    """A runaway cell must not stream its whole output to the host in frames
    and then have it capped in the response — the host paying for output it
    will not keep."""
    kernel = spawn_r_kernel(cwd=str(tmp_path), rscript=_REAL_R)
    try:
        chunks: list[str] = []
        result = kernel.execute(
            "for (i in 1:200000) cat('line', i, '\n')\ncat('done\n')",
            on_chunk=chunks.append,
        )
        assert result["error"] is None
        streamed = sum(len(chunk) for chunk in chunks)
        assert streamed <= 1_000_001, f"{streamed:,} characters streamed"
    finally:
        kernel.shutdown()


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
def test_real_r_error_lineno_survives_streaming(tmp_path):
    """The flush sits inside the loop that walks parsed expressions, next to
    the srcref bookkeeping `error_lineno` depends on. Breaking that would trade
    a feature for a regression in the one thing the R worker documents as hard
    to get right."""
    kernel = spawn_r_kernel(cwd=str(tmp_path), rscript=_REAL_R)
    try:
        # `\\n` so the R source is three lines. Written with a bare `\n` the
        # first time, which made the R source five lines and `error_lineno` 5 —
        # correctly. The test was wrong, not the worker.
        result = kernel.execute("cat('a\\n')\ncat('b\\n')\nstop('boom')")
        assert result["error"] is not None
        assert "boom" in result["error"]
        assert result["trace"]["error_lineno"] == 3
    finally:
        kernel.shutdown()


@pytest.mark.skipif(_REAL_R is None, reason="no Rscript resolvable on this machine")
@pytest.mark.skipif(os.name != "posix", reason="POSIX signal dispositions")
def test_real_r_interrupt_survives_a_spawning_process_that_ignores_sigint(tmp_path):
    """The real-R half of the backgrounded-daemon regression.

    SIG_IGN on SIGINT survives exec through sh into Rscript, and R honours an
    inherited ignore instead of installing its interrupt handler (verified
    with lldb against the stuck child: sigaction(SIGINT)=SIG_IGN,
    R_interrupts_pending stayed 0) — so before kernel/transport.py reset the
    disposition at the spawn boundary, a daemon launched as a shell
    background job dropped every R interrupt while Python kernels kept
    working. Spawning under SIG_IGN here proves the reset with the real
    interpreter; the fake-based sibling covers machines without R.
    """
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        kernel = spawn_r_kernel(cwd=str(tmp_path), rscript=_REAL_R)
        try:
            fired = threading.Event()

            def interrupt_once_streaming(_text: str) -> None:
                # One SIGINT, armed by the first drained chunk: the drain keeps
                # delivering after the first, and a conda Rscript can take
                # seconds to start — a timed signal lands in that window.
                if not fired.is_set():
                    fired.set()
                    kernel.interrupt()

            result = kernel.execute(
                "invisible(lapply(1:4000, function(i) cat(strrep('x', 1e6))))",
                on_chunk=interrupt_once_streaming,
            )
            assert fired.is_set(), "the cell finished without streaming a chunk"
            assert result.get("interrupted") is True, result
            assert kernel.is_alive()
            after = kernel.execute("cat('still here\n')")
            assert after["stdout"] == "still here\n"
        finally:
            kernel.shutdown()
    finally:
        signal.signal(signal.SIGINT, old)
