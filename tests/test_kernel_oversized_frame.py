"""What happens when a cell's response is too big to send.

`_write_frame` refuses to put a frame larger than `_MAX_FRAME_BYTES` on the
wire, which is right — the host would have to materialise it whole, and a
truncated JSON line is not a frame at all, it desynchronises the reader.

But it replaced the frame with a `{"type": "log"}` message. For a `response`
that is a lost answer: the host is waiting on a cell id that now never arrives,
so `Kernel.execute` blocks until the watchdog kills the worker. Measured before
the fix, `raise ValueError("x" * 12_000_000)` had not returned after 90
seconds. To a user that is a hang, not a refusal.

Two changes, and they do different jobs. Capping `error` — which sat beside a
capped `stdout` and a capped `stderr` — stops the frame growing past the limit
in the first place. Making a dropped `response` still answer its id stops the
*next* unbounded field being a hang rather than a message.
"""

from __future__ import annotations

import json

from openai4s.kernel import worker


class _Sink:
    def __init__(self):
        self.lines: list[str] = []

    def write(self, text):
        self.lines.append(text)

    def flush(self):
        return None


def _sent(monkeypatch, frame):
    """Drive the real `_write_frame`, replacing only the byte sink and the
    protocol lock.

    Those two normally live on `sys` and are installed by the worker's
    bootstrap, which does not run in-process — so a test that skipped them
    would be testing a copy of the guard rather than the guard.
    """
    import threading

    sink = _Sink()
    monkeypatch.setattr(worker, "_proto_out", lambda: sink)
    monkeypatch.setattr(worker, "_write_lock", lambda: threading.Lock())
    worker._write_frame(frame)
    assert len(sink.lines) == 1
    return json.loads(sink.lines[0])


def test_an_oversized_response_still_answers_its_id(monkeypatch):
    """The defect. A response the host cannot be sent must still be a response,
    or the host waits for an id that will never come."""
    huge = {
        "type": "response",
        "id": "cell-42",
        "stdout": "x" * (worker._MAX_FRAME_BYTES + 1000),
        "error": None,
    }
    out = _sent(monkeypatch, huge)
    assert out["type"] == "response", "the host was left waiting on cell-42"
    assert out["id"] == "cell-42"
    assert "oversized" in out["error"]
    assert out["stdout"] == ""


def test_the_replacement_is_small_enough_to_actually_send(monkeypatch):
    """A replacement built from the same oversized payload would be refused
    too, and the fix would be no fix."""
    out = _sent(
        monkeypatch,
        {
            "type": "response",
            "id": "cell-1",
            "stdout": "x" * (worker._MAX_FRAME_BYTES + 1000),
        },
    )
    assert len(json.dumps(out).encode("utf-8")) < worker._MAX_FRAME_BYTES


def test_a_frame_that_is_not_a_response_is_still_a_log(monkeypatch):
    """Only a `response` carries an id someone is blocked on. Turning every
    dropped frame into a response would invent answers to questions nobody
    asked."""
    out = _sent(
        monkeypatch,
        {"type": "stdout_chunk", "chunk": "x" * (worker._MAX_FRAME_BYTES + 1000)},
    )
    assert out["type"] == "log"
    assert "oversized" in out["msg"]


def test_a_response_without_an_id_cannot_be_answered_so_it_logs(monkeypatch):
    """Nobody is waiting on it, and a response with no id would be unroutable."""
    out = _sent(
        monkeypatch,
        {"type": "response", "stdout": "x" * (worker._MAX_FRAME_BYTES + 1000)},
    )
    assert out["type"] == "log"


def test_an_ordinary_frame_passes_through_untouched(monkeypatch):
    """The guard must not rewrite frames that were fine."""
    out = _sent(monkeypatch, {"type": "response", "id": "c", "stdout": "hello"})
    assert out == {"type": "response", "id": "c", "stdout": "hello"}


# --------------------------------------------------------------------------
# through a real kernel
# --------------------------------------------------------------------------


def test_a_huge_exception_message_does_not_hang_the_kernel(tmp_path):
    """The failure as a user meets it.

    The unit tests above prove the guard behaves; this proves the cell comes
    back at all. Before the fix this had not returned after 90 seconds — the
    response frame was dropped, `Kernel.execute` was waiting on an id that
    would never arrive, and only the watchdog ended it.
    """
    from openai4s.kernel.manager import Kernel

    with Kernel(cwd=str(tmp_path)) as kernel:
        warm = kernel.execute("print('warm')")
        assert (warm.get("stdout") or "").strip() == "warm"

        result = kernel.execute("raise ValueError('x' * 12_000_000)")
        assert result.get("error"), "the cell returned no error at all"
        assert "ValueError" in result["error"]
        # Capped, not dropped: the answer arrives and says what happened.
        assert len(result["error"]) < 2_000_000

        # ...and the kernel is still usable afterwards, which is the other half
        # of "did not hang": a worker killed by the watchdog would not be.
        after = kernel.execute("print('still here')")
        assert (after.get("stdout") or "").strip() == "still here"
