"""Coverage-guided fuzz target for the two untrusted wire decoders."""

from __future__ import annotations

import io
import sys

import atheris

# Scoped, not blanket. `openai4s/server/__init__.py` eagerly imports the
# gateway, so importing `ws_frames` pulls in 248 `openai4s` modules (370 total)
# -- the store, the kernel manager, the LLM client, the tool registry, the
# skills loader. Instrumenting all of that to fuzz two decoders costs start-up
# and RSS inside the job's budget, and dilutes libFuzzer's coverage map with
# counters no input here can ever reach, which degrades the very signal this
# gate exists for. `include` names the two modules the docstring claims.
with atheris.instrument_imports(
    include=["openai4s.server.ws_frames", "openai4s.share.protocol"]
):
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
