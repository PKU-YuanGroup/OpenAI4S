"""A UTF-8 byte-bounded handoff from provider threads to the owning turn."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from collections.abc import Callable

MAX_TEXT_BUFFER_BYTES = 1024 * 1024


class TextBuffer:
    def __init__(
        self,
        *,
        cancelled: Callable[[], bool],
        remaining: Callable[[], float],
        limit: int = MAX_TEXT_BUFFER_BYTES,
    ) -> None:
        if limit < 4:
            raise ValueError("text buffer must hold at least one UTF-8 character")
        self.limit = limit
        self._cancelled = cancelled
        self._remaining = remaining
        self._condition = threading.Condition()
        self._chunks: deque[tuple[str, int]] = deque()
        self._bytes = 0
        self._closed = False
        self.peak_bytes = 0

    @property
    def pending_bytes(self) -> int:
        with self._condition:
            return self._bytes

    def put(self, text: str) -> None:
        raw = text.encode("utf-8")
        start = 0
        while start < len(raw):
            end = min(start + self.limit, len(raw))
            while end < len(raw) and raw[end] & 0xC0 == 0x80:
                end -= 1
            size = end - start
            chunk = raw[start:end].decode("utf-8")
            with self._condition:
                while True:
                    if self._closed or self._cancelled():
                        return
                    remaining = self._remaining()
                    if self._bytes + size <= self.limit:
                        break
                    self._condition.wait(min(0.05, remaining))
                self._chunks.append((chunk, size))
                self._bytes += size
                self.peak_bytes = max(self.peak_bytes, self._bytes)
                self._condition.notify_all()
            start = end

    def get(self, timeout: float) -> str:
        until = time.monotonic() + timeout
        with self._condition:
            while not self._chunks:
                left = until - time.monotonic()
                if self._closed or left <= 0:
                    raise queue.Empty
                self._condition.wait(left)
            text, size = self._chunks.popleft()
            self._bytes -= size
            self._condition.notify_all()
            return text

    def empty(self) -> bool:
        with self._condition:
            return not self._chunks

    def close(self) -> None:
        """Abandon unread text and wake a blocked producer without replay."""
        with self._condition:
            self._closed = True
            self._chunks.clear()
            self._bytes = 0
            self._condition.notify_all()
