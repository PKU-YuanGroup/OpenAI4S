"""Host-side bounded capture for a worker that sinks its streams to a fifo.

Why the host and not the worker. R gives no hook inside a single top-level
expression — it is single threaded, `addTaskCallback` does not fire mid
expression, and a connection callback cannot be written in R — so a cap that
lives in the worker can only act *between* expressions. One expression printing
300 MB still writes all 300 MB, and `sink()` sends every byte of it to a
tempfile that is a tmpfs on much of Linux, meaning RAM. The worker then reads
1 MB of that file and throws the rest away.

Draining a fifo from the host removes the file entirely. The reader keeps the
first `cap` bytes, counts everything, and discards the rest as it arrives, so
the writer is bounded by a reader it cannot outrun rather than by a check that
only runs when it pauses. Measured on R 4.6.1, one expression writing 300 MB:

    tempfile   300 MB written, 1 MB kept, 1.35 s
    fifo         0 MB written, 1 MB kept, 1.49 s, seen=300,000,000 exactly

A shell drainer — `sink()` to `pipe("head -c CAP > f; cat > /dev/null")` — was
measured first and rejected on two counts. `head`'s stdout to a file is
stdio-buffered, so a writer that emitted 100 bytes, paused 1.2 s and emitted
100 more left the capture file empty at 1.8 s; that would have killed the live
output the streaming path exists to provide. And `head -c` over-consumes the
pipe, so a 300,200-byte stream accounted as 299,976 — no exact counter can be
recovered from it. Both failures are absent here: first bytes reach the host in
0.19 s, and `seen` is exact.

Stdlib only, like the rest of the kernel.
"""
from __future__ import annotations

import codecs
import errno
import os
import select
import shutil
import sys
import tempfile
import threading
import time
import traceback
from typing import Callable

# How long a read waits before re-checking whether the cell is over. Deliberately
# `select`, not `sleep`: a fixed poll interval caps throughput at one pipe buffer
# per interval, which measured 2.3 MB/s at 5 ms and turned that 1.49 s runaway
# cell into 131 s. `select` returns the instant bytes land, so the timeout only
# bounds how long an idle reader sleeps.
# The same head cap `worker.MAX_OUTPUT` applies to a python cell, restated
# rather than imported: worker.py is a subprocess entry point that performs an
# fd swap when it runs, and the daemon has no reason to import it. Restating a
# number is how the R side drifted from it before, so
# `test_the_host_sink_cap_is_the_python_workers_cap` pins the two together the
# same way the R source is pinned.
CAP_BYTES = 1_000_000

_POLL_SECONDS = 0.2
# After the response frame says the cell is over, the worker has already closed
# its end, so anything still in flight is readable now. This only bounds the
# wait for a writer that never closed at all.
_FINISH_GRACE_SECONDS = 2.0
_READ_SIZE = 262_144


def truncation_marker(cap: int) -> str:
    """The one marker both workers use, in the units this cap is measured in.

    Byte for byte what `r_worker.R` appended while it still did its own
    capping, so a caller that has been reading R output cannot tell that the
    cut moved to the host.
    """
    return f"\n...(truncated at {cap} bytes)"


class _StreamDrain:
    """One fifo, drained by one thread, retaining at most `cap` bytes.

    The thread must never stop reading on its own account. The writer opens the
    fifo blocking, so a reader that gives up converts a runaway cell into a
    hang — the worst outcome available here, and worse than the unbounded
    tempfile this replaces. Every failure path below therefore keeps draining
    and only stops keeping.
    """

    def __init__(
        self,
        path: str,
        *,
        cap: int,
        on_text: Callable[[str], None] | None = None,
    ) -> None:
        self.path = path
        self.cap = cap
        self.seen = 0
        self._on_text = on_text
        self._kept = bytearray()
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # O_RDONLY|O_NONBLOCK opens without a writer, which is what lets the
        # host be ready before the execute frame is sent. The worker's blocking
        # open then finds a reader already there and never waits.
        self._fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self._thread = threading.Thread(
            target=self._run, name=f"kernel-sink-{os.path.basename(path)}", daemon=True
        )
        self._thread.start()

    @property
    def retained(self) -> int:
        with self._lock:
            return len(self._kept)

    @property
    def dropped(self) -> int:
        with self._lock:
            return self.seen - len(self._kept)

    def _run(self) -> None:
        saw_writer = False
        deadline: float | None = None
        while True:
            try:
                chunk = os.read(self._fd, _READ_SIZE)
            except BlockingIOError:
                if self._stop.is_set():
                    # The cell is over. Give a writer that has not closed a
                    # bounded grace period, then leave rather than spin.
                    if deadline is None:
                        deadline = time.monotonic() + _FINISH_GRACE_SECONDS
                    elif time.monotonic() >= deadline:
                        return
                select.select([self._fd], [], [], _POLL_SECONDS)
                continue
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                return
            if not chunk:
                # b"" is EOF *and* "no writer has opened yet" — the same read.
                # A cell that prints nothing never tells them apart, so the
                # host does not try: it stops when the response frame says the
                # cell is over, having read whatever was left.
                if saw_writer or self._stop.is_set():
                    return
                select.select([self._fd], [], [], _POLL_SECONDS)
                continue
            saw_writer = True
            self._absorb(chunk)

    def _absorb(self, chunk: bytes) -> None:
        with self._lock:
            self.seen += len(chunk)
            room = self.cap - len(self._kept)
            if room <= 0:
                return
            keep = bytes(chunk[:room])
            self._kept += keep
        if self._on_text is None or not keep:
            return
        # Incremental, so a character split across two reads is not reported as
        # two replacement characters to a caller watching the cell live.
        try:
            text = self._decoder.decode(keep)
        except Exception:  # noqa: BLE001 — a decode must not stop the drain
            text = keep.decode("utf-8", "replace")
        if not text:
            return
        try:
            self._on_text(text)
        except Exception:  # noqa: BLE001 — nor may the caller's own callback
            traceback.print_exc()

    def finish(self, timeout: float) -> str:
        """Stop draining and return the retained text, marked if it was cut."""
        self._stop.set()
        self._thread.join(timeout)
        with self._lock:
            kept = bytes(self._kept)
            dropped = self.seen - len(kept)
        text = kept.decode("utf-8", "replace")
        if dropped > 0:
            text += truncation_marker(self.cap)
        return text

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(_FINISH_GRACE_SECONDS + _POLL_SECONDS)
        if self._thread.is_alive():
            # Closing the descriptor under a live reader would hand it a fd
            # number the process may immediately reuse. Leak it instead and say
            # so; the thread is a daemon and the process is the bound.
            print(
                f"openai4s: sink drain for {self.path} did not stop; "
                "leaking its descriptor rather than reusing it",
                file=sys.stderr,
            )
            return
        try:
            os.close(self._fd)
        except OSError:
            pass


class SinkCapture:
    """The two fifos one cell sinks to, and the text they produced."""

    def __init__(
        self,
        out_path: str,
        err_path: str,
        *,
        cap: int,
        on_chunk: Callable[[str], None] | None = None,
    ) -> None:
        self.out_path = out_path
        self.err_path = err_path
        self._out = _StreamDrain(out_path, cap=cap, on_text=on_chunk)
        try:
            self._err = _StreamDrain(err_path, cap=cap)
        except BaseException:
            self._out.close()
            raise

    def finish(self, timeout: float = _FINISH_GRACE_SECONDS * 2) -> tuple[str, str]:
        return self._out.finish(timeout), self._err.finish(timeout)

    def counters(self) -> dict[str, int]:
        """Exact accounting, which the rejected shell drainer could not give.

        `head -c` over-consumed the pipe it was bounding — a 300,200-byte
        stream accounted as 299,976 — so no counter recovered from it could be
        trusted. These are counted at the only place every byte passes.
        """
        return {
            "stdout_seen_bytes": self._out.seen,
            "stdout_retained_bytes": self._out.retained,
            "stdout_dropped_bytes": self._out.dropped,
            "stderr_seen_bytes": self._err.seen,
            "stderr_retained_bytes": self._err.retained,
            "stderr_dropped_bytes": self._err.dropped,
        }

    def close(self) -> None:
        try:
            self._out.close()
        finally:
            self._err.close()
        for path in (self.out_path, self.err_path):
            try:
                os.unlink(path)
            except OSError:
                pass


class SinkDirectory:
    """Where a kernel's per-cell fifos live, for the life of that kernel.

    Under an enforced sandbox this is the worker's own private temp directory:
    Seatbelt grants `file-write*` on that subpath and bubblewrap binds it into
    the namespace at the same path, so a fifo the host creates there after the
    spawn is the same inode the worker opens. With no sandbox it is a private
    directory of our own rather than a shared one.
    """

    def __init__(self, temp_dir: str | None = None) -> None:
        if temp_dir:
            self.root = os.path.join(temp_dir, "sinks")
            os.makedirs(self.root, exist_ok=True)
            self._owned = False
        else:
            self.root = tempfile.mkdtemp(prefix="openai4s-sinks-")
            self._owned = True
        self._counter = 0
        self._lock = threading.Lock()

    def open(
        self, *, cap: int, on_chunk: Callable[[str], None] | None = None
    ) -> SinkCapture:
        with self._lock:
            self._counter += 1
            index = self._counter
        # Numbered by the manager, never named from a caller-supplied cell id:
        # a cell id reaches this from the gateway, and a path built out of one
        # is a path a caller chooses.
        out_path = os.path.join(self.root, f"out-{index}.fifo")
        err_path = os.path.join(self.root, f"err-{index}.fifo")
        for path in (out_path, err_path):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            os.mkfifo(path, 0o600)
        try:
            return SinkCapture(out_path, err_path, cap=cap, on_chunk=on_chunk)
        except BaseException:
            for path in (out_path, err_path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            raise

    def close(self) -> None:
        # `self.root` is ours either way — when the sandbox supplied the parent
        # we still made the `sinks/` directory inside it, so removing it takes
        # only what we put there.
        shutil.rmtree(self.root, ignore_errors=True)
