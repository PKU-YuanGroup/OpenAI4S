"""Capability matching and public sensor projection, without a provider."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from openai4s.lab.devices import DeviceRegistration, DeviceRegistry
from openai4s.lab.manifest import (
    load_descriptor,
    match_command,
    normalize_quantity,
    project_descriptor,
    project_observation,
)
from openai4s.lab.models import (
    CONTRACT,
    Capability,
    CapabilityScope,
    ChannelKind,
    ChannelQuality,
    ChannelSource,
    CommandRequest,
    DeviceDescriptor,
    ErrorCode,
    LabError,
    Observation,
    ObservationChannel,
    ObservationChannelSpec,
    ParameterSpec,
    Quantity,
    ResourceSpec,
    RunMode,
    SideEffect,
    capability_revision,
)


def descriptor():
    cap = Capability(
        "transfer:a->b",
        "transfer_liquid",
        CapabilityScope.SHARED,
        "a",
        "b",
        {"volume": ParameterSpec("mL", (200, 400))},
        SideEffect.MOVES_MATERIAL,
        ("a", "b"),
        ("layers",),
        False,
        "1",
    )
    return DeviceDescriptor(
        CONTRACT,
        "device",
        RunMode.SIMULATION,
        "fake",
        "toy",
        {"package": "fake", "version": "1", "source_sha": "", "adapter_version": "1"},
        (ResourceSpec("a", "vessel", "A"), ResourceSpec("b", "vessel", "B")),
        (cap,),
        capability_revision((cap,)),
        (
            ObservationChannelSpec(
                "layers",
                ChannelKind.ARRAY,
                (2, 10),
                "dimensionless",
                ChannelSource.SIMULATED_SENSOR,
                True,
                "Layers",
            ),
        ),
        {"max_steps": 50},
        {"supported": True, "semantics": "end_session"},
        {"unit": "model_time", "wall_clock_equivalent": None},
        {"status": "unverified", "evidence": None},
        (),
    )


def request(**changes):
    return replace(
        CommandRequest(
            "run", "transfer_liquid", "a", "b", {"volume": Quantity(0.2, "L")}, 0, "one"
        ),
        **changes,
    )


def test_exact_unit_normalization():
    desc = load_descriptor(descriptor().to_dict())
    normalized = match_command(desc, request())
    assert normalized.parameters == {"volume": Quantity(200.0, "mL")}
    assert normalized.capability_id == desc.capabilities[0].capability_id
    assert normalized.expected_revision == 0 and normalized.idempotency_key == "one"
    assert normalize_quantity(Quantity(200, "mL"), ParameterSpec("L", (0.2,))) == 0.2
    # Values within the explicit tolerance snap to the advertised number only.
    assert (
        normalize_quantity(Quantity(200 + 1e-8, "mL"), ParameterSpec("mL", (200.0,)))
        == 200.0
    )


def test_no_nearest_setting():
    with pytest.raises(LabError) as caught:
        match_command(descriptor(), request(parameters={"volume": Quantity(0.15, "L")}))
    assert caught.value.code is ErrorCode.UNSUPPORTED_ACTION
    assert caught.value.details == {
        "parameter": "volume",
        "allowed": [200.0, 400.0],
        "unit": "mL",
    }
    with pytest.raises(LabError) as caught:
        normalize_quantity(Quantity(150, "mL"), ParameterSpec("mL", (200.0, 400.0)))
    assert caught.value.details == {
        "parameter": "value",
        "allowed": [200.0, 400.0],
        "unit": "mL",
    }


def test_unit_mismatch():
    with pytest.raises(LabError) as caught:
        normalize_quantity(Quantity(200, "mL"), ParameterSpec("layer_px", (200.0,)))
    assert caught.value.code is ErrorCode.UNIT_MISMATCH


@pytest.mark.parametrize(
    "parameters",
    [{}, {"volume": Quantity(200, "mL"), "extra": Quantity(1, "dimensionless")}],
)
def test_parameter_names(parameters):
    with pytest.raises(LabError) as caught:
        match_command(descriptor(), request(parameters=parameters))
    assert caught.value.code is ErrorCode.INVALID_PARAMETERS


@pytest.mark.parametrize(
    "changes", [{"operation": "other"}, {"source": "unknown"}, {"target": "unknown"}]
)
def test_action_identity_precedes_parameters(changes):
    with pytest.raises(LabError) as caught:
        match_command(descriptor(), request(parameters={}, **changes))
    assert caught.value.code is ErrorCode.UNSUPPORTED_ACTION


@pytest.mark.parametrize(
    "case",
    [
        "duplicate",
        "resource",
        "descending",
        "empty",
        "revision",
        "contract",
        "channel",
        "ambiguous",
        "observes",
        "source",
    ],
)
def test_descriptor_rejects(case):
    data = descriptor().to_dict()
    if case == "duplicate":
        data["capabilities"].append(
            {**data["capabilities"][0], "operation": "settle_model"}
        )
    elif case == "resource":
        data["capabilities"][0]["resources"] = ["missing"]
    elif case == "descending":
        data["capabilities"][0]["parameters"]["volume"]["allowed"] = [400, 200]
    elif case == "empty":
        data["capabilities"][0]["parameters"]["volume"]["allowed"] = []
    elif case == "revision":
        data["capability_revision"] = "incorrect"
    elif case == "contract":
        data["contract"] = "other"
    elif case == "channel":
        data["observation_channels"][0]["shape"] = [-1, 10]
    elif case == "ambiguous":
        data["capabilities"].append(
            {**data["capabilities"][0], "capability_id": "another"}
        )
    elif case == "observes":
        data["capabilities"][0]["observes"] = ["missing"]
    elif case == "source":
        data["capabilities"][0]["source"] = "missing"
    if case != "revision":
        del data["capability_revision"]
    with pytest.raises(LabError) as caught:
        load_descriptor(data)
    assert caught.value.code is (
        ErrorCode.ADAPTER_MISMATCH
        if case == "revision"
        else ErrorCode.INVALID_PARAMETERS
    )


def test_descriptor_computes_missing_revision():
    data = descriptor().to_dict()
    del data["capability_revision"]
    assert load_descriptor(data) == descriptor()


def observation():
    return Observation(
        "obs",
        "run",
        None,
        0,
        0,
        "model_time",
        0,
        (
            ObservationChannel(
                "layers",
                ChannelKind.ARRAY,
                "dimensionless",
                (300,),
                list(range(300)),
                ChannelQuality.OK,
                ChannelSource.SIMULATED_SENSOR,
            ),
            ObservationChannel(
                "pressure",
                ChannelKind.SCALAR,
                "dimensionless",
                (),
                None,
                ChannelQuality.UNAVAILABLE,
                ChannelSource.SIMULATED_SENSOR,
            ),
        ),
        None,
    )


def test_sensor_projection_summary_full_and_unavailable():
    obs = observation()
    projected = project_observation(obs)
    assert projected["channels"][0]["value"] == {
        "shape": [300],
        "summary": {"min": 0.0, "max": 299.0, "mean": 149.5},
        "truncated": True,
    }
    assert project_observation(obs, full=True)["channels"][0]["value"] == list(
        range(300)
    )
    assert project_observation(obs, max_elements=300)["channels"][0]["value"] == list(
        range(300)
    )
    # Even a stale in-memory numeric value cannot turn unavailable into zero.
    dirty = replace(obs, channels=(replace(obs.channels[1], value=123.0),))
    assert project_observation(dirty)["channels"][0]["value"] is None
    with pytest.raises(LabError):
        project_observation(obs, max_elements=True)


def test_projection_never_serializes_truth(monkeypatch):
    obs = observation()
    original = DeviceDescriptor.to_dict

    def polluted(self):
        data = original(self)
        data["provider_action"] = {"index": 7}
        data["evaluation"] = {"reward": 0.9, "ground_truth": {"moles": 1}}
        data["capabilities"][0]["provider_action"] = [1, 2, 3]
        data["reproducibility"]["evidence"] = {"reward": 1, "safe": "evidence"}
        return data

    monkeypatch.setattr(DeviceDescriptor, "to_dict", polluted)
    # Persistence serializers are intentionally not a projection authority.
    monkeypatch.setattr(
        Observation, "to_dict", lambda self: {"evaluation": {"reward": 42}}
    )
    output = [project_descriptor(descriptor()), project_observation(obs, full=True)]

    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(v) for v in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(v) for v in value))
        return set()

    assert not keys(output) & {
        "provider_action",
        "provider_action_json",
        "evaluation",
        "reward",
        "ground_truth",
    }
    # Bad category payloads cannot smuggle an evaluation inside channel.value.
    channel = replace(
        obs.channels[0],
        kind=ChannelKind.CATEGORY,
        shape=(),
        value={"ground_truth": {"moles": 2}},
    )
    with pytest.raises(LabError):
        project_observation(replace(obs, channels=(channel,)))
    physical = replace(obs.channels[0], source=ChannelSource.PHYSICAL_SENSOR)
    assert project_observation(replace(obs, channels=(physical,)))["channels"] == []


def test_registry_explicit_thread_safe_registration():
    registry = DeviceRegistry()
    assert registry.list() == []
    reg = DeviceRegistration(
        "b",
        "fake",
        RunMode.SIMULATION,
        "B",
        ("toy",),
        lambda profile: descriptor(),
        lambda: None,
    )

    def register_once(_):
        try:
            registry.register(reg)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(register_once, range(16))) == 1
    registry.register(replace(reg, device_id="a"))
    assert [r.device_id for r in registry.list()] == ["a", "b"]
    assert registry.get("b") is reg
    assert registry.describe("b", "toy") == descriptor()
    with pytest.raises(LabError) as caught:
        registry.get("unknown")
    assert caught.value.code is ErrorCode.DEVICE_NOT_FOUND
    with pytest.raises(LabError) as caught:
        registry.describe("b", "unknown")
    assert caught.value.code is ErrorCode.UNSUPPORTED_ACTION
