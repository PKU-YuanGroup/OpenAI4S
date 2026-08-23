"""Kernel tests: persistent namespace, print capture, error attribution,
usage accounting, and host_call RPC round-trip (dispatcher stubbed)."""

import ntpath
import os
import threading
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import build_dispatcher
from openai4s.kernel import Kernel, KernelBusyError
from openai4s.kernel import manager as manager_mod
from openai4s.kernel import worker as worker_mod
from openai4s.kernel.environment import build_kernel_environment


def _echo_dispatcher(method, args):
    if method == "ping":
        return "pong"
    if method == "add":
        return sum(args[0]["nums"])
    raise ValueError(f"unknown method {method}")


def _authorized_bash_dispatcher(tmp_path):
    """Real Host policy/authorization with an explicit test-only allow rule."""

    cfg = Config(
        data_dir=tmp_path / ".data",
        llm=LLMConfig(provider="deepseek", api_key="test-only"),
    )
    dispatcher = build_dispatcher(cfg, workspace=tmp_path)
    frame_id = dispatcher.store.new_frame(kind="turn")
    dispatcher.frame_id = frame_id
    dispatcher.store.set_permission_rule(
        scope="conversation",
        scope_id=frame_id,
        tool="bash",
        pattern="*",
        decision="allow",
    )
    return dispatcher


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


def test_files_read_are_runtime_observations_not_source_guesses(tmp_path):
    (tmp_path / "executed.txt").write_text("observed", encoding="utf-8")
    (tmp_path / "dead.txt").write_text("not observed", encoding="utf-8")

    with Kernel(dispatcher=_echo_dispatcher, cwd=str(tmp_path)) as kernel:
        result = kernel.execute(
            "from pathlib import Path\n"
            "if False:\n"
            "    Path('dead.txt').read_text()\n"
            "print(Path('executed.txt').read_text())\n"
            "Path('write-only.txt').write_text('output')"
        )

    assert result["error"] is None
    assert result["stdout"].strip() == "observed"
    assert result["files_read"] == ["executed.txt"]
    assert (tmp_path / "write-only.txt").read_text(encoding="utf-8") == "output"


def test_files_read_normalize_workspace_paths_and_exclude_external_reads(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "input.bin").write_bytes(b"data")

    with Kernel(dispatcher=_echo_dispatcher, cwd=str(tmp_path)) as kernel:
        result = kernel.execute(
            "import os\n"
            "open(os.path.join('nested', '..', 'nested', 'input.bin'), 'rb').read()\n"
            "open('/dev/null', 'rb').read()\n"
            "fd = os.open('created.bin', os.O_CREAT | os.O_WRONLY, 0o600)\n"
            "os.close(fd)"
        )

    assert result["error"] is None
    assert result["files_read"] == ["nested/input.bin"]


def test_file_read_path_normalization_uses_windows_rules_without_posix_imports():
    def relative(raw_path, *, cwd=r"C:\Workspace\analysis"):
        return worker_mod._workspace_relative_read_path(
            raw_path,
            root=r"C:\Workspace",
            cwd=cwd,
            isabs=ntpath.isabs,
            normpath=ntpath.normpath,
            relpath=ntpath.relpath,
            separator="\\",
        )

    assert relative(r"..\Data\input.csv") == "Data/input.csv"
    assert relative(r"c:\workspace\Data\input.csv") == "Data/input.csv"
    assert relative(r"link\input.csv") == "analysis/link/input.csv"
    assert relative(r"C:\Other\secret.csv") is None
    assert relative(r"D:\Workspace\input.csv") is None
    assert relative(r"C:\Workspace", cwd=r"C:\Workspace") is None


def test_variable_inspector_reads_only_safe_builtins_without_repr_hooks():
    with Kernel(dispatcher=_echo_dispatcher) as kernel:
        setup = kernel.execute(
            "events = []\n"
            "class Meta(type):\n"
            "    def __getattribute__(cls, name):\n"
            "        if name == '__name__': events.append('metaclass-name')\n"
            "        return super().__getattribute__(name)\n"
            "class Hostile(metaclass=Meta):\n"
            "    def __repr__(self): events.append('repr'); raise RuntimeError('repr')\n"
            "    def __len__(self): events.append('len'); raise RuntimeError('len')\n"
            "    def __sizeof__(self): events.append('sizeof'); raise RuntimeError('sizeof')\n"
            "class TrapList(list):\n"
            "    def __repr__(self): events.append('list-repr'); raise RuntimeError('repr')\n"
            "    def __iter__(self): events.append('list-iter'); raise RuntimeError('iter')\n"
            "    def __len__(self): events.append('list-len'); raise RuntimeError('len')\n"
            "score = 0.93\n"
            "title = 'protein'\n"
            "samples = [1, 2, 3]\n"
            "mixed = [1, Hostile()]\n"
            "hostile = Hostile()\n"
            "trap = TrapList([1, 2])"
        )
        assert setup["error"] is None

        response = kernel.inspect_variables()
        variables = {item["name"]: item for item in response["variables"]}

        assert variables["score"]["preview"] == 0.93
        assert variables["title"]["preview"] == "protein"
        assert variables["samples"]["length"] == 3
        assert len(variables["samples"]["fingerprint"]) == 64
        assert variables["mixed"] == {
            "name": "mixed",
            "type": "list",
            "kind": "container",
            "length": 2,
        }
        assert variables["hostile"] == {"name": "hostile", "type": "Hostile"}
        assert variables["trap"] == {"name": "trap", "type": "TrapList"}
        assert "host" not in variables and "openai4s" not in variables
        assert kernel.execute("print(events)")["stdout"].strip() == "[]"


def test_variable_inspector_fails_busy_without_competing_frame_reader():
    with Kernel(dispatcher=_echo_dispatcher) as kernel:
        started = threading.Event()
        result = {}

        def run():
            result["cell"] = kernel.execute(
                "print('started')\nimport time\ntime.sleep(30)",
                on_chunk=lambda _text: started.set(),
            )

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        # A completely cold optional-science install may build Matplotlib's
        # font cache before the first user byte is emitted. The cache now has a
        # stable writable location, but this concurrency assertion should not
        # mistake that one-time initialization for a protocol deadlock.
        assert started.wait(60)
        with pytest.raises(KernelBusyError, match="busy"):
            kernel.inspect_variables()
        kernel.interrupt()
        thread.join(timeout=15)
        assert not thread.is_alive()
        assert result["cell"]["interrupted"] is True
        # The failed busy read consumed no frame; the next request is aligned.
        assert isinstance(kernel.inspect_variables()["variables"], list)


def test_variable_inspector_limit_validation_is_local():
    with Kernel(dispatcher=_echo_dispatcher) as kernel:
        with pytest.raises(ValueError, match="between 1 and 500"):
            kernel.inspect_variables(limit=0)
        with pytest.raises(TypeError, match="integer"):
            kernel.inspect_variables(limit=True)
        assert kernel.execute("print('aligned')")["stdout"].strip() == "aligned"


class _InitializationFailureTransport:
    """A transport peer that fails before a kernel candidate can be published."""

    def __init__(self, failure: str) -> None:
        self.failure = failure
        self.process = None
        self.stderr_tail = None
        self.closed = False
        self.killed = False
        self._released = threading.Event()

    def write_line(self, _line: str) -> None:
        if self.failure == "write":
            raise OSError("initialization write failed")

    def read_line(self) -> str:
        if self.failure == "read":
            raise OSError("initialization read failed")
        self._released.wait(timeout=5)
        return ""

    def alive(self) -> bool:
        return not self.closed

    def interrupt(self) -> bool:
        return False

    def kill(self) -> None:
        self.killed = True
        self._released.set()

    def close(self, *, graceful: bool = True) -> None:
        self.closed = True
        self._released.set()


@pytest.mark.parametrize("failure", ["write", "read"])
def test_attestation_initialization_errors_close_the_transport(failure):
    transport = _InitializationFailureTransport(failure)
    with pytest.raises(RuntimeError, match="attestation initialization") as caught:
        Kernel(transport_factory=lambda: transport)
    assert isinstance(caught.value.__cause__, OSError)
    assert transport.closed is True


def test_attestation_initialization_deadline_kills_the_blocked_transport(
    monkeypatch,
):
    transport = _InitializationFailureTransport("block")
    monkeypatch.setattr(manager_mod, "_SKILL_SIDECAR_INITIALIZATION_TIMEOUT_S", 0.01)
    with pytest.raises(RuntimeError, match="deadline"):
        Kernel(transport_factory=lambda: transport)
    assert transport.killed is True
    assert transport.closed is True


def test_kernel_child_environment_is_rebuilt_from_strict_allowlist(tmp_path):
    source = {
        "PATH": "/host/bin",
        "HOME": "/home/scientist",
        "LANG": "en_US.UTF-8",
        "TMPDIR": "/safe/tmp",
        "MPLBACKEND": "Agg",
        "VIRTUAL_ENV": "/host/venv",
        "PYTHONPATH": "/host/injected-pythonpath",
        "CONDA_PREFIX": "/host/wrong-conda",
        "OPENAI4S_PROVENANCE_OFF": "1",
        "OPENAI4S_SAFETY_AUDIT_HOOK": "0",
        "OPENAI4S_KERNEL_MODE": "host-override",
        "OPENAI4S_KERNEL_GENERATION": "host-forged-generation",
        "OPENAI4S_WORKSPACE": "/host/wrong-workspace",
        "OPENAI4S_LLM_API_KEY": "llm-secret",
        "OPENAI4S_ARK_API_KEY": "ark-secret",
        "OPENAI_API_KEY": "openai-secret",
        "ANTHROPIC_API_KEY": "anthropic-secret",
        "HF_TOKEN": "hf-secret",
        "AWS_SECRET_ACCESS_KEY": "aws-secret",
        "SYNTHETIC_OAUTH_CREDENTIAL": "oauth-secret",
        "DATABASE_PASSWORD": "db-secret",
        "SSH_AUTH_SOCK": "/tmp/agent.sock",
        "HTTPS_PROXY": "https://user:password@proxy.invalid",
        "LD_PRELOAD": "/tmp/evil.so",
        "LD_LIBRARY_PATH": "/tmp/evil-libs",
        "DYLD_INSERT_LIBRARIES": "/tmp/evil.dylib",
        "DYLD_LIBRARY_PATH": "/tmp/evil-dylibs",
        "BASH_ENV": "/tmp/evil-bashrc",
        "NODE_OPTIONS": "--require=/tmp/evil.js",
    }
    repo_root = tmp_path / "trusted-repo"
    env_root = tmp_path / "conda" / "science"

    env = build_kernel_environment(
        source=source,
        mode="python",
        cwd=str(tmp_path),
        env_root=str(env_root),
        env_name="science",
        kernel_generation="kernel:test-generation",
        repo_root=str(repo_root),
    )

    assert env["PATH"].split(os.pathsep)[0] == str(env_root / "bin")
    assert env["HOME"] == "/home/scientist"
    assert env["LANG"] == "en_US.UTF-8"
    assert env["TMPDIR"] == "/safe/tmp"
    assert env["MPLBACKEND"] == "Agg"
    assert env["MPLCONFIGDIR"] == "/safe/tmp/openai4s-matplotlib"
    assert env["OPENAI4S_PROVENANCE_OFF"] == "1"
    assert env["OPENAI4S_SAFETY_AUDIT_HOOK"] == "0"
    assert env["OPENAI4S_KERNEL_MODE"] == "python"
    assert env["OPENAI4S_KERNEL_GENERATION"] == "kernel:test-generation"
    assert env["OPENAI4S_WORKSPACE"] == str(tmp_path.resolve())
    assert env["PWD"] == str(tmp_path.resolve())
    assert env["PYTHONPATH"] == str(repo_root.resolve())
    assert env["CONDA_PREFIX"] == str(env_root)
    assert env["CONDA_DEFAULT_ENV"] == "science"
    assert env["CONDA_SHLVL"] == "1"
    assert "VIRTUAL_ENV" not in env  # selected conda runtime wins

    forbidden = {
        "OPENAI4S_LLM_API_KEY",
        "OPENAI4S_ARK_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "HF_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "SYNTHETIC_OAUTH_CREDENTIAL",
        "DATABASE_PASSWORD",
        "SSH_AUTH_SOCK",
        "HTTPS_PROXY",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "BASH_ENV",
        "NODE_OPTIONS",
    }
    assert forbidden.isdisjoint(env)
    assert "/host/injected-pythonpath" not in env["PYTHONPATH"]
    assert source["OPENAI4S_LLM_API_KEY"] == "llm-secret"  # source not mutated


def test_python_kernel_and_its_subprocesses_cannot_inherit_host_api_key(
    monkeypatch, tmp_path
):
    marker = "synthetic-host-llm-secret-never-in-kernel"
    monkeypatch.setenv("OPENAI4S_LLM_API_KEY", marker)
    monkeypatch.setenv("OPENAI4S_ARK_API_KEY", marker)
    monkeypatch.setenv("OPENAI_API_KEY", marker)
    monkeypatch.setenv("HF_TOKEN", marker)
    monkeypatch.setenv("LD_PRELOAD", "/tmp/openai4s-never-load.so")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/tmp/openai4s-never-load.dylib")
    monkeypatch.setenv("OPENAI4S_PROVENANCE_OFF", "1")

    code = """
import os, shlex, subprocess, sys
probe = "import os; print(os.environ.get('OPENAI4S_LLM_API_KEY', '<missing>'))"
print(os.environ.get('OPENAI4S_LLM_API_KEY', '<missing>'))
print(subprocess.check_output([sys.executable, '-c', probe], text=True).strip())
cmd = shlex.quote(sys.executable) + ' -c ' + shlex.quote(probe)
print(host.bash(cmd)['stdout'].strip())
print(os.environ.get('OPENAI4S_PROVENANCE_OFF', '<missing>'))
"""
    with Kernel(
        dispatcher=_authorized_bash_dispatcher(tmp_path), cwd=str(tmp_path)
    ) as kernel:
        result = kernel.execute(code)

    assert result["error"] is None
    assert result["stdout"].splitlines() == ["<missing>", "<missing>", "<missing>", "1"]
    assert marker not in result["stdout"]


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
    """The final response frame carries exactly the documented key set.

    `cwd` is a host-side annotation added by the manager, not a field the
    worker produces: the observation formatter needs a workspace-relative place
    to spill an oversized stdout, and the manager is the only layer that knows
    where that is. ``files_read`` is worker-observed response metadata; older
    managers ignore unknown response fields and remain wire-compatible.
    """
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
            "files_read",
            "usage",
            "cwd",
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
    (with its namespace) survives the interrupt.

    The gate is a host_call, not a stdout chunk. A chunk proved nothing it was
    once read as proving: `sys.stdout` is swapped to the chunk-emitting buffer
    at the top of the cell, well before user code runs and before the SIGINT
    handler used to be armed, and the guard phase in between has been measured
    at 18 seconds against a cold Matplotlib font cache. So a test that
    interrupted on the first chunk could deliver its one signal into the window
    where the worker swallowed it — and then fail, intermittently, on a Linux
    runner under load, blaming the interrupt path for a race in its own
    premise. A `host_call` frame can only be emitted from inside the cell's
    `host._call`, so receiving one is proof that `exec` is running user code.
    """

    reached_user_code = threading.Event()

    def dispatcher(method, args):
        if method == "ping":
            reached_user_code.set()
        return _echo_dispatcher(method, args)

    with Kernel(dispatcher=dispatcher) as k:
        k.execute("marker = 'still-here'")
        result = {}

        def run():
            result["r"] = k.execute(
                "host._call('ping', [])\nimport time\ntime.sleep(30)"
            )

        t = threading.Thread(target=run, daemon=True)
        t.start()
        # 60s, not 15: a completely cold optional-science install can spend
        # most of a minute in the guard phase before the first user byte runs.
        assert reached_user_code.wait(60), "the cell never reached its host call"
        k.interrupt()
        t.join(timeout=30)
        assert not t.is_alive(), _interrupt_diagnosis(k)

        r = result["r"]
        assert r["interrupted"] is True
        assert r["error"] == "Interrupted"
        assert r["trace"]["error_lineno"] is None
        assert k.is_alive()
        assert k.execute("print(marker)")["stdout"].strip() == "still-here"


def _interrupt_diagnosis(kernel) -> str:
    """What a failing interrupt looked like, instead of `assert not True`.

    This assertion has failed twice in CI and said nothing either time, so the
    next occurrence carries its own evidence: whether the worker is alive, and
    what the kernel itself says about the signal. On Linux `/proc/<pid>/status`
    settles the question this test cannot otherwise answer — `SigCgt` bit 1 set
    means the worker HAS a SIGINT handler installed, `SigIgn` means it is
    ignoring the signal, and `ShdPnd` means one was delivered and never taken.
    """

    lines = ["interrupt did not stop the cell"]
    proc = getattr(kernel, "_proc", None)
    pid = getattr(proc, "pid", None)
    lines.append(f"  worker pid={pid} alive={kernel.is_alive()}")
    if pid is not None:
        try:
            status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        except OSError:
            lines.append("  /proc unavailable (not Linux, or the worker is gone)")
        else:
            wanted = ("State", "SigIgn", "SigCgt", "SigPnd", "ShdPnd")
            for line in status.splitlines():
                if line.split(":", 1)[0] in wanted:
                    lines.append(f"  {line.strip()}")
            lines.append("  (SIGINT is bit 1: mask 0x2 in the Sig* words)")
    return "\n".join(lines)


def test_a_chunk_from_another_cell_is_not_this_cell_s_output(monkeypatch):
    """`on_chunk` and the assembled stdout belong to the cell that was asked
    for. A frame stamped with a different cell id used to satisfy both — so a
    `logging.StreamHandler` still bound to a finished cell's `sys.stdout`, or a
    background thread writing after its cell returned, fed text into the next
    cell's stream and told a live watcher that user code had started. The
    interrupt contract is one of the things that watcher decides: a host that
    stops a cell on its first output would have been aiming at a cell that had
    not begun."""

    seen: list[str] = []
    with Kernel(dispatcher=_echo_dispatcher) as k:
        real_readline = k._readline
        injected = {"done": False}

        def readline_once_from_another_cell():
            if not injected["done"]:
                injected["done"] = True
                return {
                    "type": "stdout_chunk",
                    "id": "a-cell-that-is-not-this-one",
                    "text": "not mine\n",
                }
            return real_readline()

        monkeypatch.setattr(k, "_readline", readline_once_from_another_cell)
        out = k.execute("print('mine')", on_chunk=seen.append)

        assert injected["done"], "the foreign frame was never injected"
        assert "not mine" not in "".join(seen), "a foreign cell's text reached on_chunk"
        assert "not mine" not in out["stdout"]
        assert out["stdout"].strip() == "mine"
        assert k._stale_stdout_chunks == 1, "the dropped frame was not counted"


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


@pytest.fixture
def sigint_probe(monkeypatch):
    """Observe which SIGINT handler the worker installs, without installing one.

    The contract under test is *which* handler is armed at each moment, so the
    probe records the installs rather than performing them: a test that really
    re-pointed this process's SIGINT would be asserting the worker's behaviour
    by adopting it.
    """

    import signal as signal_mod

    installed = {"handler": None}

    def fake_signal(signum, handler):
        assert signum == signal_mod.SIGINT
        previous = installed["handler"]
        installed["handler"] = handler
        return previous

    monkeypatch.setattr(worker_mod.signal, "signal", fake_signal)
    for cell in (
        worker_mod._in_user_code,
        worker_mod._sigint_delivered,
        worker_mod._sigint_pending,
    ):
        cell[0] = False
    yield installed
    for cell in (
        worker_mod._in_user_code,
        worker_mod._sigint_delivered,
        worker_mod._sigint_pending,
    ):
        cell[0] = False


def test_a_sigint_that_beats_user_code_is_owed_not_dropped(sigint_probe):
    """`Kernel.interrupt()` sends exactly ONE signal, and `_arm_sigint` runs
    before the cell is compiled -- so a stop pressed a millisecond early lands
    while the handler cannot raise. Swallowing it there (and disarming, which
    is what the handler used to do) made the rest of the cell uninterruptible:
    the user saw no interrupt, no error, and a cell that ran to completion."""

    worker_mod._arm_sigint()
    assert sigint_probe["handler"] is worker_mod._sigint_handler

    worker_mod._sigint_handler(worker_mod.signal.SIGINT, None)  # must not raise

    assert worker_mod._sigint_pending[0] is True, "the signal was dropped"
    assert (
        sigint_probe["handler"] is worker_mod._sigint_handler
    ), "the handler disarmed itself; the rest of the cell cannot be interrupted"

    worker_mod._in_user_code[0] = True
    with pytest.raises(KeyboardInterrupt):
        worker_mod._raise_if_sigint_pending()

    # Reported as a DELIVERED signal, so the cell ends interrupted=True with no
    # error_lineno -- not as user code raising KeyboardInterrupt itself.
    assert worker_mod._sigint_delivered[0] is True
    assert worker_mod._sigint_pending[0] is False
    assert sigint_probe["handler"] is worker_mod._sigint_swallow, "not one-shot"


def test_arming_a_cell_clears_a_signal_owed_to_the_previous_one(sigint_probe):
    """A pending flag that outlived its cell would interrupt the next one at
    its first bytecode, for a stop the user pressed against a cell that has
    already finished."""

    worker_mod._sigint_pending[0] = True
    worker_mod._sigint_delivered[0] = True

    worker_mod._arm_sigint()

    assert worker_mod._sigint_pending[0] is False
    assert worker_mod._sigint_delivered[0] is False
    worker_mod._in_user_code[0] = True
    worker_mod._raise_if_sigint_pending()  # nothing owed: must not raise


def test_a_sigint_during_a_protocol_write_leaves_the_write_lock_free(monkeypatch):
    """A cell's stdout and its response frame go out through the SAME lock.

    If a KeyboardInterrupt could unwind out of that critical section, the very
    next thing the worker does after an interrupt -- write the response -- can
    deadlock against a lock its own interrupted stdout write left held. The
    kernel then stays alive with its namespace intact and never answers the
    cell it was told to stop, which reads to `Kernel.execute` as a hang rather
    than as an interrupt.
    """

    import signal as signal_mod
    import sys as _sys
    import threading as _threading

    if not hasattr(_sys, "_openai4s_protocol_lock"):
        monkeypatch.setattr(
            _sys, "_openai4s_protocol_lock", _threading.Lock(), raising=False
        )
    monkeypatch.setattr(worker_mod.signal, "signal", lambda *_args: None)

    written = []

    class _SignallingSink:
        """Stands in for the protocol channel, and fires a SIGINT mid-write."""

        def write(self, text):
            written.append(text)
            worker_mod._sigint_handler(signal_mod.SIGINT, None)

        def flush(self):
            return None

    monkeypatch.setattr(worker_mod, "_proto_out", lambda: _SignallingSink())
    worker_mod._in_user_code[0] = True
    worker_mod._sigint_delivered[0] = False
    worker_mod._sigint_pending[0] = False
    try:
        with pytest.raises(KeyboardInterrupt):
            worker_mod._write_frame({"type": "stdout_chunk", "id": "c1", "text": "hi"})

        assert written, "the frame never reached the channel"
        lock = worker_mod._write_lock()
        acquired = lock.acquire(blocking=False)
        try:
            assert acquired, "the interrupt left the protocol write lock held"
        finally:
            if acquired:
                lock.release()
        assert worker_mod._sigint_delivered[0] is True
    finally:
        for cell in (
            worker_mod._in_user_code,
            worker_mod._sigint_delivered,
            worker_mod._sigint_pending,
        ):
            cell[0] = False


def test_an_idle_worker_survives_an_interrupt_before_its_first_cell():
    """Interrupt stops a CELL. It must never end the worker.

    Until the first cell armed a handler the worker kept Python's default
    SIGINT disposition, so a stop delivered to an idle kernel raised
    KeyboardInterrupt straight out of its own read loop and took the namespace
    with it. `inspect_variables` is what proves the worker has reached that
    loop, so this asserts the contract instead of racing it.
    """

    with Kernel(dispatcher=_echo_dispatcher) as k:
        k.inspect_variables(limit=1)  # the worker is in its read loop
        k.interrupt()
        assert k.is_alive()
        result = k.execute("marker = 'survived'\nprint(marker)")
        assert result["error"] is None
        assert result["stdout"].strip() == "survived"


def test_host_bash_is_kernel_local_and_never_rpcs(tmp_path):
    """host.bash runs INSIDE the worker process — the host executes only
    python/R cells. A dispatcher that rejects a 'bash' method proves no RPC
    happens; the command's output is captured in the cell like any subprocess."""

    authorized = _authorized_bash_dispatcher(tmp_path)

    def no_bash_dispatcher(method, args):
        if method == "bash":
            raise AssertionError("host.bash must not reach the host dispatcher")
        return authorized(method, args)

    with Kernel(dispatcher=no_bash_dispatcher, cwd=str(tmp_path)) as k:
        r = k.execute("r = host.bash('echo kernel-local'); print(r['stdout'])")
        assert r["error"] is None
        assert "kernel-local" in r["stdout"]
        # the shell ran in the worker's cwd (the workspace)
        r2 = k.execute("print(host.bash('pwd')['workdir'])")
        assert str(tmp_path.resolve()) in r2["stdout"]

    methods = [
        row[0]
        for row in authorized.store._conn.execute(
            "SELECT method FROM host_call_log ORDER BY created_at"
        ).fetchall()
    ]
    assert "bash" in methods  # safe result audit
    assert "authorize_bash" in methods


def test_host_bash_capability_is_bound_to_exact_worker_spawn(tmp_path):
    dispatcher = _authorized_bash_dispatcher(tmp_path)
    observed: list[tuple[str, str | int | None]] = []
    authorize = dispatcher._bash_authorization.authorize

    def inspect_binding(spec):
        observed.append((spec.get("generation"), dispatcher.bash_generation_id))
        return authorize(spec)

    dispatcher._bash_authorization.authorize = inspect_binding
    with Kernel(dispatcher=dispatcher, cwd=str(tmp_path)) as kernel:
        first_generation = kernel.authorization_generation
        first = kernel.execute("print(host.bash('echo first')['stdout'])")
        assert first["error"] is None
        kernel.restart()
        second_generation = kernel.authorization_generation
        second = kernel.execute("print(host.bash('echo second')['stdout'])")
        assert second["error"] is None

    assert first_generation != second_generation
    assert observed == [
        (first_generation, first_generation),
        (second_generation, second_generation),
    ]
    assert dispatcher.bash_generation_id is None


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


def test_one_print_cannot_put_an_unbounded_frame_on_the_protocol_pipe():
    """A real subprocess, because the defect is in the pipe, not the arithmetic.

    `_StreamingStdout.write` forwarded whatever string it was handed as one
    `stdout_chunk` frame, and `_write_frame` had no size gate at all -- the
    15MB `_HOST_CALL_WIRE_CAP` guards only the inbound direction. So
    `print("x" * 200_000_000)` produced a single ~200MB JSON line, which the
    host's `readline()` then materialised whole: ~200MB allocated on both sides
    from one ordinary statement, with nothing in between to refuse it.

    Driven through `Kernel` so the frames really cross a pipe and a real
    `readline` really reads them. A `StringIO` here would prove the slicing and
    none of the thing that broke.
    """
    chunks: list[str] = []
    with Kernel(dispatcher=_echo_dispatcher) as k:
        result = k.execute(
            "print('x' * 20_000_000)", on_chunk=lambda text: chunks.append(text)
        )

    assert result["error"] is None
    # Every frame that crossed the pipe is individually bounded.
    assert chunks, "the cell streamed nothing at all"
    assert max(len(chunk) for chunk in chunks) <= 64_000 + 64
    # And the stream as a whole is bounded, with one marker rather than one
    # per oversized write.
    streamed = "".join(chunks)
    assert len(streamed) < 20_000_000
    assert streamed.count("...(truncated at") == 1
    # The captured result stays bounded too, and says what it counted.
    assert len(result["stdout"]) <= 1_000_000 + 64
    assert "characters" in result["stdout"]
