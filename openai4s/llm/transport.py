"""Pure-stdlib HTTP transports used by the LLM provider adapters."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .models import LLMError


def post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise LLMError(f"LLM HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"LLM connection error: {e.reason}") from e


_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def post_sse(url: str, payload: dict, headers: dict, timeout: float, on_event) -> None:
    """POST and decode a Server-Sent-Events stream.

    SSE events are delimited by a blank line and may contain multiple ``data:``
    rows. Tool calls are control-plane actions, so a malformed non-empty event
    is surfaced instead of being silently discarded.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise LLMError(f"LLM HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"LLM connection error: {e.reason}") from e
    data_lines: list[str] = []

    def dispatch() -> None:
        if not data_lines:
            return
        chunk = "\n".join(data_lines).strip()
        data_lines.clear()
        if not chunk or chunk == "[DONE]":
            return
        try:
            event = json.loads(chunk)
        except ValueError as e:
            raise LLMError(f"invalid JSON in LLM event stream: {chunk[:400]}") from e
        if not isinstance(event, dict):
            raise LLMError("LLM event stream yielded a non-object JSON event")
        on_event(event)

    try:
        try:
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    dispatch()
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    value = line[5:]
                    data_lines.append(value[1:] if value.startswith(" ") else value)
            dispatch()
        except LLMError:
            raise
        except Exception as e:  # noqa: BLE001 - normalize transport read failures
            raise LLMError(f"LLM event stream read error: {e}") from e
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
