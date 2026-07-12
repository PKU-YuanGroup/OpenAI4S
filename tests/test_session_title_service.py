"""Direct contracts for background web-session titles."""

from __future__ import annotations

import pytest

from openai4s import llm
from openai4s.server.titles import SessionTitleService


class FakeStore:
    def __init__(self, frame: dict | None, events: list | None = None) -> None:
        self.frame = frame
        self.events = events if events is not None else []
        self.get_calls = []
        self.update_calls = []

    def get_frame(self, frame_id):
        self.events.append(("get", frame_id))
        self.get_calls.append(frame_id)
        return self.frame

    def update_frame(self, frame_id, **fields):
        self.events.append(("update", frame_id, fields))
        self.update_calls.append((frame_id, fields))


class ImmediateThreadFactory:
    def __init__(self) -> None:
        self.created = []

    def __call__(self, *, target, name, daemon):
        record = {
            "target": target,
            "name": name,
            "daemon": daemon,
            "started": False,
        }
        self.created.append(record)

        class ImmediateThread:
            def start(inner_self):
                record["started"] = True
                target()

        return ImmediateThread()


def _service(
    *,
    store: FakeStore | None = None,
    chat_call=None,
    events: list | None = None,
):
    event_log = events if events is not None else []
    actual_store = store or FakeStore(None, event_log)
    broadcasts = []
    threads = ImmediateThreadFactory()

    def broadcast(frame_id, event):
        event_log.append(("broadcast", frame_id, event))
        broadcasts.append((frame_id, event))

    service = SessionTitleService(
        store=actual_store,
        broadcast=broadcast,
        chat_call=chat_call,
        thread_factory=threads,
    )
    return service, actual_store, broadcasts, threads


def test_summarize_late_binds_chat_and_preserves_prompt_contract(monkeypatch):
    calls = []
    service, _store, _broadcasts, _threads = _service()

    def fake_chat(messages, cfg, **kwargs):
        calls.append((messages, cfg, kwargs))
        return {"content": "Title: “Useful title”\nignored"}

    # The service already exists: replacing llm.chat now must still take effect.
    monkeypatch.setattr(llm, "chat", fake_chat)
    cfg = object()

    assert service.summarize("  study\n\t protein   folding  ", cfg) == "Useful title"
    messages, used_cfg, kwargs = calls[0]
    assert used_cfg is cfg
    assert messages[1] == {"role": "user", "content": "study protein folding"}
    assert "at most 16 characters" in messages[0]["content"]
    assert kwargs == {"max_tokens": 64, "temperature": 0.3}


@pytest.mark.parametrize("finish_reason", ["length", "LENGTH", "MAX_TOKENS"])
def test_summarize_rejects_truncated_provider_replies(finish_reason):
    service, *_ = _service(
        chat_call=lambda *_a, **_kw: {
            "content": "Partial title",
            "finish_reason": finish_reason,
        }
    )

    assert service.summarize("first message", object()) is None


def test_summarize_skips_empty_input_without_chat_and_cleans_balanced_wrappers():
    replies = iter(
        [
            {"content": "《完整标题》"},
            {"content": "《红楼梦》赏析"},
            {"content": "x" * 90},
        ]
    )
    calls = []

    def chat_call(*_args, **_kwargs):
        calls.append(True)
        return next(replies)

    service, *_ = _service(chat_call=chat_call)

    assert service.summarize(" \n\t ", object()) is None
    assert calls == []
    assert service.summarize("one", object()) == "完整标题"
    assert service.summarize("two", object()) == "《红楼梦》赏析"
    assert service.summarize("three", object()) == "x" * 80


def test_spawn_reads_placeholder_before_write_and_broadcasts_bare_event():
    events = []
    store = FakeStore(
        {"name": None, "task_summary": "Immediate placeholder"},
        events,
    )

    def chat_call(*_args, **_kwargs):
        events.append(("chat",))
        return {"content": "Concise title"}

    service, _store, broadcasts, threads = _service(
        store=store,
        chat_call=chat_call,
        events=events,
    )

    assert (
        service.spawn(
            "frame-1",
            "first message",
            object(),
            "Immediate placeholder",
        )
        is None
    )

    assert [event[0] for event in events] == ["chat", "get", "update", "broadcast"]
    assert store.update_calls == [("frame-1", {"task_summary": "Concise title"})]
    assert broadcasts == [
        (
            "frame-1",
            {
                "type": "frame_update",
                "frame_id": "frame-1",
                "status": "titled",
                "task_summary": "Concise title",
            },
        )
    ]
    assert set(broadcasts[0][1]) == {
        "type",
        "frame_id",
        "status",
        "task_summary",
    }
    assert threads.created[0]["name"] == "os-title-frame-1"
    assert threads.created[0]["daemon"] is True
    assert threads.created[0]["started"] is True


@pytest.mark.parametrize(
    "frame",
    [
        {"name": "User title", "task_summary": "Placeholder"},
        {"name": None, "task_summary": "Changed while thinking"},
        None,
    ],
)
def test_spawn_does_not_overwrite_user_or_concurrent_title_changes(frame):
    store = FakeStore(frame)
    service, _store, broadcasts, _threads = _service(
        store=store,
        chat_call=lambda *_a, **_kw: {"content": "Generated title"},
    )

    service.spawn("frame-2", "message", object(), "Placeholder")

    assert store.get_calls == ["frame-2"]
    assert store.update_calls == []
    assert broadcasts == []


@pytest.mark.parametrize(
    "chat_call",
    [
        lambda *_a, **_kw: {"content": ""},
        lambda *_a, **_kw: {"content": "Placeholder"},
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("offline")),
    ],
)
def test_spawn_keeps_placeholder_without_read_on_unusable_or_failed_summary(
    chat_call,
):
    store = FakeStore({"name": None, "task_summary": "Placeholder"})
    service, _store, broadcasts, _threads = _service(
        store=store,
        chat_call=chat_call,
    )

    service.spawn("frame-3", "message", object(), "Placeholder")

    assert store.get_calls == []
    assert store.update_calls == []
    assert broadcasts == []


def test_spawn_resolves_replaced_store_when_background_work_starts():
    old_store = FakeStore({"name": None, "task_summary": "Placeholder"})
    new_store = FakeStore({"name": None, "task_summary": "Placeholder"})
    current = {"store": old_store}
    threads = ImmediateThreadFactory()
    service = SessionTitleService(
        store=lambda: current["store"],
        broadcast=lambda *_args: None,
        chat_call=lambda *_args, **_kwargs: {"content": "Generated title"},
        thread_factory=threads,
    )
    current["store"] = new_store

    service.spawn("frame-4", "message", object(), "Placeholder")

    assert old_store.get_calls == []
    assert old_store.update_calls == []
    assert new_store.update_calls == [("frame-4", {"task_summary": "Generated title"})]


def test_spawn_uses_injected_summary_wrapper_for_runtime_overrides():
    store = FakeStore({"name": None, "task_summary": "Placeholder"})
    calls = []
    service, _store, _broadcasts, _threads = _service(store=store)
    service._summarize_call = lambda text, cfg: calls.append((text, cfg)) or "Override"
    cfg = object()

    service.spawn("frame-5", "message", cfg, "Placeholder")

    assert calls == [("message", cfg)]
    assert store.update_calls == [("frame-5", {"task_summary": "Override"})]
