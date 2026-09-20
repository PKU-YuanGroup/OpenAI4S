"""Local loopback TypeSafe System One fake endpoint.

Deterministic answers, optional scripted answers, and fault injection. Binds
loopback only. The JSONL request log records the body and never the
Authorization header.

In-process::

    url, stop = start_fake(port=0)
    ...
    stop()

CLI::

    python -m harness.providers.typesafe_fake --port N [--host 127.0.0.1]
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_MAX_BODY_BYTES = 2 * 1024 * 1024
_PATH = "/v1/systemone"


class FakeState:
    """Mutable per-server options. Tests may inspect ``captures`` in-process."""

    def __init__(
        self,
        *,
        script: Mapping[str, Any] | None = None,
        fail_status: int | None = None,
        fail_count: int = 0,
        latency_ms: int = 0,
        log_path: str | Path | None = None,
        retry_after: str | None = "0",
        captures: list[dict[str, Any]] | None = None,
        redirect_to: str | None = None,
        redirect_status: int = 302,
        raw_body: bytes | None = None,
        oversize_bytes: int = 0,
    ) -> None:
        self.script = dict(script) if script else {}
        self.fail_status = fail_status
        self.fail_count = int(fail_count)
        self.latency_ms = int(latency_ms)
        self.log_path = Path(log_path) if log_path is not None else None
        self.retry_after = retry_after
        self.captures = captures if captures is not None else []
        self.redirect_to = redirect_to
        self.redirect_status = int(redirect_status)
        self.raw_body = raw_body
        self.oversize_bytes = int(oversize_bytes)
        self.lock = threading.Lock()


def _require_loopback_host(host: str) -> str:
    if host in ("127.0.0.1", "::1"):
        return host
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(
            f"typesafe fake host must be a loopback address, not {host!r}"
        ) from exc
    if not address.is_loopback:
        raise ValueError(f"typesafe fake refuses to bind non-loopback address {host!r}")
    return host


def parse_fail_spec(spec: str) -> tuple[int, int]:
    """Parse ``429:2`` into ``(status, count)``."""

    status_text, separator, count_text = spec.partition(":")
    if not separator:
        count_text = "1"
    try:
        status = int(status_text)
        count = int(count_text)
    except ValueError as exc:
        raise ValueError(f"invalid --fail spec: {spec!r}") from exc
    if status < 100 or status > 599 or count < 0:
        raise ValueError(f"invalid --fail spec: {spec!r}")
    return status, count


def _choice_probabilities(names: list[str]) -> dict[str, float]:
    if not names:
        return {}
    if len(names) == 1:
        return {names[0]: 1.0}
    leftover = 0.4
    share = leftover / float(len(names) - 1)
    probs: dict[str, float] = {names[0]: 0.6}
    rest = names[1:]
    for index, name in enumerate(rest):
        if index == len(rest) - 1:
            probs[name] = round(1.0 - sum(probs.values()), 12)
        else:
            probs[name] = share
    return probs


def default_answer(question: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministic answer for one System One question object."""

    qtype = question.get("type")
    if qtype == "noul":
        return {"type": "noul", "noul": 0.5}
    if qtype == "choice":
        criteria = question.get("criteria")
        if not isinstance(criteria, Mapping) or len(criteria) < 2:
            raise ValueError("choice criteria")
        names = [str(name) for name in criteria]
        probs = _choice_probabilities(names)
        return {
            "type": "choice",
            "choice": names[0],
            "probabilities": probs,
            "confidence": probs[names[0]],
        }
    if qtype == "score":
        levels = question.get("criteria")
        if not isinstance(levels, list) or len(levels) < 2:
            raise ValueError("score criteria")
        n_levels = len(levels)
        mid = (n_levels - 1) // 2
        return {
            "type": "score",
            "score": float(mid),
            "legend": {str(index): str(levels[index]) for index in range(n_levels)},
            "probabilities": {
                str(index): (1.0 if index == mid else 0.0) for index in range(n_levels)
            },
            "confidence": 1.0,
        }
    raise ValueError("unknown question type")


def _instructions_text(question: Mapping[str, Any]) -> str:
    instructions = question.get("instructions")
    if isinstance(instructions, str):
        return instructions
    try:
        return json.dumps(instructions, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(instructions)


def _scripted_answer(
    qid: str, question: Mapping[str, Any], script: Mapping[str, Any]
) -> dict[str, Any] | None:
    if not script:
        return None
    ids = script.get("ids")
    if isinstance(ids, Mapping) and qid in ids:
        item = ids[qid]
        return dict(item) if isinstance(item, Mapping) else None
    if qid in script and qid not in ("ids", "instructions"):
        item = script[qid]
        if isinstance(item, Mapping) and "type" in item:
            return dict(item)
    text = _instructions_text(question)
    ins_map = script.get("instructions")
    if isinstance(ins_map, Mapping):
        for needle, item in ins_map.items():
            if needle and str(needle) in text and isinstance(item, Mapping):
                return dict(item)
    for needle, item in script.items():
        if needle in ("ids", "instructions", qid):
            continue
        if str(needle) and str(needle) in text and isinstance(item, Mapping):
            return dict(item)
    return None


def _append_log(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _make_handler(state: FakeState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path.rstrip("/") != _PATH:
                self._send(404, {"error": "not found"})
                return
            length_header = self.headers.get("Content-Length", "0")
            try:
                length = int(length_header)
            except ValueError:
                self._send(422, {"error": "invalid content-length"})
                return
            if length < 0 or length > _MAX_BODY_BYTES:
                self._send(422, {"error": "body too large"})
                return
            raw = self.rfile.read(length) if length else b""
            payload: Any
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None

            authorization = self.headers.get("Authorization")
            safe_headers = {
                name: value
                for name, value in self.headers.items()
                if name.lower() != "authorization"
            }
            capture = {
                "path": parsed.path,
                "body": payload,
                "authorization": authorization,
                "headers": safe_headers,
            }
            with state.lock:
                state.captures.append(capture)
                log_path = state.log_path
            if log_path is not None:
                _append_log(
                    log_path,
                    {"path": parsed.path, "body": payload, "headers": safe_headers},
                )

            token = ""
            if isinstance(authorization, str) and authorization.lower().startswith(
                "bearer "
            ):
                token = authorization[7:].strip()
            if not token:
                self._send(401, {"error": "missing bearer token"})
                return

            if state.latency_ms > 0:
                time.sleep(state.latency_ms / 1000.0)

            with state.lock:
                if state.fail_status is not None and state.fail_count > 0:
                    status = int(state.fail_status)
                    state.fail_count -= 1
                    retry_after = state.retry_after
                else:
                    status = 0
                    retry_after = None
            if status:
                extra = {}
                if retry_after is not None and status in (429, 503, 529):
                    extra["Retry-After"] = str(retry_after)
                self._send(status, {"error": f"injected {status}"}, extra=extra)
                return

            if state.redirect_to:
                self._send_redirect(state.redirect_status, state.redirect_to)
                return
            if state.oversize_bytes > 0:
                self._send_bytes(
                    200,
                    b"x" * min(state.oversize_bytes, 64),
                    content_length=state.oversize_bytes,
                )
                return
            if state.raw_body is not None:
                self._send_bytes(200, state.raw_body)
                return

            if not isinstance(payload, dict):
                self._send(422, {"error": "body must be a JSON object"})
                return
            questions = payload.get("questions")
            if not isinstance(questions, dict) or not questions:
                self._send(422, {"error": "questions required"})
                return
            answers: dict[str, Any] = {}
            try:
                for qid, question in questions.items():
                    if not isinstance(question, Mapping):
                        raise ValueError("question")
                    scripted = _scripted_answer(str(qid), question, state.script)
                    answers[str(qid)] = scripted or default_answer(question)
            except (TypeError, ValueError):
                self._send(422, {"error": "malformed question"})
                return
            model = payload.get("model")
            if not isinstance(model, str) or not model.strip():
                model = "jev-1.13.0"
            self._send(
                200,
                {
                    "model": model,
                    "answers": answers,
                    "usage": {"input_tokens": 10, "output_tokens": 4},
                },
            )

        def do_GET(self) -> None:  # noqa: N802
            self._send(405, {"error": "POST required"})

        def _send(
            self,
            status: int,
            payload: Mapping[str, Any],
            *,
            extra: Mapping[str, str] | None = None,
        ) -> None:
            body = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if extra:
                for name, value in extra.items():
                    self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(
            self,
            status: int,
            body: bytes,
            *,
            content_length: int | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header(
                "Content-Length",
                str(len(body) if content_length is None else content_length),
            )
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _send_redirect(self, status: int, location: str) -> None:
            self.send_response(status)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def _server_class(host: str) -> type[ThreadingHTTPServer]:
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    if host == "::1":
        Server.address_family = socket.AF_INET6
    return Server


def _public_url(host: str, port: int) -> str:
    if host == "::1":
        return f"http://[::1]:{port}{_PATH}"
    return f"http://127.0.0.1:{port}{_PATH}"


def start_fake(
    port: int = 0,
    *,
    host: str = "127.0.0.1",
    script: Mapping[str, Any] | None = None,
    fail: str | None = None,
    latency_ms: int = 0,
    log_path: str | Path | None = None,
    retry_after: str | None = "0",
    captures: list[dict[str, Any]] | None = None,
    redirect_to: str | None = None,
    redirect_status: int = 302,
    raw_body: bytes | None = None,
    oversize_bytes: int = 0,
) -> tuple[str, Callable[[], None]]:
    """Start a loopback fake and return ``(url, stop)``."""

    host = _require_loopback_host(host)
    fail_status: int | None = None
    fail_count = 0
    if fail:
        fail_status, fail_count = parse_fail_spec(fail)
    state = FakeState(
        script=script,
        fail_status=fail_status,
        fail_count=fail_count,
        latency_ms=latency_ms,
        log_path=log_path,
        retry_after=retry_after,
        captures=captures,
        redirect_to=redirect_to,
        redirect_status=redirect_status,
        raw_body=raw_body,
        oversize_bytes=oversize_bytes,
    )
    server = _server_class(host)((host, int(port)), _make_handler(state))
    thread = threading.Thread(
        target=lambda: server.serve_forever(poll_interval=0.01),
        daemon=True,
        name="typesafe-fake",
    )
    thread.start()
    bound_host, bound_port = server.server_address[:2]
    url = _public_url(str(bound_host), int(bound_port))
    stopped = threading.Event()

    def stop() -> None:
        if stopped.is_set():
            return
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    return url, stop


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harness.providers.typesafe_fake")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--script", dest="script_path")
    parser.add_argument("--fail")
    parser.add_argument("--latency-ms", type=int, default=0)
    parser.add_argument("--log", dest="log_path")
    args = parser.parse_args(argv)
    host = _require_loopback_host(args.host)
    script = None
    if args.script_path:
        script = json.loads(Path(args.script_path).read_text(encoding="utf-8"))
        if not isinstance(script, dict):
            raise SystemExit("script JSON must be an object")
    fail_status: int | None = None
    fail_count = 0
    if args.fail:
        fail_status, fail_count = parse_fail_spec(args.fail)
    state = FakeState(
        script=script,
        fail_status=fail_status,
        fail_count=fail_count,
        latency_ms=args.latency_ms,
        log_path=args.log_path,
    )
    server = _server_class(host)((host, int(args.port)), _make_handler(state))
    bound_host, bound_port = server.server_address[:2]
    print(_public_url(str(bound_host), int(bound_port)), flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


__all__ = [
    "FakeState",
    "default_answer",
    "parse_fail_spec",
    "start_fake",
]


if __name__ == "__main__":
    raise SystemExit(main())
