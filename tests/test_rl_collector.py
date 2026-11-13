from __future__ import annotations

import asyncio

import pytest

from openai4s.rl.collector import (
    CollectedRollout,
    CollectorError,
    RolloutCollector,
)
from openai4s.rl.models import Episode, Trajectory

pytestmark = pytest.mark.stubbed_backend


def _request(
    index: int,
    *,
    request_id: str | None = None,
    episode_id: str | None = None,
    **extra: object,
) -> dict:
    return {
        "episode": Episode(
            episode_id=episode_id or f"episode-{index}",
            task="collector task",
            session_id=f"session-{index}",
            created_at=1,
        ),
        "messages": [{"role": "user", "content": f"go-{index}"}],
        "request_id": request_id or f"req-{index}",
        **extra,
    }


class _Worker:
    """Request-dict worker with an observable lifecycle."""

    def __init__(
        self,
        request: dict,
        *,
        started: asyncio.Event | None = None,
        gate: asyncio.Event | None = None,
        state: dict | None = None,
        fail: bool = False,
        close_event: asyncio.Event | None = None,
        close: bool = True,
    ) -> None:
        self.request = request
        self.started = started
        self.gate = gate
        self.state = state
        self.fail = fail
        self.close_event = close_event
        self.close_calls = 0
        self.has_close = close
        self.cancelled = asyncio.Event()

    async def run_async(self, request: dict, sampler: object) -> Trajectory:
        assert request["request_id"] == self.request["request_id"]
        if self.started is not None:
            self.started.set()
        if self.state is not None:
            self.state["active"] += 1
            self.state["max_active"] = max(
                self.state["max_active"], self.state["active"]
            )
            self.state["started"] += 1
        try:
            if self.gate is not None:
                await self.gate.wait()
            if self.fail:
                raise RuntimeError("worker failed")
            return Trajectory(episode=request["episode"])
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            if self.state is not None:
                self.state["active"] -= 1

    async def aclose(self) -> None:
        if not self.has_close:
            raise AssertionError("worker has no managed close hook")
        self.close_calls += 1
        if self.close_event is not None:
            self.close_event.set()


class _NoCloseWorker(_Worker):
    has_close = False

    def __getattribute__(self, name: str):
        if name in {"aclose", "close"}:
            raise AttributeError(name)
        return super().__getattribute__(name)


class _LocalStyleWorker:
    """Positional ``RolloutWorker`` protocol used by existing compositions."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.close_calls = 0

    async def run_async(
        self,
        episode,
        messages,
        sampler,
        *,
        initial_artifacts=None,
        config=None,
        budget=None,
        max_turns=None,
        request_id=None,
    ) -> Trajectory:
        self.calls.append(
            (
                episode,
                messages,
                sampler,
                initial_artifacts,
                config,
                budget,
                max_turns,
                request_id,
            )
        )
        return Trajectory(episode=episode)

    async def aclose(self) -> None:
        self.close_calls += 1


def test_collector_runs_fixed_slots_with_real_overlap() -> None:
    async def scenario() -> list[CollectedRollout]:
        state = {"active": 0, "max_active": 0, "started": 0}
        both_started = asyncio.Event()
        gate = asyncio.Event()

        def on_started() -> None:
            if state["started"] == 2:
                both_started.set()

        class Worker(_Worker):
            async def run_async(self, request, sampler):
                if self.started is not None:
                    self.started.set()
                state["active"] += 1
                state["started"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
                on_started()
                try:
                    await gate.wait()
                    return Trajectory(episode=request["episode"])
                finally:
                    state["active"] -= 1

        collector = RolloutCollector(
            lambda request: Worker(request, started=asyncio.Event(), gate=gate),
            [_request(0), _request(1)],
            lambda request: object(),
            max_concurrency=2,
            max_pending=2,
            max_results=2,
        )

        async def release() -> None:
            await both_started.wait()
            gate.set()

        releaser = asyncio.create_task(release())
        try:
            results = await asyncio.wait_for(collector.collect(), 5)
        finally:
            await collector.aclose()
            releaser.cancel()
            await asyncio.gather(releaser, return_exceptions=True)
        assert state["max_active"] == 2
        return results

    results = asyncio.run(scenario())
    assert {item.request_id for item in results} == {"req-0", "req-1"}


def test_collector_pulls_input_lazily_and_respects_queue_bounds() -> None:
    async def scenario() -> None:
        pulled: list[int] = []

        def source():
            for index in range(10):
                pulled.append(index)
                yield _request(index)

        started = asyncio.Event()
        gate = asyncio.Event()

        def factory(request: dict) -> _Worker:
            return _Worker(request, started=started, gate=gate)

        collector = RolloutCollector(
            factory,
            source(),
            lambda request: object(),
            max_concurrency=1,
            max_pending=1,
            max_results=4,
        )
        first = asyncio.create_task(collector.__anext__())
        await asyncio.wait_for(started.wait(), 5)
        await asyncio.sleep(0.05)
        # One executing request, one queued request, and at most one request
        # already pulled from the generator while blocked on the bounded put.
        assert len(pulled) <= 3
        gate.set()
        first_result = await asyncio.wait_for(first, 5)
        rest = await asyncio.wait_for(collector.collect(), 5)
        results = [first_result, *rest]
        assert len(results) == 10
        assert len(pulled) == 10
        await collector.aclose()

    asyncio.run(scenario())


def test_collector_applies_backpressure_before_executing_new_work() -> None:
    async def scenario() -> None:
        state = {"active": 0, "started": 0}
        first_started = asyncio.Event()

        class Worker(_Worker):
            async def run_async(self, request, sampler):
                state["started"] += 1
                first_started.set()
                return Trajectory(episode=request["episode"])

        collector = RolloutCollector(
            lambda request: Worker(request),
            [_request(index) for index in range(4)],
            lambda request: object(),
            max_concurrency=4,
            max_pending=4,
            max_results=1,
        )
        await collector._ensure_started()
        await asyncio.wait_for(first_started.wait(), 5)
        await asyncio.sleep(0.1)
        # The completed result occupies the only output slot, so no other
        # worker may begin until the caller consumes it.
        assert state["started"] == 1
        results = await asyncio.wait_for(collector.collect(), 5)
        assert len(results) == 4
        await collector.aclose()

    asyncio.run(scenario())


def test_collector_yields_in_completion_order() -> None:
    async def scenario() -> list[CollectedRollout]:
        slow_gate = asyncio.Event()
        slow_started = asyncio.Event()

        class Worker(_Worker):
            async def run_async(self, request, sampler):
                if request["request_id"] == "req-slow":
                    slow_started.set()
                    await slow_gate.wait()
                return Trajectory(episode=request["episode"])

        requests = [
            _request(0, request_id="req-slow"),
            _request(1, request_id="req-fast"),
        ]
        collector = RolloutCollector(
            lambda request: Worker(request),
            requests,
            lambda request: object(),
            max_concurrency=2,
            max_pending=2,
            max_results=2,
        )
        task = asyncio.create_task(collector.collect())
        await asyncio.wait_for(slow_started.wait(), 5)
        await asyncio.sleep(0.05)
        slow_gate.set()
        results = await asyncio.wait_for(task, 5)
        await collector.aclose()
        return results

    results = asyncio.run(scenario())
    assert [item.request_id for item in results] == ["req-fast", "req-slow"]


def test_collector_creates_one_worker_per_request_and_closes_each_once() -> None:
    async def scenario() -> None:
        workers: list[_Worker] = []
        sampler_calls: list[str] = []

        def factory(request: dict) -> _Worker:
            worker = _Worker(request)
            workers.append(worker)
            return worker

        def sampler_factory(request: dict) -> object:
            sampler_calls.append(request["request_id"])
            return object()

        collector = RolloutCollector(
            factory,
            [_request(index) for index in range(3)],
            sampler_factory,
            max_concurrency=2,
            max_results=2,
        )
        results = await asyncio.wait_for(collector.collect(), 5)
        await collector.aclose()
        assert len(results) == 3
        assert len(workers) == 3
        assert {worker.request["request_id"] for worker in workers} == {
            "req-0",
            "req-1",
            "req-2",
        }
        assert all(worker.close_calls == 1 for worker in workers)
        assert sorted(sampler_calls) == ["req-0", "req-1", "req-2"]

    asyncio.run(scenario())


def test_collector_supports_the_existing_positional_worker_protocol() -> None:
    async def scenario() -> None:
        workers: list[_LocalStyleWorker] = []

        def factory(request: dict) -> _LocalStyleWorker:
            worker = _LocalStyleWorker()
            workers.append(worker)
            return worker

        request = _request(
            0, initial_artifacts={"a.txt": "v1"}, config={"x": 1}, max_turns=2
        )
        collector = RolloutCollector(
            factory,
            [request],
            lambda _request: object(),
            max_concurrency=1,
        )
        results = await asyncio.wait_for(collector.collect(), 5)
        await collector.aclose()
        assert len(results) == 1
        assert results[0].ok
        episode, messages, _sampler, artifacts, config, budget, max_turns, rid = (
            workers[0].calls[0]
        )
        assert episode.episode_id == "episode-0"
        assert messages == [{"role": "user", "content": "go-0"}]
        assert artifacts == {"a.txt": "v1"}
        assert config == {"x": 1}
        assert budget is None
        assert max_turns == 2
        assert rid == "req-0"
        assert workers[0].close_calls == 1

    asyncio.run(scenario())


def test_collector_surfaces_worker_errors_and_continues() -> None:
    async def scenario() -> list[CollectedRollout]:
        def factory(request: dict) -> _Worker:
            return _Worker(request, fail=request["request_id"] == "req-0")

        collector = RolloutCollector(
            factory,
            [_request(0), _request(1)],
            lambda request: object(),
            max_concurrency=2,
            max_results=2,
        )
        results = await asyncio.wait_for(collector.collect(), 5)
        await collector.aclose()
        return results

    results = asyncio.run(scenario())
    by_id = {item.request_id: item for item in results}
    assert by_id["req-0"].error is not None
    assert by_id["req-0"].ok is False
    assert by_id["req-1"].ok is True


def test_collector_rejects_duplicate_request_and_episode_ids() -> None:
    async def scenario(requests: list[dict]) -> None:
        collector = RolloutCollector(
            lambda request: _Worker(request),
            requests,
            lambda request: object(),
            max_concurrency=1,
        )
        with pytest.raises(CollectorError):
            await asyncio.wait_for(collector.collect(), 5)
        await collector.aclose()

    asyncio.run(scenario([_request(0), _request(1, request_id="req-0")]))
    asyncio.run(scenario([_request(0), _request(1, episode_id="episode-0")]))


def test_collector_cancellation_drains_managed_close_and_reports_unknown() -> None:
    async def managed() -> tuple[bool, bool, tuple[str, ...]]:
        started = asyncio.Event()
        closed = asyncio.Event()
        worker = _Worker(
            _request(0), started=started, gate=asyncio.Event(), close_event=closed
        )
        collector = RolloutCollector(
            lambda request: worker,
            [_request(0)],
            lambda request: object(),
            max_concurrency=1,
        )
        await collector._ensure_started()
        await asyncio.wait_for(started.wait(), 5)
        await asyncio.wait_for(collector.aclose(), 5)
        return (
            worker.cancelled.is_set(),
            closed.is_set(),
            collector.unconfirmed_requests,
        )

    cancelled, closed, unconfirmed = asyncio.run(managed())
    assert cancelled is True
    assert closed is True
    assert unconfirmed == ()

    async def unmanaged() -> tuple[str, ...]:
        started = asyncio.Event()
        worker = _NoCloseWorker(
            _request(0), started=started, gate=asyncio.Event(), close=False
        )
        collector = RolloutCollector(
            lambda request: worker,
            [_request(0)],
            lambda request: object(),
            max_concurrency=1,
        )
        await collector._ensure_started()
        await asyncio.wait_for(started.wait(), 5)
        await asyncio.wait_for(collector.aclose(), 5)
        return collector.unconfirmed_requests

    assert asyncio.run(unmanaged()) == ("req-0",)

    async def transport_only() -> tuple[str, ...]:
        started = asyncio.Event()
        closed = asyncio.Event()
        worker = _Worker(
            _request(0),
            started=started,
            gate=asyncio.Event(),
            close_event=closed,
        )
        worker.cancel_confirmed = False
        collector = RolloutCollector(
            lambda request: worker,
            [_request(0)],
            lambda request: object(),
            max_concurrency=1,
        )
        await collector._ensure_started()
        await asyncio.wait_for(started.wait(), 5)
        await asyncio.wait_for(collector.aclose(), 5)
        assert closed.is_set()
        return collector.unconfirmed_requests

    assert asyncio.run(transport_only()) == ("req-0",)


def test_managed_workers_overlap_on_owned_threads_without_blocking_loop(tmp_path):
    import threading
    from contextlib import contextmanager

    from openai4s.agent.engine import AgentEngine
    from openai4s.agent.models import ExecutionOutcome
    from openai4s.config import Config
    from openai4s.rl import RolloutEnvironment, RolloutWorker
    from openai4s.store import get_store

    store = get_store(Config(data_dir=tmp_path / "data").db_path)
    barrier = threading.Barrier(2, timeout=5)
    history = {}
    loop_thread = threading.get_ident()

    @contextmanager
    def factory(reset):
        key = reset.episode.episode_id
        history[key] = [threading.get_ident()]

        class Executor:
            def execute(self, *args):
                history[key].append(threading.get_ident())
                barrier.wait()
                return ExecutionOutcome(observation="ok", stop_reason="done")

        try:
            yield AgentEngine(None, Executor(), max_turns=1)
        finally:
            history[key].append(threading.get_ident())

    async def exercise():
        ticks = []

        async def heartbeat():
            for _ in range(20):
                await asyncio.sleep(0)
                ticks.append(1)

        async def sample(*args):
            assert threading.get_ident() == loop_thread
            return {"content": "```python\npass\n```"}

        collector = RolloutCollector(
            lambda request: RolloutWorker(
                RolloutEnvironment(
                    factory,
                    repository=store.rl,
                    workspace_root=tmp_path / "work",
                    default_max_turns=1,
                )
            ),
            [_request(0), _request(1)],
            lambda request: sample,
            max_concurrency=2,
        )
        results, _ = await asyncio.gather(collector.collect(), heartbeat())
        await collector.aclose()
        assert all(item.ok for item in results), [item.error for item in results]
        assert len(ticks) == 20

    try:
        asyncio.run(exercise())
        assert len(history) == 2
        assert all(
            len(set(threads)) == 1 and threads[0] != loop_thread
            for threads in history.values()
        )
        assert len({threads[0] for threads in history.values()}) == 2
        assert list((tmp_path / "work").iterdir()) == []
        assert store.rl.load_trajectory("episode-0").episode.status == "done"
    finally:
        store.close()


def test_async_cancellation_drains_running_execution_before_cleanup(tmp_path):
    import threading
    from contextlib import contextmanager

    from openai4s.agent.engine import AgentEngine
    from openai4s.agent.models import ExecutionOutcome
    from openai4s.rl import RolloutEnvironment, RolloutWorker

    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    @contextmanager
    def factory(reset):
        class Executor:
            def execute(self, *args):
                entered.set()
                assert release.wait(5)
                return ExecutionOutcome(observation="ok")

        try:
            yield AgentEngine(None, Executor(), max_turns=1)
        finally:
            closed.set()

    async def exercise():
        worker = RolloutWorker(RolloutEnvironment(factory, workspace_root=tmp_path))
        task = asyncio.create_task(
            worker.run_async(
                Episode("cancel", "task", "session", 1),
                [{"role": "user", "content": "go"}],
                lambda observation: {"content": "```python\npass\n```"},
            )
        )
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not closed.is_set()
        assert not task.done()
        assert worker.environment.active
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
        assert worker.cancel_confirmed
        assert list(tmp_path.iterdir()) == []

    asyncio.run(exercise())


def test_collector_real_kernel_phase_result_and_cleanup(tmp_path):
    from contextlib import contextmanager

    from openai4s.agent.engine import AgentEngine
    from openai4s.agent.ledger import RuntimeActionLedger
    from openai4s.agent.runtime import CompletionSignal, LocalActionExecutor
    from openai4s.config import Config
    from openai4s.host_dispatch import build_dispatcher
    from openai4s.kernel import Kernel
    from openai4s.kernel.lazy import LazyKernel
    from openai4s.rl import RolloutEnvironment, RolloutWorker
    from openai4s.store import get_store

    cfg = Config(data_dir=tmp_path / "data")
    store = get_store(cfg.db_path)
    kernels = []

    @contextmanager
    def factory(reset):
        frame = store.new_frame(kind="turn")
        dispatcher = build_dispatcher(cfg, frame_id=frame, workspace=reset.workspace)
        ledger = RuntimeActionLedger(store, frame, frame)

        def create():
            kernel = Kernel(dispatcher=dispatcher, cwd=str(reset.workspace))
            kernels.append(kernel)
            return kernel

        with LazyKernel(create) as kernel:
            executor = LocalActionExecutor(
                kernel,
                dispatcher,
                lambda *a: None,
                lambda code: {},
                action_ledger=ledger,
            )
            yield AgentEngine(
                None,
                executor,
                event_sink=ledger,
                completion=CompletionSignal(lambda: dispatcher.last_output),
                max_turns=3,
            )

    def sampler_factory(request):
        replies = iter(
            [
                "```python\nphase=host.begin_segment('science')\nx=41\n```",
                "```python\nphase.observe({'answer':x+1})\nprint(host.artifacts())\n```",
                "```python\nphase.close()\nhost.submit_output({'answer':x+1}, ['Computed answer'])\n```",
            ]
        )
        return lambda observation: {"content": next(replies)}

    async def exercise():
        async with RolloutCollector(
            lambda request: RolloutWorker(
                RolloutEnvironment(
                    factory,
                    repository=store.rl,
                    workspace_root=tmp_path / "work",
                    default_max_turns=3,
                )
            ),
            [_request(10), _request(11)],
            sampler_factory,
            max_concurrency=2,
        ) as collector:
            return await collector.collect()

    try:
        results = asyncio.run(exercise())
        assert all(item.ok for item in results), [item.error for item in results]
        for result in results:
            trajectory = result.trajectory
            assert trajectory.episode.status == "completed"
            assert len(trajectory.decisions) == 3
            assert (
                len(
                    [
                        s
                        for s in trajectory.segments
                        if s.metadata["openai4s.segment"]["source"] == "explicit"
                    ]
                )
                == 1
            )
            assert (
                store.rl.runs.result(result.request_id).canonical_json()
                == trajectory.canonical_json()
            )
        assert len(kernels) == 2 and all(not kernel.is_alive() for kernel in kernels)
        assert list((tmp_path / "work").iterdir()) == []
    finally:
        store.close()


def test_collector_runs_repository_less_rollout_workers(tmp_path) -> None:
    """A generated request_id is collector bookkeeping, not a durability request.

    Passing it to a RolloutWorker without a receipt repository made every
    request fail with "request_id requires a receipt repository".
    """
    from openai4s.agent.engine import AgentEngine
    from openai4s.agent.models import ExecutionOutcome
    from openai4s.rl import RolloutEnvironment, RolloutWorker

    class _Model:
        def complete(self, _messages, _on_delta):
            raise AssertionError("the sampler provides every reply")

    class _Executor:
        def execute(self, _action, _reply, _state):
            return ExecutionOutcome(observation="ok")

    class _Sampler:
        def sample(self, _observation, *, tools=()):
            return {"content": "```python\npass\n```"}

    def factory(_request: dict) -> RolloutWorker:
        return RolloutWorker(
            RolloutEnvironment(
                lambda _reset: AgentEngine(_Model(), _Executor(), max_turns=1),
                workspace_root=tmp_path / "ws",
            )
        )

    requests = [
        {
            "episode": Episode(f"bare-{index}", "t", "s", 1),
            "messages": [{"role": "user", "content": "go"}],
        }
        for index in range(2)
    ]

    async def scenario() -> list:
        collector = RolloutCollector(
            factory, requests, lambda _request: _Sampler(), max_concurrency=1
        )
        results = await asyncio.wait_for(collector.collect(), 30)
        await collector.aclose()
        return results

    results = asyncio.run(scenario())
    assert [result.ok for result in results] == [True, True]
    assert [result.error for result in results] == [None, None]
