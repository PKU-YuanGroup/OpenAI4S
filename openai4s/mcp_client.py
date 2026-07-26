"""Minimal MCP (Model Context Protocol) stdio client — pure stdlib.

Speaks newline-delimited JSON-RPC 2.0 to a spawned MCP server process, enough to
power the Connectors control plane: handshake (initialize + initialized), tools,
resources, and prompts. A process-wide MCPManager caches one live connection per
connector id so repeated calls reuse the same server.

Sampling and server-initiated requests are deliberately outside this client.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Mapping
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
_DEFAULT_TIMEOUT = 30.0

#: Absolute deadline for one request. Previously there was none: `_read_reply`
#: called `readline()` in a loop, so a connector that accepted a request and
#: never answered held its caller forever -- and, because the manager takes its
#: own lock across connect, held every other connector too.
DEFAULT_TIMEOUT_S = 60.0
#: Largest single JSON-RPC line accepted. `readline()` has no size bound, so one
#: newline-free multi-gigabyte line was a single allocation in the daemon.
_MAX_FRAME_BYTES = 4 * 1024 * 1024
#: Bounded diagnostic tail. stderr used to go to DEVNULL: no deadlock, and also
#: no way to say why a connector failed.
_STDERR_TAIL_LINES = 200
#: How many replies to ids nobody asked for before treating the channel as
#: desynchronised. Staying attached to such a server only defers the failure.
_MAX_INVALID_IDS = 64


def _read_bounded_line(stream: Any) -> str | None:
    """Read one line, refusing to materialise an unbounded one.

    Returns ``None`` at EOF. A line over the budget is consumed and dropped
    rather than truncated: half a JSON object is not a frame, and returning it
    would desynchronise the reader on the next line.
    """
    if stream is None:
        return None
    chunks: list[str] = []
    size = 0
    while True:
        char = stream.read(1)
        if not char:
            return "".join(chunks) if chunks else None
        if char == "\n":
            return "".join(chunks)
        size += 1
        if size > _MAX_FRAME_BYTES:
            # Drain to the newline so the next read starts on a frame boundary.
            while True:
                skip = stream.read(1)
                if not skip or skip == "\n":
                    break
            return ""
        chunks.append(char)


# A connector is third-party code.  Never copy the daemon's complete environment
# into it: that would silently expose provider keys, cloud credentials, and
# unrelated application secrets.  These variables are the small cross-platform
# runtime substrate needed to locate a command, create temporary files, select a
# locale, and use the host trust store.  Connector-specific credentials must be
# supplied explicitly in the persisted connector ``env`` mapping.
_CONNECTOR_RUNTIME_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "USERPROFILE",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_EXTRA_CA_CERTS",
    }
)


class MCPError(RuntimeError):
    pass


class MCPTimeout(MCPError):
    """A request outlived its deadline.

    A subclass so every existing `except MCPError` still catches it and the
    soft-fail wrappers in `host/mcp.py` keep their exact message shapes.
    """


def _connector_environment(
    explicit: Mapping[str, Any] | None = None,
    *,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a fresh least-privilege environment for one MCP subprocess.

    ``source`` exists for deterministic tests.  Explicit connector values are
    intentionally allowed to contain credentials: they are the connector's
    declared secret boundary, unlike arbitrary variables inherited by the
    daemon from the user's shell.
    """

    host = os.environ if source is None else source
    env = {
        name: str(host[name])
        for name in _CONNECTOR_RUNTIME_ENV
        if name in host and host[name] is not None
    }
    env.setdefault("PATH", os.defpath)
    env["PYTHONUNBUFFERED"] = "1"
    if explicit is None:
        return env
    if not isinstance(explicit, Mapping):
        raise MCPError("connector env must be an object")
    for raw_name, raw_value in explicit.items():
        if not isinstance(raw_name, str) or not raw_name:
            raise MCPError("connector env names must be non-empty strings")
        if "=" in raw_name or "\x00" in raw_name:
            raise MCPError(f"invalid connector env name: {raw_name!r}")
        if raw_value is None:
            raise MCPError(f"connector env value for {raw_name!r} cannot be null")
        value = str(raw_value)
        if "\x00" in value:
            raise MCPError(f"connector env value for {raw_name!r} contains NUL")
        env[raw_name] = value
    return env


class MCPConnection:
    def __init__(
        self,
        command: list[str],
        env: dict | None = None,
        cwd: str | None = None,
        *,
        timeout: float | None = None,
    ):
        self.command = command
        self._id = 0
        self._lock = threading.Lock()
        self._timeout = float(timeout if timeout is not None else DEFAULT_TIMEOUT_S)
        #: id -> the waiter that asked for it. A dedicated reader thread routes
        #: replies here instead of every caller racing on `readline`.
        self._pending: dict[int, "queue.Queue[dict]"] = {}
        #: ids whose caller has already given up. A server may still answer a
        #: timed-out request, and without this the reply would be sitting in the
        #: pipe for the NEXT request to read as its own -- which is why adding a
        #: timeout to the old per-call `readline` would have been a correctness
        #: regression rather than a fix.
        self._abandoned: set[int] = set()
        self._closed = threading.Event()
        self._failure: str | None = None
        self._stderr_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        self._proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # Piped so a failing connector can say why, and drained
            # concurrently below. A pipe nobody reads fills at 64 KB and blocks
            # the child in `write` -- the two halves of this change cannot be
            # separated.
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            cwd=cwd,
        )
        self._reader = threading.Thread(
            target=self._read_loop, name="mcp-reader", daemon=True
        )
        self._reader.start()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, name="mcp-stderr", daemon=True
        )
        self._stderr_thread.start()
        self._init()

    # -- wire ----------------------------------------------------------------
    def _send(self, obj: dict) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()

    def _drain_stderr(self) -> None:
        stream = self._proc.stderr
        if stream is None:
            return
        try:
            for line in stream:
                self._stderr_tail.append(line.rstrip("\n"))
        except (OSError, ValueError):
            pass

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    def _fail_all(self, reason: str) -> None:
        """Wake every waiter once the channel can no longer answer them."""
        self._failure = reason
        self._closed.set()
        with self._lock:
            waiters = list(self._pending.items())
            self._pending.clear()
        for _mid, waiter in waiters:
            try:
                waiter.put_nowait({"__closed__": reason})
            except Exception:  # noqa: BLE001 - a waiter that already gave up
                pass

    def _read_loop(self) -> None:
        """The only reader. One thread owns the pipe; callers own their ids."""
        stream = self._proc.stdout
        invalid_ids = 0
        try:
            while True:
                line = _read_bounded_line(stream)
                if line is None:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict):
                    continue
                mid = msg.get("id")
                if mid is None:
                    continue  # a notification; nobody is waiting on it
                with self._lock:
                    waiter = self._pending.pop(mid, None)
                    if waiter is None:
                        if mid in self._abandoned:
                            # Exactly what this set exists for: discard the
                            # late answer instead of letting it be mistaken
                            # for the next request's.
                            self._abandoned.discard(mid)
                            continue
                        invalid_ids += 1
                if waiter is not None:
                    try:
                        waiter.put_nowait(msg)
                    except Exception:  # noqa: BLE001
                        pass
                elif invalid_ids > _MAX_INVALID_IDS:
                    # A server answering ids nobody asked for is desynchronised,
                    # and staying attached to it only defers the failure.
                    break
        except (OSError, ValueError):
            pass
        finally:
            self._fail_all(self._failure or "MCP server closed the connection")

    def _request(self, method: str, params: dict | None = None) -> dict:
        deadline = time.monotonic() + self._timeout
        with self._lock:
            if self._closed.is_set():
                raise MCPError(self._failure or "MCP server closed the connection")
            self._id += 1
            mid = self._id
            waiter: "queue.Queue[dict]" = queue.Queue(maxsize=1)
            self._pending[mid] = waiter
            try:
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "method": method,
                        "params": params or {},
                    }
                )
            except Exception:
                self._pending.pop(mid, None)
                raise
        try:
            msg = waiter.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            with self._lock:
                self._pending.pop(mid, None)
                self._abandoned.add(mid)
            raise MCPTimeout(
                f"MCP request {method!r} exceeded {self._timeout:g}s"
            ) from None
        if "__closed__" in msg:
            raise MCPError(str(msg["__closed__"]))
        if "error" in msg and msg["error"] is not None:
            error = msg["error"]
            detail = error.get("message") if isinstance(error, dict) else None
            raise MCPError(str(detail or error))
        return msg.get("result") or {}

    def _notify(self, method: str, params: dict | None = None) -> None:
        with self._lock:
            self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # -- lifecycle -----------------------------------------------------------
    def _init(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "openai4s", "version": "1.0.0"},
            },
        )
        try:
            self._notify("notifications/initialized")
        except Exception:  # noqa: BLE001
            pass

    def alive(self) -> bool:
        return self._proc.poll() is None

    def close(self) -> None:
        self._fail_all("MCP connection closed")
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass
            else:
                # Reap it. `kill()` only delivers the signal; without the wait
                # the child stays a zombie, and a connector that has to be
                # killed is exactly the one that gets closed repeatedly.
                try:
                    self._proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    pass
        for stream in (self._proc.stdout, self._proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass

    # -- tools ---------------------------------------------------------------
    def list_tools(self) -> list[dict]:
        res = self._request("tools/list")
        return res.get("tools", []) if isinstance(res, dict) else []

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        res = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        # normalize content blocks -> plain text for the agent
        text_parts = []
        for block in res.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        return {
            "is_error": bool(res.get("isError")),
            "text": "\n".join(text_parts),
            "raw": res,
        }

    # -- resources -----------------------------------------------------------
    def list_resources(self, cursor: str | None = None) -> dict:
        params = {"cursor": cursor} if cursor is not None else None
        res = self._request("resources/list", params)
        if not isinstance(res, dict):
            raise MCPError("resources/list returned a non-object result")
        return res

    def read_resource(self, uri: str) -> dict:
        res = self._request("resources/read", {"uri": uri})
        if not isinstance(res, dict):
            raise MCPError("resources/read returned a non-object result")
        return res

    # -- prompts -------------------------------------------------------------
    def list_prompts(self, cursor: str | None = None) -> dict:
        params = {"cursor": cursor} if cursor is not None else None
        res = self._request("prompts/list", params)
        if not isinstance(res, dict):
            raise MCPError("prompts/list returned a non-object result")
        return res

    def get_prompt(self, name: str, arguments: dict | None = None) -> dict:
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        res = self._request("prompts/get", params)
        if not isinstance(res, dict):
            raise MCPError("prompts/get returned a non-object result")
        return res


class MCPManager:
    """One live connection per connector id (lazily connected, cached)."""

    def __init__(self) -> None:
        self._conns: dict[str, MCPConnection] = {}
        #: Live probes. They are not cached by connector id -- a probe is
        #: deliberately a fresh connection -- but they are still children this
        #: process owns, and `shutdown` has to be able to reach them.
        self._probes: set[MCPConnection] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _argv(config: dict) -> list[str]:
        cmd = config.get("command")
        args = config.get("args") or []
        if isinstance(cmd, list):
            argv = list(cmd) + list(args)
        elif isinstance(cmd, str) and cmd.strip():
            argv = cmd.split() + list(args)
        else:
            raise MCPError("connector has no command")
        return argv

    def _connect(self, config: dict) -> MCPConnection:
        env = _connector_environment(config.get("env"))
        return MCPConnection(self._argv(config), env=env, cwd=config.get("cwd"))

    def get(self, connector_id: str, config: dict) -> MCPConnection:
        with self._lock:
            conn = self._conns.get(connector_id)
            if conn is not None and conn.alive():
                return conn
            if conn is not None:
                conn.close()
            conn = self._connect(config)
            self._conns[connector_id] = conn
            return conn

    def probe(self, config: dict) -> dict:
        """Connect fresh, list tools, close. Returns {ok, tools|error}."""
        try:
            conn = self._connect(config)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        # Registered while it lives, so `shutdown` can reach it. A probe used
        # to connect without registering anywhere: if it hung, its `finally`
        # never ran and the orphaned connector process was invisible to both
        # `disconnect` and `shutdown` forever -- one leaked child per hung
        # "Test" click, unreapable.
        with self._lock:
            self._probes.add(conn)
        try:
            tools = conn.list_tools()
            return {"ok": True, "tools": tools}
        except Exception as e:  # noqa: BLE001
            detail = str(e)
            tail = conn.stderr_tail()
            if tail:
                detail = f"{detail} (connector stderr: {tail[-500:]})"
            return {"ok": False, "error": detail}
        finally:
            with self._lock:
                self._probes.discard(conn)
            conn.close()

    def list_tools(self, connector_id: str, config: dict) -> list[dict]:
        return self.get(connector_id, config).list_tools()

    def call_tool(
        self, connector_id: str, config: dict, tool: str, arguments: dict | None = None
    ) -> dict:
        return self.get(connector_id, config).call_tool(tool, arguments)

    def list_resources(
        self,
        connector_id: str,
        config: dict,
        cursor: str | None = None,
    ) -> dict:
        return self.get(connector_id, config).list_resources(cursor)

    def read_resource(self, connector_id: str, config: dict, uri: str) -> dict:
        return self.get(connector_id, config).read_resource(uri)

    def list_prompts(
        self,
        connector_id: str,
        config: dict,
        cursor: str | None = None,
    ) -> dict:
        return self.get(connector_id, config).list_prompts(cursor)

    def get_prompt(
        self,
        connector_id: str,
        config: dict,
        name: str,
        arguments: dict | None = None,
    ) -> dict:
        return self.get(connector_id, config).get_prompt(name, arguments)

    def disconnect(self, connector_id: str) -> None:
        with self._lock:
            conn = self._conns.pop(connector_id, None)
        if conn is not None:
            conn.close()

    def shutdown(self) -> None:
        with self._lock:
            conns = list(self._conns.values()) + list(self._probes)
            self._conns.clear()
            self._probes.clear()
        for c in conns:
            c.close()


# a process-wide manager (the daemon is single-process)
_MANAGER: MCPManager | None = None


def manager() -> MCPManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = MCPManager()
    return _MANAGER


def example_server_config() -> dict:
    """Config for the bundled example server (always available)."""
    return {"command": [sys.executable, "-m", "openai4s.mcp_servers.example_server"]}
