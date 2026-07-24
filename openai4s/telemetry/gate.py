"""The transmission gate: one worker, one bounded queue, one revoke barrier.

Everything that reaches the network goes through here, and it exists for two
promises the rest of the package could not keep on its own.

**Revocation is a boundary, not a request to stop soon.** ``sender.send`` read
consent and then, at a later moment on another thread, opened a socket. A
revoke landing in that window returned to the caller while a payload sealed
under the destroyed identity was still on its way out. The barrier below is
held across *both* the consent read and the socket open, and ``consent.revoke``
takes the same lock — so once revoke returns, every subsequent send re-reads a
consent row that is gone, and no request can begin. The cost is stated rather
than hidden: a revoke arriving mid-request waits for that one request's
timeout. That is the price of the guarantee, and it is bounded.

**Delivery is bounded.** ``emit`` used to start a daemon thread per flush, so a
stalled collector turned an event rate into a thread rate; ``_MAX_BUFFER``
bounded the records and nothing bounded the threads, sockets or payloads in
flight. There is one worker and one queue of :data:`MAX_PENDING`. Past that the
*newest* payload is dropped and counted — dropping the oldest would reorder a
best-effort stream to no benefit, and blocking the caller would put telemetry
on the critical path, which is the one thing it may never be.

A revoke also drops whatever is still queued. A payload waiting here was sealed
under an identity that no longer exists, and the sender would refuse it anyway;
discarding it is simply the earlier and more honest place to say so.
"""
from __future__ import annotations

import contextlib
import queue
import threading
from typing import Any, Iterator

#: The most sealed payloads that may be waiting for the worker at once.
MAX_PENDING = 32

#: Held across the consent read *and* the socket open by senders, and around
#: the consent delete by revoke. Re-entrant so a send that ends up calling back
#: into the gate cannot deadlock against itself.
_BARRIER = threading.RLock()

_QUEUE: queue.Queue = queue.Queue(maxsize=MAX_PENDING)
_STATE = threading.Lock()
_IDLE = threading.Condition(_STATE)
_WORKER: threading.Thread | None = None
_DROPPED = 0
_IN_FLIGHT = 0

#: Test support. `_RUNNING` unset parks the worker at the top of its loop; the
#: sentinel exists to wake one that is already blocked on an empty queue, so a
#: pause never has to be a poll.
_RUNNING = threading.Event()
_RUNNING.set()
_PARKED = threading.Event()
_PARK = object()


@contextlib.contextmanager
def transmitting() -> Iterator[None]:
    """The window a sender must not be interrupted in.

    Enclose the authorisation check and the socket open together. Splitting
    them is the defect: consent read here, request begun there, revoke in
    between.
    """
    with _BARRIER:
        yield


@contextlib.contextmanager
def revoking() -> Iterator[None]:
    """Destroy consent with no send able to start on either side of it.

    Whatever is still queued goes too: it was sealed under the identity being
    destroyed.
    """
    with _BARRIER:
        try:
            yield
        finally:
            discard_pending()


def submit(store: Any, payload: Any) -> bool:
    """Hand one sealed payload to the worker. Returns whether it was accepted.

    Never blocks and never raises. A refusal is a counted drop, not an error:
    telemetry that can stall a turn has already failed at its only job.
    """
    global _DROPPED
    _ensure_worker()
    try:
        _QUEUE.put_nowait((store, payload))
    except queue.Full:
        with _STATE:
            _DROPPED += 1
        return False
    return True


def discard_pending() -> int:
    """Empty the queue. Returns how many payloads were dropped."""
    dropped = 0
    while True:
        try:
            item = _QUEUE.get_nowait()
        except queue.Empty:
            break
        _QUEUE.task_done()
        if item is not _PARK:
            dropped += 1
    return dropped


def pending() -> int:
    return _QUEUE.qsize()


def dropped() -> int:
    with _STATE:
        return _DROPPED


def wait_idle(timeout: float = 5.0) -> bool:
    """Block until nothing is queued and no send is in flight. Test support."""
    with _IDLE:
        return _IDLE.wait_for(
            lambda: _QUEUE.empty() and _IN_FLIGHT == 0, timeout=timeout
        )


def _ensure_worker() -> None:
    global _WORKER
    with _STATE:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(
            target=_worker_loop, name="openai4s-telemetry", daemon=True
        )
        _WORKER.start()


def _worker_loop() -> None:
    global _IN_FLIGHT
    while True:
        _RUNNING.wait()
        item = _QUEUE.get()
        if item is _PARK:
            _QUEUE.task_done()
            _PARKED.set()
            continue
        with _STATE:
            _IN_FLIGHT += 1
        try:
            store, payload = item
            from openai4s.telemetry import sender as sender_mod

            sender_mod.send(store, payload)
        except Exception:  # noqa: BLE001 - a failed report is not the user's
            pass
        finally:
            _QUEUE.task_done()
            with _IDLE:
                _IN_FLIGHT -= 1
                _IDLE.notify_all()


def pause_worker(timeout: float = 5.0) -> None:
    """Hold the worker so a submitted payload stays observably queued."""
    _PARKED.clear()
    _RUNNING.clear()
    with _STATE:
        alive = _WORKER is not None and _WORKER.is_alive()
    if not alive:
        return
    try:
        _QUEUE.put_nowait(_PARK)
    except queue.Full:
        return
    _PARKED.wait(timeout=timeout)


def resume_worker() -> None:
    _PARKED.clear()
    _RUNNING.set()


def _reset_for_tests() -> None:
    global _DROPPED
    resume_worker()
    discard_pending()
    with _STATE:
        _DROPPED = 0


__all__ = [
    "MAX_PENDING",
    "discard_pending",
    "dropped",
    "pending",
    "revoking",
    "submit",
    "transmitting",
    "wait_idle",
]
