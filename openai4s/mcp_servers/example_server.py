#!/usr/bin/env python3
"""A tiny, self-contained MCP server over stdio (pure stdlib, no `mcp` package).

Speaks newline-delimited JSON-RPC 2.0 — the MCP stdio transport — so the built-in
Connectors feature has a real server to attach to and exercise end-to-end:

  initialize  →  serverInfo + capabilities
  tools/list  →  {echo, now, calc, random_int}
  tools/call  →  {content:[{type:"text", text:...}]}
  resources/list + resources/read → a small text resource
  prompts/list + prompts/get → a parameterized summarization prompt

Run: `python3 -m openai4s.mcp_servers.example_server` (stdin/stdout are the wire).
"""

from __future__ import annotations

import ast
import datetime
import json
import operator
import os
import random
import sys

PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the given text.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "now",
        "description": "Current UTC date-time (ISO 8601).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "calc",
        "description": "Evaluate a basic arithmetic expression "
        "(+, -, *, /, **, parentheses).",
        "inputSchema": {
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    },
    {
        "name": "random_int",
        "description": "A random integer in [low, high].",
        "inputSchema": {
            "type": "object",
            "properties": {"low": {"type": "integer"}, "high": {"type": "integer"}},
            "required": ["low", "high"],
        },
    },
]

RESOURCE_URI = "openai4s://example/guide"
RESOURCES = [
    {
        "uri": RESOURCE_URI,
        "name": "OpenAI4S example connector guide",
        "description": "A local text resource for exercising MCP resources/read.",
        "mimeType": "text/plain",
    }
]

PROMPTS = [
    {
        "name": "summarize",
        "description": "Ask for a concise summary of supplied text.",
        "arguments": [
            {
                "name": "text",
                "description": "Text to summarize.",
                "required": True,
            }
        ],
    }
]

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.Mod: operator.mod,
}


def _safe_eval(expr: str) -> float:
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("unsupported expression")

    return ev(ast.parse(expr, mode="eval"))


def _call_tool(name: str, args: dict) -> str:
    if name == "echo":
        return str(args.get("text", ""))
    if name == "now":
        return datetime.datetime.now(datetime.timezone.utc).isoformat()
    if name == "calc":
        return str(_safe_eval(str(args.get("expression", "0"))))
    if name == "random_int":
        lo, hi = int(args.get("low", 0)), int(args.get("high", 0))
        return str(random.randint(min(lo, hi), max(lo, hi)))
    raise ValueError(f"unknown tool: {name}")


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _handle(msg: dict) -> dict | None:
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "openai4s-example", "version": "1.0.0"},
            },
        }
    if method in ("notifications/initialized", "initialized"):
        return None  # notification — no reply
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            text = _call_tool(params.get("name", ""), params.get("arguments") or {})
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "isError": False,
                },
            }
        except Exception as e:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "content": [{"type": "text", "text": f"error: {e}"}],
                    "isError": True,
                },
            }
    if method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {"resources": RESOURCES},
        }
    if method == "resources/read":
        uri = (msg.get("params") or {}).get("uri")
        if uri != RESOURCE_URI:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {"code": -32002, "message": f"resource not found: {uri}"},
            }
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "contents": [
                    {
                        "uri": RESOURCE_URI,
                        "mimeType": "text/plain",
                        "text": (
                            "This bundled connector demonstrates MCP tools, "
                            "resources, and prompts without third-party packages."
                        ),
                    }
                ]
            },
        }
    if method == "prompts/list":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {"prompts": PROMPTS},
        }
    if method == "prompts/get":
        params = msg.get("params") or {}
        if params.get("name") != "summarize":
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {
                    "code": -32602,
                    "message": f"unknown prompt: {params.get('name')}",
                },
            }
        text = str((params.get("arguments") or {}).get("text", ""))
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "description": "A concise summarization request.",
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": f"Summarize this text concisely:\n\n{text}",
                        },
                    }
                ],
            },
        }
    if mid is not None:
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }
    return None


def main() -> None:
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        reply = _handle(msg)
        if reply is not None:
            _send(reply)


if __name__ == "__main__":
    main()
