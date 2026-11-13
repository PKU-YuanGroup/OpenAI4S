"""One serial, owned synchronous execution lane per asynchronous Episode.

The complete synchronous lifecycle runs on one thread. Async sampling remains
on the caller's loop; cancellation drains the lane before its owner is released.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
from typing import Any, Callable

from .rollout import _sample


class Cancellation:
    def __init__(self, original: Any, requested: threading.Event):
        self.original = original
        self.requested = requested

    def cancelled(self) -> bool:
        return self.requested.is_set() or bool(self.original.cancelled())


class SampleBridge:
    """Invoke sync policy on the lane and awaitable policy on its owning loop."""

    def __init__(
        self, sampler: Any, loop: asyncio.AbstractEventLoop, cancelled: threading.Event
    ):
        self.sampler = sampler
        self.loop = loop
        self.cancelled = cancelled

    def sample(self, observation: Any, *, tools: Any = ()) -> Any:
        if self.cancelled.is_set():
            raise InterruptedError("rollout sampling was cancelled")
        callback = getattr(self.sampler, "sample_async", None)
        if not callable(callback):
            callback = getattr(self.sampler, "sample", self.sampler)
        if inspect.iscoroutinefunction(callback):

            async def invoke() -> Any:
                value = _sample(callback, observation, tools)
                return await value if inspect.isawaitable(value) else value

            return self._wait(invoke())
        value = _sample(self.sampler, observation, tools)
        if inspect.isawaitable(value):
            return self._wait(value)
        return value

    def _wait(self, value: Any) -> Any:
        async def await_value() -> Any:
            return await value

        future = asyncio.run_coroutine_threadsafe(await_value(), self.loop)
        while True:
            try:
                return future.result(timeout=0.05)
            except concurrent.futures.TimeoutError:
                if future.done():
                    return future.result()
                if self.cancelled.is_set():
                    future.cancel()
                    raise InterruptedError("rollout sampling was cancelled")


async def run_on_lane(
    execute: Callable[[SampleBridge, threading.Event], Any],
    sampler: Any,
    *,
    interrupt: Callable[[], None] | None = None,
) -> Any:
    loop = asyncio.get_running_loop()
    requested = threading.Event()
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="rl-episode"
    )
    bridge = SampleBridge(sampler, loop, requested)
    future = loop.run_in_executor(pool, execute, bridge, requested)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        requested.set()
        if interrupt is not None:
            # Interrupt is an explicit thread-safe, exact-owner callback, not
            # shutdown. Even a failed interrupt must not skip draining.
            try:
                interrupt()
            except Exception:
                pass
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if future.done() and not future.cancelled():
            future.exception()  # consume the completed lane's exception
        raise
    finally:
        # Only reached after actual execution drained; never join a live lane
        # on the event loop, nor detach a live thread and release its slot.
        pool.shutdown(wait=True)
