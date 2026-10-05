"""One process, one session, one reader. Receipt retention is process-local."""

import sys
import traceback
from collections import OrderedDict

from . import PROTOCOL_VERSION
from .protocol import (
    MAX_FRAME_BYTES,
    BackendError,
    ProtocolError,
    decode_frame,
    encode_frame,
    failed_receipt,
)


class Server:
    def __init__(self, backend):
        self.backend = backend
        self.opened = False
        # Share the adapter cache so rejected commands and queries use the same 256 slots.
        self.receipts = getattr(backend, "receipts", OrderedDict())
        self.fences = {}
        self.resource_sets = {}
        self.sim_time = 0.0
        self.step_index = 0

    def dispatch(self, op, args):
        keys = {
            "hello": set(),
            "describe": {"profile"},
            "open": {"profile", "seed", "options", "expected_capability_revision"},
            "execute": {"provider_command_id", "command", "fencing_tokens"},
            "query": {"provider_command_id"},
            "stop": {"reason"},
            "close": set(),
        }
        if op not in keys:
            raise ProtocolError("unknown operation")
        if set(args) != keys[op]:
            raise BackendError("invalid_parameters", "invalid operation arguments")
        if op == "hello":
            return {
                "protocol": PROTOCOL_VERSION,
                "backend": self.backend.name,
                "backend_version": self.backend.version,
            }
        if op == "describe":
            return self.backend.describe(**args)
        if op == "close":
            self.backend.close()
            return {"closed": True}
        if op == "open":
            if self.opened:
                raise BackendError("invalid_parameters", "a session was already opened")
            result = self.backend.open(**args)
            self.opened = True
            self.sim_time = result["observation"]["sim_time"]
            self.resource_sets = {
                c["capability_id"]: set(c["resources"])
                for c in result["descriptor"]["capabilities"]
            }
            return result
        if not self.opened:
            raise BackendError("run_not_found", "no open session")
        if op == "stop":
            return self.backend.stop(**args)
        command_id = args["provider_command_id"]
        if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
            raise BackendError("invalid_parameters", "invalid provider command id")
        if command_id in self.receipts:
            self.receipts.move_to_end(command_id)
            return self.receipts[command_id]
        if op == "query":
            return {"known": False}
        tokens = args["fencing_tokens"]
        if (
            not isinstance(args["command"], dict)
            or not isinstance(tokens, dict)
            or any(
                not isinstance(k, str) or type(v) is not int or v < 0
                for k, v in tokens.items()
            )
        ):
            raise BackendError(
                "invalid_parameters", "invalid command or fencing tokens"
            )
        required = self.resource_sets.get(args["command"].get("capability_id"))
        if required is not None and set(tokens) != required:
            raise BackendError(
                "invalid_parameters",
                "fencing tokens must cover the capability resources",
            )
        stale = any(
            token < self.fences.get(resource, -1) for resource, token in tokens.items()
        )
        # Remember every observed maximum, including other resources of a stale batch.
        for resource, token in tokens.items():
            self.fences[resource] = max(token, self.fences.get(resource, -1))
        if stale:
            result = failed_receipt(
                command_id,
                "resource_busy",
                "stale resource fencing token",
                sim_time=self.sim_time,
                step_index=self.step_index,
            )
        else:
            result = self.backend.execute(**args)
        if result["applied"]:
            self.sim_time = result["sim_time"]
            self.step_index = result["step_index"]
        self.receipts[command_id] = result
        if len(self.receipts) > 256:
            self.receipts.popitem(last=False)
        return result

    def serve(self, source, sink):
        try:
            while True:
                data = source.readline(MAX_FRAME_BYTES + 1)
                if not data:
                    return
                request_id = "invalid"
                close = False
                try:
                    frame = decode_frame(data)
                    request_id = frame["id"]
                    result = self.dispatch(frame["op"], frame["args"])
                    response = {"v": 1, "id": request_id, "ok": True, "result": result}
                    close = frame["op"] == "close"
                except (ProtocolError, BackendError) as exc:
                    # Invalid framing is unrecoverable: do not consume the rest of an oversized line.
                    response = {
                        "v": 1,
                        "id": request_id,
                        "ok": False,
                        "error": {
                            "code": getattr(exc, "code", "provider_protocol_error"),
                            "message": str(exc),
                        },
                    }
                    close = isinstance(exc, ProtocolError)
                except Exception as exc:
                    # Arbitrary exception text and source lines can embed simulator truth.
                    # Emit stack locations only, never locals, source, or exception values.
                    print("Backend traceback (values redacted):", file=sys.stderr)
                    for item in traceback.extract_tb(exc.__traceback__):
                        print(f"  {item.name}:{item.lineno}", file=sys.stderr)
                    message = f"{type(exc).__name__}: backend operation failed"
                    print(message, file=sys.stderr)
                    response = {
                        "v": 1,
                        "id": request_id,
                        "ok": False,
                        "error": {
                            "code": "provider_protocol_error",
                            "message": message,
                        },
                    }
                try:
                    encoded = encode_frame(response, response=True)
                except ProtocolError:
                    encoded = encode_frame(
                        {
                            "v": 1,
                            "id": request_id,
                            "ok": False,
                            "error": {
                                "code": "provider_protocol_error",
                                "message": "provider response exceeds wire limits",
                            },
                        },
                        response=True,
                    )
                    close = True
                sink.write(encoded)
                sink.flush()
                if close:
                    return
        finally:
            self.backend.close()
