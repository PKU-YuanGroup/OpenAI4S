from __future__ import annotations

import io
import struct

import pytest

from openai4s.server import ws_frames


def _reader(data: bytes) -> io.BytesIO:
    return io.BytesIO(data)


def test_server_frame_length_ladder():
    small = ws_frames.ws_encode(b"hello")
    assert small[0] == 0x81 and small[1] == 5 and small[2:] == b"hello"
    edge = ws_frames.ws_encode(b"x" * 126)
    assert edge[1] == 126 and edge[2:4] == struct.pack(">H", 126)
    big = ws_frames.ws_encode(b"y" * 65536, opcode=0x2)
    assert big[0] == 0x82 and big[1] == 127
    assert big[2:10] == struct.pack(">Q", 65536)


def test_client_mask_roundtrip():
    payload = b'{"type":"ping"}'
    encoded = ws_frames.ws_encode(payload, mask=True)
    assert encoded[1] & 0x80  # mask bit set
    # a masked client frame is accepted in expect_mask=True
    assert ws_frames.ws_read_frame(_reader(encoded), expect_mask=True) == (0x1, payload)


def test_expect_mask_direction_enforced():
    server = ws_frames.ws_encode(b"hi")  # unmasked
    # relay side requires masked client frames -> reject unmasked
    assert ws_frames.ws_read_frame(_reader(server), expect_mask=True) is None
    client = ws_frames.ws_encode(b"hi", mask=True)
    # client side requires unmasked server frames -> reject masked
    assert ws_frames.ws_read_frame(_reader(client), expect_mask=False) is None


def test_lenient_mode_accepts_both():
    server = ws_frames.ws_encode(b"srv")
    client = ws_frames.ws_encode(b"cli", mask=True)
    assert ws_frames.ws_read_frame(_reader(server), expect_mask=None) == (0x1, b"srv")
    assert ws_frames.ws_read_frame(_reader(client), expect_mask=None) == (0x1, b"cli")


def test_truncated_header_and_body():
    assert ws_frames.ws_read_frame(_reader(b"\x81")) is None  # 1-byte header
    frame = ws_frames.ws_encode(b"abcdef")
    assert ws_frames.ws_read_frame(_reader(frame[:-2])) is None  # short body


def test_rsv_bits_rejected():
    frame = bytearray(ws_frames.ws_encode(b"x"))
    frame[0] |= 0x40  # set RSV1
    assert ws_frames.ws_read_frame(_reader(bytes(frame))) is None


def test_fragmented_frame_rejected():
    frame = bytearray(ws_frames.ws_encode(b"x"))
    frame[0] &= 0x7F  # clear FIN
    assert ws_frames.ws_read_frame(_reader(bytes(frame))) is None


def test_unknown_opcode_rejected():
    frame = bytearray(ws_frames.ws_encode(b"x"))
    frame[0] = 0x80 | 0x3  # reserved opcode 0x3
    assert ws_frames.ws_read_frame(_reader(bytes(frame))) is None


def test_control_frame_over_125_rejected():
    # a close frame claiming a 126 extended length is illegal
    frame = bytes([0x88, 126]) + struct.pack(">H", 200) + b"z" * 200
    assert ws_frames.ws_read_frame(_reader(frame)) is None


def test_non_canonical_16bit_length_rejected():
    frame = bytes([0x81, 126]) + struct.pack(">H", 10) + b"a" * 10
    assert ws_frames.ws_read_frame(_reader(frame)) is None


def test_non_canonical_64bit_length_rejected():
    frame = bytes([0x81, 127]) + struct.pack(">Q", 10) + b"a" * 10
    assert ws_frames.ws_read_frame(_reader(frame)) is None


def test_64bit_high_bit_rejected():
    frame = bytes([0x81, 127]) + struct.pack(">Q", 0x8000000000000000)
    assert ws_frames.ws_read_frame(_reader(frame)) is None


def test_max_len_enforced():
    frame = ws_frames.ws_encode(b"a" * 2000)
    assert ws_frames.ws_read_frame(_reader(frame), max_len=1000) is None
    assert ws_frames.ws_read_frame(_reader(frame), max_len=4000) == (0x1, b"a" * 2000)


def test_strict_mode_rejects_bad_utf8_text():
    bad = b"\xff\xfe"
    frame = ws_frames.ws_encode(bad, opcode=0x1, mask=True)
    # strict mode: invalid UTF-8 text frame -> None
    assert ws_frames.ws_read_frame(_reader(frame), expect_mask=True) is None
    # binary frame with the same bytes is fine
    bframe = ws_frames.ws_encode(bad, opcode=0x2, mask=True)
    assert ws_frames.ws_read_frame(_reader(bframe), expect_mask=True) == (0x2, bad)


def test_accept_matches_rfc_example():
    # RFC 6455 §1.3 worked example
    assert (
        ws_frames.ws_accept("dGhlIHNhbXBsZSBub25jZQ==")
        == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    )


def test_frame_header_size():
    assert ws_frames.frame_header_size(10) == 2
    assert ws_frames.frame_header_size(126) == 4
    assert ws_frames.frame_header_size(70000) == 10


@pytest.mark.parametrize("n", [0, 1, 125, 126, 65535, 65536])
def test_roundtrip_all_length_classes(n):
    enc = ws_frames.ws_encode(b"a" * n)
    assert ws_frames.ws_read_frame(_reader(enc)) == (0x1, b"a" * n)
