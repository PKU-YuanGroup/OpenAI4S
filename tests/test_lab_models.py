"""Offline closed-schema Lab contract regressions."""

import ast
import dataclasses
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

from openai4s.lab import models as m


def examples():
    quantity = m.Quantity(200.0, "mL")
    parameter = m.ParameterSpec("mL", (200.0, 400.0))
    capability = m.Capability(
        "transfer:a->b",
        "transfer_liquid",
        m.CapabilityScope.SHARED,
        "a",
        "b",
        {"volume": parameter},
        m.SideEffect.MOVES_MATERIAL,
        ("a", "b"),
        ("layers",),
        False,
        "1",
    )
    resource = m.ResourceSpec("a", "vessel", "Vessel A")
    spec = m.ObservationChannelSpec(
        "layers",
        m.ChannelKind.ARRAY,
        (2, 2),
        "dimensionless",
        m.ChannelSource.SIMULATED_SENSOR,
        True,
        "Visible layers",
        axes=(m.ChannelAxis("resource", ("a", "b")), m.ChannelAxis("layer_px")),
    )
    descriptor = m.DeviceDescriptor(
        m.CONTRACT,
        "fake.device",
        m.RunMode.SIMULATION,
        "fake",
        "toy",
        {"package": "fake", "version": "1", "source_sha": "", "adapter_version": "1"},
        (resource, m.ResourceSpec("b", "vessel", "B")),
        (capability,),
        m.capability_revision((capability,)),
        (spec,),
        {"max_steps": 50},
        {"supported": True, "semantics": "end_session"},
        {"unit": "model_time", "wall_clock_equivalent": None},
        {"status": "unverified", "evidence": None},
        (),
    )
    request = m.CommandRequest(
        "labrun-example",
        "transfer_liquid",
        "a",
        "b",
        {"volume": quantity},
        0,
        "request-1",
    )
    normalized = m.NormalizedCommand(
        **{f.name: getattr(request, f.name) for f in dataclasses.fields(request)},
        capability_id=capability.capability_id,
    )
    dispatch = m.Dispatch("labcmd-example", normalized, {"a": 1, "b": 1})
    channel = m.ObservationChannel(
        "layers",
        m.ChannelKind.ARRAY,
        "dimensionless",
        (2, 2),
        [[0.0, 1.0], [1.0, 0.0]],
        m.ChannelQuality.OK,
        m.ChannelSource.SIMULATED_SENSOR,
    )
    payload = {"sim_time": 0.0, "channels": [channel.to_dict()]}
    evaluation_payload = {"reward": 0.2, "ground_truth": {"vessels": []}}
    receipt = m.Receipt(
        "labcmd-example",
        True,
        m.CommandState.SUCCEEDED,
        None,
        {"terminated": False, "truncated": False},
        None,
        0.0,
        1,
        payload,
        evaluation_payload,
    )
    observation = m.Observation(
        "labobs-example",
        "labrun-example",
        "labcmd-example",
        1,
        0.0,
        "model_time",
        1,
        (channel,),
        None,
    )
    evaluation = m.Evaluation(
        "labeval-example",
        "labrun-example",
        "labcmd-example",
        1,
        0.2,
        {"vessels": []},
        {},
    )
    return [
        quantity,
        parameter,
        capability,
        resource,
        spec,
        descriptor,
        request,
        normalized,
        dispatch,
        receipt,
        channel,
        observation,
        evaluation,
        m.SessionOpenRequest("toy", 1, {}, descriptor.capability_revision),
        m.SessionOpened("session-1", descriptor, payload, evaluation_payload),
        m.StopResult(True, "end_session"),
        m.Budgets(),
        m.LabCaller("root", "frame", None, m.CommandOrigin.AGENT_TOOL, None, "agent"),
    ]


EXAMPLES = examples()


@pytest.mark.parametrize("value", EXAMPLES, ids=lambda v: type(v).__name__)
def test_roundtrip(value):
    assert type(value).from_dict(json.loads(json.dumps(value.to_dict()))) == value
    assert dataclasses.is_dataclass(value) and value.__dataclass_params__.frozen


@pytest.mark.parametrize("value", EXAMPLES, ids=lambda v: type(v).__name__)
def test_unknown_fields(value):
    with pytest.raises(m.LabError, match="unexpected") as caught:
        type(value).from_dict({**value.to_dict(), "unexpected": 1})
    assert caught.value.code is m.ErrorCode.INVALID_PARAMETERS


REQUIRED = [
    (v, f.name)
    for v in EXAMPLES
    for f in dataclasses.fields(v)
    if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
]


@pytest.mark.parametrize(
    "value,key", REQUIRED, ids=[f"{type(v).__name__}.{key}" for v, key in REQUIRED]
)
def test_missing_required(value, key):
    data = value.to_dict()
    del data[key]
    with pytest.raises(m.LabError, match=key) as caught:
        type(value).from_dict(data)
    assert caught.value.code is m.ErrorCode.INVALID_PARAMETERS


@pytest.mark.parametrize(
    "name,key,bad",
    [
        (name, key, bad)
        for name, key in (
            ("Quantity", "value"),
            ("CommandRequest", "expected_revision"),
            ("Receipt", "step_index"),
            ("Observation", "sim_time"),
            ("Evaluation", "reward"),
            ("Budgets", "max_steps"),
            ("SessionOpenRequest", "seed"),
        )
        for bad in (True, float("nan"), float("inf"), -float("inf"), "200", None)
        if not (name == "SessionOpenRequest" and bad is None)
    ],
)
def test_numeric_types(name, key, bad):
    value = next(v for v in EXAMPLES if type(v).__name__ == name)
    data = value.to_dict()
    data[key] = bad
    with pytest.raises(m.LabError, match=key) as caught:
        type(value).from_dict(data)
    assert caught.value.code is m.ErrorCode.INVALID_PARAMETERS


@pytest.mark.parametrize(
    "name,key,bad",
    [
        ("Quantity", "unit", 1),
        ("Capability", "terminal", "false"),
        ("Capability", "resources", {}),
        ("Capability", "scope", "physical"),
        ("Observation", "channels", "layers"),
        ("Dispatch", "fencing_tokens", []),
        ("SessionOpenRequest", "options", {"x": float("nan")}),
        (
            "DeviceDescriptor",
            "stop",
            {"supported": True, "semantics": "end_session", "extra": 1},
        ),
    ],
)
def test_structural_types(name, key, bad):
    value = next(v for v in EXAMPLES if type(v).__name__ == name)
    with pytest.raises(m.LabError, match=key) as caught:
        type(value).from_dict({**value.to_dict(), key: bad})
    assert caught.value.code is m.ErrorCode.INVALID_PARAMETERS


@pytest.mark.parametrize(
    "name,key,bad",
    [
        ("Quantity", "unit", "seconds"),
        ("ParameterSpec", "allowed", []),
        ("ParameterSpec", "allowed", [2, 1]),
        ("CommandRequest", "idempotency_key", ""),
        ("CommandRequest", "idempotency_key", "\n"),
        ("CommandRequest", "idempotency_key", "x" * 129),
        ("CommandRequest", "expected_revision", -1),
        ("Receipt", "status", "running"),
        ("Receipt", "end_reason", "stopped"),
        ("Observation", "sequence", -1),
        ("Observation", "sim_time_unit", "seconds"),
        ("ObservationChannel", "shape", [4]),
        ("ObservationChannel", "quality", "unavailable"),
        ("Budgets", "max_commands", 0),
        ("LabCaller", "execution_owner", "unknown"),
    ],
)
def test_semantic_validation(name, key, bad):
    value = next(v for v in EXAMPLES if type(v).__name__ == name)
    with pytest.raises(m.LabError) as caught:
        type(value).from_dict({**value.to_dict(), key: bad})
    assert caught.value.code is m.ErrorCode.INVALID_PARAMETERS


def test_state_machines():
    expected = {
        "created": {"awaiting_approval", "admitted", "rejected", "not_dispatched"},
        "awaiting_approval": {"admitted", "rejected", "not_dispatched"},
        "admitted": {"dispatching", "rejected", "not_dispatched"},
        "dispatching": {
            "running",
            "succeeded",
            "failed",
            "outcome_unknown",
            "stop_requested",
            "not_dispatched",
        },
        "running": {"succeeded", "failed", "outcome_unknown", "stop_requested"},
        "stop_requested": {"stopped", "succeeded", "failed", "outcome_unknown"},
        "outcome_unknown": {"succeeded", "failed", "not_dispatched"},
    }
    for source in m.CommandState:
        for target in m.CommandState:
            assert m.can_transition_command(source, target) == (
                target.value in expected.get(source.value, set())
            )
    assert m.command_sources_for(m.CommandState.SUCCEEDED) == frozenset(
        {
            m.CommandState.DISPATCHING,
            m.CommandState.RUNNING,
            m.CommandState.STOP_REQUESTED,
            m.CommandState.OUTCOME_UNKNOWN,
        }
    )
    assert m.TERMINAL_COMMAND_STATES == frozenset(
        m.CommandState(v)
        for v in ("succeeded", "failed", "rejected", "not_dispatched", "stopped")
    )
    assert m.TERMINAL_RUN_STATUSES == {m.RunStatus.ENDED, m.RunStatus.FAILED}
    assert m.run_sources_for(m.RunStatus.ENDED) == frozenset(
        {
            m.RunStatus.CREATING,
            m.RunStatus.READY,
            m.RunStatus.BUSY,
            m.RunStatus.QUARANTINED,
        }
    )
    assert m.run_sources_for(m.RunStatus.FAILED) == {m.RunStatus.CREATING}
    runs = {
        "creating": {"ready", "failed", "ended"},
        "ready": {"busy", "quarantined", "ended"},
        "busy": {"ready", "ended", "quarantined"},
        "quarantined": {"ready", "ended"},
    }
    for source in m.RunStatus:
        for target in m.RunStatus:
            assert m.can_transition_run(source, target) == (
                target.value in runs.get(source.value, set())
            )
    with pytest.raises(TypeError):
        m.COMMAND_TRANSITIONS[m.CommandState.CREATED] = frozenset()
    with pytest.raises(TypeError):
        m.RUN_TRANSITIONS[m.RunStatus.CREATING] = frozenset()


def test_hashes_and_ids():
    request = next(v for v in EXAMPLES if type(v) is m.NormalizedCommand)
    request = dataclasses.replace(
        request,
        run_id="中文",
        parameters={"b": m.Quantity(2.0, "mL"), "a": m.Quantity(1.0, "mL")},
    )
    reversed_request = dict(reversed(list(request.to_dict().items())))
    decoded = m.NormalizedCommand.from_dict(
        json.loads(json.dumps(reversed_request, ensure_ascii=True))
    )
    assert m.request_hash(decoded) == m.request_hash(request)
    assert m.request_hash(
        dataclasses.replace(request, idempotency_key="other", expected_revision=99)
    ) == m.request_hash(request)
    assert m.request_hash(
        dataclasses.replace(request, target="different")
    ) != m.request_hash(request)
    data = request.to_dict()
    del data["idempotency_key"]
    del data["expected_revision"]
    canonical = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    assert m.request_hash(request) == hashlib.sha256(canonical.encode()).hexdigest()
    cap = next(v for v in EXAMPLES if type(v) is m.Capability)
    caps = (cap, dataclasses.replace(cap, capability_id="a"))
    assert m.capability_revision(caps) == m.capability_revision(caps[::-1])
    assert m.canonical_json({"中文": 2, "a": 1}) == '{"a":1,"中文":2}'
    with pytest.raises(ValueError):
        m.canonical_json(float("nan"))
    expected = {
        "device_id": "d",
        "profile": "p",
        "seed": 2,
        "budgets": m.Budgets().to_dict(),
        "options": {"x": 1},
    }
    assert m.config_hash("d", "p", 2, m.Budgets(), {"x": 1}) == m.sha256_hex(
        m.canonical_json(expected)
    )
    for prefix in ("labrun", "labcmd", "labobs", "labeval"):
        assert re.fullmatch(prefix + r"-[0-9a-f]{12}", m.new_id(prefix))
    with pytest.raises(ValueError):
        m.new_id("other")


def test_error_and_defaults():
    error = m.LabError(m.ErrorCode.RESOURCE_BUSY, "busy")
    assert str(error) == "resource_busy: busy"
    assert error.to_dict() == {"code": "resource_busy", "message": "busy"}
    assert m.LabError(
        m.ErrorCode.UNSUPPORTED_ACTION, "bad", {"allowed": [1]}
    ).to_dict()["details"] == {"allowed": [1]}
    assert m.Budgets.from_dict({}).to_dict() == {
        "max_steps": 50,
        "max_commands": 200,
        "max_wall_ms": 1800000,
        "max_consecutive_failures": 3,
        "idle_timeout_ms": 3600000,
    }
    assert (
        len(m.ErrorCode) == 21 and len(m.EndReason) == 9 and len(m.CommandState) == 12
    )
    assert list(m.RunMode) == [m.RunMode.SIMULATION]


def test_core_imports_are_stdlib():
    root = Path(__file__).resolve().parents[1] / "openai4s" / "lab"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert not node.level, f"Use explicit openai4s imports: {path}"
                names = [node.module or ""]
            for name in names:
                assert name.split(".")[0] in sys.stdlib_module_names | {"openai4s"}, (
                    path,
                    name,
                )


@pytest.mark.parametrize(
    "name,key",
    [
        ("Evaluation", "ground_truth"),
        ("Evaluation", "metrics"),
        ("SessionOpenRequest", "options"),
        ("SessionOpened", "evaluation"),
    ],
)
def test_private_mapping_keys_never_reach_validation_errors(name, key):
    value = next(v for v in EXAMPLES if type(v).__name__ == name)
    # Arbitrary mapping keys can themselves be material identities. Neither
    # a nested JSON walk nor the typed Mapping decoder may print those keys.
    for payload in (
        {"PRIVATE_SPECIES": float("nan")},
        {"vessels": [{"moles": {"PRIVATE_SPECIES": float("nan")}}]},
    ):
        with pytest.raises(m.LabError, match=key) as caught:
            type(value).from_dict({**value.to_dict(), key: payload})
        assert caught.value.code is m.ErrorCode.INVALID_PARAMETERS
        assert "PRIVATE_SPECIES" not in str(caught.value)
        assert "PRIVATE_SPECIES" not in json.dumps(caught.value.to_dict())


@pytest.mark.parametrize(
    "axes",
    [
        [{"name": "resource", "labels": ["a"]}, {"name": "layer_px"}],
        [{"name": "resource"}],
        [{"name": "same"}, {"name": "same"}],
        [{"name": "resource", "labels": ["a", "a"]}, {"name": "layer_px"}],
        [{"name": ""}, {"name": "layer_px"}],
    ],
)
def test_channel_axes_must_describe_the_shape(axes):
    spec = {
        "name": "layers",
        "kind": "array",
        "shape": [2, 3],
        "unit": "dimensionless",
        "source": "simulated_sensor",
        "available": True,
        "description": "rows are vessels",
    }
    labelled = {
        **spec,
        "axes": [{"name": "resource", "labels": ["a", "b"]}, {"name": "layer_px"}],
    }
    assert m.ObservationChannelSpec.from_dict(labelled).to_dict() == labelled
    assert "axes" not in m.ObservationChannelSpec.from_dict(spec).to_dict()
    with pytest.raises(m.LabError) as caught:
        m.ObservationChannelSpec.from_dict({**spec, "axes": axes})
    assert caught.value.code is m.ErrorCode.INVALID_PARAMETERS
