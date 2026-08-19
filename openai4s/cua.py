"""CUA cloud desktop's fixed, credential-brokered MCP integration.

This module owns the one managed connector added for CUA (the Volcengine
Agent-plan cloud Windows desktop).  Its endpoint is product configuration
rather than user input, while its credential — a dedicated CUA API Key, not
the shared Agent Plan Key — is resolved from
:class:`~openai4s.security.secret_broker.SecretBroker` only when an outbound
request is about to be assembled.  Nothing returned from this module contains
the credential or the injected ``Authorization`` header.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any, Protocol

from openai4s.mcp_protocol import redact_reflected_secret

CONNECTOR_ID = "cua"
SKILL_NAME = "cua"
ENDPOINT = "https://sd8j64df316pc5mfa3qpg.apigateway-cn-beijing.volceapi.com/skill/mcp"
SERVER_NAME = "cua_skill_v2"

TOOL_NAMES = (
    "cua_ping",
    "cua_delegate",
    "cua_watch",
    "cua_answer",
    "cua_cancel",
    "cua_observe",
)

CUA_API_KEY_SETTING = "cua_api_key"
_CUA_SCOPE = "cua"

AUTH_FAILURE_MESSAGE = "CUA API Key 无效或未授权。"
AVAILABLE_MESSAGE = "CUA 可用"
MIN_CUA_API_KEY_CHARS = 8

# cua_delegate / cua_watch are long polls whose server-side wait can far
# outlive the transport's 60-second default; a shorter budget would abort a
# wait the tool contract explicitly allows.
REQUEST_TIMEOUT_SECONDS = 300.0

# The fixed six-tool surface, transcribed from the live server's `tools/list`
# reply (name / description / inputSchema only; presentation-only fields were
# dropped).  Discovery is answered from here rather than over the wire: the
# surface is fixed anyway, and dialling for it put an authenticated request
# carrying the user's credential behind an ungated capability.
_TOOL_DESCRIPTORS: tuple[dict[str, Any], ...] = (
    {
        "name": "cua_ping",
        "description": (
            "只读连通性检查工具。 用于确认当前 Agent 会话已经能调用 CUA Skill "
            "MCP，并且 Bearer 鉴权与桌面绑定可用。 不创建任务，不观察桌面，"
            "不签发桌面访问链接，也不调用 CUA 后端。 首次使用 CUA 前如果不确定 "
            "MCP 是否已接入，请优先调用此工具；不要随机调用其他 CUA 工具做连通性测试。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#",
        },
    },
    {
        "name": "cua_delegate",
        "description": (
            "委托一个用户目标给 CUA，创建 Skill invocation。 直接传递用户原始意图"
            "作为 objective，不要替 CUA 规划、拆解或追加条件。 CUA 会自主理解需求、"
            "规划步骤并执行。 返回后检查 outcome：completed 表示已完成；needs_input "
            "表示需要用户回答；in_progress 表示仍在执行，必须调用 cua_watch 继续等待，"
            "不能根据进度信息回答。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": (
                        "用户希望 CUA 完成的原始目标。直接传递用户意图，"
                        "不要替 CUA 规划、拆解或追加条件。"
                    ),
                },
                "wait_ms": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "description": (
                        "提交后最多等待多久返回（毫秒）。null 使用 "
                        "CUA_SKILL_DELEGATE_DEFAULT_WAIT_MS。"
                        "本字段只影响本次调用等待时长，不取消任务。"
                    ),
                },
            },
            "required": ["objective", "wait_ms"],
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#",
        },
    },
    {
        "name": "cua_watch",
        "description": (
            "查询或等待已有 invocation 的下一个语义状态。 当 cua_delegate 返回 "
            "in_progress 时调用此工具继续等待。 也可用于客户端断线后恢复或稍后查看"
            "结果。 只有 outcome=completed 且 result.text 非空时，才可以把 CUA 结果"
            "作为最终答案。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "invocation_id": {
                    "type": "string",
                    "description": "由 cua_delegate 返回的 invocation_id。",
                },
                "wait_ms": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "description": (
                        "最多等待多久返回（毫秒）。null 使用 "
                        "CUA_SKILL_WATCH_DEFAULT_WAIT_MS。"
                        "本字段只影响本次调用等待时长，不取消任务。"
                    ),
                },
            },
            "required": ["invocation_id", "wait_ms"],
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#",
        },
    },
    {
        "name": "cua_answer",
        "description": (
            "当 CUA 返回 needs_input 时，提交用户回答并继续执行。 将 "
            "input_request.question 展示给用户，收到回答后调用此工具。 提交后 CUA "
            "会继续执行，返回 envelope 表示新状态。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "invocation_id": {
                    "type": "string",
                    "description": "正在等待用户输入的 invocation_id。",
                },
                "answer": {
                    "type": "string",
                    "description": "用户针对 input_request.question 的回答。",
                },
                "wait_ms": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "description": (
                        "提交回答后最多等待多久返回（毫秒）。null 使用 "
                        "CUA_SKILL_ANSWER_DEFAULT_WAIT_MS。"
                    ),
                },
            },
            "required": ["invocation_id", "answer", "wait_ms"],
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#",
        },
    },
    {
        "name": "cua_cancel",
        "description": (
            "请求取消一个正在执行的 invocation。 仅在用户明确要求停止时调用。"
            "不要因为等待时间长就取消。 取消不保证已发生的桌面操作会被回滚。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "invocation_id": {
                    "type": "string",
                    "description": "需要取消的 invocation_id。",
                },
            },
            "required": ["invocation_id"],
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#",
        },
    },
    {
        "name": "cua_observe",
        "description": (
            "只读查看当前用户的云端桌面环境状态、访问入口和可选截图。 不启动新任务，"
            "不操作桌面。 当用户想查看桌面当前画面或手动操作时使用。 返回的桌面访问"
            "链接短期有效；如果用户打开失败或提示过期，请重新调用本工具获取新的访问"
            "链接。 不要用 cua_observe 来判断任务是否完成，使用 cua_watch。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "invocation_id": {
                    "type": ["string", "null"],
                    "description": (
                        "可选。指定 invocation 时观察该 invocation 所属的执行环境；"
                        "null 表示当前用户默认环境。"
                    ),
                },
                "include_screenshot": {
                    "type": "boolean",
                    "description": "是否包含截图。",
                },
            },
            "required": ["invocation_id", "include_screenshot"],
            "additionalProperties": False,
            "$schema": "http://json-schema.org/draft-07/schema#",
        },
    },
)


def tool_descriptors() -> list[dict[str, Any]]:
    """The managed connector's fixed six-tool surface, answered locally.

    Deep-copied per call so a caller mutating a descriptor cannot corrupt the
    module-level source of truth shared by every later discovery.
    """

    return [copy.deepcopy(descriptor) for descriptor in _TOOL_DESCRIPTORS]


class CUAStore(Protocol):
    """The credential and connector subset used by this integration."""

    def get_secret_setting(self, key: str) -> str: ...

    def set_secret_setting(self, key: str, value: str, *, scope: str) -> str: ...

    def connector_env(self, connector: dict) -> dict: ...


class CUACredentialError(RuntimeError):
    """The managed connector has no credential it is allowed to send."""


def _validated_cua_api_key(value: Any) -> str:
    """Normalize the CUA credential before storage or outbound use.

    Exact-secret redaction cannot both remove a one-character credential and
    preserve ordinary text containing that character.  A service credential
    shorter than eight characters is not credible, so reject it before it can
    be persisted or sent rather than weakening the no-reflection boundary.
    """

    if not isinstance(value, str):
        raise ValueError("CUA API Key must be a string")
    key = value.strip()
    if not key:
        raise ValueError("CUA API Key is required")
    if len(key) < MIN_CUA_API_KEY_CHARS:
        raise ValueError(
            f"CUA API Key must be at least {MIN_CUA_API_KEY_CHARS} characters"
        )
    if len(key) > 8_192:
        raise ValueError("CUA API Key is too long")
    if "\r" in key or "\n" in key or "\x00" in key:
        raise ValueError("CUA API Key contains an invalid character")
    return key


def _has_valid_cua_api_key(value: Any) -> bool:
    try:
        _validated_cua_api_key(value)
    except ValueError:
        return False
    return True


def managed_connector_command() -> dict[str, str]:
    """Public, non-secret transport metadata persisted with the connector."""

    return {"transport": "streamable_http", "url": ENDPOINT}


def resolve_cua_api_key(store: CUAStore) -> str:
    """Return the dedicated key through SecretBroker, never its settings ref.

    Deliberately *not* the shared Agent Plan Key resolver: the CUA service
    only accepts keys it issued itself and refuses an Ark plan key in-band,
    so mirroring or reusing the LLM credential would send a key to a party
    that never issued it and report the failure as the user's.
    """

    return str(store.get_secret_setting(CUA_API_KEY_SETTING) or "").strip()


def credential_state(store: CUAStore) -> dict[str, bool]:
    return {"key_configured": _has_valid_cua_api_key(resolve_cua_api_key(store))}


def save_cua_api_key(store: CUAStore, value: Any) -> None:
    """Broker the dedicated CUA API Key.

    Unlike DataPro's shared Agent Plan Key there is no mirror into the LLM
    profile: the credential authorizes only this product and no other setting
    resolves from it.
    """

    key = _validated_cua_api_key(value)
    store.set_secret_setting(CUA_API_KEY_SETTING, key, scope=_CUA_SCOPE)


def _outbound_headers(store: CUAStore) -> dict[str, str]:
    """Resolve the Bearer header immediately before one HTTP request."""

    key = resolve_cua_api_key(store)
    if not key:
        raise CUACredentialError("CUA API Key is not configured")
    try:
        key = _validated_cua_api_key(key)
    except ValueError:
        raise CUACredentialError("CUA API Key is invalid") from None
    return {"Authorization": f"Bearer {key}"}


def runtime_cache_scope(store: CUAStore) -> str:
    """Return a non-secret identity for this live Store generation.

    A Store reopened on the same database path is a new generation and must
    not inherit an HTTP session whose header provider closes over the old
    Store.  Object identity supplies that process-local boundary without
    deriving a cache key from either the credential or its fingerprint.  A
    cached connection retains the provider closure (and therefore ``store``),
    so the identity cannot be reused while that connection is live.
    """

    return f"store:{id(store)}"


def connector_runtime_config(store: CUAStore, connector: dict) -> dict:
    """Build the launch/request config shared by Web and ``host.mcp``.

    Only the managed connector may select Streamable HTTP here.  Custom rows
    retain the existing stdio behavior, so a user-controlled connector cannot
    turn this fixed authenticated integration into an arbitrary-header SSRF.
    """

    if str(connector.get("connector_id") or "") == CONNECTOR_ID:
        return {
            "transport": "streamable_http",
            "url": ENDPOINT,
            # Runtime-only and non-secret. MCPManager uses it to keep two
            # concurrently hosted Store generations from sharing one session.
            "cache_scope": runtime_cache_scope(store),
            # A callable, not a resolved dict: the HTTP transport invokes it
            # for every POST, after cache lookup and immediately before send.
            "headers_provider": lambda: _outbound_headers(store),
            "timeout": REQUEST_TIMEOUT_SECONDS,
        }
    config: dict[str, Any] = {
        "command": connector["command"],
        "args": connector.get("args"),
        "env": store.connector_env(connector),
    }
    if connector.get("cwd"):
        config["cwd"] = connector["cwd"]
    return config


def structured_content(result: Mapping[str, Any] | Any) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        return {}
    raw = result.get("raw")
    if not isinstance(raw, Mapping):
        return {}
    value = raw.get("structuredContent")
    return dict(value) if isinstance(value, Mapping) else {}


def _first_text(result: Mapping[str, Any] | Any) -> str:
    """The result's normalized text, or its first raw text block."""

    if not isinstance(result, Mapping):
        return ""
    text = result.get("text")
    if isinstance(text, str) and text.strip():
        return text
    raw = result.get("raw")
    if isinstance(raw, Mapping):
        content = raw.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, Mapping) and block.get("type") == "text":
                candidate = block.get("text")
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
    return ""


def _text_payload(result: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Parse the result text as one JSON object; anything else is empty."""

    try:
        parsed = json.loads(_first_text(result))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _envelope(result: Mapping[str, Any] | Any) -> dict[str, Any]:
    """The tool's JSON payload: ``structuredContent``, else the text block."""

    content = structured_content(result)
    return content if content else _text_payload(result)


def is_auth_error(result: Mapping[str, Any] | Any) -> bool:
    """True when the upstream refused this call's Bearer credential in-band.

    The CUA service reports an invalid key as a *successful* JSON-RPC reply
    whose tool result carries ``isError`` and one text block containing a JSON
    document like ``{"error":"AuthError","status":401,...}``.  Judged on the
    manager's normalized ``{"is_error","text","raw"}`` shape.
    """

    if not isinstance(result, Mapping) or not result.get("is_error"):
        return False
    payload = _text_payload(result)
    if not payload:
        return False
    status = payload.get("status")
    return str(payload.get("error") or "") == "AuthError" or (
        type(status) is int and status == 401
    )


def redact_secret(value: Any, secret: str) -> Any:
    """Recursively remove an echoed credential before persistence/projection."""

    return redact_reflected_secret(value, secret)


def redact_mcp_result(result: Mapping[str, Any], secret: str) -> dict[str, Any]:
    """Redact untrusted MCP data while rebuilding its trusted result skeleton.

    Arbitrary upstream mapping keys are data and may reflect the credential,
    so :func:`redact_secret` scrubs them.  The few protocol keys consumed by
    OpenAI4S are then restored from their original locations so a pathological
    key cannot erase the ``is_error`` / ``content`` / ``structuredContent``
    slots the projections and auth decision read.
    """

    safe = redact_secret(result, secret)
    if not isinstance(safe, dict):
        safe = {}
    for key in ("is_error", "text"):
        if key in result:
            safe[key] = redact_secret(result[key], secret)
    raw = result.get("raw")
    if isinstance(raw, Mapping):
        safe_raw = redact_secret(raw, secret)
        if not isinstance(safe_raw, dict):
            safe_raw = {}
        if "content" in raw:
            safe_raw["content"] = redact_secret(raw["content"], secret)
        structured = raw.get("structuredContent")
        if isinstance(structured, Mapping):
            safe_structured = redact_secret(structured, secret)
            if not isinstance(safe_structured, dict):
                safe_structured = {}
            safe_raw["structuredContent"] = safe_structured
        safe["raw"] = safe_raw
    return safe


def ping_projection(result: Mapping[str, Any] | Any) -> dict[str, bool]:
    """Project a ``cua_ping`` result to the one fact the UI consumes.

    Only this fixed shape leaves the module — no upstream field is passed
    through, so a compromised reply cannot smuggle content into the browser.
    """

    return {"ok": _envelope(result).get("ok") is True}


def observe_projection(result: Mapping[str, Any] | Any) -> dict[str, Any] | None:
    """Project a ``cua_observe`` result to its short-lived desktop URL.

    Returns ``None`` when the reply carries no usable ``access_url``; like
    :func:`ping_projection`, nothing beyond this fixed shape is forwarded.
    """

    access_url = _envelope(result).get("access_url")
    if not isinstance(access_url, str) or not access_url.strip():
        return None
    return {"access_url": access_url, "temporary": True}


__all__ = [
    "AUTH_FAILURE_MESSAGE",
    "AVAILABLE_MESSAGE",
    "CONNECTOR_ID",
    "CUA_API_KEY_SETTING",
    "CUACredentialError",
    "ENDPOINT",
    "REQUEST_TIMEOUT_SECONDS",
    "SERVER_NAME",
    "SKILL_NAME",
    "TOOL_NAMES",
    "connector_runtime_config",
    "credential_state",
    "is_auth_error",
    "managed_connector_command",
    "observe_projection",
    "ping_projection",
    "redact_mcp_result",
    "redact_secret",
    "resolve_cua_api_key",
    "runtime_cache_scope",
    "save_cua_api_key",
    "structured_content",
    "tool_descriptors",
]
