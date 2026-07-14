"""Host API contract tests (PR 10).

Locks the CURRENT HostDispatcher behavior before any extraction of
host_dispatch.py: the unknown-method failure, the single-key {"error": ...}
soft-fail shape, host.submit_output completion semantics, the SDK<->dispatcher
wire codec, and SDK/dispatcher method parity. All offline — the LLM is never
called and the store lives in the per-test tmp data dir.
"""
import re
from pathlib import Path

import pytest

from openai4s.config import get_config
from openai4s.host_dispatch import HostDispatcher, build_dispatcher
from openai4s.kernel import Kernel
from openai4s.sdk.host import decode_args, encode_args
from openai4s.tools.registry import BUILTIN_CONTROL_HOST_METHODS

_REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def dispatcher():
    return build_dispatcher(get_config())


# --- unknown method --------------------------------------------------------


def test_unknown_method_raises_value_error(dispatcher):
    """A method with no _m_ handler is a hard ValueError host-side."""
    with pytest.raises(ValueError, match="unknown host method"):
        dispatcher("definitely_not_a_method", [])


def test_unknown_method_soft_fails_inside_kernel(dispatcher):
    """Through the full stack (worker -> manager -> dispatcher) the same
    unknown method surfaces IN the cell as a catchable RuntimeError — the
    kernel and namespace stay alive."""
    with Kernel(dispatcher=dispatcher) as k:
        r = k.execute(
            "try:\n"
            "    host._call('definitely_not_a_method', [])\n"
            "except RuntimeError as e:\n"
            "    print('caught:', 'unknown host method' in str(e))"
        )
        assert r["error"] is None
        assert r["stdout"].strip() == "caught: True"
        assert k.is_alive()


# --- host.submit_output completion semantics -------------------------------


def test_submit_output_valid_sets_last_output(dispatcher):
    assert dispatcher.last_output is None
    res = dispatcher(
        "submit_output",
        [{"output": {"answer": 42}, "completion_bullets": ["Computed the answer"]}],
    )
    assert res == {"status": "ok"}
    assert dispatcher.last_output == {
        "output": {"answer": 42},
        "completion_bullets": ["Computed the answer"],
    }


@pytest.mark.parametrize(
    "bullets, msg_fragment",
    [
        ([], "list of 1-4 items"),
        (["Did a"] * 5, "list of 1-4 items"),
        ([""], "non-empty string"),
        ([42], "non-empty string"),
        (["Run the analysis"], "past-tense verb"),
    ],
)
def test_submit_output_rejects_bad_bullets(dispatcher, bullets, msg_fragment):
    """Invalid completion_bullets return the single-key {'error': ...}
    soft-fail dict and must NOT set last_output (the task is not complete)."""
    res = dispatcher("submit_output", [{"output": {}, "completion_bullets": bullets}])
    assert set(res) == {"error"}
    assert msg_fragment in res["error"]
    assert dispatcher.last_output is None


def test_submit_output_schema_validation_soft_fails(dispatcher):
    schema = {"type": "object", "required": ["x"]}
    # missing required field -> soft-fail, not completed
    res = dispatcher(
        "submit_output",
        [
            {
                "output": {"y": 1},
                "completion_bullets": ["Computed it"],
                "output_schema": schema,
            }
        ],
    )
    assert set(res) == {"error"}
    assert "missing required field 'x'" in res["error"]
    assert dispatcher.last_output is None
    # wrong top-level type -> soft-fail
    res = dispatcher(
        "submit_output",
        [
            {
                "output": [1, 2],
                "completion_bullets": ["Computed it"],
                "output_schema": schema,
            }
        ],
    )
    assert set(res) == {"error"}
    assert "must be an object" in res["error"]
    # conforming output -> completed
    res = dispatcher(
        "submit_output",
        [
            {
                "output": {"x": 1},
                "completion_bullets": ["Computed it"],
                "output_schema": schema,
            }
        ],
    )
    assert res == {"status": "ok"}
    assert dispatcher.last_output["output"] == {"x": 1}


# --- wire codec (SDK snake_case <-> wire camelCase) -------------------------


def test_wire_codec_encode_drops_none_and_camelcases():
    out = encode_args(
        [{"completion_bullets": ["Did x"], "output_schema": None, "output": {"a": 1}}]
    )
    assert out == [{"completionBullets": ["Did x"], "output": {"a": 1}}]


def test_wire_codec_decode_restores_snake_case_top_level_only():
    out = decode_args([{"completionBullets": ["Did x"], "output": {"innerKey": 1}}])
    # top-level keys back to snake_case; nested payloads untouched
    assert out == [{"completion_bullets": ["Did x"], "output": {"innerKey": 1}}]


def test_wire_codec_passes_non_dict_args_through():
    assert encode_args(["v-123", 7]) == ["v-123", 7]
    assert decode_args(["v-123", 7]) == ["v-123", 7]


# --- SDK <-> dispatcher parity ----------------------------------------------


def test_every_sdk_host_call_has_a_dispatch_route():
    """Every SDK wire method must resolve through a legacy or native route."""
    src = (_REPO / "openai4s" / "sdk" / "host.py").read_text(encoding="utf-8")
    names = sorted(set(re.findall(r'self\._call\(\s*"([A-Za-z0-9_]+)"', src)))
    assert len(names) >= 60  # the SDK surface as of PR 10
    missing = [
        name
        for name in names
        if not hasattr(HostDispatcher, f"_m_{name}")
        and name not in BUILTIN_CONTROL_HOST_METHODS
    ]
    assert missing == [], f"SDK methods without dispatcher routes: {missing}"
