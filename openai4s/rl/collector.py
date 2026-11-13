"""Bounded asynchronous orchestration for local and remote rollouts.

The collector is deliberately a scheduler, not another execution loop.  It
starts a fixed number of consumer slots, pulls input lazily through a bounded
pending queue, and publishes completed rollouts through a bounded result
buffer.  Every request gets exactly one worker, and every worker owns exactly
one Episode.  Completion order, not submission order, controls the iterator.

The ``run_async`` call is awaited in the owning event loop.  Workers that
expose a purely synchronous ``run_async`` are offloaded, and a future managed
``close``/``aclose`` hook is drained under a shield.  If cancellation cannot
prove that a worker was cleaned up, the request id is reported through
:attr:`RolloutCollector.unconfirmed_requests` instead of being optimistically
released.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable, Mapping

from .models import Episode, Trajectory

_END = object()
_DONE = object()
_STOP = object()

_REQUEST_KEYS = frozenset(
    {
        "episode",
        "messages",
        "initial_artifacts",
        "config",
        "budget",
        "max_turns",
        "request_id",
        "execution_pool",
    }
)


class CollectorError(RuntimeError):
    """The collector rejected its input or lost a scheduling invariant."""


class CollectorClosedError(CollectorError):
    """The collector was closed before the operation could start."""


@dataclass(frozen=True)
class CollectedRollout:
    """One request's outcome in completion order.

    ``error`` records a failure from the worker factory or rollout; a completed
    but unclean worker contributes ``cleanup_error`` without discarding the
    confirmed trajectory.
    """

    request_id: str
    episode_id: str
    trajectory: Trajectory | None = None
    error: BaseException | None = None
    cleanup_error: BaseException | None = None

    @property
    def ok(self) -> bool:
        return self.trajectory is not None and self.error is None


class RolloutCollector:
    """Run requests through fixed worker slots with bounded queues.

    ``worker_factory(request)`` is called once per request and must return an
    object with an async ``run_async`` method.  Both the local
    :class:`openai4s.rl.worker.RolloutWorker` positional protocol
    ``(episode, messages, sampler, **options)`` and the request-dict protocol
    ``(request, sampler)`` are supported.

    ``sampler_factory(request)`` is likewise called once per request, inside
    the request's slot lane.
    """

    def __init__(
        self,
        worker_factory: Any,
        requests: Iterable[Mapping[str, Any]] | AsyncIterator[Mapping[str, Any]],
        sampler_factory: Any,
        *,
        max_concurrency: int = 4,
        max_pending: int = 8,
        max_results: int = 8,
        pool_limits: Mapping[str, int] | None = None,
    ) -> None:
        for name, value in (
            ("max_concurrency", max_concurrency),
            ("max_pending", max_pending),
            ("max_results", max_results),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if not callable(worker_factory):
            raise TypeError("worker_factory must be callable")
        if not callable(sampler_factory):
            raise TypeError("sampler_factory must be callable")

        self._pool_limits = dict(pool_limits or {"science": max_concurrency})
        if any(
            not isinstance(name, str)
            or not name
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit <= 0
            for name, limit in self._pool_limits.items()
        ):
            raise ValueError("pool_limits must map pool names to positive capacities")
        self._pools = {
            name: asyncio.Semaphore(limit) for name, limit in self._pool_limits.items()
        }
        self._worker_factory = worker_factory
        self._requests = requests
        self._sampler_factory = sampler_factory
        self._max_concurrency = max_concurrency
        self._max_pending = max_pending
        self._max_results = max_results

        self._pending: asyncio.Queue[Any] = asyncio.Queue(maxsize=max_pending)
        self._results: asyncio.Queue[Any] = asyncio.Queue()
        # Reserving capacity separately keeps the unbounded queue honest: at
        # most max_results completed items can be waiting, and the completion
        # sentinel can always be published without blocking close().
        self._result_slots = asyncio.Semaphore(max_results)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._producer: asyncio.Task[Any] | None = None
        self._started = False
        self._closed = False
        self._done = False
        self._slots_done = 0
        self._producer_error: BaseException | None = None
        self._slot_errors: list[BaseException] = []
        self._unconfirmed: list[str] = []
        self._quarantined_pools: set[str] = set()
        self._seen_requests: set[str] = set()
        # Ids this collector made up for tracking, not ones a caller chose.
        self._generated_request_ids: set[str] = set()
        self._seen_episodes: set[str] = set()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def unconfirmed_requests(self) -> tuple[str, ...]:
        """Request ids whose cancellation cleanup could not be confirmed."""

        return tuple(self._unconfirmed)

    def __aiter__(self) -> "RolloutCollector":
        return self

    async def __anext__(self) -> CollectedRollout:
        await self._ensure_started()
        if self._done:
            raise StopAsyncIteration
        item = await self._results.get()
        if item is _DONE:
            self._done = True
            if self._slot_errors:
                raise CollectorError("collector slot failed") from self._slot_errors[0]
            if self._producer_error is not None:
                error = self._producer_error
                self._producer_error = None
                raise error
            raise StopAsyncIteration
        self._result_slots.release()
        return item

    async def __aenter__(self) -> "RolloutCollector":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    async def collect(self) -> list[CollectedRollout]:
        """Consume the whole run in completion order."""

        return [item async for item in self]

    async def aclose(self) -> None:
        """Stop admission, drain in-flight cleanup, and wake the iterator.

        Closing is idempotent.  Cancellation of the surrounding coroutine is
        not used as evidence that a worker stopped: each owned worker is
        drained through its managed close hook first.
        """

        if self._closed:
            return
        self._closed = True
        tasks = [task for task in self._tasks if not task.done()]
        if self._producer is not None and not self._producer.done():
            tasks.append(self._producer)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._results.put_nowait(_DONE)

    async def _ensure_started(self) -> None:
        if self._started:
            return
        if self._closed:
            raise CollectorClosedError("collector is closed")
        self._started = True
        self._producer = asyncio.create_task(
            self._produce(), name="rollout-collector-producer"
        )
        for slot in range(self._max_concurrency):
            task = asyncio.create_task(
                self._consume(slot), name=f"rollout-collector-slot-{slot}"
            )
            self._tasks.add(task)

    async def _produce(self) -> None:
        try:
            async for item in _iter_requests(self._requests):
                if self._closed:
                    return
                request = self._normalize_request(item)
                await self._pending.put(request)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._producer_error = exc
        finally:
            if not self._closed:
                for _ in range(self._max_concurrency):
                    await self._pending.put(_STOP)

    async def _consume(self, slot: int) -> None:
        del slot  # slot identity is useful only in the task name
        try:
            while True:
                request = await self._pending.get()
                if request is _STOP:
                    return
                # Reserve output capacity before executing.  Without this the
                # consumers would keep running new Episodes while completed
                # results sat unconsumed, which is unbounded work disguised as
                # a bounded buffer.
                await self._result_slots.acquire()
                try:
                    await self._run_request(request)
                except BaseException:
                    self._result_slots.release()
                    raise
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # a slot bug must not hang the iterator
            self._slot_errors.append(exc)
        finally:
            self._slots_done += 1
            if self._slots_done == self._max_concurrency:
                self._results.put_nowait(_DONE)

    async def _run_request(self, request: Mapping[str, Any]) -> None:
        name = request["execution_pool"]
        pool = self._pools[name]
        await pool.acquire()
        try:
            if name in self._quarantined_pools:
                self._results.put_nowait(
                    CollectedRollout(
                        request["request_id"],
                        request["episode"].episode_id,
                        error=CollectorError(
                            "execution pool is quarantined pending recovery"
                        ),
                    )
                )
                return
            await self._run_in_pool(request)
        finally:
            if request["request_id"] in self._unconfirmed:
                self._quarantined_pools.add(name)
            # Wake queued consumers so they get an explicit refusal instead of
            # hanging. The pool is closed to new work, not optimistically freed.
            pool.release()

    async def _run_in_pool(self, request: Mapping[str, Any]) -> None:
        request_id = request["request_id"]
        episode_id = request["episode"].episode_id
        worker: Any = None
        interrupted = False
        result: CollectedRollout
        try:
            worker = await _call_factory(self._worker_factory, request)
            sampler = await _maybe_await(self._sampler_factory(request))
            # A generated id only tracks the request here; a local worker
            # gets it only if it is durable, or a non-durable worker would
            # refuse every request ("request_id requires a receipt repository").
            pass_request_id = (
                request_id not in self._generated_request_ids
                or getattr(worker, "repository", None) is not None
            )
            trajectory = await _invoke_worker(
                worker, request, sampler, pass_request_id=pass_request_id
            )
            if not isinstance(trajectory, Trajectory):
                raise CollectorError("worker.run_async must return a Trajectory")
            result = CollectedRollout(
                request_id=request_id,
                episode_id=episode_id,
                trajectory=trajectory,
            )
        except asyncio.CancelledError:
            interrupted = True
            if worker is not None:
                await self._drain_worker(worker, request_id)
            else:
                self._unconfirmed.append(request_id)
            raise
        except BaseException as exc:
            if getattr(exc, "state_unknown", False):
                self._unconfirmed.append(request_id)
            result = CollectedRollout(
                request_id=request_id,
                episode_id=episode_id,
                error=exc,
            )

        cleanup_error: BaseException | None = None
        if worker is not None and not interrupted:
            cleanup_error = await self._close_worker(worker, request_id)
        if cleanup_error is not None:
            result = CollectedRollout(
                request_id=result.request_id,
                episode_id=result.episode_id,
                trajectory=result.trajectory,
                error=result.error,
                cleanup_error=cleanup_error,
            )
        self._results.put_nowait(result)

    async def _drain_worker(self, worker: Any, request_id: str) -> None:
        """Finish managed cleanup even though the slot was cancelled.

        A close hook that returns proves the *transport* was released, not
        that a remote execution stopped.  Workers that cannot prove the latter
        set ``cancel_confirmed = False``; the request is then reported instead
        of being optimistically released.
        """

        closer = _close_hook(worker)
        if closer is None:
            self._unconfirmed.append(request_id)
            return
        try:
            await _drain_shielded(_call_closer(closer))
        except asyncio.CancelledError:
            raise
        except BaseException:
            self._unconfirmed.append(request_id)
            return
        if getattr(worker, "cancel_confirmed", True) is False:
            self._unconfirmed.append(request_id)

    async def _close_worker(self, worker: Any, request_id: str) -> BaseException | None:
        closer = _close_hook(worker)
        if closer is None:
            return None
        try:
            await _drain_shielded(_call_closer(closer))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._unconfirmed.append(request_id)
            return exc
        return None

    def _normalize_request(self, item: Any) -> dict[str, Any]:
        if not isinstance(item, Mapping):
            raise CollectorError("each rollout request must be a mapping")
        unknown = sorted(set(item) - _REQUEST_KEYS)
        if unknown:
            raise CollectorError(f"unknown rollout request key: {unknown[0]}")
        if "episode" not in item:
            raise CollectorError("rollout request requires an episode")
        if "messages" not in item:
            raise CollectorError("rollout request requires messages")
        episode = _episode(item["episode"])
        messages = _messages(item["messages"])
        request_id = item.get("request_id")
        if request_id is None:
            request_id = "rl-req-" + uuid.uuid4().hex
            self._generated_request_ids.add(request_id)
        elif not isinstance(request_id, str) or not request_id.strip():
            raise CollectorError("request_id must be a non-empty string")
        if request_id in self._seen_requests:
            raise CollectorError(f"duplicate request_id: {request_id!r}")
        if episode.episode_id in self._seen_episodes:
            raise CollectorError(f"duplicate episode_id: {episode.episode_id!r}")
        self._seen_requests.add(request_id)
        self._seen_episodes.add(episode.episode_id)

        pool = item.get("execution_pool", "science")
        if not isinstance(pool, str) or pool not in self._pools:
            raise CollectorError("execution_pool must name a configured pool")
        request: dict[str, Any] = {
            "episode": episode,
            "messages": messages,
            "request_id": request_id,
            "execution_pool": pool,
        }
        for key in ("initial_artifacts", "config", "budget"):
            if key in item and item[key] is not None:
                if not isinstance(item[key], Mapping):
                    raise CollectorError(f"{key} must be a mapping")
                request[key] = dict(item[key])
        if "max_turns" in item and item["max_turns"] is not None:
            max_turns = item["max_turns"]
            if isinstance(max_turns, bool) or not isinstance(max_turns, int):
                raise CollectorError("max_turns must be an integer or null")
            if max_turns < 0:
                raise CollectorError("max_turns must be non-negative")
            request["max_turns"] = max_turns
        return request


def _episode(value: Any) -> Episode:
    if isinstance(value, Episode):
        return value
    if not isinstance(value, Mapping):
        raise CollectorError("request episode must be an Episode or mapping")
    try:
        return Episode(**dict(value))
    except (TypeError, ValueError) as exc:
        raise CollectorError(f"invalid request episode: {exc}") from exc


def _messages(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise CollectorError("messages must be a list or tuple")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise CollectorError(f"message {index} must be a mapping")
        result.append(dict(item))
    return result


async def _iter_requests(
    source: Iterable[Mapping[str, Any]] | AsyncIterator[Mapping[str, Any]],
) -> AsyncIterator[Mapping[str, Any]]:
    if hasattr(source, "__aiter__"):
        async for item in source:
            yield item
        return
    iterator = iter(source)
    while True:
        item = await asyncio.to_thread(_next_or_end, iterator)
        if item is _END:
            return
        yield item


def _next_or_end(iterator: Any) -> Any:
    try:
        return next(iterator)
    except StopIteration:
        return _END


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _call_factory(factory: Any, request: Mapping[str, Any]) -> Any:
    if inspect.iscoroutinefunction(factory):
        return await factory(request)
    task = asyncio.create_task(asyncio.to_thread(factory, request))
    try:
        value = await asyncio.shield(task)
    except asyncio.CancelledError:
        # A synchronous factory may have acquired resources. Wait for its
        # actual result and close that owner before abandoning the slot.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not task.cancelled() and task.exception() is None:
            closer = _close_hook(task.result())
            if closer is not None:
                await _drain_shielded(_call_closer(closer))
        raise
    return await _maybe_await(value)


async def _invoke_worker(
    worker: Any,
    request: Mapping[str, Any],
    sampler: Any,
    *,
    pass_request_id: bool = True,
) -> Any:
    runner = getattr(worker, "run_async", None)
    if not callable(runner):
        raise CollectorError("worker must provide run_async")
    if _accepts_request_dict(runner):
        call: tuple[Any, ...] = (
            {key: value for key, value in request.items() if key != "execution_pool"},
            sampler,
        )
        kwargs: dict[str, Any] = {}
    else:
        options = {
            key: request[key]
            for key in (
                "initial_artifacts",
                "config",
                "budget",
                "max_turns",
                "request_id",
            )
            if key in request and (key != "request_id" or pass_request_id)
        }
        call = (request["episode"], request["messages"], sampler)
        kwargs = options
    if inspect.iscoroutinefunction(runner):
        return await runner(*call, **kwargs)
    value = await asyncio.to_thread(runner, *call, **kwargs)
    return await _maybe_await(value)


def _accepts_request_dict(runner: Any) -> bool:
    try:
        parameters = inspect.signature(runner).parameters
    except (TypeError, ValueError):
        return False
    return "request" in parameters or (
        "episode" not in parameters and "messages" not in parameters
    )


def _close_hook(worker: Any) -> Any:
    for name in ("aclose", "close"):
        hook = getattr(worker, name, None)
        if callable(hook):
            return hook
    return None


async def _call_closer(closer: Any) -> None:
    if inspect.iscoroutinefunction(closer):
        await closer()
        return
    value = await asyncio.to_thread(closer)
    await _maybe_await(value)


async def _drain_shielded(awaitable: Any) -> Any:
    """Await cleanup to completion despite an outer cancellation."""

    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        except BaseException:
            pass
        raise


__all__ = [
    "CollectedRollout",
    "CollectorClosedError",
    "CollectorError",
    "RolloutCollector",
]
