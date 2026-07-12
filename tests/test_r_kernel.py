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
import stat
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
def test_real_r_variable_inspector_does_not_force_bindings_or_object_methods(tmp_path):
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
        assert variables["active_trap"] == {
            "name": "active_trap",
            "type": "active_binding",
        }
        assert variables["later"]["type"] == "language"
        assert "preview" not in variables["later"]
        assert variables["score"]["length"] == 1
        assert variables["samples"]["length"] == 2
        assert variables["custom"] == {"name": "custom", "type": "list"}
        assert kernel.execute("cat(forced)")["stdout"] == "FALSE"
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
