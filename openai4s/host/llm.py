"""Host-side LLM calls used from inside a running science cell."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from openai4s.config import Config


class LLMService:
    """Run synchronous single or bounded-fanout in-kernel LLM requests."""

    def __init__(
        self,
        config: Config | Callable[[], Config],
        *,
        chat_call: Callable[..., dict] | None = None,
        one_call: Callable[[dict], str] | None = None,
        fanout_cap: int | Callable[[], int] = 32,
        executor_factory: Callable[..., Any] = ThreadPoolExecutor,
        quota_gate: Callable[..., None] | None = None,
        usage_sink: Callable[[Any], None] | None = None,
    ) -> None:
        self.config = config
        self.chat_call = chat_call
        self.one_call = one_call
        self.fanout_cap = fanout_cap
        self.executor_factory = executor_factory
        # The two halves the daemon owns and this service must not implement:
        # who may spend, and where the spend is recorded. Both are injected so
        # a bare LLMService (CLI, tests) stays inert.
        self.quota_gate = quota_gate
        self.usage_sink = usage_sink
        # Spend promised to requests that are in flight. The ledger the gate
        # reads is written only when a call RETURNS, so without this every
        # worker of a fan-out asks the same pre-spend question and gets the
        # same "yes": measured at 32 items putting 1280 tokens through a
        # 100-token window, where a serialised caller is refused on its fourth.
        #
        # In-process and released in a `finally`, deliberately. A durable
        # reservation would survive a crash and shrink the window with nothing
        # able to clear it; this one dies with the daemon, and a hung request
        # holds its promise only as long as `total_timeout_s` allows.
        self._inflight_lock = threading.Lock()
        self._inflight_input = 0.0
        self._inflight_output = 0.0

    def _config(self) -> Config:
        return self.config() if callable(self.config) else self.config

    def _fanout_cap(self) -> int:
        return self.fanout_cap() if callable(self.fanout_cap) else self.fanout_cap

    def _chat(self, *args, **kwargs) -> dict:
        if self.chat_call is not None:
            return self.chat_call(*args, **kwargs)
        from openai4s.llm import chat

        return chat(*args, **kwargs)

    def _projected(self, spec: dict, config: Config) -> tuple[float, float]:
        """This request's pre-spend upper bound, split per quota kind.

        Split rather than combined: comparing one figure against either limit
        over-refuses, and a quota that turns away work it was never going to
        cost is its own defect. Unknown bounds are 0, which leaves the gate
        exactly as strong as it was before -- honest, rather than a guess
        wearing a limit's name.
        """
        from openai4s.server.auto_budget import token_upper_bound_parts

        try:
            parts = token_upper_bound_parts(
                config.llm,
                messages=spec.get("messages") or [],
                max_tokens=spec.get("max_tokens"),
            )
        except Exception:  # noqa: BLE001 - an unpriceable call is not refused
            parts = None
        if parts is None:
            return (0.0, 0.0)
        prompt, completion, attempts = parts
        return (float(prompt * attempts), float(completion * attempts))

    def _admit(self, spec: dict, config: Config) -> tuple[float, float]:
        """Gate this request against the ledger PLUS everything in flight."""
        if self.quota_gate is None:
            return (0.0, 0.0)
        promised = self._projected(spec, config)
        with self._inflight_lock:
            # Charged with what OTHER requests have promised, not with this
            # one's own bound. Including it would make the gate refuse work it
            # was never going to cost: these bounds carry a wire allowance and
            # the transport's retry ceiling, so one 40-token call prices at
            # ~3.8k and a 100-token window would admit nothing at all. The
            # missing fact was never "what will I cost" -- the ledger records
            # that a moment later -- it is "what is already promised and not
            # yet recorded", which is exactly what a fan-out hides.
            #
            # So a lone call is gated exactly as it was before, and siblings
            # that start while it is in flight are the ones held back. Near a
            # limit that degrades a fan-out to serial, which is the correct
            # direction; far from one the promises are noise against the limit
            # and nothing is throttled.
            #
            # The lock spans the check AND the claim, or two callers read the
            # same pending total and both add to it.
            self.quota_gate(
                projected_input=self._inflight_input,
                projected_output=self._inflight_output,
            )
            self._inflight_input += promised[0]
            self._inflight_output += promised[1]
        return promised

    def _release(self, promised: tuple[float, float]) -> None:
        with self._inflight_lock:
            self._inflight_input = max(0.0, self._inflight_input - promised[0])
            self._inflight_output = max(0.0, self._inflight_output - promised[1])

    def one(self, spec: dict) -> str:
        config = self._config()
        # Before the request, not after: a refusal must not have spent anything.
        # Outside the metering try below, because a swallowed gate is not a gate.
        promised = self._admit(spec, config)
        try:
            response = self._chat(
                spec.get("messages") or [],
                config.llm,
                max_tokens=spec.get("max_tokens"),
                temperature=spec.get("temperature"),
            )
        except BaseException as error:
            # A call that reached the provider was billed even though it
            # raised. `llm.chat` attaches the evidence for exactly this.
            if self.usage_sink is not None and not getattr(
                error, "llm_not_started", False
            ):
                self.usage_sink(getattr(error, "usage", None))
            raise
        finally:
            # Every exit path, so a promise cannot outlive the request it was
            # made for. A leaked promise would shrink the window for the rest
            # of the daemon's life, which is worse than the overshoot it
            # prevents -- the trade this codebase already refused once.
            self._release(promised)
        # `host.llm` used to project `content` out and drop the whole reply, so
        # a cell's own LLM spend reached no frame counter, no governance ledger
        # and no budget -- the widest of the daemon's unmetered ports, at up to
        # LLM_FANOUT_CAP billed requests per call.
        if self.usage_sink is not None:
            self.usage_sink(response.get("usage"))
        return response.get("content", "")

    def _complete_one(self, spec: dict) -> str:
        return self.one_call(spec) if self.one_call is not None else self.one(spec)

    def complete(self, spec: dict) -> Any:
        if "batch" in spec:
            batch = spec.get("batch") or []
            if not batch:
                return []
            requested = spec.get("max_concurrency") or self._fanout_cap()
            workers = max(1, min(self._fanout_cap(), requested, len(batch)))
            with self.executor_factory(max_workers=workers) as executor:
                return list(executor.map(self._complete_one, batch))
        return self._complete_one(spec)

    def current_model(self) -> str:
        return self._config().llm.model

    def list_models(self) -> list[dict]:
        config = self._config()
        return [
            {
                "id": config.llm.model,
                "context_window": config.context_window_tokens,
                "default": True,
            }
        ]


__all__ = ["LLMService"]
