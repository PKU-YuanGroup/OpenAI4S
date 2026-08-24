"""Newline-delimited stdio MCP server for atomic protein-design tools."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from .schemas import TOOLS
from .service import ProteinDesignService

PROTOCOL_VERSION = "2024-11-05"
SERVER_VERSION = "1.0.0"
_MAX_FRAME_BYTES = 4 * 1024 * 1024


def _send(message: dict[str, Any]) -> None:
    encoded = json.dumps(message, ensure_ascii=True, separators=(",", ":"))
    sys.stdout.write(encoded + "\n")
    sys.stdout.flush()


def _tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, sort_keys=True, ensure_ascii=True),
            }
        ],
        "structuredContent": payload,
        "isError": payload.get("status") != "succeeded",
    }


def handle(
    message: dict[str, Any], service: ProteinDesignService
) -> dict[str, Any] | None:
    message_id = message.get("id")
    method = message.get("method")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "openai4s-protein-design",
                    "version": SERVER_VERSION,
                },
            },
        }
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": message_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": message_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        payload = service.call(name, arguments)
        return {"jsonrpc": "2.0", "id": message_id, "result": _tool_result(payload)}
    if message_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return None


def main() -> None:
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    service = ProteinDesignService()
    for raw in sys.stdin.buffer:
        if len(raw) > _MAX_FRAME_BYTES:
            sys.stderr.write("ignored oversized MCP request frame\n")
            continue
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                continue
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        reply = handle(message, service)
        if reply is not None:
            _send(reply)


if __name__ == "__main__":
    main()
