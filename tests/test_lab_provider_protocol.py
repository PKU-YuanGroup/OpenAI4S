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
