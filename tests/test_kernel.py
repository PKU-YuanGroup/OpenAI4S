"""Kernel tests: persistent namespace, print capture, error attribution,
usage accounting, and host_call RPC round-trip (dispatcher stubbed)."""
import threading

import pytest

from openai4s.kernel import Kernel


def _echo_dispatcher(method, args):
    if method == "ping":
        return "pong"
    if method == "add":
        return sum(args[0]["nums"])
    raise ValueError(f"unknown method {method}")


def test_print_capture():
    with Kernel(dispatcher=_echo_dispatcher) as k:
        r = k.execute("print('hello')")
        assert r["stdout"] == "hello\n"
        assert r["error"] is None


def test_persistent_namespace():
    with Kernel(dispatcher=_echo_dispatcher) as k:
        k.execute("x = 41")
        r = k.execute("print(x + 1)")
        assert r["stdout"].strip() == "42"


def test_expr_echo():
    with Kernel(dispatcher=_echo_dispatcher) as k:
        r = k.execute("21 * 2")
        assert r["stdout"].strip() == "42"


def test_error_lineno():
    with Kernel(dispatcher=_echo_dispatcher) as k:
        r = k.execute("a = 1\nb = 2\nraise ValueError('boom')")
        assert r["error"] is not None
        assert "ValueError" in r["error"]
        assert r["trace"]["error_lineno"] == 3


def test_usage_accounting():
    with Kernel(dispatcher=_echo_dispatcher) as k:
        r = k.execute("sum(range(1000))")
        u = r["usage"]
        assert set(u) == {"wall_s", "cpu_s", "peak_rss_kb"}
        assert u["wall_s"] >= 0 and u["peak_rss_kb"] > 0


def test_host_call_roundtrip():
    with Kernel(dispatcher=_echo_dispatcher) as k:
        r = k.execute("reply = host._call('ping', [])\n" "print(reply)")
        assert r["stdout"].strip() == "pong"


def test_host_call_with_args():
    with Kernel(dispatcher=_echo_dispatcher) as k:
        r = k.execute("print(host._call('add', [{'nums': [1, 2, 3, 4]}]))")
        assert r["stdout"].strip() == "10"


def test_host_call_error_propagates():
    with Kernel(dispatcher=_echo_dispatcher) as k:
        r = k.execute(
            "try:\n"
            "    host._call('nope', [])\n"
            "except RuntimeError as e:\n"
            "    print('caught:', 'unknown method' in str(e))"
        )
        assert r["stdout"].strip() == "caught: True"


# --- frame-protocol contract tests (PR 10) ---------------------------------
# These lock the CURRENT worker/manager wire contract before any extraction
# of kernel/manager internals. They follow the existing Kernel(...) patterns
# exactly — do not add new frame types or reader loops here.


def _contract_dispatcher(method, args):
    if method == "soft":
        # single-key {"error": ...} is the soft-fail shape: the manager must
        # route it onto the error channel, NOT hand it back as data.
        return {"error": "soft failure from host"}
    if method == "error_plus_data":
        # an error key WITH siblings is ordinary data, not a soft-fail.
        return {"error": "x", "detail": "still data"}
    if method == "none":
        return None
    raise ValueError(f"unknown method {method}")


def test_response_frame_shape():
    """The final response frame carries exactly the documented key set."""
    with Kernel(dispatcher=_echo_dispatcher) as k:
        r = k.execute("print('shape')")
        assert set(r) == {
            "type",
            "id",
            "stdout",
            "stderr",
            "error",
            "interrupted",
            "trace",
            "guards",
            "usage",
        }
        assert r["type"] == "response"
        assert r["interrupted"] is False
        assert set(r["trace"]) == {"error_lineno", "error_call"}
        assert isinstance(r["guards"], dict)


def test_stderr_captured_separately():
    with Kernel(dispatcher=_echo_dispatcher) as k:
        r = k.execute("import sys\nsys.stderr.write('warn!\\n')\nprint('out')")
        assert r["stdout"].strip() == "out"
        assert "warn!" in r["stderr"]
        assert r["error"] is None


def test_stdout_chunks_stream_via_on_chunk():
    """stdout_chunk frames stream live; on_chunk sees the same text the final
    response frame reports."""
    chunks = []
    with Kernel(dispatcher=_echo_dispatcher) as k:
        r = k.execute("print('live')", on_chunk=chunks.append)
        assert "live" in "".join(chunks)
        assert r["stdout"] == "live\n"


def test_explicit_cell_id_roundtrips_through_worker_response():
    with Kernel(dispatcher=_echo_dispatcher) as kernel:
        result = kernel.execute("print('identified')", cell_id="cell-shared")
        automatic_one = kernel.execute("pass")
        automatic_two = kernel.execute("pass")

    assert result["id"] == "cell-shared"
    assert result["stdout"] == "identified\n"
    assert automatic_one["id"]
    assert automatic_one["id"] != automatic_two["id"]


def test_save_artifact_host_call_carries_canonical_and_declared_cell_ids():
    calls = []

    def dispatcher(method, args):
        calls.append((method, args))
        return {"ok": True}

    with Kernel(dispatcher=dispatcher) as kernel:
        inherited = kernel.execute(
            "print(host.save_artifact('result.csv')['ok'])",
            cell_id="cell-artifact",
        )
        explicit = kernel.execute(
            "host.save_artifact('result.csv', producing_cell_id='manual-cell')",
            cell_id="cell-other",
        )

    assert inherited["error"] is None
    assert inherited["stdout"].strip() == "True"
    assert explicit["error"] is None
    save_calls = [call for call in calls if call[0] == "save_artifact"]
    assert save_calls == [
        (
            "save_artifact",
            [
                {
                    "path": "result.csv",
                    "inputVersionIds": [],
                    "priority": 0,
                    "executionCellId": "cell-artifact",
                    "producingCellId": "cell-artifact",
                }
            ],
        ),
        (
            "save_artifact",
            [
                {
                    "path": "result.csv",
                    "inputVersionIds": [],
                    "producingCellId": "manual-cell",
                    "priority": 0,
                    "executionCellId": "cell-other",
                }
            ],
        ),
    ]


def test_host_call_soft_fail_single_key_error_dict():
    """Dispatcher returning {'error': msg} (and nothing else) surfaces in the
    kernel as a RuntimeError('host.<method> error: <msg>') — the soft-fail
    contract every host handler relies on."""
    with Kernel(dispatcher=_contract_dispatcher) as k:
        r = k.execute(
            "try:\n"
            "    host._call('soft', [])\n"
            "except RuntimeError as e:\n"
            "    print('caught:', e)"
        )
        assert r["error"] is None
        assert "caught: host.soft error: soft failure from host" in r["stdout"]


def test_host_call_error_key_with_siblings_is_plain_data():
    with Kernel(dispatcher=_contract_dispatcher) as k:
        r = k.execute(
            "d = host._call('error_plus_data', [])\n"
            "print(sorted(d), d['error'], d['detail'])"
        )
        assert r["error"] is None
        assert r["stdout"].strip() == "['detail', 'error'] x still data"


def test_host_call_none_data_roundtrips():
    with Kernel(dispatcher=_contract_dispatcher) as k:
        r = k.execute("print(host._call('none', []) is None)")
        assert r["stdout"].strip() == "True"


def test_host_call_without_dispatcher_errors():
    with Kernel(dispatcher=None) as k:
        r = k.execute(
            "try:\n"
            "    host._call('ping', [])\n"
            "except RuntimeError as e:\n"
            "    print('caught:', e)"
        )
        assert "no host dispatcher configured" in r["stdout"]


def test_system_exit_is_trapped_and_worker_survives():
    """exit()/SystemExit must not kill the worker: it is reported as an error
    and the SAME kernel (same namespace) keeps serving cells."""
    with Kernel(dispatcher=_echo_dispatcher) as k:
        k.execute("x = 7")
        r = k.execute("raise SystemExit(3)")
        assert r["error"] is not None
        assert "SystemExit trapped" in r["error"]
        assert k.is_alive()
        r2 = k.execute("print(x)")  # namespace survived the trapped exit
        assert r2["stdout"].strip() == "7"


def test_restart_bumps_generation_and_resets_namespace():
    with Kernel(dispatcher=_echo_dispatcher) as k:
        assert k.generation == 0
        k.execute("x = 1")
        k.restart()
        assert k.generation == 1
        assert k.is_alive()
        r = k.execute("print('x' in globals())")
        assert r["stdout"].strip() == "False"


# --- inner-RPC wire contracts (PR 06/PR 10 reviewers) -----------------------
# 15MB wire cap, host_ack pre-response frames, bounded-discard desync
# recovery, and the SIGINT interrupt contract. Extra pre-response frames are
# injected through the manager's OWN _send/_service_host_call machinery —
# the single-frame-reader loop and the host-call transaction stay intact.


def test_host_call_wire_cap_rejects_oversized_payload():
    """A host_call whose JSON frame exceeds the 15MB wire cap raises
    ValueError IN the kernel before anything is written — the dispatcher never
    sees the call and the channel stays usable for the next RPC."""
    with Kernel(dispatcher=_echo_dispatcher) as k:
        r = k.execute(
            "big = 'x' * 15_000_001\n"
            "try:\n"
            "    host._call('ping', [big])\n"
            "except ValueError as e:\n"
            "    print('capped:', '15MB wire cap' in str(e))\n"
            "print(host._call('ping', []))"
        )
        assert r["error"] is None
        # if the frame had reached the wire, 'ping' would have succeeded and
        # the 'capped:' line would be missing entirely
        assert r["stdout"].splitlines() == ["capped: True", "pong"]


def test_host_ack_and_bounded_discard_recovery():
    """Pre-response frames the worker must survive: a correct-id host_ack is
    skipped WITHOUT counting against the discard budget, and up to exactly
    _DISCARD_BUDGET (8) out-of-order/garbage frames are discarded before the
    id-matched response — the RPC still returns its data."""
    with Kernel(dispatcher=_echo_dispatcher) as k:
        orig = k._service_host_call

        def noisy(frame):
            # correct-id ack: the "keep waiting" signal, never a discard
            k._send({"type": "host_ack", "id": frame["id"]})
            # exactly 8 discardable frames = the full budget:
            # 6 stale responses (id mismatch) + 1 non-JSON line + 1 non-dict
            for i in range(6):
                k._send({"type": "host_response", "id": f"stale-{i}", "data": "old"})
            k._proc.stdin.write("this line is not json\n")
            k._proc.stdin.flush()
            k._send([1, 2, 3])
            orig(frame)

        k._service_host_call = noisy
        r = k.execute("print(host._call('ping', []))")
        assert r["error"] is None
        assert r["stdout"].strip() == "pong"


def test_host_call_desync_over_budget_raises_and_kernel_survives():
    """One frame past the discard budget (9 mismatched frames, no real
    response) makes host_call give up with a 'protocol desync' RuntimeError
    instead of spinning forever — and the worker keeps serving afterwards."""
    with Kernel(dispatcher=_echo_dispatcher) as k:
        orig = k._service_host_call

        def flood(frame):
            for i in range(9):
                k._send({"type": "host_response", "id": f"stale-{i}", "data": None})
            # never send the real response — the worker must bail on its own

        k._service_host_call = flood
        r = k.execute(
            "try:\n"
            "    host._call('ping', [])\n"
            "except RuntimeError as e:\n"
            "    print('caught:', e)"
        )
        assert r["error"] is None
        assert "host.ping: protocol desync" in r["stdout"]
        # with a sane host again, the same worker answers the next RPC
        k._service_host_call = orig
        r2 = k.execute("print(host._call('ping', []))")
        assert r2["stdout"].strip() == "pong"


def test_sigint_interrupt_reports_interrupted_true_lineno_none():
    """The host.exec_interrupt contract: a DELIVERED SIGINT ends the cell with
    interrupted=True, error='Interrupted' and NO error_lineno — and the kernel
    (with its namespace) survives the interrupt."""
    with Kernel(dispatcher=_echo_dispatcher) as k:
        k.execute("marker = 'still-here'")
        started = threading.Event()
        result = {}

        def run():
            result["r"] = k.execute(
                "print('cell-started')\nimport time\ntime.sleep(30)",
                on_chunk=lambda _text: started.set(),
            )

        t = threading.Thread(target=run, daemon=True)
        t.start()
        # the first stdout chunk proves user code is executing (handler armed)
        assert started.wait(15), "cell never produced its first stdout chunk"
        k.interrupt()
        t.join(timeout=15)
        assert not t.is_alive(), "interrupt did not stop the cell"

        r = result["r"]
        assert r["interrupted"] is True
        assert r["error"] == "Interrupted"
        assert r["trace"]["error_lineno"] is None
        assert k.is_alive()
        assert k.execute("print(marker)")["stdout"].strip() == "still-here"


def test_user_raised_keyboardinterrupt_is_normal_error_with_lineno():
    """The contrast half of the SIGINT contract: user code raising
    KeyboardInterrupt itself is a NORMAL error (interrupted=False) with a real
    lineno — only a delivered signal sets interrupted=True."""
    with Kernel(dispatcher=_echo_dispatcher) as k:
        r = k.execute("x = 1\nraise KeyboardInterrupt('manual')")
        assert r["interrupted"] is False
        assert "KeyboardInterrupt" in r["error"]
        assert r["trace"]["error_lineno"] == 2
        assert k.is_alive()


def test_host_bash_is_kernel_local_and_never_rpcs(tmp_path):
    """host.bash runs INSIDE the worker process — the host executes only
    python/R cells. A dispatcher that rejects a 'bash' method proves no RPC
    happens; the command's output is captured in the cell like any subprocess."""

    def no_bash_dispatcher(method, args):
        if method == "bash":
            raise AssertionError("host.bash must not reach the host dispatcher")
        return _echo_dispatcher(method, args)

    with Kernel(dispatcher=no_bash_dispatcher, cwd=str(tmp_path)) as k:
        r = k.execute("r = host.bash('echo kernel-local'); print(r['stdout'])")
        assert r["error"] is None
        assert "kernel-local" in r["stdout"]
        # the shell ran in the worker's cwd (the workspace)
        r2 = k.execute("print(host.bash('pwd')['workdir'])")
        assert str(tmp_path.resolve()) in r2["stdout"]


def test_host_bash_static_precheck_blocks_catastrophe(tmp_path):
    with Kernel(dispatcher=_echo_dispatcher, cwd=str(tmp_path)) as k:
        r = k.execute(
            "try:\n"
            "    host.bash('rm -rf /')\n"
            "except RuntimeError as e:\n"
            "    print('BLOCKED:', e)\n"
        )
        assert r["error"] is None
        assert "BLOCKED:" in r["stdout"] and "precheck" in r["stdout"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
