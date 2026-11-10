"""JSON-lines client for a deployment-provided external rollout worker.

The transport is intentionally small and dependency-free.  A caller either
supplies an argv vector (never a shell string) or an already-established pair
of text streams.  Exactly one request is in flight at a time; responses are
matched by correlation id and bounded by ``max_line_bytes``.

Transport loss is reported as :class:`RolloutTransportError` with
``state_unknown=True``.  A mutating request is never retried automatically:
the caller must query the durable ``recover``/``result``/``step_result``
endpoints on a fresh connection before deciding what to do next.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import queue
import subprocess
import threading
import uuid
from collections import deque
from dataclasses import asdict
from typing import Any, Iterable, Mapping, Sequence, TextIO, cast

from openai4s.agent.models import ModelReply

from .models import (
    CostRecord,
    Episode,
    ExecutionEvent,
    Observation,
    PolicyDecision,
    RewardAnnotation,
    Segment,
    Trajectory,
)
from .rpc import DEFAULT_MAX_LINE_BYTES, PROTOCOL

_END = object()
_OVERSIZED = object()

_RESET_KEYS = frozenset(
    {
        "episode",
        "messages",
        "initial_artifacts",
        "config",
        "budget",
        "max_turns",
        "request_id",
    }
)


class RolloutClientError(RuntimeError):
    """Base class for client-side failures."""

    state_unknown = False


class RolloutClientClosed(RolloutClientError):
    """The client or its transport has already been closed."""


class RolloutTransportError(RolloutClientError):
    """The transport failed while a request was in flight.

    ``state_unknown`` means a request was written but no definitive response
    was received.  The caller must query the durable result instead of
    replaying the operation.
    """

    def __init__(self, message: str, *, state_unknown: bool = False) -> None:
        super().__init__(message)
        self.state_unknown = state_unknown


class RolloutTimeoutError(RolloutTransportError):
    """No response arrived before the configured deadline."""


class RolloutProtocolError(RolloutClientError):
    """The peer sent something that is not a valid correlated response."""

    state_unknown = True


class RolloutRemoteError(RolloutClientError):
    """The worker returned a structured error response."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        request_id: Any = None,
        response: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.request_id = request_id
        self.response = None if response is None else dict(response)


class _FrameError(Exception):
    """A response frame is malformed or correlates to another request."""


class _RemoteFailure(Exception):
    def __init__(self, code: str, message: str, response: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.response = dict(response)


def _encode_request(request_id: int, method: str, params: Mapping[str, Any]) -> str:
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": dict(params),
    }
    try:
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            + "\n"
        )
    except (TypeError, ValueError) as exc:
        raise TypeError("RPC request params must be JSON-compatible") from exc


def _decode_response(line: str, expected_id: int) -> Any:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise _FrameError("response is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise _FrameError("response must be a JSON object")
    if payload.get("id") != expected_id:
        raise _FrameError("response correlation id does not match the request")
    ok = payload.get("ok")
    if ok is True:
        if "result" not in payload:
            raise _FrameError("response is missing its result")
        return payload["result"]
    if ok is False:
        error = payload.get("error")
        if not isinstance(error, Mapping) or not isinstance(error.get("code"), str):
            raise _FrameError("response error must contain a string code")
        raise _RemoteFailure(error["code"], str(error.get("message", "")), payload)
    raise _FrameError("response 'ok' field must be a boolean")


def _json_value(value: Any, name: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be JSON-compatible") from exc


def _json_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    result = _json_value(dict(value), name)
    if not isinstance(result, dict):
        raise TypeError(f"{name} must encode as a JSON object")
    return result


def _episode_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Episode):
        return value.to_dict()
    return _json_object(value, "episode")


def _messages_payload(value: Any, name: str = "messages") -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{name} must be a list or tuple")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise TypeError(f"{name}[{index}] must be a mapping")
        result.append(_json_object(item, f"{name}[{index}]"))
    return result


def _tools_payload(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    return _messages_payload(value, "tools")


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _reset_params(request: Any) -> dict[str, Any]:
    if not isinstance(request, Mapping):
        raise TypeError("reset request must be a mapping")
    unknown = sorted(set(request) - _RESET_KEYS)
    if unknown:
        raise ValueError(f"unknown reset parameter: {unknown[0]}")
    if "episode" not in request:
        raise ValueError("reset request requires an episode")
    if "messages" not in request:
        raise ValueError("reset request requires messages")
    params: dict[str, Any] = {
        "episode": _episode_payload(request["episode"]),
        "messages": _messages_payload(request["messages"]),
    }
    for key in ("initial_artifacts", "config", "budget"):
        if key in request and request[key] is not None:
            params[key] = _json_object(request[key], key)
    if "max_turns" in request and request["max_turns"] is not None:
        max_turns = request["max_turns"]
        if isinstance(max_turns, bool) or not isinstance(max_turns, int):
            raise TypeError("max_turns must be an integer or null")
        if max_turns < 0:
            raise ValueError("max_turns must be non-negative")
        params["max_turns"] = max_turns
    if "request_id" in request and request["request_id"] is not None:
        params["request_id"] = _text("request_id", request["request_id"])
    return params


def reply_payload(reply: Any) -> dict[str, Any]:
    """Return one detached JSON reply for the remote worker boundary.

    ``ModelReply`` carries provider-native objects in ``raw``; this boundary
    deliberately sends the normalized content, assistant message, tool calls,
    wire state, usage and extra keys and never leaks ``raw`` into JSON.
    """

    if isinstance(reply, Mapping):
        return _json_object(reply, "reply")
    if not isinstance(reply, ModelReply):
        raise TypeError("sampler must return ModelReply or a mapping")
    payload = _json_object(reply.extra, "reply extra")
    payload["content"] = reply.content
    payload["assistant_message"] = _json_object(
        dict(reply.assistant_message), "assistant_message"
    )
    if reply.reasoning is not None:
        payload["reasoning"] = _json_value(reply.reasoning, "reasoning")
    if reply.usage:
        payload["usage"] = _json_object(reply.usage, "usage")
    if reply.finish_reason is not None:
        payload["finish_reason"] = _json_value(reply.finish_reason, "finish_reason")
    if reply.provider_finish_reason is not None:
        payload["provider_finish_reason"] = _json_value(
            reply.provider_finish_reason, "provider_finish_reason"
        )
    if reply.tool_calls:
        payload["tool_calls"] = [
            _json_object(asdict(call), "tool_call") for call in reply.tool_calls
        ]
    if reply.wire_state:
        payload["wire_state"] = _json_object(reply.wire_state, "wire_state")
    return payload


def trajectory_from_dict(value: Any) -> Trajectory:
    """Rebuild a :class:`Trajectory` from one exported JSON object."""

    if not isinstance(value, Mapping):
        raise TypeError("trajectory export must be a JSON object")
    episode = value.get("episode")
    if not isinstance(episode, Mapping):
        raise ValueError("trajectory export is missing its episode")

    def items(name: str, record: type[Any]) -> tuple[Any, ...]:
        raw = value.get(name, ())
        if not isinstance(raw, (list, tuple)):
            raise ValueError(f"trajectory export field {name!r} must be a list")
        built = []
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                raise ValueError(f"trajectory export {name}[{index}] is not object")
            try:
                built.append(record(**dict(item)))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"trajectory export {name}[{index}] is invalid: {exc}"
                ) from exc
        return tuple(built)

    schema_version = value.get("schema_version", "openai4s.science-trajectory.v1")
    if not isinstance(schema_version, str) or not schema_version:
        raise ValueError("trajectory schema_version must be a non-empty string")
    try:
        return Trajectory(
            episode=Episode(**dict(episode)),
            observations=items("observations", Observation),
            decisions=items("decisions", PolicyDecision),
            segments=items("segments", Segment),
            execution_events=items("execution_events", ExecutionEvent),
            rewards=items("rewards", RewardAnnotation),
            costs=items("costs", CostRecord),
            schema_version=schema_version,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid trajectory export: {exc}") from exc


class RolloutClient:
    """Synchronous JSONL client over argv subprocess pipes or text streams."""

    protocol = PROTOCOL

    def __init__(
        self,
        argv: Sequence[str] | None = None,
        *,
        streams: tuple[TextIO, TextIO] | None = None,
        timeout: float | None = 30.0,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        close_streams: bool = True,
    ) -> None:
        if argv is not None and (
            isinstance(argv, (str, bytes))
            or not isinstance(argv, Sequence)
            or not argv
            or any(not isinstance(part, str) or not part for part in argv)
        ):
            raise TypeError("argv must be a non-empty sequence of strings")
        if argv is not None and streams is not None:
            raise ValueError("provide either argv or streams, not both")
        if argv is None and streams is None:
            raise ValueError("provide either argv or streams")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        ):
            raise TypeError("timeout must be a positive number or null")
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive or null")
        if isinstance(max_line_bytes, bool) or not isinstance(max_line_bytes, int):
            raise TypeError("max_line_bytes must be an integer")
        if max_line_bytes <= 0:
            raise ValueError("max_line_bytes must be positive")

        self.timeout = None if timeout is None else float(timeout)
        self.max_line_bytes = max_line_bytes
        self.close_streams = close_streams
        self._lock = threading.RLock()
        self._closed = False
        self._poisoned = False
        self._next_id = 0
        self._frames: queue.Queue[Any] = queue.Queue(maxsize=2)
        self._closing = threading.Event()
        self._stderr: deque[str] = deque(maxlen=32)
        self._process: subprocess.Popen[str] | None = None
        self._stderr_stream: TextIO | None = None

        if argv is not None:
            self._process = subprocess.Popen(
                list(argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=None if env is None else dict(env),
                cwd=cwd,
                shell=False,
            )
            assert self._process.stdin is not None
            assert self._process.stdout is not None
            assert self._process.stderr is not None
            self._reader = cast(TextIO, self._process.stdout)
            self._writer = cast(TextIO, self._process.stdin)
            self._stderr_stream = cast(TextIO, self._process.stderr)
        else:
            assert streams is not None
            reader, writer = streams
            if not callable(getattr(reader, "readline", None)):
                raise TypeError("reader must provide readline()")
            if not callable(getattr(writer, "write", None)) or not callable(
                getattr(writer, "flush", None)
            ):
                raise TypeError("writer must provide write() and flush()")
            self._reader = reader
            self._writer = writer

        self._reader_thread = threading.Thread(
            target=self._read_loop, name="rollout-client-reader", daemon=True
        )
        self._reader_thread.start()
        self._stderr_thread: threading.Thread | None = None
        if self._stderr_stream is not None:
            self._stderr_thread = threading.Thread(
                target=self._stderr_loop,
                name="rollout-client-stderr",
                daemon=True,
            )
            self._stderr_thread.start()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def stderr_tail(self) -> tuple[str, ...]:
        """The last stderr lines, useful when a worker exits unexpectedly."""

        return tuple(self._stderr)

    # -- transport -----------------------------------------------------

    def _publish(self, item: Any) -> None:
        while not self._closing.is_set():
            try:
                self._frames.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    def _read_loop(self) -> None:
        while not self._closing.is_set():
            try:
                line = self._reader.readline(self.max_line_bytes + 1)
            except (OSError, ValueError, UnicodeError):
                self._publish(_END)
                return
            if not line:
                self._publish(_END)
                return
            if len(line.encode("utf-8", "replace")) > self.max_line_bytes:
                self._publish(_OVERSIZED)
                return
            self._publish(line)

    def _stderr_loop(self) -> None:
        assert self._stderr_stream is not None
        while not self._closing.is_set():
            try:
                line = self._stderr_stream.readline(8192)
            except (OSError, ValueError, UnicodeError):
                return
            if not line:
                return
            self._stderr.append(line.rstrip("\n"))

    def _ensure_open(self) -> None:
        if self._closed or self._poisoned:
            raise RolloutClientClosed("rollout client is closed")

    def _shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._closing.set()
        process = self._process
        if process is not None or self.close_streams:
            try:
                self._writer.close()
            except (OSError, ValueError):
                pass
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                    pass
            except OSError:  # pragma: no cover - already dead
                pass
            for stream in (process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except (OSError, ValueError):
                    pass
        elif self.close_streams:
            try:
                self._reader.close()
            except (OSError, ValueError):
                pass
        if (
            (process is not None or self.close_streams)
            and self._reader_thread.is_alive()
            and self._reader_thread is not threading.current_thread()
        ):
            self._reader_thread.join(timeout=2)
        if (
            self._stderr_thread is not None
            and self._stderr_thread.is_alive()
            and self._stderr_thread is not threading.current_thread()
        ):
            self._stderr_thread.join(timeout=2)

    def _poison(self) -> None:
        self._poisoned = True
        self._shutdown()

    def close(self) -> None:
        """Terminate the worker (if owned) and release the transport once."""

        with self._lock:
            self._shutdown()

    def __enter__(self) -> "RolloutClient":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def _request(self, method: str, params: Mapping[str, Any]) -> Any:
        with self._lock:
            self._ensure_open()
            self._next_id += 1
            request_id = self._next_id
            line = _encode_request(request_id, method, params)
            if len(line.encode("utf-8")) > self.max_line_bytes:
                raise ValueError("RPC request exceeds max_line_bytes")
            try:
                self._writer.write(line)
                self._writer.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                self._poison()
                raise RolloutTransportError(
                    f"{method} request could not be sent: {exc}",
                    state_unknown=True,
                ) from exc
            try:
                if self.timeout is None:
                    item = self._frames.get()
                else:
                    item = self._frames.get(timeout=self.timeout)
            except queue.Empty:
                self._poison()
                raise RolloutTimeoutError(
                    f"worker did not reply to {method} within {self.timeout}s",
                    state_unknown=True,
                ) from None
            if item is _END:
                self._poison()
                raise RolloutTransportError(
                    f"worker closed before replying to {method}",
                    state_unknown=True,
                )
            if item is _OVERSIZED:
                self._poison()
                raise RolloutProtocolError("response line exceeds max_line_bytes")
            try:
                return _decode_response(item, request_id)
            except _RemoteFailure as exc:
                raise RolloutRemoteError(
                    exc.code,
                    exc.message,
                    request_id=request_id,
                    response=exc.response,
                ) from None
            except _FrameError as exc:
                self._poison()
                raise RolloutProtocolError(str(exc)) from None

    # -- RPC surface ---------------------------------------------------

    def reset(self, request: Mapping[str, Any]) -> Any:
        return self._request("reset", _reset_params(request))

    def observe(self) -> Any:
        return self._request("observe", {})

    def step(
        self,
        reply: Any,
        *,
        step_id: str,
        expected_turn: int,
        observation: Iterable[Mapping[str, Any]] | None = None,
    ) -> Any:
        step_id = _text("step_id", step_id)
        if isinstance(expected_turn, bool) or not isinstance(expected_turn, int):
            raise TypeError("expected_turn must be an integer")
        if expected_turn < 0:
            raise ValueError("expected_turn must be non-negative")
        params: dict[str, Any] = {
            "reply": reply_payload(reply),
            "step_id": step_id,
            "expected_turn": expected_turn,
        }
        if observation is not None:
            params["observation"] = _messages_payload(observation, "observation")
        return self._request("step", params)

    def finish(self, status: str = "completed") -> Any:
        status = _text("status", status)
        if status == "running":
            raise ValueError("status must be terminal")
        return self._request("finish", {"status": status})

    def export(self, format: str = "json") -> Any:  # noqa: A002 - protocol name
        return self._request("export", {"format": _text("format", format)})

    def release(self) -> Any:
        return self._request("release", {})

    def rewards(
        self,
        *,
        verifier: str | None = None,
        verifier_version: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if verifier is not None:
            params["verifier"] = _text("verifier", verifier)
        if verifier_version is not None:
            params["verifier_version"] = _text("verifier_version", verifier_version)
        return self._request("rewards", params)

    def costs(self) -> Any:
        return self._request("costs", {})

    def recover(self, request_id: str, *, step_id: str | None = None) -> Any:
        params: dict[str, Any] = {"request_id": _text("request_id", request_id)}
        if step_id is not None:
            params["step_id"] = _text("step_id", step_id)
        return self._request("recover", params)

    def result(self, request_id: str) -> Any:
        return self._request("result", {"request_id": _text("request_id", request_id)})

    def step_result(self, request_id: str, step_id: str) -> Any:
        return self._request(
            "step_result",
            {
                "request_id": _text("request_id", request_id),
                "step_id": _text("step_id", step_id),
            },
        )


class AsyncRolloutClient:
    """JSONL client over already-established asyncio streams."""

    protocol = PROTOCOL

    def __init__(
        self,
        reader: Any,
        writer: Any,
        *,
        timeout: float | None = 30.0,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
        close_streams: bool = True,
    ) -> None:
        if not callable(getattr(reader, "readline", None)):
            raise TypeError("reader must provide readline()")
        if not callable(getattr(writer, "write", None)) or not callable(
            getattr(writer, "drain", None)
        ):
            raise TypeError("writer must provide write() and drain()")
        if timeout is not None and (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        ):
            raise TypeError("timeout must be a positive number or null")
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive or null")
        if isinstance(max_line_bytes, bool) or not isinstance(max_line_bytes, int):
            raise TypeError("max_line_bytes must be an integer")
        if max_line_bytes <= 0:
            raise ValueError("max_line_bytes must be positive")
        self._reader = reader
        self._writer = writer
        self.timeout = None if timeout is None else float(timeout)
        self.max_line_bytes = max_line_bytes
        self.close_streams = close_streams
        self._lock = asyncio.Lock()
        self._closed = False
        self._writer_closed = False
        self._next_id = 0

    @property
    def closed(self) -> bool:
        return self._closed

    async def _poison(self) -> None:
        self._closed = True
        await self._close_writer()

    async def _close_writer(self) -> None:
        if self._writer_closed:
            return
        self._writer_closed = True
        if not self.close_streams:
            return
        try:
            close = getattr(self._writer, "close", None)
            if callable(close):
                close()
            wait_closed = getattr(self._writer, "wait_closed", None)
            if callable(wait_closed):
                try:
                    await wait_closed()
                except (OSError, ConnectionError, RuntimeError):
                    pass
        except (OSError, ValueError):
            pass

    async def aclose(self) -> None:
        if self._closed and self._writer_closed:
            return
        self._closed = True
        await self._close_writer()

    async def _request_async(self, method: str, params: Mapping[str, Any]) -> Any:
        async with self._lock:
            if self._closed:
                raise RolloutClientClosed("rollout client is closed")
            self._next_id += 1
            request_id = self._next_id
            line = _encode_request(request_id, method, params)
            encoded = line.encode("utf-8")
            if len(encoded) > self.max_line_bytes:
                raise ValueError("RPC request exceeds max_line_bytes")
            try:
                self._writer.write(encoded)
                if self.timeout is None:
                    await self._writer.drain()
                else:
                    await asyncio.wait_for(self._writer.drain(), timeout=self.timeout)
            except asyncio.TimeoutError as exc:
                await self._poison()
                raise RolloutTimeoutError(
                    f"worker did not accept {method} within {self.timeout}s",
                    state_unknown=True,
                ) from exc
            except (ConnectionError, OSError, RuntimeError) as exc:
                await self._poison()
                raise RolloutTransportError(
                    f"{method} request could not be sent: {exc}",
                    state_unknown=True,
                ) from exc
            try:
                if self.timeout is None:
                    raw = await self._reader.readline()
                else:
                    raw = await asyncio.wait_for(
                        self._reader.readline(), timeout=self.timeout
                    )
            except asyncio.TimeoutError as exc:
                await self._poison()
                raise RolloutTimeoutError(
                    f"worker did not reply to {method} within {self.timeout}s",
                    state_unknown=True,
                ) from exc
            except (ValueError, asyncio.LimitOverrunError) as exc:
                await self._poison()
                raise RolloutProtocolError(
                    "response line exceeds max_line_bytes"
                ) from exc
            except (ConnectionError, OSError) as exc:
                await self._poison()
                raise RolloutTransportError(
                    f"worker connection failed during {method}: {exc}",
                    state_unknown=True,
                ) from exc
            if not raw:
                await self._poison()
                raise RolloutTransportError(
                    f"worker closed before replying to {method}",
                    state_unknown=True,
                )
            if len(raw) > self.max_line_bytes:
                await self._poison()
                raise RolloutProtocolError("response line exceeds max_line_bytes")
            if isinstance(raw, bytes):
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    await self._poison()
                    raise RolloutProtocolError("response is not valid UTF-8") from exc
            else:
                line = str(raw)
            try:
                return _decode_response(line, request_id)
            except _RemoteFailure as exc:
                raise RolloutRemoteError(
                    exc.code,
                    exc.message,
                    request_id=request_id,
                    response=exc.response,
                ) from None
            except _FrameError as exc:
                await self._poison()
                raise RolloutProtocolError(str(exc)) from None

    # -- RPC surface ---------------------------------------------------

    async def reset(self, request: Mapping[str, Any]) -> Any:
        return await self._request_async("reset", _reset_params(request))

    async def observe(self) -> Any:
        return await self._request_async("observe", {})

    async def step(
        self,
        reply: Any,
        *,
        step_id: str,
        expected_turn: int,
        observation: Iterable[Mapping[str, Any]] | None = None,
    ) -> Any:
        step_id = _text("step_id", step_id)
        if isinstance(expected_turn, bool) or not isinstance(expected_turn, int):
            raise TypeError("expected_turn must be an integer")
        if expected_turn < 0:
            raise ValueError("expected_turn must be non-negative")
        params: dict[str, Any] = {
            "reply": reply_payload(reply),
            "step_id": step_id,
            "expected_turn": expected_turn,
        }
        if observation is not None:
            params["observation"] = _messages_payload(observation, "observation")
        return await self._request_async("step", params)

    async def finish(self, status: str = "completed") -> Any:
        status = _text("status", status)
        if status == "running":
            raise ValueError("status must be terminal")
        return await self._request_async("finish", {"status": status})

    async def export(self, format: str = "json") -> Any:  # noqa: A002
        return await self._request_async("export", {"format": _text("format", format)})

    async def release(self) -> Any:
        return await self._request_async("release", {})

    async def rewards(
        self,
        *,
        verifier: str | None = None,
        verifier_version: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
        if verifier is not None:
            params["verifier"] = _text("verifier", verifier)
        if verifier_version is not None:
            params["verifier_version"] = _text("verifier_version", verifier_version)
        return await self._request_async("rewards", params)

    async def costs(self) -> Any:
        return await self._request_async("costs", {})

    async def recover(self, request_id: str, *, step_id: str | None = None) -> Any:
        params: dict[str, Any] = {"request_id": _text("request_id", request_id)}
        if step_id is not None:
            params["step_id"] = _text("step_id", step_id)
        return await self._request_async("recover", params)

    async def result(self, request_id: str) -> Any:
        return await self._request_async(
            "result", {"request_id": _text("request_id", request_id)}
        )

    async def step_result(self, request_id: str, step_id: str) -> Any:
        return await self._request_async(
            "step_result",
            {
                "request_id": _text("request_id", request_id),
                "step_id": _text("step_id", step_id),
            },
        )


class RemoteRolloutWorker:
    """Drive ``reset``/``observe``/``step`` against an external worker.

    The sampler is the trainer's policy.  This class executes no action itself:
    it only samples, forwards the reply, and reads the next observation.  It
    implements the collector's request-dict worker protocol and exposes an
    ``aclose()`` hook so the collector can own the client lifetime.
    """

    def __init__(
        self,
        client: "RolloutClient | AsyncRolloutClient",
        *,
        cancel_confirmed: bool = False,
    ) -> None:
        if not isinstance(client, (RolloutClient, AsyncRolloutClient)):
            raise TypeError("client must be a RolloutClient or AsyncRolloutClient")
        self.client = client
        # Closing the transport does not prove that a deployment reached over
        # SSH, a scheduler, or another machine stopped executing.  A trusted
        # local deployment may opt in; the conservative default lets the
        # collector report an unconfirmed cancellation instead of releasing
        # capacity optimistically.
        self.cancel_confirmed = cancel_confirmed
        self.cleanup_error: BaseException | None = None
        self.last_request_id: str | None = None

    async def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self.client, name)
        if inspect.iscoroutinefunction(method):
            return await method(*args, **kwargs)
        return await asyncio.to_thread(method, *args, **kwargs)

    async def run_async(self, request: Mapping[str, Any], sampler: Any) -> Trajectory:
        params = _reset_params(request)
        request_id = params.get("request_id")
        if not isinstance(request_id, str):
            request_id = "remote-" + uuid.uuid4().hex
            params["request_id"] = request_id
        self.last_request_id = request_id
        reset = await self._call("reset", params)
        response = _response_object(reset, "reset")
        if response.get("status") == "recovery_required":
            raise RolloutTransportError(
                "existing run requires recovery; actions will not be replayed",
                state_unknown=True,
            )
        turn = _turn(response.get("turn", 0), "reset turn")
        max_turns = response.get("max_turns")
        if max_turns is not None:
            max_turns = _turn(max_turns, "max_turns")
        observation = _messages_payload(response.get("observation", ()), "observation")
        tools = _tools_payload(response.get("tools", ()))
        terminal = _is_terminal(response)

        try:
            while not terminal:
                sampled = await _sample_remote(sampler, observation, tools)
                reply = reply_payload(sampled)
                step_id = f"{request_id}:turn-{turn}"
                stepped = await self._call(
                    "step",
                    reply,
                    step_id=step_id,
                    expected_turn=turn,
                    observation=observation,
                )
                step_response = _response_object(stepped, "step")
                terminal = _is_terminal(step_response)
                if terminal:
                    break
                observed = step_response.get(
                    "next_observation", step_response.get("observation", ())
                )
                observation = _messages_payload(observed, "next observation")
                tools = _tools_payload(
                    step_response.get("next_tools", step_response.get("tools", ()))
                )
                state = step_response.get("state")
                if isinstance(state, Mapping) and "turn" in state:
                    next_turn = _turn(state["turn"], "state turn")
                else:
                    next_turn = turn + 1
                if next_turn <= turn:
                    raise RolloutProtocolError("worker returned a non-advancing turn")
                turn = next_turn
                if max_turns is not None and turn >= max_turns:
                    raise RolloutProtocolError(
                        "worker returned a non-terminal response at max_turns"
                    )
        except BaseException:
            # A lost step response or cancelled waiter cannot prove whether
            # scientific execution settled. Never send a synthetic finish that
            # turns that uncertainty into a fabricated terminal result.
            raise

        exported = await self._call("export", "json")
        trajectory = _trajectory_from_export(exported)
        try:
            await self._call("release")
        except BaseException as exc:  # cleanup must not erase a confirmed result
            self.cleanup_error = exc
        return trajectory

    async def aclose(self) -> None:
        closer = getattr(self.client, "aclose", None)
        if callable(closer):
            await closer()
            return
        closer = getattr(self.client, "close", None)
        if callable(closer):
            if inspect.iscoroutinefunction(closer):
                await closer()
            else:
                await asyncio.to_thread(closer)


def _response_object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RolloutProtocolError(f"{name} response must be a JSON object")
    return value


def _turn(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RolloutProtocolError(f"{name} must be a non-negative integer")
    return value


def _is_terminal(response: Mapping[str, Any]) -> bool:
    return bool(
        response.get("terminal") is True
        or response.get("status") == "terminal"
        or response.get("stop_reason") is not None
    )


def _trajectory_from_export(value: Any) -> Trajectory:
    if isinstance(value, Mapping) and "content" in value:
        content = value.get("content")
        if not isinstance(content, str):
            raise RolloutProtocolError("export content must be a string")
        try:
            decoded = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RolloutProtocolError("export content is not valid JSON") from exc
        return trajectory_from_dict(decoded)
    return trajectory_from_dict(value)


async def _sample_remote(
    sampler: Any,
    observation: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> Any:
    """Call a sync or async sampler, offloading purely synchronous work."""

    # Imported lazily so the client module does not depend on the rollout
    # module's import graph at module load time.
    from .rollout import _sample, _sample_async

    async_callback = getattr(sampler, "sample_async", None)
    if callable(async_callback):
        return await _sample_async(sampler, tuple(observation), tuple(tools))
    callback = sampler.sample if hasattr(sampler, "sample") else sampler
    if inspect.iscoroutinefunction(callback):
        return await _sample_async(sampler, tuple(observation), tuple(tools))
    sampled = await asyncio.to_thread(
        _sample, sampler, tuple(observation), tuple(tools)
    )
    if inspect.isawaitable(sampled):
        sampled = await sampled
    return sampled


__all__ = [
    "AsyncRolloutClient",
    "RemoteRolloutWorker",
    "RolloutClient",
    "RolloutClientClosed",
    "RolloutClientError",
    "RolloutProtocolError",
    "RolloutRemoteError",
    "RolloutTimeoutError",
    "RolloutTransportError",
    "reply_payload",
    "trajectory_from_dict",
]
