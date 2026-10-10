"""Strict, bounded newline-delimited JSON. No backend or host imports."""

import json
import math
import re

from . import PROTOCOL_VERSION

MAX_FRAME_BYTES = 8 * 1024 * 1024
OPS = frozenset({"hello", "describe", "open", "execute", "query", "stop", "close"})


class ProtocolError(ValueError):
    """Malformed wire data; messages never include the offending payload."""


class BackendError(Exception):
    """An intentional, already-redacted backend failure."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _reject_constant(value):
    raise ProtocolError("non-finite JSON number")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ProtocolError("non-finite JSON number")
    return number


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate JSON key")
        result[key] = value
    return result


_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")


def validate_frame(frame, *, response=False):
    if not isinstance(frame, dict):
        raise ProtocolError("frame must be an object")
    if type(frame.get("v")) is not int or frame["v"] != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version")
    # A closed alphabet: an id must always survive being echoed back, which a
    # lone surrogate (valid JSON, not encodable UTF-8) would not.
    if not isinstance(frame.get("id"), str) or not _ID.fullmatch(frame["id"]):
        raise ProtocolError("invalid request id")
    if response:
        if type(frame.get("ok")) is not bool:
            raise ProtocolError("response must contain a boolean ok")
        field = "result" if frame["ok"] else "error"
        if set(frame) != {"v", "id", "ok", field} or not isinstance(frame[field], dict):
            raise ProtocolError("invalid response envelope")
        if not frame["ok"]:
            error = frame["error"]
            if set(error) != {"code", "message"} or not all(
                isinstance(v, str) for v in error.values()
            ):
                raise ProtocolError("invalid error envelope")
    elif (
        set(frame) != {"v", "id", "op", "args"}
        or not isinstance(frame["op"], str)
        or not isinstance(frame["args"], dict)
    ):
        raise ProtocolError("invalid operation or arguments")
    return frame


def decode_frame(data, *, response=False, max_frame_bytes=MAX_FRAME_BYTES):
    if len(data) > max_frame_bytes:
        raise ProtocolError("frame exceeds byte limit")
    if not data.endswith(b"\n") or b"\n" in data[:-1]:
        raise ProtocolError("frame must be one complete line")
    try:
        frame = json.loads(
            data.decode("utf-8"),
            parse_constant=_reject_constant,
            parse_float=_finite_float,
            object_pairs_hook=_object,
        )
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError("invalid JSON frame") from None
    return validate_frame(frame, response=response)


def encode_frame(frame, *, response=False, max_frame_bytes=MAX_FRAME_BYTES):
    validate_frame(frame, response=response)
    try:
        data = (
            json.dumps(
                frame, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            )
            + "\n"
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProtocolError("frame is not JSON serializable") from None
    if len(data) > max_frame_bytes:
        raise ProtocolError("frame exceeds byte limit")
    return data


def failed_receipt(
    command_id, code, message, *, status="failed", sim_time=0.0, step_index=0
):
    return {
        "provider_command_id": command_id,
        "applied": False,
        "status": status,
        "error": {"code": code, "message": message},
        "raw": {"terminated": False, "truncated": False},
        "end_reason": None,
        "sim_time": sim_time,
        "step_index": step_index,
        "observation": None,
        "evaluation": None,
    }
