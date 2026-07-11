"""Host-side LLM calls used from inside a running science cell."""

from __future__ import annotations

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
    ) -> None:
        self.config = config
        self.chat_call = chat_call
        self.one_call = one_call
        self.fanout_cap = fanout_cap
        self.executor_factory = executor_factory

    def _config(self) -> Config:
        return self.config() if callable(self.config) else self.config

    def _fanout_cap(self) -> int:
        return self.fanout_cap() if callable(self.fanout_cap) else self.fanout_cap

    def _chat(self, *args, **kwargs) -> dict:
        if self.chat_call is not None:
            return self.chat_call(*args, **kwargs)
        from openai4s.llm import chat

        return chat(*args, **kwargs)

    def one(self, spec: dict) -> str:
        config = self._config()
        response = self._chat(
            spec.get("messages") or [],
            config.llm,
            max_tokens=spec.get("max_tokens"),
            temperature=spec.get("temperature"),
        )
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
