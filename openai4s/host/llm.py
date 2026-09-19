"""Host-side LLM calls used from inside a running science cell."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from openai4s.config import Config


def _priceable(value: Any) -> Any:
    """A copy of `value` that `json.dumps(allow_nan=False)` will accept.

    Routed through json itself rather than a recursive walk. The first attempt
    at this recursed over the structure and raised `RecursionError` on a deeply
    nested message -- inventing a brand new "unpriceable" path, and therefore a
    brand new bypass, out of the fix for the last one. `json.dumps` handles
    that same input, so mirroring it cannot fail where the pricer succeeds.

    `parse_constant` is json's own hook for exactly the three tokens
    `allow_nan=False` rejects. Each becomes its quoted name, which is longer
    than the bare token, so the byte bound over the copy is never smaller than
    the real payload would have produced.
    """
    return json.loads(json.dumps(value, default=repr), parse_constant=str)


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
        # A call nobody can price runs alone. Three separate bypasses of this
        # gate were the same shape -- make the request unpriceable and the
        # promise is 0, so every sibling starts against the same pre-spend
        # window -- and patching each shape as it was found is a losing game:
        # the third one was introduced BY the fix for the second. So an
        # unpriceable call no longer claims nothing, it claims the only slot.
        # The fan-out degrades to serial, the ledger catches up between calls,
        # and the gate starts refusing on its own.
        #
        # Only where a gate is installed. Without one -- the CLI, tests, a
        # single-user daemon with no owner row (INV-1) -- there is nothing to
        # protect and serialising would be pure cost.
        self._unpriced_slot = threading.Semaphore(1)

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
        wearing a limit's name. The responses wire is the live case: the bound
        declines to certify it, so a fan-out there is admitted exactly as it
        was before this gate existed.
        """
        from openai4s.server.auto_budget import token_upper_bound_parts

        # Price what the wire will SEND, not what the spec asked for. Every
        # adapter resolves the cap as `max_tokens or cfg.max_tokens`, so a
        # spec asking for 0 still sends the configured cap -- while the bound
        # reads 0 as "no completion" and declines to price the call at all.
        # A cell could therefore reserve nothing by asking for nothing:
        # measured at `max_tokens: 0` putting all 32 fan-out items through a
        # 100-token window again, the whole overshoot back in one field.
        # The same `type(x) is int` test the pricer applies, so the two cannot
        # disagree about what a cap is. They did: `type(True) is int` is False
        # while the wire validator's `isinstance(True, int)` is True, so
        # `max_tokens: true` priced as unpriceable and was waved through to the
        # provider -- 32 calls and 1280 tokens against a 100-token window.
        # Anything that is not a positive int is priced at the configured cap,
        # which is also what every adapter's `max_tokens or cfg.max_tokens`
        # sends for a falsy one.
        requested = spec.get("max_tokens")
        completion = (
            requested
            if type(requested) is int and requested > 0
            else getattr(config.llm, "max_tokens", None)
        )
        messages = spec.get("messages") or []
        try:
            parts = token_upper_bound_parts(
                config.llm, messages=messages, max_tokens=completion
            )
            if parts is None:
                # Retried on a JSON-safe copy. The bound refuses a request it
                # cannot serialise EXACTLY -- `allow_nan=False` -- because the
                # Auto Budget needs a hard ceiling. This gate needs only an
                # upper bound, and the adapters drop the very keys that make a
                # message unserialisable, so bounding the raw messages
                # over-estimates rather than under.
                #
                # "Unpriceable" was cell-controllable and therefore free: a
                # single extra key holding `float("nan")` rides a `host_call`
                # frame intact (the kernel writes frames with json's default
                # `allow_nan=True`), makes every sibling claim nothing, and
                # puts all 32 fan-out items through a 100-token window again.
                #
                # This retry closes the `allow_nan` shape only. The surface is
                # wider than that -- the bound can also RAISE, and the blanket
                # `except` below reads any raise as free -- which is why the
                # slot in `_admit` exists and why the node count in
                # `token_upper_bound_parts` had to stop recursing.
                parts = token_upper_bound_parts(
                    config.llm,
                    messages=_priceable(messages),
                    max_tokens=completion,
                )
        except Exception:  # noqa: BLE001 - an unpriceable call is not refused
            parts = None
        if parts is None:
            return (0.0, 0.0)
        prompt, completion, attempts = parts
        return (float(prompt * attempts), float(completion * attempts))

    def _admit(self, spec: dict, config: Config) -> tuple[float, float] | None:
        """Gate this request against the ledger PLUS everything in flight.

        Returns the promise to release later, or None when the gate is inert.
        """
        if self.quota_gate is None:
            return None
        promised = self._projected(spec, config)
        unpriced = promised == (0.0, 0.0)
        if unpriced:
            # Unpriceable. Hold the single slot for the whole call rather than
            # claiming nothing; released in `one`'s `finally` with the promise.
            self._unpriced_slot.acquire()
        try:
            return self._claim(promised)
        except BaseException:
            # The gate refusing must not keep the slot. It did, for one
            # revision of this method: a refused unpriceable call left the
            # semaphore held and every later one blocked forever -- a port
            # bricked for the life of the daemon, which is worse than the
            # overshoot the slot exists to stop. Measured as a hang, here,
            # before this was written.
            if unpriced:
                self._unpriced_slot.release()
            raise

    def _claim(self, promised: tuple[float, float]) -> tuple[float, float]:
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

    def _release(self, promised: tuple[float, float] | None) -> None:
        if promised is None:
            return
        if promised == (0.0, 0.0):
            self._unpriced_slot.release()
            return
        with self._inflight_lock:
            self._inflight_input = max(0.0, self._inflight_input - promised[0])
            self._inflight_output = max(0.0, self._inflight_output - promised[1])

    def one(self, spec: dict) -> str:
        config = self._config()
        # Before the request, not after: a refusal must not have spent anything.
        # Outside the metering try below, because a swallowed gate is not a gate.
        promised = self._admit(spec, config)
        try:
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
            # `host.llm` used to project `content` out and drop the whole
            # reply, so a cell's own LLM spend reached no frame counter, no
            # governance ledger and no budget -- the widest of the daemon's
            # unmetered ports, at up to LLM_FANOUT_CAP billed requests per call.
            if self.usage_sink is not None:
                self.usage_sink(response.get("usage"))
        finally:
            # After the sink, not before it. The release used to sit in a
            # `finally` that ran while the ledger row was still unwritten,
            # leaving a window where the spend was in neither place and a
            # sibling admitted against a window that had already been spent.
            #
            # And on every exit path, so a promise cannot outlive its request:
            # a leaked promise would shrink the window for the rest of the
            # daemon's life, which is worse than the overshoot it prevents.
            self._release(promised)
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
