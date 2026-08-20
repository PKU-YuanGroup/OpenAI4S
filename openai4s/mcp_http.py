"""Pure-stdlib MCP Streamable HTTP transport.

The stdio client remains the default transport.  This module is imported
only when a connector explicitly selects ``streamable_http`` so importing the
core does not create network state or pull in an optional dependency.

Custom headers are deliberately write-only: they are validated and retained
for outbound requests, but never exposed through ``command``, ``repr`` or
error messages.  HTTP response bodies are likewise never copied into transport
errors because an upstream may reflect a credential-bearing request.
"""

from __future__ import annotations

import json
import math
import re
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, Callable

from openai4s import egress, webtools
from openai4s.http_deadline import HTTPExchangeDeadline, read_body_capped
from openai4s.mcp_protocol import (
    MAX_FRAME_BYTES,
    MCPError,
    MCPOversizedResponse,
    MCPTimeout,
    redact_reflected_secret,
)

PROTOCOL_VERSION = "2025-06-18"
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_PROTOCOL_VERSION = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RESERVED_HEADERS = frozenset(
    {
        "accept",
        "content-length",
        "content-type",
        "host",
        "mcp-protocol-version",
        "mcp-session-id",
    }
)
_MAX_REFLECTION_SECRETS = 32
_MAX_FRAME_BYTES = MAX_FRAME_BYTES


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Make redirects visible as HTTP errors; credentials never change host."""

    def redirect_request(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
        return None


class _SessionExpired(Exception):
    """Internal signal for the one permitted re-initialization attempt."""


def _validated_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MCPError("streamable HTTP connector has no URL")
    url = value.strip()
    try:
        parsed = urllib.parse.urlsplit(url)
        # Reading ``port`` makes urllib validate malformed/non-numeric ports.
        _port = parsed.port
    except ValueError:
        raise MCPError("streamable HTTP connector URL is invalid") from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise MCPError("streamable HTTP connector URL must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise MCPError("streamable HTTP connector URL must not contain credentials")
    if parsed.fragment:
        raise MCPError("streamable HTTP connector URL must not contain a fragment")
    return url


def _validated_headers(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise MCPError("streamable HTTP connector headers must be an object")
    headers: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not _HEADER_NAME.fullmatch(raw_name):
            raise MCPError("streamable HTTP connector has an invalid header name")
        if raw_name.lower() in _RESERVED_HEADERS:
            raise MCPError(
                "streamable HTTP connector tried to override a protocol header"
            )
        if raw_value is None:
            raise MCPError("streamable HTTP connector has a null header value")
        text = str(raw_value)
        if any(ch in text for ch in ("\r", "\n", "\x00")):
            raise MCPError("streamable HTTP connector has an invalid header value")
        headers[raw_name] = text
    return headers


def _session_id(value: str | None) -> str | None:
    if value is None:
        return None
    # The MCP specification restricts this to visible ASCII.  A length ceiling
    # prevents a response header from becoming unbounded retained state.
    if (
        not value
        or len(value) > 1024
        or any(not 0x21 <= ord(ch) <= 0x7E for ch in value)
    ):
        raise MCPError("MCP server returned an invalid session identifier")
    return value


def _protocol_version(value: Any) -> str:
    if isinstance(value, str) and _PROTOCOL_VERSION.fullmatch(value):
        return value
    return PROTOCOL_VERSION


def _sse_messages(body: bytes) -> list[dict[str, Any]]:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise MCPError("MCP server returned invalid UTF-8 event data") from None

    messages: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def finish_event() -> None:
        if not data_lines:
            return
        data = "\n".join(data_lines)
        data_lines.clear()
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            raise MCPError("MCP server returned invalid JSON event data") from None
        if isinstance(parsed, dict):
            messages.append(parsed)
        elif isinstance(parsed, list):
            messages.extend(item for item in parsed if isinstance(item, dict))

    # SSE terminates lines with CR, LF or CRLF and nothing else.  ``splitlines``
    # would also break on U+0085/U+2028/U+2029, which are legal *unescaped*
    # inside a JSON string (Node's ``JSON.stringify`` emits them raw), cutting a
    # well-formed ``data:`` line in two.
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            finish_event()
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
    finish_event()
    return messages


def _json_messages(body: bytes) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MCPError("MCP server returned invalid JSON") from None
    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    raise MCPError("MCP server returned a non-object JSON-RPC response")


def _select_response(
    body: bytes,
    content_type: str,
    expected_id: int,
) -> dict[str, Any]:
    media_type = content_type.partition(";")[0].strip().lower()
    if media_type == "text/event-stream":
        messages = _sse_messages(body)
    elif media_type == "application/json" or (
        not media_type and body.lstrip()[:1] in (b"{", b"[")
    ):
        messages = _json_messages(body)
    else:
        raise MCPError("MCP server returned an unsupported response media type")
    for message in messages:
        # ``==`` alone would accept ``True`` for id 1 and ``2.0`` for id 2, so an
        # untrusted server could place a decoy ahead of the genuine reply and
        # have it selected.  Ids we issue are always plain ints.
        message_id = message.get("id")
        if type(message_id) is int and message_id == expected_id:
            return message
    raise MCPError("MCP server response did not contain the requested JSON-RPC id")


def _redact_reflection(value: Any, secret: str) -> Any:
    """Remove an outbound secret reflected by an untrusted MCP response."""

    return redact_reflected_secret(value, secret)


def _redact_jsonrpc_message(message: dict[str, Any], secret: str) -> dict[str, Any]:
    """Scrub response data and restore the trusted JSON-RPC/MCP skeleton."""

    safe = _redact_reflection(message, secret)
    if not isinstance(safe, dict):
        safe = {}
    for key in ("jsonrpc", "id", "error"):
        if key in message:
            safe[key] = _redact_reflection(message[key], secret)
    result = message.get("result")
    if isinstance(result, Mapping):
        safe_result = _redact_reflection(result, secret)
        if not isinstance(safe_result, dict):
            safe_result = {}
        for key in (
            "protocolVersion",
            "capabilities",
            "serverInfo",
            "tools",
            "content",
            "isError",
        ):
            if key in result:
                safe_result[key] = _redact_reflection(result[key], secret)
        structured = result.get("structuredContent")
        if isinstance(structured, Mapping):
            safe_structured = _redact_reflection(structured, secret)
            if not isinstance(safe_structured, dict):
                safe_structured = {}
            if "code" in structured:
                safe_structured["code"] = _redact_reflection(structured["code"], secret)
            safe_result["structuredContent"] = safe_structured
        safe["result"] = safe_result
    elif "result" in message:
        safe["result"] = _redact_reflection(result, secret)
    return safe


class MCPHTTPConnection:
    """One logical MCP session carried by independent HTTP POST requests."""

    def __init__(
        self,
        url: str,
        headers: Mapping[str, Any] | None = None,
        *,
        headers_provider: Callable[[], Mapping[str, Any] | None] | None = None,
        timeout: float | None = None,
    ) -> None:
        self.command = ["streamable_http"]
        self._url = _validated_url(url)
        if headers is not None and headers_provider is not None:
            raise MCPError(
                "streamable HTTP connector cannot set both headers and a provider"
            )
        if headers_provider is not None and not callable(headers_provider):
            raise MCPError("streamable HTTP connector header provider is not callable")
        self._static_headers = _validated_headers(headers)
        self._headers_provider = headers_provider
        try:
            chosen_timeout = 60.0 if timeout is None else float(timeout)
        except (TypeError, ValueError):
            raise MCPError(
                "streamable HTTP connector timeout must be a number"
            ) from None
        if not math.isfinite(chosen_timeout) or chosen_timeout <= 0:
            raise MCPError("streamable HTTP connector timeout must be positive")
        self._timeout = chosen_timeout
        self._id = 0
        self._lock = threading.RLock()
        self._session: str | None = None
        self._version = PROTOCOL_VERSION
        self._initialized = False
        self._closed = False
        self._failure: str | None = None
        # A Streamable HTTP session may see a rotated Key on a later POST.
        # Keep only the values this connection actually sent, solely so a
        # malicious/delayed upstream reflection of an earlier Key is scrubbed
        # before leaving the transport.  The bounded list is cleared on close;
        # reaching the bound invalidates the session before sending another.
        self._reflection_secrets: list[str] = []
        self._initialize()

    def __repr__(self) -> str:
        return "MCPHTTPConnection(transport='streamable_http')"

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _wire_headers(self) -> dict[str, str]:
        if self._headers_provider is None:
            headers = dict(self._static_headers)
        else:
            try:
                resolved = self._headers_provider()
            except Exception as exc:
                # A broker/backend exception can carry its own diagnostic
                # payload.  Only its class crosses this credential boundary.
                raise MCPError(
                    "MCP HTTP outbound headers could not be resolved "
                    f"({type(exc).__name__})"
                ) from None
            headers = _validated_headers(resolved)
        headers["Accept"] = "application/json, text/event-stream"
        headers["Content-Type"] = "application/json"
        if self._initialized:
            headers["MCP-Protocol-Version"] = self._version
        if self._session is not None:
            headers["Mcp-Session-Id"] = self._session
        return headers

    def _check_network_policy(self) -> None:
        try:
            if not webtools.network_allowed():
                raise webtools.NetworkDisabled("network disabled")
            egress.check_url(self._url)
            webtools.guard_url(self._url)
        except Exception as exc:
            # Policy errors can contain a user-controlled URL/domain.  The
            # caller needs to know the class of refusal, never the target or a
            # query-string credential.
            raise MCPError(
                f"MCP HTTP request was blocked by network policy ({type(exc).__name__})"
            ) from None

    def _read_body(
        self,
        response: Any,
        exchange: HTTPExchangeDeadline,
    ) -> bytes:
        """This transport's failure vocabulary over the shared bounded reader.

        The loop itself lives in ``http_deadline`` so that the properties it
        enforces -- one raw read per deadline check, the byte cap, stopping at
        end-of-body, refusing a body cut short of its declared length, and
        refusing to read one at all with no bound -- hold for every consumer
        rather than for whichever copy last received the fix.
        """

        def _oversize() -> BaseException:
            return MCPOversizedResponse("MCP HTTP response exceeded the 4 MiB limit")

        return read_body_capped(
            response,
            limit=_MAX_FRAME_BYTES,
            exchange=exchange,
            on_timeout=lambda: MCPTimeout(
                f"MCP HTTP request exceeded {self._timeout:g}s"
            ),
            on_oversize=_oversize,
            on_truncated=lambda: MCPError(
                "MCP HTTP response ended before its declared length"
            ),
            on_unbounded=lambda: MCPError(
                "MCP HTTP response has no bounded read transport"
            ),
        )

    def _post(
        self,
        payload: dict[str, Any],
        *,
        expected_id: int | None,
    ) -> dict[str, Any] | None:
        if self._closed:
            raise MCPError(self._failure or "MCP HTTP connection is closed")
        self._check_network_policy()
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(body) > _MAX_FRAME_BYTES:
            raise MCPError("MCP HTTP request exceeded the 4 MiB limit")
        wire_headers = self._wire_headers()
        # This local exists only for the lifetime of this POST.  It closes the
        # key-rotation race between a caller's pre/post-call scrub and the
        # provider's just-in-time SecretBroker lookup: the exact key sent on
        # this request scrubs its response before it leaves the transport.
        plan_key = next(
            (
                value
                for name, value in wire_headers.items()
                if name.lower() == "x-agent-plan-key"
            ),
            "",
        )
        if plan_key and plan_key not in self._reflection_secrets:
            if len(self._reflection_secrets) >= _MAX_REFLECTION_SECRETS:
                self._closed = True
                self._failure = "MCP HTTP credential rotation limit reached"
                self._reflection_secrets.clear()
                raise MCPError(self._failure)
            self._reflection_secrets.append(plan_key)
        request = urllib.request.Request(
            self._url,
            data=body,
            headers=wire_headers,
            method="POST",
        )
        try:
            # Only the network exchange belongs inside the deadline.  Parsing and
            # redacting a multi-megabyte reply is local CPU work; charging it to
            # the wire budget let a complete, correct response be thrown away as
            # a timeout.  ``doubao_search`` already splits it this way.
            with HTTPExchangeDeadline(self._timeout) as exchange:
                opener = exchange.build_opener(_NoRedirect)
                with exchange.open(opener, request) as response:
                    status = getattr(response, "status", None) or response.getcode()
                    if status == 202:
                        if expected_id is None:
                            return None
                        raise MCPError("MCP server returned 202 for a JSON-RPC request")
                    if status != 200:
                        raise MCPError(
                            f"MCP HTTP request failed with status {int(status)}"
                        )
                    session = response.headers.get("Mcp-Session-Id")
                    if session is not None:
                        self._session = _session_id(session)
                    content_type = response.headers.get("Content-Type", "")
                    response_body = self._read_body(response, exchange)
                    if expected_id is None:
                        return None
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                exc.close()
            except Exception:  # noqa: BLE001 - best-effort socket cleanup
                pass
            if status == 404 and self._session is not None:
                raise _SessionExpired from None
            if 300 <= status < 400:
                raise MCPError(
                    f"MCP HTTP redirect was refused (status {status})"
                ) from None
            raise MCPError(f"MCP HTTP request failed with status {status}") from None
        except (socket.timeout, TimeoutError):
            raise MCPTimeout(f"MCP HTTP request exceeded {self._timeout:g}s") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise MCPTimeout(
                    f"MCP HTTP request exceeded {self._timeout:g}s"
                ) from None
            raise MCPError(
                f"MCP HTTP transport failed ({type(exc.reason).__name__})"
            ) from None
        except (MCPError, _SessionExpired):
            raise
        except Exception as exc:
            raise MCPError(
                f"MCP HTTP transport failed ({type(exc).__name__})"
            ) from None
        message = _select_response(response_body, content_type, expected_id)
        # Longest first: an older Key may be a prefix of its rotated replacement.
        # Scrubbing the prefix first would leave the replacement's suffix visible
        # and no longer match the whole.
        for sent_secret in sorted(self._reflection_secrets, key=len, reverse=True):
            message = _redact_jsonrpc_message(message, sent_secret)
        return message

    def _request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        recover_session: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            retried = False
            while True:
                mid = self._next_id()
                try:
                    message = self._post(
                        {
                            "jsonrpc": "2.0",
                            "id": mid,
                            "method": method,
                            "params": params or {},
                        },
                        expected_id=mid,
                    )
                    break
                except _SessionExpired:
                    if not recover_session or retried:
                        raise MCPError(
                            "MCP HTTP session expired after re-initialization"
                        ) from None
                    retried = True
                    self._session = None
                    self._initialized = False
                    try:
                        self._initialize()
                    except Exception:
                        # Without this the connection stays cached with no
                        # session, and ``_SessionExpired`` can never fire again
                        # (it needs ``self._session``), so every later call fails
                        # against a healthy server until the daemon restarts.
                        self._closed = True
                        self._failure = (
                            self._failure or "MCP HTTP session re-initialization failed"
                        )
                        raise
            assert message is not None
            if message.get("error") is not None:
                error = message.get("error")
                code = error.get("code") if isinstance(error, dict) else None
                suffix = f" (code {code})" if isinstance(code, int) else ""
                raise MCPError(f"MCP server returned a JSON-RPC error{suffix}")
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._post(
                {"jsonrpc": "2.0", "method": method, "params": params or {}},
                expected_id=None,
            )

    def _initialize(self) -> None:
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "openai4s", "version": "1.0.0"},
            },
            recover_session=False,
        )
        self._version = _protocol_version(result.get("protocolVersion"))
        self._initialized = True
        try:
            self._notify("notifications/initialized")
        except Exception:  # noqa: BLE001 - a server may 202/404 the notification
            # Tolerated, as over stdio, but not silently: there the notify is a
            # pipe write whose only realistic failure is a dead child that the
            # reader independently faults.  Over HTTP it is a separate request
            # that can fail on its own, leaving a session the server considers
            # un-initialized and answers ``-32002`` forever.
            self._initialized = False

    def alive(self) -> bool:
        return not self._closed

    def faulted(self) -> bool:
        # A connection that lost its session and could not re-handshake can never
        # answer another request, so the manager must evict it.  Reporting only
        # ``_closed`` made this health check dead for the HTTP transport.
        return self._closed or not self._initialized

    def failure(self) -> str | None:
        return self._failure

    def stderr_tail(self) -> str:
        return ""

    def close(self) -> bool:
        with self._lock:
            self._closed = True
            self._failure = self._failure or "MCP HTTP connection closed"
            self._session = None
            self._static_headers.clear()
            self._headers_provider = None
            self._reflection_secrets.clear()
        return True

    def list_tools(self) -> list[dict]:
        result = self._request("tools/list")
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        result = self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        text_parts = []
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
        return {
            "is_error": bool(result.get("isError")),
            "text": "\n".join(text_parts),
            "raw": result,
        }

    def list_resources(self, cursor: str | None = None) -> dict:
        params = {"cursor": cursor} if cursor is not None else None
        return self._request("resources/list", params)

    def read_resource(self, uri: str) -> dict:
        return self._request("resources/read", {"uri": uri})

    def list_prompts(self, cursor: str | None = None) -> dict:
        params = {"cursor": cursor} if cursor is not None else None
        return self._request("prompts/list", params)

    def get_prompt(self, name: str, arguments: dict | None = None) -> dict:
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return self._request("prompts/get", params)
