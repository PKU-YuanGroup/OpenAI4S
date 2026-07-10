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
