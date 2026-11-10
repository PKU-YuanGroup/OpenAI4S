"""Dependency-free JSON-lines RPC for an external RL trainer.

The RPC layer is deliberately thin: :class:`RolloutEnvironmentAdapter` owns
the episode state and delegates execution to the existing ``AgentEngine``.
This module only validates requests, maps them to that adapter, and emits one
detached response per input line.  It never starts a second execution loop.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any, TextIO

from .environment import RolloutEnvironmentAdapter

PROTOCOL = "openai4s-rl-jsonl-v1"
DEFAULT_MAX_LINE_BYTES = 4 * 1024 * 1024
RPC_METHODS = frozenset(
    {"reset", "observe", "step", "finish", "export", "release", "rewards", "costs"}
)


class RolloutRPC:
    """Dispatch trainer requests against one environment adapter.

    Requests use a JSON-RPC-shaped envelope without requiring a third-party
    JSON-RPC implementation::

        {"jsonrpc":"2.0", "id": 1, "method":"step",
         "params":{"reply":{"content":"..."}}}

    Every non-notification request receives exactly one response.  Errors are
    structured and do not include a traceback or private workspace path.
    """

    protocol = PROTOCOL

    def __init__(self, adapter: RolloutEnvironmentAdapter):
        if not isinstance(adapter, RolloutEnvironmentAdapter):
            raise TypeError("adapter must be a RolloutEnvironmentAdapter")
        self.adapter = adapter

    def dispatch(self, request: Any) -> dict[str, Any]:
        """Dispatch one decoded JSON value and return a JSON-safe response."""

        if not isinstance(request, Mapping):
            return _error(None, "invalid_request", "request must be a JSON object")
        request_id = request.get("id")
        if "id" not in request or not _valid_id(request_id):
            return _error(
                None, "invalid_request", "request id must be a string or number"
            )
        if request.get("jsonrpc") not in (None, "2.0"):
            return _error(request_id, "invalid_request", "jsonrpc must be '2.0'")
        method = request.get("method")
        if not isinstance(method, str) or not method:
            return _error(
                request_id, "invalid_request", "method must be a non-empty string"
            )
        if method not in RPC_METHODS:
            return _error(request_id, "method_not_found", f"unknown method: {method}")
        params = request.get("params", {})
        if not isinstance(params, Mapping):
            return _error(request_id, "invalid_params", "params must be a JSON object")

        try:
            result = self._call(method, params)
            return _success(request_id, result)
        except KeyError as exc:
            name = str(exc.args[0]) if exc.args else "required field"
            return _error(request_id, "invalid_params", f"missing parameter: {name}")
        except (TypeError, ValueError) as exc:
            return _error(request_id, "invalid_params", str(exc))
        except RuntimeError as exc:
            return _error(request_id, "state_error", str(exc))
        except Exception as exc:  # pragma: no cover - defensive backend envelope
            return _error(request_id, "execution_error", str(exc))

    def _call(self, method: str, params: Mapping[str, Any]) -> Any:
        if method == "reset":
            _reject_unknown(
                params,
                {
                    "episode",
                    "messages",
                    "initial_artifacts",
                    "config",
                    "budget",
                    "max_turns",
                    "request_id",
                },
                method,
            )
            return self.adapter.reset(params)
        if method == "observe":
            _require_empty(params, method)
            return self.adapter.observe()
        if method == "step":
            _reject_unknown(params, {"reply", "observation"}, method)
            if "reply" not in params:
                raise KeyError("reply")
            reply = params["reply"]
            if not isinstance(reply, Mapping):
                raise TypeError("reply must be a JSON object")
            observation = params.get("observation")
            return self.adapter.step(reply, observation=observation)
        if method == "finish":
            _reject_unknown(params, {"status"}, method)
            status = params.get("status", "completed")
            if not isinstance(status, str) or not status:
                raise TypeError("status must be a non-empty string")
            if status == "running":
                raise ValueError("status must be terminal")
            return self.adapter.finish(status)
        if method == "export":
            _reject_unknown(params, {"format"}, method)
            format_name = params.get("format", "json")
            if not isinstance(format_name, str):
                raise TypeError("format must be a string")
            return self.adapter.export(format_name)
        if method == "release":
            _require_empty(params, method)
            return self.adapter.release()
        if method == "rewards":
            allowed = {"verifier", "verifier_version"}
            _reject_unknown(params, allowed, method)
            verifier = params.get("verifier")
            verifier_version = params.get("verifier_version")
            if verifier is not None and not isinstance(verifier, str):
                raise TypeError("verifier must be a string or null")
            if verifier_version is not None and not isinstance(verifier_version, str):
                raise TypeError("verifier_version must be a string or null")
            return self.adapter.rewards(
                verifier=verifier, verifier_version=verifier_version
            )
        if method == "costs":
            _require_empty(params, method)
            return self.adapter.costs()
        raise AssertionError(f"unhandled RPC method {method!r}")


def serve(
    adapter: RolloutEnvironmentAdapter,
    reader: TextIO,
    writer: TextIO,
    *,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
) -> int:
    """Serve newline-delimited requests until ``reader`` reaches EOF.

    Blank lines are ignored.  Oversized lines are drained through their
    newline before an error response is written, keeping the stream aligned
    for the next request.  The return value is the number of responses sent.
    """

    if max_line_bytes <= 0:
        raise ValueError("max_line_bytes must be positive")
    rpc = RolloutRPC(adapter)
    responses = 0
    while True:
        line = reader.readline(max_line_bytes + 1)
        if not line:
            break
        if len(line.encode("utf-8")) > max_line_bytes:
            while "\n" not in line:
                line = reader.readline(max_line_bytes + 1)
                if not line:
                    break
            response = _error(None, "request_too_large", "request line exceeds limit")
        elif not line.strip():
            continue
        else:
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response = _error(None, "parse_error", "request is not valid JSON")
            else:
                response = rpc.dispatch(request)
        writer.write(_encode(response))
        writer.flush()
        responses += 1
    return responses


def _valid_id(value: Any) -> bool:
    return value is None or (
        isinstance(value, (str, int, float))
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value))
    )


def _require_empty(params: Mapping[str, Any], method: str) -> None:
    if params:
        raise ValueError(f"{method} does not accept parameters")


def _reject_unknown(params: Mapping[str, Any], allowed: set[str], method: str) -> None:
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ValueError(f"unknown {method} parameter: {unknown[0]}")


def _success(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "ok": True, "result": result}


def _error(request_id: Any, code: str, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _encode(value: Mapping[str, Any]) -> str:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        + "\n"
    )


__all__ = ["DEFAULT_MAX_LINE_BYTES", "PROTOCOL", "RPC_METHODS", "RolloutRPC", "serve"]
