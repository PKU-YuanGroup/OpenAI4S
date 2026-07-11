"""Deterministic snapshots of selected r5 *pre-change* production behavior.

This module is deliberately separate from :mod:`harness.runner`: the generic
runner validates the harness contract without importing OpenAI4S, whereas these
characterizations dynamically import and drive production entry points.  Every
external boundary is replaced with a stdlib ``unittest.mock`` fake, and every
SQLite store lives below the caller-provided temporary data directory.

Several snapshots describe known bugs.  They are not assertions that the bugs
must live forever: when a runtime fix intentionally changes one, regenerate the
golden explicitly and review the accompanying ``desired_contract`` text.
"""

from __future__ import annotations

import copy
import importlib
import io
import json
import os
import urllib.error
from pathlib import Path
from typing import Any, Callable, Mapping
from unittest import mock

from .faults import FakeClock, FakeUUIDFactory
from .normalize import normalize_trace
from .schema import SCHEMA_VERSION, EventEnvelope

CHARACTERIZATION_SCHEMA_VERSION = 1


class _Recorder:
    """Small deterministic EventEnvelope recorder for one production probe."""

    def __init__(self) -> None:
        self.clock = FakeClock()
        self.ids = FakeUUIDFactory()
        self.run_id = self.ids()
        self.root_frame_id = self.ids()
        self.turn_id = self.ids()
        self.events: list[EventEnvelope] = []

    def emit(self, kind: str, status: str, payload: Mapping[str, Any]) -> None:
        parent = self.events[-1].event_id if self.events else None
        self.events.append(
            EventEnvelope(
                schema_version=SCHEMA_VERSION,
                event_id=self.ids(),
                seq=len(self.events) + 1,
                run_id=self.run_id,
                root_frame_id=self.root_frame_id,
                turn_id=self.turn_id,
                parent_event_id=parent,
                kind=kind,
                phase="characterization",
                status=status,
                monotonic_ms=self.clock.monotonic_ms(),
                payload=dict(payload),
            )
        )
        self.clock.advance_ms(1)


class _FakeKernel:
    """Deterministic Kernel replacement used only by the CLI max-turn probe."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.execute_calls = 0

    def __enter__(self) -> "_FakeKernel":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, code: str, origin: str = "agent", **kwargs: Any) -> dict:
        self.execute_calls += 1
        return {
            "type": "response",
            "id": f"fake-cell-{self.execute_calls}",
            "stdout": "",
            "stderr": "",
            "error": None,
            "interrupted": False,
            "trace": {"error_lineno": None, "error_call": None},
            # A real worker success frame always carries non-empty usage
            # (worker.py), so keep the observation shape realistic.
            "usage": {"wall_s": 0.001, "cpu_s": 0.001, "peak_rss_kb": 1024},
        }


class _JsonResponse:
    """Minimal urllib response for the second, currently-unreached retry step."""

    def __init__(self, body: Mapping[str, Any]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> "_JsonResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _FakeMCPManager:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def list_tools(self, connector_id: str, config: dict) -> list[dict]:
        self.calls.append(
            {"connector_id": connector_id, "command": copy.deepcopy(config["command"])}
        )
        return []


def _run_case(
    *,
    case_id: str,
    current_behavior: str,
    desired_contract: str,
    known_bug: bool,
    probe: Callable[[], Mapping[str, Any]],
    replacements: Mapping[str, str],
) -> dict[str, Any]:
    recorder = _Recorder()
    recorder.emit("characterization_started", "running", {"case_id": case_id})
    try:
        observed = dict(probe())
    except Exception as exc:  # pragma: no cover - a probe bug must stay visible
        observed = {
            "probe_error_type": type(exc).__name__,
            "probe_error": str(exc),
        }
        recorder.emit("production_observed", "error", observed)
        recorder.emit("characterization_finished", "terminal", {"captured": False})
    else:
        recorder.emit("production_observed", "ok", observed)
        recorder.emit("characterization_finished", "terminal", {"captured": True})
    return {
        "id": case_id,
        "current_behavior": current_behavior,
        "desired_contract": desired_contract,
        "known_bug": known_bug,
        "trace": normalize_trace(recorder.events, replacements=replacements),
    }


def _cli_max_turns(data_dir: Path) -> Mapping[str, Any]:
    loop = importlib.import_module("openai4s.agent.loop")
    config = importlib.import_module("openai4s.config")
    cfg = config.Config(
        data_dir=data_dir,
        llm=config.LLMConfig(
            provider="ark",
            api_key="characterization-key",
            base_url="https://characterization.invalid/v1",
            model="characterization-model",
        ),
        security=config.SecurityConfig(
            safety_mode="off",
            audit_hook=False,
            biosecurity=False,
            injection_scan=False,
        ),
        max_turns=2,
    )
    fake_chat = mock.Mock(
        return_value={
            "content": "```python\nx = 1\n```",
            "reasoning": None,
            "usage": {},
            "finish_reason": "stop",
            "raw": {},
        }
    )
    with mock.patch.object(loop, "Kernel", _FakeKernel), mock.patch.object(
        loop, "chat", fake_chat
    ):
        result = loop.Agent(
            cfg=cfg, max_turns=2, use_skills=False, allow_delegate=False
        ).run("never submit")
    roles = [entry["role"] for entry in result["transcript"]]
    return {
        "stop_reason": result["stop_reason"],
        "model_calls": fake_chat.call_count,
        "transcript_roles": roles,
        "submitted_output": result["submitted_output"],
    }


def _rate_limit_single_attempt() -> Mapping[str, Any]:
    llm = importlib.import_module("openai4s.llm")
    config = importlib.import_module("openai4s.config")
    cfg = config.LLMConfig(
        provider="ark",
        api_key="characterization-key",
        base_url="https://characterization.invalid/v1",
        model="characterization-model",
    )
    first = urllib.error.HTTPError(
        url="https://characterization.invalid/v1/chat/completions",
        code=429,
        msg="Too Many Requests",
        hdrs={"Retry-After": "1"},
        fp=io.BytesIO(b'{"error":"rate limited"}'),
    )
    second = _JsonResponse(
        {
            "choices": [{"message": {"content": "recovered"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    )
    urlopen = mock.Mock(side_effect=[first, second])
    error_type = None
    error_text = None
    content = None
    with mock.patch("urllib.request.urlopen", urlopen), mock.patch(
        "time.sleep", return_value=None
    ):
        try:
            result = llm.chat([{"role": "user", "content": "hello"}], cfg)
            content = result.get("content")
        except Exception as exc:  # current production path
            error_type = type(exc).__name__
            error_text = str(exc)
    return {
        "attempts": urlopen.call_count,
        "content": content,
        "error_type": error_type,
        "error_text": error_text,
        "retry_after_was_available": True,
    }


def _partial_sse_hard_failure() -> Mapping[str, Any]:
    llm = importlib.import_module("openai4s.llm")
    config = importlib.import_module("openai4s.config")
    cfg = config.LLMConfig(
        provider="ark",
        api_key="characterization-key",
        base_url="https://characterization.invalid/v1",
        model="characterization-model",
    )
    deltas: list[str] = []

    def fail_after_delta(url, payload, headers, timeout, on_event) -> None:
        on_event({"choices": [{"delta": {"content": "committed-delta"}}]})
        raise llm.LLMError("stream disconnected after committed delta")

    post_sse = mock.Mock(side_effect=fail_after_delta)
    post_json = mock.Mock()
    error_type = None
    error_text = None
    with mock.patch.object(llm, "_post_sse", post_sse), mock.patch.object(
        llm, "_post_json", post_json
    ), mock.patch.dict(os.environ, {"OPENAI4S_LLM_STREAM": "1"}):
        try:
            llm.chat(
                [{"role": "user", "content": "hello"}], cfg, on_delta=deltas.append
            )
        except Exception as exc:  # expected current and desired behavior
            error_type = type(exc).__name__
            error_text = str(exc)
    return {
        "sse_attempts": post_sse.call_count,
        "blocking_fallback_attempts": post_json.call_count,
        "deltas": deltas,
        "error_type": error_type,
        "error_text": error_text,
    }


def _compaction_provider_hoist(data_dir: Path) -> Mapping[str, Any]:
    compaction = importlib.import_module("openai4s.agent.compaction")
    config = importlib.import_module("openai4s.config")
    llm = importlib.import_module("openai4s.llm")
    cfg = config.Config(
        data_dir=data_dir,
        llm=config.LLMConfig(
            provider="ark",
            api_key="characterization-key",
            base_url="https://characterization.invalid/v1",
            model="characterization-model",
        ),
    )
    messages = [
        {"role": "system", "content": "POLICY"},
        {"role": "user", "content": "ORIGINAL_TASK"},
        {"role": "assistant", "content": "old work"},
        {"role": "user", "content": "old observation"},
        {"role": "assistant", "content": "RECENT"},
    ]
    with mock.patch.object(compaction, "chat", return_value={"content": "SUMMARY"}):
        compacted = compaction.compact(messages, cfg, keep_recent=1)

    payloads: dict[str, dict[str, Any]] = {}

    def capture_post(url, payload, headers, timeout):
        if "/v1/messages" in url:
            payloads["anthropic"] = copy.deepcopy(payload)
            return {
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {},
            }
        if ":generateContent" in url:
            payloads["gemini"] = copy.deepcopy(payload)
            return {
                "candidates": [
                    {"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}
                ],
                "usageMetadata": {},
            }
        raise AssertionError(f"unexpected URL {url}")

    with mock.patch.object(llm, "_post_json", side_effect=capture_post):
        llm.chat(
            compacted,
            config.LLMConfig(
                provider="claude",
                api_key="characterization-key",
                base_url="https://anthropic.characterization.invalid",
                model="characterization-claude",
            ),
        )
        llm.chat(
            compacted,
            config.LLMConfig(
                provider="gemini",
                api_key="characterization-key",
                base_url="https://gemini.characterization.invalid",
                model="characterization-gemini",
            ),
        )

    anthropic = payloads["anthropic"]
    gemini = payloads["gemini"]
    anth_system = anthropic.get("system", "")
    gem_system = json.dumps(gemini.get("systemInstruction", {}), ensure_ascii=False)
    anth_messages = json.dumps(anthropic.get("messages", []), ensure_ascii=False)
    gem_contents = json.dumps(gemini.get("contents", []), ensure_ascii=False)
    return {
        "compacted_roles": [message["role"] for message in compacted],
        "anthropic_summary_in_top_level_system": "SUMMARY" in anth_system,
        "anthropic_summary_in_messages": "SUMMARY" in anth_messages,
        "anthropic_message_roles": [
            message["role"] for message in anthropic.get("messages", [])
        ],
        "gemini_summary_in_system_instruction": "SUMMARY" in gem_system,
        "gemini_summary_in_contents": "SUMMARY" in gem_contents,
        "gemini_content_roles": [
            message["role"] for message in gemini.get("contents", [])
        ],
    }


def _oversized_observation() -> Mapping[str, Any]:
    loop = importlib.import_module("openai4s.agent.loop")
    stdout = "x" * 2_000_000
    rendered = loop._format_observation(  # noqa: SLF001 - production characterization
        {"stdout": stdout, "stderr": "", "error": None, "usage": {}}
    )
    return {
        "input_chars": len(stdout),
        "model_view_chars": len(rendered),
        "full_tail_preserved": rendered.endswith(stdout),
        "has_content_ref": "content ref=" in rendered.lower(),
        "has_omission_marker": "omitted" in rendered.lower(),
    }


def _headless_permission(data_dir: Path) -> Mapping[str, Any]:
    permissions = importlib.import_module("openai4s.permissions")
    store_mod = importlib.import_module("openai4s.store")
    store = store_mod.Store(data_dir / "permissions.db")
    try:
        store.seed_default_permission_rules()
        ask_decision = store.resolve_permission(
            tool="bash", pattern_input="echo characterization"
        )
        broker = permissions.PermissionBroker()
        ask_result = broker.gate(
            store=store,
            frame_id=None,
            method="bash",
            target="echo characterization",
        )
        store.set_permission_rule(
            scope="global",
            scope_id="",
            tool="bash",
            pattern="rm *",
            decision="deny",
        )
        deny_decision = store.resolve_permission(
            tool="bash", pattern_input="rm characterization"
        )
        deny_result = broker.gate(
            store=store,
            frame_id=None,
            method="bash",
            target="rm characterization",
        )
        return {
            "ask_effective_decision": ask_decision,
            "headless_ask_allowed": bool(ask_result.get("allow")),
            "deny_effective_decision": deny_decision,
            "headless_deny_allowed": bool(deny_result.get("allow")),
            "deny_message": deny_result.get("message"),
        }
    finally:
        store.close()


def _disabled_mcp_tools_connects(data_dir: Path) -> Mapping[str, Any]:
    config = importlib.import_module("openai4s.config")
    host_dispatch = importlib.import_module("openai4s.host_dispatch")
    mcp_client = importlib.import_module("openai4s.mcp_client")
    cfg = config.Config(
        data_dir=data_dir,
        llm=config.LLMConfig(
            provider="ark",
            api_key="characterization-key",
            base_url="https://characterization.invalid/v1",
            model="characterization-model",
        ),
    )
    dispatcher = host_dispatch.HostDispatcher(cfg=cfg)
    dispatcher.store.upsert_connector(
        connector_id="disabled-characterization",
        name="disabled-characterization",
        description="offline fake",
        command=["fake-mcp"],
        args=[],
        env={},
        enabled=False,
    )
    fake_manager = _FakeMCPManager()
    with mock.patch.object(mcp_client, "manager", return_value=fake_manager):
        result = dispatcher("mcp_tools", ["disabled-characterization"])
    return {
        "connector_enabled": False,
        "manager_list_tools_calls": len(fake_manager.calls),
        "result": result,
    }


def collect_prechange_characterization(data_dir: str | Path) -> dict[str, Any]:
    """Run all offline production probes and return a normalized JSON document."""

    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    replacements = {str(root.resolve()): "<data-dir>"}
    definitions = (
        {
            "case_id": "cli_max_turns",
            "current_behavior": "CLI returns stop_reason=max_turns after the configured number of model turns.",
            "desired_contract": "TerminalReason=max_turns remains explicit and later matches the gateway canonical RunOutcome.",
            "known_bug": False,
            "probe": lambda: _cli_max_turns(root / "cli-max-turns"),
        },
        {
            "case_id": "rate_limit_single_attempt",
            "current_behavior": "An HTTP 429 raises LLMError after one transport attempt even when Retry-After is present.",
            "desired_contract": "Typed rate_limit errors honor Retry-After and retry within a cancellable source-aware budget before output is committed.",
            "known_bug": True,
            "probe": _rate_limit_single_attempt,
        },
        {
            "case_id": "partial_sse_hard_failure",
            "current_behavior": "After one delta is emitted, a stream disconnect is surfaced as a hard error with no blocking fallback or duplicate delta.",
            "desired_contract": "Preserve the no-transparent-replay commit boundary after any UI-visible delta.",
            "known_bug": False,
            "probe": _partial_sse_hard_failure,
        },
        {
            "case_id": "compaction_summary_provider_hoist",
            "current_behavior": "The compaction note is a mid-timeline system message that Anthropic and Gemini hoist into their initial system fields.",
            "desired_contract": "Compile a typed compaction_summary at its timeline position; never merge it into initial policy or cache prefix.",
            "known_bug": True,
            "probe": lambda: _compaction_provider_hoist(root / "compaction"),
        },
        {
            "case_id": "oversized_observation_unbudgeted",
            "current_behavior": "The model-view formatter preserves an entire 2,000,000-character observation with no content reference or omission marker.",
            "desired_contract": "Apply per-result and aggregate model-view budgets, spilling full bytes to an authorized content reference before previewing.",
            "known_bug": True,
            "probe": _oversized_observation,
        },
        {
            "case_id": "headless_ask_fails_closed_deny_absolute",
            "current_behavior": "A headless effective ask is denied unless an operator explicitly enables unattended approval; a matching standing deny remains absolute.",
            "desired_contract": "Deny unresolved ask in headless mode while preserving an explicit operator override and standing deny as absolute.",
            "known_bug": False,
            "probe": lambda: _headless_permission(root / "permissions"),
        },
        {
            "case_id": "disabled_mcp_tools_connects",
            "current_behavior": "mcp_tools calls the connector manager even when the connector row is disabled.",
            "desired_contract": "Disabled or untrusted MCP connectors are zero-spawn and never enter connect/list/call.",
            "known_bug": True,
            "probe": lambda: _disabled_mcp_tools_connects(root / "mcp"),
        },
    )
    cases = [
        _run_case(replacements=replacements, **definition) for definition in definitions
    ]
    return {
        "schema_version": CHARACTERIZATION_SCHEMA_VERSION,
        "kind": "r5_prechange_production_characterization",
        "update_policy": (
            "Known-bug traces are pre-change evidence, not permanent contracts. "
            "An intentional runtime fix must explicitly regenerate and review this golden."
        ),
        "cases": cases,
    }


def characterization_bytes(data_dir: str | Path) -> bytes:
    """Canonical bytes compared with ``golden_traces/v1/r5_prechange.json``."""

    document = collect_prechange_characterization(data_dir)
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
