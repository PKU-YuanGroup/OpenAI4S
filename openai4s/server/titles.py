"""Background session-title generation for the web gateway."""

from __future__ import annotations

import re
import threading
from typing import Any, Callable, Protocol


class SessionTitleStore(Protocol):
    """Persistence surface needed by :class:`SessionTitleService`."""

    def get_frame(self, frame_id: str) -> dict | None: ...

    def update_frame(self, frame_id: str, **fields: Any) -> None: ...


ChatCall = Callable[..., dict]
Broadcast = Callable[[str, dict], None]
ThreadFactory = Callable[..., Any]
StoreProvider = Callable[[], SessionTitleStore]
SummarizeCall = Callable[[str, Any, str], str | None]


class SessionTitleService:
    """Summarize a first message and conditionally replace its placeholder."""

    def __init__(
        self,
        *,
        store: SessionTitleStore | StoreProvider,
        broadcast: Broadcast,
        chat_call: ChatCall | None = None,
        thread_factory: ThreadFactory | None = None,
        summarize_call: SummarizeCall | None = None,
        usage_sink: Callable[[str, Any], None] | None = None,
    ) -> None:
        self._store_source = store
        self.broadcast = broadcast
        self._chat_call = chat_call
        self._thread_factory = thread_factory
        self._summarize_call = summarize_call
        # Titling is real billed spend on ordinary user traffic. It reached no
        # meter at all, so a session's first message was free in the ledger.
        self._usage_sink = usage_sink

    def _store(self) -> SessionTitleStore:
        source = self._store_source
        return source() if callable(source) else source

    def _chat(self, messages: list[dict], llm_cfg, **kwargs) -> dict:
        if self._chat_call is not None:
            return self._chat_call(messages, llm_cfg, **kwargs)
        # Resolve the wire client at call time.  Besides avoiding eager provider
        # work, this preserves the gateway's late-monkeypatch behavior.
        from openai4s import llm

        return llm.chat(messages, llm_cfg, **kwargs)

    def summarize(self, user_text: str, llm_cfg, root_frame_id: str = "") -> str | None:
        """Return a cleaned short title, or ``None`` for an unusable reply."""
        source = re.sub(r"\s+", " ", user_text or "").strip()[:2000]
        if not source:
            return None
        messages = [
            {
                "role": "system",
                "content": (
                    "You name chat sessions. Read the user's first message and reply "
                    "with a short title capturing its intent — at most 16 characters "
                    "for Chinese/CJK, or 6 words for English. Reply in the SAME "
                    "language as the message. Output the title only: no surrounding "
                    "quotes, no trailing punctuation, no label like '标题:' or 'Title:'."
                ),
            },
            {"role": "user", "content": source},
        ]
        try:
            result = self._chat(
                messages,
                llm_cfg,
                max_tokens=64,
                temperature=0.3,
            )
        except BaseException as error:
            if self._usage_sink is not None and not getattr(
                error, "llm_not_started", False
            ):
                self._usage_sink(root_frame_id, getattr(error, "usage", None))
            raise
        if self._usage_sink is not None:
            self._usage_sink(root_frame_id, result.get("usage"))
        if str(result.get("finish_reason") or "").lower() in (
            "length",
            "max_tokens",
        ):
            return None
        title = (result.get("content") or "").strip()
        if not title:
            return None
        title = title.splitlines()[0].strip()
        title = re.sub(
            r"^(标题|title)\s*[:：]\s*",
            "",
            title,
            flags=re.IGNORECASE,
        )
        title = title.strip().strip("\"“”'`*").strip()
        for opening, closing in (
            ("《", "》"),
            ("「", "」"),
            ("『", "』"),
            ("【", "】"),
            ("（", "）"),
            ("(", ")"),
        ):
            if (
                len(title) >= 2
                and title[0] == opening
                and title[-1] == closing
                and title.count(opening) == 1
                and title.count(closing) == 1
            ):
                title = title[1:-1].strip()
                break
        return title[:80] or None

    def spawn(
        self,
        root_frame_id: str,
        user_text: str,
        llm_cfg,
        placeholder: str,
    ) -> None:
        """Generate and conditionally persist a title on a daemon thread."""

        def target() -> None:
            try:
                summarize = self._summarize_call or self.summarize
                # The frame travels to whatever is installed here: the gateway
                # overrides `summarize_call`, so a meter that only hooked the
                # default method would never fire on the real path.
                title = summarize(user_text, llm_cfg, root_frame_id)
            except Exception:  # noqa: BLE001 - titling must never break a turn
                return
            if not title or title == placeholder:
                return
            store = self._store()
            current = store.get_frame(root_frame_id) or {}
            if current.get("name") or current.get("task_summary") != placeholder:
                return
            store.update_frame(root_frame_id, task_summary=title)
            self.broadcast(
                root_frame_id,
                {
                    "type": "frame_update",
                    "frame_id": root_frame_id,
                    "status": "titled",
                    "task_summary": title,
                },
            )

        factory = self._thread_factory or threading.Thread
        factory(
            target=target,
            name=f"os-title-{root_frame_id}",
            daemon=True,
        ).start()


__all__ = ["SessionTitleService"]
