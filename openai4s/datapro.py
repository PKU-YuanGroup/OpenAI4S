"""Volcengine DataPro's fixed, credential-brokered MCP integration.

This module owns the one managed connector added for DataPro.  Its endpoint is
product configuration rather than user input, while its credential is resolved
from :class:`~openai4s.security.secret_broker.SecretBroker` only when an
outbound request is about to be assembled.  Nothing returned from this module
contains the credential or the injected headers.
"""

from __future__ import annotations

import json
import urllib.parse
import uuid
from collections.abc import Mapping
from typing import Any, Protocol

from openai4s.mcp_protocol import redact_reflected_secret
from openai4s.storage.datapro_index import DataProIndexCapacity

CONNECTOR_ID = "volcengine-datapro"
SKILL_NAME = "volcengine-datapro"
TOOL_NAME = "dataPro_search"
ENDPOINT = "https://datapro.hqd.cn-beijing.volces.com/mcp"

AGENT_PLAN_KEY_SETTING = "agent_plan_key"
_AGENT_PLAN_SCOPE = "agent_plan"
_ARK_PROVIDER = "ark"
_EXTRA_INFO = "openai4s"
# Hosts whose credentials this integration is allowed to forward to Volcengine.
_VOLCENGINE_DOMAINS = ("volces.com", "volcengine.com", "volcengineapi.com")

AUTH_FAILURE_CODE = 4011
AUTH_FAILURE_MESSAGE = "Key 无效、额度不足，或者专业数据集 Harness 未开启。"
AVAILABLE_MESSAGE = "专业数据集可用"
MAX_QUERY_CHARS = 10_000
MIN_AGENT_PLAN_KEY_CHARS = 8


def tool_descriptor() -> dict[str, Any]:
    """The managed connector's fixed single-tool surface.

    Discovery is answered from here rather than over the wire: the upstream
    reply was filtered down to this one entry anyway, and dialling for it put an
    authenticated request carrying the user's credential behind an ungated
    capability.
    """

    return {
        "name": TOOL_NAME,
        "description": (
            "Search Volcengine professional scientific datasets. "
            "Takes exactly one string argument, `query`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }


class DataProStore(Protocol):
    """The credential and connector subset used by this integration."""

    def get_setting(self, key: str, default: str | None = None) -> str | None: ...

    def get_secret_setting(self, key: str) -> str: ...

    def set_secret_setting(self, key: str, value: str, *, scope: str) -> str: ...

    def connector_env(self, connector: dict) -> dict: ...

    def index_datapro_result(
        self,
        *,
        query: str,
        structured_content: Mapping[str, Any],
        frame_id: str | None = None,
        artifact_id: str | None = None,
        occurrence_id: str | None = None,
        source_content: Any | None = None,
    ) -> dict[str, Any]: ...

    def link_datapro_index_artifact(self, batch_id: str, artifact_id: str) -> None: ...


class DataProCredentialError(RuntimeError):
    """The managed connector has no credential it is allowed to send."""


class DataProIndexError(RuntimeError):
    """A successful DataPro response could not be indexed completely."""


def _validated_agent_plan_key(value: Any) -> str:
    """Normalize the shared credential before storage or outbound use.

    Exact-secret redaction cannot both remove a one-character credential and
    preserve ordinary text containing that character. A provider credential
    shorter than eight characters is not credible, so reject it before it can
    be persisted or sent rather than weakening the no-reflection boundary.
    """

    if not isinstance(value, str):
        raise ValueError("Agent Plan Key must be a string")
    key = value.strip()
    if not key:
        raise ValueError("Agent Plan Key is required")
    if len(key) < MIN_AGENT_PLAN_KEY_CHARS:
        raise ValueError(
            f"Agent Plan Key must be at least {MIN_AGENT_PLAN_KEY_CHARS} characters"
        )
    if len(key) > 8_192:
        raise ValueError("Agent Plan Key is too long")
    if "\r" in key or "\n" in key or "\x00" in key:
        raise ValueError("Agent Plan Key contains an invalid character")
    return key


def _has_valid_agent_plan_key(value: Any) -> bool:
    try:
        _validated_agent_plan_key(value)
    except ValueError:
        return False
    return True


def managed_connector_command() -> dict[str, str]:
    """Public, non-secret transport metadata persisted with the connector."""

    return {"transport": "streamable_http", "url": ENDPOINT}


def _provider(store: DataProStore) -> str:
    return str(store.get_setting("llm_provider") or "").strip().lower()


def explicit_agent_plan_key(store: DataProStore) -> str:
    """Return the dedicated key through SecretBroker, never its settings ref."""

    return str(store.get_secret_setting(AGENT_PLAN_KEY_SETTING) or "").strip()


def is_volcengine_endpoint(base_url: str) -> bool:
    """True when an LLM endpoint really is Volcengine's.

    ``ark`` names a *wire protocol* — the UI labels that field 兼容协议 and
    accepts any Base URL — so the provider string alone does not identify whose
    account a key belongs to.
    """

    url = base_url.strip()
    if not url:
        # No override: the provider default endpoint, which is Volcengine's.
        return True
    host = (urllib.parse.urlsplit(url).hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    return any(
        host == domain or host.endswith("." + domain) for domain in _VOLCENGINE_DOMAINS
    )


def ark_key_for_datapro(store: DataProStore) -> str:
    """Reuse the active Ark key, but never a key for a different account."""

    if _provider(store) != _ARK_PROVIDER:
        return ""
    # The provider name is not proof of ownership.  An ``ark``-protocol profile
    # pointed at a corporate gateway or any other compatible endpoint holds a
    # credential that endpoint issued; forwarding it to Volcengine (and, through
    # the shared resolver, to the Doubao search host) discloses it to two
    # parties that never issued it.
    if not is_volcengine_endpoint(str(store.get_setting("llm_base_url") or "")):
        return ""
    return str(store.get_secret_setting("llm_api_key") or "").strip()


def resolve_agent_plan_key(store: DataProStore) -> str:
    """Resolve the canonical DataPro credential from SecretBroker.

    The brokered live Ark key wins while Ark is active, keeping model-profile
    key rotation and DataPro in sync.  A dedicated Agent Plan Key is used when
    another provider is active.  An OpenAI/Anthropic/etc. key must never be
    sent to Volcengine merely because it occupies ``llm_api_key``.
    """

    return ark_key_for_datapro(store) or explicit_agent_plan_key(store)


def credential_state(store: DataProStore) -> dict[str, bool]:
    reused = _has_valid_agent_plan_key(ark_key_for_datapro(store))
    # Report the credential the outbound resolver will actually select.  An
    # invalid active Ark value shadows the dedicated fallback by design, so an
    # independently valid dedicated key must not make the UI claim that this
    # effective authorization context is usable.
    configured = _has_valid_agent_plan_key(resolve_agent_plan_key(store))
    return {"key_configured": configured, "ark_key_reused": reused}


def save_agent_plan_key(store: DataProStore, value: Any) -> None:
    """Broker an Agent Plan Key and mirror it to an already-active Ark setup.

    The mirror makes the one password field sufficient in either direction:
    an active Ark API key is reusable by DataPro, and saving an Agent Plan Key
    refreshes the live Ark credential when Ark is the selected provider.  The
    two settings intentionally own separate broker entries so clearing either
    one cannot delete a secret still referenced by the other.

    The mirror is symmetric with :func:`ark_key_for_datapro`, and gated on the
    same endpoint check: an ``ark``-protocol profile pointed at a non-Volcengine
    endpoint would otherwise have its LLM credential *replaced* by a
    DataPro-scoped key, which is then sent as the bearer token to that endpoint.
    """

    key = _validated_agent_plan_key(value)
    store.set_secret_setting(AGENT_PLAN_KEY_SETTING, key, scope=_AGENT_PLAN_SCOPE)
    if _provider(store) == _ARK_PROVIDER and is_volcengine_endpoint(
        str(store.get_setting("llm_base_url") or "")
    ):
        store.set_secret_setting("llm_api_key", key, scope="llm")


def _outbound_headers(store: DataProStore) -> dict[str, str]:
    """Resolve both fixed headers immediately before one HTTP request."""

    key = resolve_agent_plan_key(store)
    if not key:
        raise DataProCredentialError("Agent Plan Key is not configured")
    try:
        key = _validated_agent_plan_key(key)
    except ValueError:
        raise DataProCredentialError("Agent Plan Key is invalid") from None
    return {
        "X-Agent-Plan-Key": key,
        "X-Hqd-Extra-Info": _EXTRA_INFO,
    }


def runtime_cache_scope(store: DataProStore) -> str:
    """Return a non-secret identity for this live Store generation.

    A Store reopened on the same database path is a new generation and must
    not inherit an HTTP session whose header provider closes over the old
    Store.  Object identity supplies that process-local boundary without
    deriving a cache key from either the credential or its fingerprint.  A
    cached connection retains the provider closure (and therefore ``store``),
    so the identity cannot be reused while that connection is live.
    """

    return f"store:{id(store)}"


def connector_runtime_config(store: DataProStore, connector: dict) -> dict:
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


def strict_code(content: Mapping[str, Any] | Any) -> int | None:
    """Return only a real integer code (``bool`` is deliberately rejected)."""

    if not isinstance(content, Mapping):
        return None
    code = content.get("code")
    return code if type(code) is int else None


def status_message(code: int | None) -> str:
    if code == 0:
        return AVAILABLE_MESSAGE
    if code == AUTH_FAILURE_CODE:
        return AUTH_FAILURE_MESSAGE
    if code is None:
        return "专业数据集返回未包含有效的 structuredContent.code。"
    return f"专业数据集不可用（code {code}）。"


def redact_secret(value: Any, secret: str) -> Any:
    """Recursively remove an echoed credential before persistence/projection."""

    return redact_reflected_secret(value, secret)


def redact_mcp_result(result: Mapping[str, Any], secret: str) -> dict[str, Any]:
    """Redact untrusted MCP data while rebuilding its trusted result skeleton.

    Arbitrary upstream mapping keys are data and may reflect the credential,
    so :func:`redact_secret` scrubs them.  The few protocol keys consumed by
    OpenAI4S are then restored from their original locations, preventing a
    short invalid key from erasing the ``structuredContent.code`` used for the
    required 4011 decision.
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
            if "code" in structured:
                safe_structured["code"] = redact_secret(structured["code"], secret)
            safe_raw["structuredContent"] = safe_structured
        safe["raw"] = safe_raw
    return safe


def public_search_result(result: Mapping[str, Any], secret: str) -> dict[str, Any]:
    """Project a tool result without transport internals or credentials."""

    original_content = structured_content(result)
    code = strict_code(original_content)
    safe = redact_mcp_result(result, secret)
    content = structured_content(safe)
    # JavaScript has one numeric type: JSON ``0.0`` would otherwise satisfy
    # ``=== 0`` after parsing even though the server did not return an integer.
    # Normalize a non-integer code before it reaches the browser while keeping
    # the rest of the structured result intact.
    if "code" in original_content and type(original_content.get("code")) is not int:
        content = {**content, "code": None}
    elif "code" in original_content:
        content = {**content, "code": code}
    return {
        "structuredContent": content,
        "content": (
            safe.get("raw", {}).get("content", [])
            if isinstance(safe.get("raw"), Mapping)
            else []
        ),
        "is_error": bool(safe.get("is_error")),
        "code": code,
        "available": code == 0,
        "message": status_message(code),
    }


def is_successful_search(result: Mapping[str, Any] | Any) -> bool:
    """True only for a strict integer code-0 response, in either projection.

    The single definition of "this search actually returned data", shared by
    indexing and by the routes that persist a result Artifact.  They used to
    disagree: the index was gated on code 0 while the upload was unconditional,
    so a 4011 still wrote a saved-result file the UI advertised as "已保存".
    """

    return strict_code(_public_structured_content(result)) == 0


def _public_structured_content(result: Mapping[str, Any] | Any) -> dict[str, Any]:
    """Read structured content from either MCP or dedicated-Web projection."""

    if not isinstance(result, Mapping):
        return {}
    direct = result.get("structuredContent")
    if isinstance(direct, Mapping):
        return dict(direct)
    return structured_content(result)


def index_successful_search(
    store: DataProStore,
    *,
    query: str,
    result: Mapping[str, Any],
    frame_id: str | None = None,
    artifact_id: str | None = None,
    secrets: tuple[str, ...] = (),
    source_result: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Atomically index every field returned by one successful search.

    The transport and public projector already scrub the credential.  This
    boundary repeats exact-secret redaction because it is the final point
    before durable indexing, and callers may be tests or embedders that replace
    the transport.  A non-zero or non-integer code is deliberately a no-op.

    A strict code-0 result is not allowed to escape as "available" unless the
    repository proves that its canonical source and indexed document contain
    the same JSON leaves.  This is response completeness, not a claim that the
    upstream corpus (whose tool exposes no cursor/page contract) was mirrored.
    """

    safe_result: Any = dict(result)
    safe_source: Any = dict(source_result if source_result is not None else result)
    safe_query: Any = query
    for secret in sorted({value for value in secrets if value}, key=len, reverse=True):
        safe_result = redact_secret(safe_result, secret)
        safe_source = redact_secret(safe_source, secret)
        safe_query = redact_secret(safe_query, secret)
    content = _public_structured_content(safe_result)
    if not is_successful_search(safe_result):
        return None
    if not isinstance(safe_query, str):
        raise DataProIndexError("DataPro query could not be safely indexed")
    try:
        receipt = store.index_datapro_result(
            query=safe_query,
            structured_content=content,
            frame_id=frame_id,
            artifact_id=artifact_id,
            # Distinct real calls are distinct evidence occurrences even when the
            # query and response bytes happen to match. Repository callers that do
            # not supply an occurrence retain content-idempotent replay semantics.
            occurrence_id=uuid.uuid4().hex,
            source_content=safe_source,
        )
    except DataProIndexCapacity:
        # The search succeeded; only the derived index is too large to build.
        # Propagating would report a working connector as failed and throw the
        # retrieved records away, so degrade exactly like a non-zero code does:
        # no receipt, and the caller still delivers the data.
        return None
    if not isinstance(receipt, Mapping):
        raise DataProIndexError("DataPro result indexing did not return a receipt")
    source_count = receipt.get("source_leaf_count")
    indexed_count = receipt.get("indexed_leaf_count")
    source_digest = receipt.get("source_digest")
    indexed_digest = receipt.get("indexed_digest")
    complete = (
        receipt.get("complete") is True
        and type(source_count) is int
        and type(indexed_count) is int
        and source_count == indexed_count
        and isinstance(source_digest, str)
        and bool(source_digest)
        and source_digest == indexed_digest
    )
    if not complete:
        raise DataProIndexError("DataPro result indexing was incomplete")
    return dict(receipt)


def result_artifact_payload(
    *, query: str, result: Mapping[str, Any], frame_id: str | None = None
) -> dict[str, Any]:
    """Build one JSON upload; callers hand it to ``ArtifactManager.upload``."""

    saved = {
        "connector": CONNECTOR_ID,
        "tool": TOOL_NAME,
        "query": query,
        "result": dict(result),
    }
    payload: dict[str, Any] = {
        "filename": f"datapro-search-{uuid.uuid4().hex[:12]}.json",
        "content_text": json.dumps(saved, ensure_ascii=False, indent=2),
    }
    if frame_id:
        payload["frame_id"] = frame_id
    return payload


def validate_query(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("query must be a string")
    query = value.strip()
    if not query:
        raise ValueError("query is required")
    if len(query) > MAX_QUERY_CHARS:
        raise ValueError(f"query exceeds {MAX_QUERY_CHARS} characters")
    return query


__all__ = [
    "AGENT_PLAN_KEY_SETTING",
    "AUTH_FAILURE_CODE",
    "AUTH_FAILURE_MESSAGE",
    "AVAILABLE_MESSAGE",
    "CONNECTOR_ID",
    "DataProCredentialError",
    "DataProIndexError",
    "ENDPOINT",
    "SKILL_NAME",
    "TOOL_NAME",
    "connector_runtime_config",
    "credential_state",
    "managed_connector_command",
    "is_volcengine_endpoint",
    "index_successful_search",
    "is_successful_search",
    "public_search_result",
    "redact_mcp_result",
    "redact_secret",
    "resolve_agent_plan_key",
    "result_artifact_payload",
    "runtime_cache_scope",
    "save_agent_plan_key",
    "strict_code",
    "structured_content",
    "tool_descriptor",
    "validate_query",
]
