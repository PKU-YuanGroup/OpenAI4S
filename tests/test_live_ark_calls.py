"""Explicit live_llm opt-in; credentials arrive on an inherited descriptor.

Set OPENAI4S_LIVE_ARK_KEY_FD to a readable descriptor containing the key and
OPENAI4S_LIVE_RECEIPT_DIR to a private output directory, then select -m live_llm.
The ordinary suite never opens the descriptor or contacts a provider.
"""

import json
import os
import threading
import time
from pathlib import Path

import pytest

from openai4s import llm
from openai4s.agent.runtime import ChatModel
from openai4s.config import LLMConfig
from openai4s.llm.models import llm_failure_code
from openai4s.llm.usage import measured_total

pytestmark = pytest.mark.live_llm


def test_live_ark_stream_stop_and_next_call(monkeypatch):
    descriptor = os.environ.get("OPENAI4S_LIVE_ARK_KEY_FD")
    destination = os.environ.get("OPENAI4S_LIVE_RECEIPT_DIR")
    if descriptor is None or destination is None:
        pytest.skip(
            "explicit credential descriptor and private receipt directory required"
        )
    credential = os.read(int(descriptor), 4096).decode().strip()
    if not credential:
        pytest.fail("live credential descriptor was empty")
    monkeypatch.setenv("OPENAI4S_ARK_API_KEY", credential)
    del credential
    endpoint = "https://ark.cn-beijing.volces.com/api/plan/v3"
    cfg = LLMConfig(
        provider="ark",
        base_url=endpoint,
        model="doubao-seed-2.0-pro",
        max_tokens=1024,
        timeout_s=30,
        total_timeout_s=90,
    )
    records = []
    local = threading.local()
    stop, accounted = threading.Event(), threading.Event()
    abandoned = []
    original_open = llm.transport._urlopen

    def open_request(request, **kwargs):
        if request.full_url != endpoint + "/chat/completions":
            raise RuntimeError("live destination refused")
        response = original_open(request, **kwargs)
        local.record["requests"].append(
            {
                "endpoint": request.full_url,
                "request_id": response.headers.get("x-request-id"),
                "status": response.status,
            }
        )
        return response

    monkeypatch.setattr(llm.transport, "_urlopen", open_request)

    def invoke(messages, config, **kwargs):
        if len(records) >= 2:
            raise RuntimeError("live call cap reached")
        record = {"requested_model": config.model, "requests": [], "usage": "unknown"}
        records.append(record)
        local.record = record
        started = time.monotonic()

        def sse(url, payload, headers, timeout, on_event, **context):
            def observe(event):
                record["events"] = record.get("events", 0) + 1
                for choice in event.get("choices") or []:
                    delta = choice.get("delta") or {}
                    for kind in ("content", "reasoning_content", "reasoning"):
                        if delta.get(kind):
                            counter = kind + "_bytes"
                            record[counter] = record.get(counter, 0) + len(
                                delta[kind].encode("utf-8")
                            )
                    if choice.get("finish_reason"):
                        record["wire_finish"] = choice["finish_reason"]
                if event.get("usage") is not None:
                    record["raw_usage"] = event["usage"]
                if event.get("model"):
                    record["returned_model"] = event["model"]
                return on_event(event)

            return llm.transport.post_sse(
                url, payload, headers, timeout, observe, **context
            )

        try:
            result = llm.client.chat(
                messages,
                config,
                post_json=llm.transport.post_json,
                post_sse=sse,
                max_tokens=1024,
                **kwargs,
            )
            record["terminal"] = result.get("finish_reason")
            record["usage"] = dict(result["usage"].evidence.raw)
            record["measured_total"] = measured_total(result["usage"])
            return result
        except Exception as error:
            record["failure"] = llm_failure_code(error) or type(error).__name__
            raise RuntimeError(
                "live provider call failed; inspect sanitized receipt"
            ) from None
        finally:
            record["elapsed_s"] = round(time.monotonic() - started, 3)

    class Cancel:
        def cancelled(self):
            return stop.is_set()

    def account(reply):
        abandoned.append(measured_total(reply.get("usage")))
        accounted.set()

    model = ChatModel(
        cfg,
        invoke,
        stream=True,
        cancellation=Cancel(),
        drain_cancelled_stream=True,
        abandoned_reply=account,
        call_scope="live-ark-synthetic",
    )
    first_chunks, second_chunks = [], []

    def first_delta(text):
        first_chunks.append(text)
        stop.set()

    try:
        first = model.complete(
            [
                {
                    "role": "user",
                    "content": "Output exactly: synthetic sample A, value 7.",
                }
            ],
            first_delta,
        )
        assert first_chunks and first["finish_reason"] == "cancelled"
        first_count = len(first_chunks)
        stop.clear()
        second = model.complete(
            [{"role": "user", "content": "Reply exactly READY."}], second_chunks.append
        )
        assert second["finish_reason"] == "stop" and "READY" in second["content"]
        assert accounted.wait(90), "old stream did not settle within its total deadline"
        assert len(abandoned) == 1 and len(first_chunks) == first_count
        assert len(records) == 2 and all(record["requests"] for record in records)
    finally:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "ark-stream-stop-recovery.json").write_text(
            json.dumps({"calls": records, "abandoned_totals": abandoned}, indent=2)
        )
