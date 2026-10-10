"""Explicit integration gate against the user's isolated pinned provider interpreter."""

import json
import os
from pathlib import Path

import pytest

from openai4s_lab_provider.chemgymrl import PROFILES
from openai4s_lab_provider.client import DEFAULT_TIMEOUTS, ProviderClient, ProviderError

pytestmark = pytest.mark.external
ROOT = Path(__file__).resolve().parents[1]


def request(client, op, args):
    return client.request(op, args, timeout=DEFAULT_TIMEOUTS[op])


@pytest.fixture
def provider(tmp_path):
    python = os.environ.get("OPENAI4S_LAB_CHEMGYMRL_PYTHON")
    if not python:
        pytest.skip(
            "set OPENAI4S_LAB_CHEMGYMRL_PYTHON to the pinned provider environment's Python"
        )
    env = {
        "HOME": str(tmp_path),
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(tmp_path / "mpl"),
        "NUMBA_CACHE_DIR": str(tmp_path / "numba"),
        "PYTHONNOUSERSITE": "1",
    }
    clients = []

    def start():
        client = ProviderClient(
            [
                python,
                "-I",
                str(ROOT / "openai4s_lab_provider/__main__.py"),
                "--backend",
                "chemgymrl",
            ],
            env=env,
            cwd=tmp_path,
        ).start()
        clients.append(client)
        return client

    yield start
    for client in clients:
        client.close()


def command(cap, value=None):
    return {
        **{key: cap[key] for key in ("capability_id", "operation", "source", "target")},
        "parameters": {
            key: {
                "unit": spec["unit"],
                "value": spec["allowed"][0] if value is None else value,
            }
            for key, spec in cap["parameters"].items()
        },
    }


def execute(client, cap, ident, value=None):
    return request(
        client,
        "execute",
        {
            "provider_command_id": ident,
            "command": command(cap, value),
            "fencing_tokens": {key: 1 for key in cap["resources"]},
        },
    )


def no_truth(value):
    if isinstance(value, dict):
        assert not set(value) & {
            "evaluation",
            "ground_truth",
            "moles",
            "reward",
            "temperature_K",
            "volume_L",
            "gym_action",
            "provider_action",
        }
        for item in value.values():
            no_truth(item)
    elif isinstance(value, list):
        for item in value:
            no_truth(item)


@pytest.mark.parametrize("profile", PROFILES)
def test_live_manifest_receipts_and_seed_reproducibility(provider, profile):
    expected = json.loads(
        (
            ROOT / "openai4s_lab_provider/chemgymrl/manifests" / f"{profile}.json"
        ).read_text()
    )
    streams = []
    evaluations = []
    for repeat in range(2):
        client = provider()
        descriptor = request(client, "describe", {"profile": profile})
        # The committed manifest is portable; only the reproducibility claim
        # depends on the runtime that describes itself.
        assert {k: v for k, v in descriptor.items() if k != "reproducibility"} == {
            k: v for k, v in expected.items() if k != "reproducibility"
        }
        assert not any(
            c["name"] == "pressure" and c["available"]
            for c in descriptor["observation_channels"]
        )
        no_truth(descriptor)
        opened = request(
            client,
            "open",
            {
                "profile": profile,
                "seed": 42,
                "options": {},
                "expected_capability_revision": descriptor["capability_revision"],
            },
        )
        assert type(opened["evaluation"]["reward"]) is float
        stream = [opened["observation"]]
        caps = descriptor["capabilities"]
        empty = next(
            c
            for c in caps
            if c["capability_id"] == "transfer_liquid:beaker_1->extraction_vessel"
        )
        rejected = execute(client, empty, "empty-source")
        assert rejected["status"] == "failed" and not rejected["applied"]
        assert rejected["error"]["code"] == "precondition_failed"
        mix = next(c for c in caps if c["operation"] == "mix_model")
        invalid = execute(client, mix, "invalid-mix", -0.3)
        assert invalid["status"] == "rejected" and not invalid["applied"]
        step = 0
        # Same sequence as SOURCE.md: stock transfer, mixing, settling, draining, end.
        for index in [30, 9, 35, 0, 3, 35, 8, 35, 0, 4, 35, 0, 40]:
            if index == 30:
                cap = next(
                    c
                    for c in caps
                    if c["operation"] == "transfer_liquid"
                    and c["source"] in ("h2o_vessel", "diethyl_ether_vessel")
                )
                value = cap["parameters"]["volume"]["allowed"][0]
            elif index in (8, 9):
                cap = mix
                # Actions 5-9 shake for 0.2..1.0 model time (upstream -0.2..-1.0).
                value = cap["parameters"]["duration"]["allowed"][index - 5]
            elif index == 35:
                cap = next(c for c in caps if c["operation"] == "settle_model")
                value = None
            elif index == 40:
                cap = next(c for c in caps if c["operation"] == "end_experiment")
                value = None
            else:
                cap = next(c for c in caps if c["operation"] == "drain_layers")
                value = cap["parameters"]["pixels"]["allowed"][index]
            if repeat == 1:
                assert request(client, "describe", {"profile": profile}) == descriptor
            receipt = execute(client, cap, f"step-{step}", value)
            step += 1
            assert receipt["status"] == "succeeded" and receipt["applied"]
            assert receipt["step_index"] == step
            assert receipt["provider_action"] == {"gym_action": index}
            assert receipt["raw"] == {"terminated": index == 40, "truncated": False}
            assert receipt["end_reason"] == ("end_action" if index == 40 else None)
            no_truth(receipt["observation"])
            stream.append(receipt["observation"])
        assert execute(client, mix, "after-end")["error"]["code"] == "run_ended"
        evaluations.append(receipt["evaluation"])
        streams.append(stream)
        client.close()
        assert client.process.returncode == 0
        assert client.stderr_tail() == ""
    assert streams[0] == streams[1]
    evaluations_match = evaluations[0] == evaluations[1]
    assert evaluations_match
    assert expected["reproducibility"] == {"status": "unverified", "evidence": None}
    claim = descriptor["reproducibility"]
    if claim["status"] == "verified_for_profile":
        # Only the measured runtime may claim it, and it cites the evidence.
        assert claim["evidence"].endswith("SOURCE.md#reproducibility")
    else:
        assert claim == {"status": "unverified", "evidence": None}
    source = (ROOT / "openai4s_lab_provider/chemgymrl/SOURCE.md").read_text()
    assert profile in source and "verified_for_profile" in source


@pytest.mark.parametrize("profile", PROFILES)
def test_live_max_steps_stop_and_revision_mismatch(provider, profile):
    client = provider()
    d = request(client, "describe", {"profile": profile})
    args = {
        "profile": profile,
        "seed": 42,
        "options": {},
        "expected_capability_revision": "mismatch",
    }
    with pytest.raises(ProviderError) as failure:
        request(client, "open", args)
    assert failure.value.code == "adapter_mismatch"
    args["expected_capability_revision"] = d["capability_revision"]
    request(client, "open", args)
    mix = next(c for c in d["capabilities"] if c["operation"] == "mix_model")
    for step in range(d["limits"]["max_steps"]):
        receipt = execute(client, mix, f"limit-{step}")
        assert receipt["applied"]
    assert receipt["raw"] == {"terminated": True, "truncated": False}
    assert receipt["end_reason"] == "max_steps"
    assert request(client, "stop", {"reason": "test"}) == {
        "stopped": True,
        "semantics": "end_session",
    }
    assert execute(client, mix, "after-stop")["error"]["code"] == "run_ended"


def test_a_pour_back_of_exactly_what_was_poured_is_not_refused(provider):
    # W1 review: upstream levels come from np.linspace, so 600 mL is really
    # 0.6000000000000001 L. Pouring it on and then pouring the same level back
    # used to fail as "source volume is insufficient" by a float tail.
    client = provider()
    profile = "GenWurtzExtract-v2"
    descriptor = request(client, "describe", {"profile": profile})
    request(
        client,
        "open",
        {
            "profile": profile,
            "seed": 42,
            "options": {},
            "expected_capability_revision": descriptor["capability_revision"],
        },
    )
    caps = {c["capability_id"]: c for c in descriptor["capabilities"]}
    steps = [
        ("transfer_liquid:diethyl_ether_vessel->extraction_vessel", 200.0),
        ("transfer_liquid:extraction_vessel->beaker_2", 600.0),
        ("transfer_liquid:beaker_2->extraction_vessel", 600.0),
    ]
    for index, (capability_id, volume) in enumerate(steps):
        receipt = execute(client, caps[capability_id], f"boundary-{index}", volume)
        assert receipt["status"] == "succeeded", (capability_id, receipt["error"])
