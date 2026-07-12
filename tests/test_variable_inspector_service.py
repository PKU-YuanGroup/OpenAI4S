from __future__ import annotations

import threading
from types import SimpleNamespace

from openai4s.server.variable_inspector import VariableInspectorService


class _Kernel:
    def __init__(self, payload=None, *, fail=False) -> None:
        self.payload = payload or {"variables": []}
        self.fail = fail
        self.calls = 0

    def is_alive(self):
        return True

    def inspect_variables(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("private worker detail")
        return self.payload


class _Kernels:
    def __init__(self, kernel) -> None:
        self._lease = SimpleNamespace(
            kernel=kernel,
            generation_id="generation-live",
        )

    def lease(self, language):
        return self._lease if language == "python" else None

    def inspect_variables(self, language, *, limit=200):
        assert language == "python" and limit == 200
        return self._lease.kernel.inspect_variables()


def _service(state, *, recovering=False, generation=None, owner=None):
    return VariableInspectorService(
        state_for=lambda _root: state,
        execution_snapshot=lambda _root: {"owner": owner},
        recovering=lambda _root: recovering,
        latest_generation=lambda *_args, **_kwargs: generation,
        latest_state_revision=lambda _root: 17,
    )


def test_active_inspection_is_narrow_and_does_not_enter_cell_state():
    class _NeverStringify:
        def __str__(self):
            raise AssertionError("unsafe preview was stringified")

    kernel = _Kernel(
        {
            "variables": [
                {
                    "name": "score",
                    "type": "float",
                    "kind": "scalar",
                    "length": 1,
                    "preview": 0.93,
                    "fingerprint": "a" * 64,
                    "raw": "must be dropped",
                },
                {
                    "name": "model",
                    "type": "CustomModel",
                    "preview": _NeverStringify(),
                    "fingerprint": "not-a-hash",
                },
            ],
            "private": {"workspace": "secret"},
        }
    )
    state = SimpleNamespace(turn_lock=threading.Lock(), kernels=_Kernels(kernel))

    result = _service(state).inspect("root", "python")

    assert result == {
        "available": True,
        "root_frame_id": "root",
        "branch_id": "root",
        "language": "python",
        "state": "active",
        "generation_id": "generation-live",
        "state_revision": 17,
        "variables": [
            {
                "name": "score",
                "type": "float",
                "kind": "scalar",
                "length": 1,
                "preview": 0.93,
                "fingerprint": "a" * 64,
            },
            {"name": "model", "type": "CustomModel"},
        ],
        "truncated": False,
    }
    assert kernel.calls == 1


def test_busy_restoring_ended_and_never_started_fail_without_protocol_reads():
    kernel = _Kernel()
    state = SimpleNamespace(turn_lock=threading.Lock(), kernels=_Kernels(kernel))

    state.turn_lock.acquire()
    try:
        assert _service(state).inspect("root", "python")["state"] == "busy"
    finally:
        state.turn_lock.release()
    assert (
        _service(state, owner={"execution_id": "exec"}).inspect("root", "python")[
            "state"
        ]
        == "busy"
    )
    assert (
        _service(state, recovering=True).inspect("root", "python")["state"]
        == "restoring"
    )
    assert kernel.calls == 0

    ended = _service(
        None,
        generation={"generation_id": "generation-ended"},
    ).inspect("root", "python")
    assert ended["available"] is False
    assert ended["state"] == "ended"
    assert ended["generation_id"] == "generation-ended"
    assert _service(None).inspect("root", "r")["state"] == "not_started"


def test_protocol_failure_is_generic_and_invalid_language_is_rejected():
    kernel = _Kernel(fail=True)
    state = SimpleNamespace(turn_lock=threading.Lock(), kernels=_Kernels(kernel))
    result = _service(state).inspect("root", "python")
    assert result["state"] == "failed"
    assert result["reason"] == "variable inspection failed closed"
    assert "private worker detail" not in repr(result)

    try:
        _service(state).inspect("root", "javascript")
    except ValueError as error:
        assert str(error) == "language must be python or r"
    else:
        raise AssertionError("unsupported languages must fail closed")
