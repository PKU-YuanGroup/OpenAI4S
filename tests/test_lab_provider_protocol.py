"""Offline wire and single-session dispatch contracts."""

import io
import json

import pytest

from openai4s_lab_provider.protocol import (
    MAX_FRAME_BYTES,
    ProtocolError,
    decode_frame,
    encode_frame,
)
from openai4s_lab_provider.server import Server


def test_roundtrip():
    frame = {"v": 1, "id": "请求", "op": "hello", "args": {}}
    assert decode_frame(encode_frame(frame)) == frame
    with pytest.raises(ProtocolError):
        encode_frame({**frame, "args": {"value": float("nan")}})


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
