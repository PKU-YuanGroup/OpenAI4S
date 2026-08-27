"""Coverage-guided fuzz target for the two untrusted wire decoders."""

from __future__ import annotations

import io
import sys

import atheris

with atheris.instrument_imports():
    from openai4s.server.ws_frames import ws_read_frame
    from openai4s.share.protocol import ProtocolError, decode_control, decode_data


def TestOneInput(data: bytes) -> None:
    if not data:
        return

    selector = data[0] % 5
    payload = data[1:]
    if selector < 3:
        expect_mask = (None, False, True)[selector]
        ws_read_frame(
            io.BytesIO(payload),
            expect_mask=expect_mask,
            max_len=1 << 20,
        )
        return

    try:
        if selector == 3:
            decode_control(payload)
        else:
            decode_data(payload)
    except ProtocolError:
        # Malformed peer input is the documented rejection path. Any other
        # exception remains visible to libFuzzer as a crash.
        pass


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
