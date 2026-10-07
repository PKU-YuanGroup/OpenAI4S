"""Pure-data regressions; no upstream package is required or imported."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from openai4s_lab_provider.chemgymrl import mapping

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/lab/chemgymrl_wateroil_actions.json").read_text()
)
ROWS, LABELS = FIXTURE["action_rows"], FIXTURE["vessel_labels"]
LAYOUT = {
    "order": "vessel_major",
    "vessel_indices": [0, 1],
    "observation_list": ["layers", "targets"],
    "sizes": {"layers": 100, "targets": 1},
    "targets": ["NaCl"],
}


def descriptor():
    return mapping.derive_descriptor(
        "WaterOilExtract-v0",
        ROWS,
        LABELS,
        LAYOUT,
        {"max_steps": 50},
        {"adapter_version": "1"},
    )


def command(capability, value=None):
    return {
        **{
            key: capability[key]
            for key in ("capability_id", "operation", "source", "target")
        },
        "parameters": {
            key: {
                "value": spec["allowed"][0] if value is None else value,
                "unit": spec["unit"],
            }
            for key, spec in capability["parameters"].items()
        },
    }


def test_descriptor_groups_sorted_exact_volumes_and_identity():
    result = descriptor()
    assert len(result["capabilities"]) == 9
    caps = result["capabilities"]
    assert caps == sorted(caps, key=lambda cap: cap["capability_id"])
    cap = next(
        cap
        for cap in caps
        if cap["capability_id"] == "transfer_liquid:beaker_1->extraction_vessel"
    )
    # Litres become millilitres, and upstream float noise
    # (0.6000000000000001 L) is not advertised as 600.0000000000001 mL.
    assert cap["parameters"]["volume"]["allowed"] == [
        200.0,
        400.0,
        600.0,
        800.0,
        1000.0,
    ]
    assert [round(row["parameters"][0][0] * 1000, 9) for row in ROWS[10:15]] == (
        cap["parameters"]["volume"]["allowed"]
    )
    stock = next(
        cap
        for cap in caps
        if cap["capability_id"] == "transfer_liquid:c6h14_vessel->extraction_vessel"
    )
    assert stock["parameters"]["volume"]["allowed"] == [
        100.0,
        200.0,
        300.0,
        400.0,
        500.0,
    ]
    # Every advertised level still maps back to exactly one upstream action.
    for c in caps:
        for name, spec in c["parameters"].items():
            for level in spec["allowed"]:
                assert (
                    mapping.command_to_action(ROWS, LABELS, command(c, level))
                    is not None
                )
    for c in caps:
        for spec in c["parameters"].values():
            assert spec["allowed"] == sorted(set(spec["allowed"]))
    assert (
        result["capability_revision"]
        == hashlib.sha256(mapping.canonical_json(caps).encode()).hexdigest()
    )
    assert [(r["resource_id"], r["kind"]) for r in result["resources"]] == [
        ("extraction_vessel", "vessel"),
        ("beaker_1", "vessel"),
        ("waste_vessel", "vessel"),
        ("c6h14_vessel", "source"),
        ("h2o_vessel", "source"),
    ]
    assert '"index"' not in json.dumps(result) and "gym_action" not in json.dumps(
        result
    )


def test_multivessel_settle_and_mix_advertise_positive_model_durations():
    caps = descriptor()["capabilities"]
    settle = next(c for c in caps if c["operation"] == "settle_model")
    assert settle["source"] is None and settle["target"] is None
    assert settle["resources"] == ["beaker_1", "extraction_vessel", "waste_vessel"]
    assert settle["parameters"]["duration"]["allowed"] == [0.01, 0.02, 0.04, 0.08, 0.16]
    assert mapping.command_to_action(ROWS, LABELS, command(settle)) == 35
    mix = next(c for c in caps if c["operation"] == "mix_model")
    # Upstream shakes with a negative step of its settling clock; the operation
    # carries the sign, so the advertised duration is the positive magnitude.
    assert mix["parameters"]["duration"]["allowed"] == [0.2, 0.4, 0.6, 0.8, 1.0]
    assert mapping.command_to_action(ROWS, LABELS, command(mix)) == 5
    assert mapping.command_to_action(ROWS, LABELS, command(mix, 1.0)) == 9
    assert mapping.command_to_action(ROWS, LABELS, command(mix, -0.2)) is None


def test_layer_rows_name_the_vessels_they_show():
    layers = next(
        channel
        for channel in descriptor()["observation_channels"]
        if channel["name"] == "layers"
    )
    assert layers["axes"] == [
        {"name": "resource", "labels": ["extraction_vessel", "beaker_1"]},
        {"name": "layer_px"},
    ]


def test_exact_mapping_rejects_nearby_or_wrong_unit_and_ambiguity():
    cap = next(
        c
        for c in descriptor()["capabilities"]
        if c["capability_id"] == "transfer_liquid:beaker_1->extraction_vessel"
    )
    good = command(cap)
    assert mapping.command_to_action(ROWS, LABELS, good) == 10
    for value in (199, 200 + 1e-10, True, float("nan")):
        assert mapping.command_to_action(ROWS, LABELS, command(cap, value)) is None
    wrong = copy.deepcopy(good)
    wrong["parameters"]["volume"]["unit"] = "L"
    assert mapping.command_to_action(ROWS, LABELS, wrong) is None
    wrong = copy.deepcopy(good)
    wrong["parameters"]["extra"] = {"value": 1, "unit": "mL"}
    assert mapping.command_to_action(ROWS, LABELS, wrong) is None
    with pytest.raises(ValueError, match="ambiguous"):
        mapping.command_to_action(ROWS + [ROWS[10]], LABELS, good)


@pytest.mark.parametrize("labels", [["", "b"], ["a", "a"], ["a b", "a_b"]])
def test_resource_identity_rejects_invalid_labels(labels):
    with pytest.raises(ValueError):
        mapping.resource_ids(labels)


def test_observation_vessel_major_and_no_truth():
    channels = mapping.decode_observation([0.2] * 100 + [1] + [0.8] * 100 + [1], LAYOUT)
    assert channels[0]["value"] == [[0.2] * 100, [0.8] * 100]
    assert channels[0]["shape"] == [2, 100]
    assert channels[1]["kind"] == "category" and channels[1]["value"] == "NaCl"
    assert channels[1]["shape"] == []
    assert all(
        c["source"] == "simulated_sensor" and c["quality"] == "ok" for c in channels
    )
    assert not any(
        word in json.dumps(channels)
        for word in ("ground_truth", "moles", "reward", "pressure")
    )
    with pytest.raises(ValueError):
        mapping.decode_observation([0.2] * 202, LAYOUT)
    with pytest.raises(ValueError):
        mapping.decode_observation([0.2], LAYOUT)


def test_ground_truth_is_a_separate_projection():
    vessels = [
        {
            "label": "Beaker 1",
            "temperature_K": 297,
            "volume_L": 0.5,
            "moles": {"test_material": 0.2},
            "internal": "do not copy",
        }
    ]
    assert mapping.ground_truth(vessels) == {
        "vessels": [
            {
                "resource_id": "beaker_1",
                "temperature_K": 297,
                "volume_L": 0.5,
                "moles": {"test_material": 0.2},
            }
        ]
    }


def test_reproducibility_status_requires_measured_runtime(monkeypatch):
    from openai4s_lab_provider.chemgymrl import adapter

    versions = dict(
        line.split("==")
        for line in Path(adapter.__file__)
        .with_name("requirements.in")
        .read_text()
        .splitlines()
        if line and not line.startswith("#")
    )
    monkeypatch.setattr(
        adapter.platform, "platform", lambda: "macOS-27.0.1-arm64-arm-64bit"
    )
    monkeypatch.setattr(adapter.platform, "python_version", lambda: "3.10.21")
    monkeypatch.setattr(adapter.importlib.metadata, "version", versions.__getitem__)
    assert adapter._verified_runtime()
    versions["numpy"] = "different"
    assert not adapter._verified_runtime()


def test_describe_active_session_does_not_create_another_environment(monkeypatch):
    from openai4s_lab_provider.chemgymrl import adapter
    from openai4s_lab_provider.protocol import BackendError

    backend = adapter.Backend()
    backend.opened = True
    backend.descriptor = {"profile": "WaterOilExtract-v0"}

    def forbidden_make(profile):
        raise AssertionError("read-only describe recreated the session RNGs")

    monkeypatch.setattr(adapter, "_make", forbidden_make)
    assert backend.describe("WaterOilExtract-v0") == backend.descriptor
    with pytest.raises(BackendError) as failure:
        backend.describe("GenWurtzExtract-v2")
    assert failure.value.code == "invalid_parameters"


class _Vessel:
    """Just the surface transfer_precondition reads from an upstream vessel."""

    def __init__(self, filled, capacity, *, sticky=False):
        self.filled, self.volume, self.sticky = filled, capacity, sticky

    def filled_volume(self):
        return self.filled

    def _pour(self, dt, other, volume):
        moved = 0.0 if self.sticky else min(volume, self.filled)
        self.filled -= moved
        other.filled += moved
        if other.filled > other.volume:
            other.filled = other.volume
            return -1
        return 0

    _event_dict = {"pour by volume": _pour, "drain by pixel": _pour}


def test_transfer_preconditions_follow_upstream_and_tolerate_float_tails():
    from openai4s_lab_provider.chemgymrl.adapter import transfer_precondition

    # The float tail of an upstream level is not "insufficient".
    assert (
        transfer_precondition(
            _Vessel(0.6, 1.0), _Vessel(0.0, 1.0), "pour by volume", 0.6000000000000001
        )
        is None
    )
    # A real shortfall is refused rather than clamped as upstream would.
    assert (
        transfer_precondition(
            _Vessel(0.6, 1.0), _Vessel(0.0, 1.0), "pour by volume", 0.6001
        )
        == "source volume is insufficient"
    )
    assert (
        transfer_precondition(
            _Vessel(0.0, 1.0), _Vessel(0.0, 1.0), "pour by volume", 0.1
        )
        == "source volume is insufficient"
    )
    # Upstream's own overflow status decides capacity.
    source, target = _Vessel(0.6, 1.0), _Vessel(0.5, 1.0)
    assert (
        transfer_precondition(source, target, "pour by volume", 0.6)
        == "target capacity would be exceeded"
    )
    # The check never mutates the simulation it guards.
    assert (source.filled, target.filled) == (0.6, 0.5)
    # A drain that would move nothing is refused.
    assert (
        transfer_precondition(
            _Vessel(0.5, 1.0, sticky=True), _Vessel(0.0, 1.0), "drain by pixel", 2
        )
        == "source volume is insufficient"
    )
