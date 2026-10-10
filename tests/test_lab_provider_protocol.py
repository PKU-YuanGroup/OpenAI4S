"""Offline wire and single-session dispatch contracts."""

import io
import json

import pytest

from openai4s_lab_provider.protocol import (
    MAX_FRAME_BYTES,
    BackendError,
    ProtocolError,
    decode_frame,
    encode_frame,
)
from openai4s_lab_provider.server import Server


def test_roundtrip():
    frame = {"v": 1, "id": "req-1", "op": "hello", "args": {"note": "请求"}}
    assert decode_frame(encode_frame(frame)) == frame
    with pytest.raises(ProtocolError):
        encode_frame({**frame, "args": {"value": float("nan")}})


@pytest.mark.parametrize("ident", ["请求", "a b", "\ud800", "x" * 65, ""])
def test_request_ids_are_a_closed_alphabet(ident):
    # An id must survive being echoed back; a lone surrogate is valid JSON but
    # cannot be encoded, and used to crash the provider's error path.
    frame = {"v": 1, "id": ident, "op": "hello", "args": {}}
    with pytest.raises(ProtocolError):
        encode_frame(frame)
    raw = (json.dumps(frame) + "\n").encode()
    with pytest.raises(ProtocolError):
        decode_frame(raw)


@pytest.mark.parametrize(
    "data",
    [
        b"not json\n",
        b'{"v":1,"op":"hello","args":{}}\n',
        b'{"v":2,"id":"x","op":"hello","args":{}}\n',
        b'{"v":true,"id":"x","op":"hello","args":{}}\n',
        b'{"v":1,"id":"","op":"hello","args":{}}\n',
        b'{"v":1,"id":"x","id":"y","op":"hello","args":{}}\n',
        b'{"v":1,"id":"x","op":"hello","args":{"n":NaN}}\n',
        b"{}",
        b"{}\n{}\n",
        b"\xff\n",
    ],
)
def test_invalid_frames(data):
    with pytest.raises(ProtocolError):
        decode_frame(data)


def test_frame_limits():
    frame = {"v": 1, "id": "x", "op": "hello", "args": {"text": "a" * MAX_FRAME_BYTES}}
    with pytest.raises(ProtocolError):
        encode_frame(frame)
    with pytest.raises(ProtocolError):
        decode_frame((json.dumps(frame) + "\n").encode())


class Backend:
    name = "unit"
    version = {}

    def close(self):
        pass

    def describe(self, profile):
        raise RuntimeError("private simulator composition must not escape")


def test_unknown_op_and_exception_redaction(capsys):
    for op, args in [("unknown", {}), ("describe", {"profile": "x"})]:
        source = io.BytesIO(
            (json.dumps({"v": 1, "id": "r1", "op": op, "args": args}) + "\n").encode()
        )
        sink = io.BytesIO()
        Server(Backend()).serve(source, sink)
        frame = decode_frame(sink.getvalue(), response=True)
        assert frame["id"] == "r1"
        assert frame["ok"] is False
        assert frame["error"]["code"] == "provider_protocol_error"
        assert "private simulator" not in sink.getvalue().decode()
    stderr = capsys.readouterr().err
    assert "RuntimeError" in stderr
    assert "private simulator" not in stderr


def test_client_rejects_raw_oversize_and_invalid_response(tmp_path):
    import os
    import sys

    from openai4s_lab_provider.client import ProviderClient, ProviderProtocolError

    for payload in (b"x" * (MAX_FRAME_BYTES + 1), b"not-json\n"):
        peer = (
            "import sys; sys.stdin.buffer.readline(); sys.stdout.buffer.write("
            + ("b'x' * (8*1024*1024+1)" if len(payload) > 100 else repr(payload))
            + "); sys.stdout.buffer.flush()"
        )
        client = ProviderClient(
            [sys.executable, "-I", "-c", peer],
            env={"HOME": str(tmp_path)},
            cwd=tmp_path,
        ).start()
        try:
            with pytest.raises(ProviderProtocolError):
                client.request("hello", {}, timeout=5)
            assert not client.alive()
            with pytest.raises(ProcessLookupError):
                os.kill(client.process.pid, 0)
        finally:
            client.close()


def test_timeout_kills_descendant_even_after_leader_exit(tmp_path):
    import os
    import signal
    import sys
    import time

    from openai4s_lab_provider.client import ProviderClient, ProviderTimeout

    # The child retains stdout and ignores TERM. Leader exits after replying once.
    peer = """import json,os,signal,sys,time
child=os.fork()
if child==0:
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    time.sleep(30)
    os._exit(0)
f=json.loads(sys.stdin.readline())
print(json.dumps({'v':1,'id':f['id'],'ok':True,'result':{'child':child}}),flush=True)
sys.stdin.readline()
os._exit(0)
"""
    client = ProviderClient(
        [sys.executable, "-I", "-c", peer], env={"HOME": str(tmp_path)}, cwd=tmp_path
    ).start()
    child = None
    try:
        child = client.request("hello", {}, timeout=5)["child"]
        with pytest.raises(ProviderTimeout):
            client.request("execute", {}, timeout=0.2)
        deadline = time.monotonic() + 5
        while True:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            if time.monotonic() > deadline:
                pytest.fail("provider descendant survived timeout")
            time.sleep(0.01)
        assert client.process.returncode == 0
    finally:
        if child is not None:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        client.close()


@pytest.mark.parametrize("number", ["1e999", "-1e999"])
@pytest.mark.parametrize("response", [False, True])
def test_numeric_overflow_is_rejected(number, response):
    envelope = (
        '{"v":1,"id":"x","ok":true,"result":{"sim_time":NUMBER}}'
        if response
        else '{"v":1,"id":"x","op":"open","args":{"seed":NUMBER}}'
    )
    with pytest.raises(ProtocolError):
        decode_frame(
            (envelope.replace("NUMBER", number) + "\n").encode(), response=response
        )


def test_native_output_is_flushed_before_restoring_diagnostic_fds(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    entry = Path(__file__).resolve().parents[1] / "openai4s_lab_provider/__main__.py"
    peer = """import ctypes,runpy,sys
namespace=runpy.run_path(sys.argv[1],run_name="bootstrap_only")
namespace["_load_own_package"]()
from openai4s_lab_provider.chemgymrl.adapter import _quiet
libc=ctypes.CDLL(None)
@_quiet
def output():
    libc.printf(b"NATIVE_OUTPUT_SENTINEL\\n")
output()
libc.fflush(None)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", peer, str(entry)],
        cwd=tmp_path,
        env={"HOME": str(tmp_path)},
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == b"" and result.stderr == b""


class _StepBackend:
    """Enough of a backend to drive Server.dispatch directly."""

    name = "step"
    version = {"package": "step", "version": "0"}

    def __init__(self, fail_with=None):
        self.fail_with = fail_with
        self.steps = 0

    def open(self, profile, seed, options, expected_capability_revision):
        return {
            "session_id": "s",
            "descriptor": {
                "capabilities": [{"capability_id": "act", "resources": ["vessel"]}]
            },
            "observation": {"sim_time": 0.0, "channels": []},
            "evaluation": None,
        }

    def execute(self, provider_command_id, command, fencing_tokens):
        if self.fail_with is not None:
            raise self.fail_with
        self.steps += 1
        return {
            "provider_command_id": provider_command_id,
            "applied": True,
            "status": "succeeded",
            "error": None,
            "raw": {"terminated": False, "truncated": False},
            "end_reason": None,
            "sim_time": float(self.steps),
            "step_index": self.steps,
            "observation": {"sim_time": float(self.steps), "channels": []},
            "evaluation": None,
        }

    def close(self):
        pass


def _opened(backend):
    server = Server(backend)
    server.dispatch(
        "open",
        {
            "profile": "p",
            "seed": 1,
            "options": {},
            "expected_capability_revision": "r",
        },
    )
    return server


def _run(server, command_id):
    return server.dispatch(
        "execute",
        {
            "provider_command_id": command_id,
            "command": {"capability_id": "act"},
            "fencing_tokens": {"vessel": 1},
        },
    )


def test_known_false_means_never_received_and_evicted_ids_never_run_twice():
    backend = _StepBackend()
    server = _opened(backend)
    for index in range(257):
        _run(server, f"c{index}")
    assert backend.steps == 257
    query = server.dispatch("query", {"provider_command_id": "c0"})
    assert query == {"known": True, "receipt": None}
    assert server.dispatch("query", {"provider_command_id": "never"}) == {
        "known": False
    }
    with pytest.raises(BackendError) as again:
        _run(server, "c0")
    assert again.value.code == "outcome_unknown"
    assert backend.steps == 257


def test_an_unexpected_backend_failure_poisons_the_session():
    backend = _StepBackend(fail_with=RuntimeError("half-run step"))
    server = _opened(backend)
    with pytest.raises(RuntimeError):
        _run(server, "broken")
    # Outcome unknown, not "never received" ...
    assert server.dispatch("query", {"provider_command_id": "broken"}) == {
        "known": True,
        "receipt": None,
    }
    # ... and nothing else may run on a simulation in an unknown state.
    backend.fail_with = None
    with pytest.raises(BackendError) as refused:
        _run(server, "after")
    assert refused.value.code == "outcome_unknown"
    assert backend.steps == 0


def test_a_declared_refusal_does_not_mark_the_id_received():
    backend = _StepBackend(fail_with=BackendError("invalid_parameters", "no"))
    server = _opened(backend)
    with pytest.raises(BackendError):
        _run(server, "refused")
    assert server.dispatch("query", {"provider_command_id": "refused"}) == {
        "known": False
    }


def test_an_unencodable_request_leaves_the_provider_alive(tmp_path):
    import sys
    from pathlib import Path

    from openai4s_lab_provider.client import ProviderClient

    entry = (
        Path(__file__).resolve().parents[1] / "openai4s_lab_provider" / "__main__.py"
    )
    client = ProviderClient(
        [sys.executable, "-I", str(entry), "--backend", "toy"],
        env={"HOME": str(tmp_path), "PYTHONNOUSERSITE": "1"},
        cwd=tmp_path,
    ).start()
    try:
        with pytest.raises(ValueError):
            client.request("stop", {"reason": float("nan")}, timeout=5)
        # Nothing was written, so the provider is still the same healthy one.
        assert client.alive()
        assert client.request("hello", {}, timeout=5)["protocol"] == 1
    finally:
        client.close()


def test_an_unreadable_request_is_a_protocol_error_not_a_lost_provider(tmp_path):
    import sys

    from openai4s_lab_provider.client import ProviderClient, ProviderProtocolError

    # A peer answering the way the server does for a frame it cannot read.
    peer = (
        "import sys; sys.stdin.buffer.readline(); sys.stdout.buffer.write("
        'b\'{"v":1,"id":"invalid","ok":false,"error":'
        '{"code":"provider_protocol_error","message":"bad"}}\\n\''
        "); sys.stdout.buffer.flush(); sys.stdin.buffer.read()"
    )
    client = ProviderClient(
        [sys.executable, "-I", "-c", peer],
        env={"HOME": str(tmp_path)},
        cwd=tmp_path,
    ).start()
    try:
        with pytest.raises(ProviderProtocolError):
            client.request("hello", {}, timeout=5)
    finally:
        client.close()


@pytest.mark.parametrize(
    "ending", ["timeout_close", "eof", "orderly_close", "observed_exit"]
)
def test_dispose_descendants_before_reaping_leader(tmp_path, ending):
    import os
    import signal
    import sys
    import time

    from openai4s_lab_provider.client import ProviderClient, ProviderGone

    peer = """import json,os,signal,sys,time
r,w=os.pipe()
child=os.fork()
if child == 0:
    os.close(r)
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    os.close(0)
    os.close(1)
    os.write(w,b'1'); os.close(w)
    time.sleep(30)
    os._exit(0)
os.close(w); os.read(r,1); os.close(r)
f=json.loads(sys.stdin.readline())
print(json.dumps({'v':1,'id':f['id'],'ok':True,'result':{'child':child}}),flush=True)
f=json.loads(sys.stdin.readline())
ending=sys.argv[1]
if ending=='timeout_close':
    time.sleep(30)
elif ending=='orderly_close':
    print(json.dumps({'v':1,'id':f['id'],'ok':True,'result':{'closed':True}}),flush=True)
os._exit(0)
"""
    client = ProviderClient(
        [sys.executable, "-I", "-c", peer, ending],
        env={"HOME": str(tmp_path)},
        cwd=tmp_path,
    ).start()
    child = None
    try:
        child = client.request("hello", {}, timeout=5)["child"]
        if ending == "observed_exit":
            os.write(client.process.stdin.fileno(), b'{"op":"execute"}\n')
            assert client._wait_unreaped(5)
            assert not client.alive()
            assert client.process.returncode is None
            client.close()
        elif ending == "eof":
            with pytest.raises(ProviderGone):
                client.request("execute", {}, timeout=5)
        else:
            client.close(timeout=0.2)
        deadline = time.monotonic() + 5
        while True:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            if time.monotonic() > deadline:
                pytest.fail("TERM-resistant descendant survived provider disposal")
            time.sleep(0.01)
        assert not client.alive()
    finally:
        if child is not None:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
        client.close()
