from __future__ import annotations

import pytest

from openai4s.share import protocol as p


def test_data_frame_roundtrip():
    frame = p.encode_data(417, b"hello world", end=True)
    request_id, flags, chunk = p.decode_data(frame)
    assert request_id == 417
    assert flags & p.FLAG_END
    assert not flags & p.FLAG_ABORT
    assert chunk == b"hello world"


def test_data_frame_abort_flag():
    frame = p.encode_data(1, b"", abort=True)
    _, flags, chunk = p.decode_data(frame)
    assert flags & p.FLAG_ABORT and chunk == b""


def test_data_frame_too_short():
    with pytest.raises(p.ProtocolError):
        p.decode_data(b"\x01\x00\x00")


def test_data_chunk_size_limit():
    with pytest.raises(p.ProtocolError):
        p.encode_data(1, b"x" * (p.MAX_DATA_CHUNK + 1))


def test_data_unknown_category():
    with pytest.raises(p.ProtocolError):
        p.decode_data(b"\x02" + b"\x00\x00\x00\x01" + b"\x00")


def test_control_roundtrip():
    frame = p.encode_control({"type": p.HTTP_REQUEST, "id": 5, "path": "/api/view"})
    obj = p.decode_control(frame)
    assert obj["type"] == p.HTTP_REQUEST and obj["id"] == 5


def test_control_unknown_type_rejected():
    with pytest.raises(p.ProtocolError):
        p.encode_control({"type": "definitely_not_real"})
    import json

    raw = json.dumps({"type": "definitely_not_real"}).encode()
    with pytest.raises(p.ProtocolError):
        p.decode_control(raw)


def test_control_requires_object_with_type():
    with pytest.raises(p.ProtocolError):
        p.encode_control({"no_type": 1})
    with pytest.raises(p.ProtocolError):
        p.decode_control(b"[]")
    with pytest.raises(p.ProtocolError):
        p.decode_control(b"not json")


def test_control_size_limit():
    huge = {"type": p.ERROR, "message": "x" * (p.MAX_CONTROL_JSON + 10)}
    with pytest.raises(p.ProtocolError):
        p.encode_control(huge)


def test_request_header_allowlist():
    filtered = p.filter_request_headers(
        {
            "Accept": "*/*",
            "Range": "bytes=0-10",
            "Cookie": "secret=1",
            "Authorization": "Bearer x",
            "X-Weird": "y",
        }
    )
    assert filtered == {"accept": "*/*", "range": "bytes=0-10"}
    assert "cookie" not in filtered and "authorization" not in filtered
