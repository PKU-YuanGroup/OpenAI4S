"""Policy tests use a DevicePort adapter only inside this test module."""

import json
import random
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from openai4s.lab.fake import FakeExtractorDevice
from openai4s.lab.manifest import match_command, project_descriptor
from openai4s.lab.models import (
    CommandOrigin,
    CommandRequest,
    DeviceDescriptor,
    Dispatch,
    ErrorCode,
    LabCaller,
    LabError,
    SessionOpenRequest,
    capability_revision,
)
from openai4s.lab.policies import (
    FixedRulePolicy,
    ManagerPolicyEnv,
    RandomValidPolicy,
    run_episode,
)


class DeviceEnv:
    """Test-only adaptation; raw rows intentionally exercise runner projection."""

    def __init__(self):
        self.device = FakeExtractorDevice()
        self.descriptor = self.device.describe("toy-extract-v0")
        self.opened = self.device.open(
            SessionOpenRequest(
                self.descriptor.profile, 17, {}, self.descriptor.capability_revision
            )
        )
        self.run = {
            "run_id": "labrun-policy-test",
            "device_id": self.descriptor.device_id,
            "profile": self.descriptor.profile,
            "capability_revision": self.descriptor.capability_revision,
            "status": "ready",
            "revision": 0,
            "step_count": 0,
            "config": {"private": True},
        }
        self.requests = []
        self.receipts = []
        self.command = None
        self.observation = self._observation(self.opened.observation, None)

    def _observation(self, payload, command_id):
        return {
            "observation_id": f"labobs-{self.run['revision']}",
            "run_id": self.run["run_id"],
            "command_id": command_id,
            "sequence": self.run["revision"],
            "sim_time": payload["sim_time"],
            "sim_time_unit": "model_time",
            "wall_time_ms": 0,
            "channels": payload["channels"],
            "artifact_version_id": None,
            "evaluation": self.opened.evaluation,
        }

    def status(self):
        return {
            "run": deepcopy(self.run),
            "descriptor": project_descriptor(self.descriptor),
            "command": deepcopy(self.command),
        }

    def observe(self):
        return deepcopy(self.observation)

    def execute(self, request):
        command = CommandRequest.from_dict(request)
        assert command.run_id == self.run["run_id"]
        assert command.expected_revision == self.run["revision"]
        normalized = match_command(self.descriptor, command)
        capability = next(
            cap
            for cap in self.descriptor.capabilities
            if cap.capability_id == normalized.capability_id
        )
        self.requests.append(request)
        command_id = f"labcmd-{len(self.requests)}"
        receipt = self.device.execute(
            self.opened.session_id,
            Dispatch(
                command_id,
                normalized,
                {resource: len(self.requests) for resource in capability.resources},
            ),
        )
        receipt = replace(receipt, provider_action={"gym_action": 12345})
        self.receipts.append(receipt)
        if receipt.applied:
            self.run["revision"] += 1
            self.run["step_count"] += 1
            self.observation = self._observation(receipt.observation, command_id)
        if receipt.end_reason:
            self.run.update(status="ended", end_reason=receipt.end_reason.value)
        self.command = {
            "command_id": command_id,
            "run_id": self.run["run_id"],
            "request": normalized.to_dict(),
            "state": receipt.status.value,
            "error_code": receipt.error["code"] if receipt.error else None,
            "receipt": receipt.to_dict(),
            "provider_action": receipt.provider_action,
            "fencing_token": len(self.requests),
        }
        return {
            "run": deepcopy(self.run),
            "command": deepcopy(self.command),
            "observation": self.observe() if receipt.applied else None,
            "evaluation": receipt.evaluation,
        }


class EmptyTransfer:
    def select_action(self, descriptor, observation, *, step):
        return {
            "operation": "transfer_liquid",
            "source": "beaker_1",
            "target": "extraction_vessel",
            "parameters": {"volume": {"value": 200, "unit": "mL"}},
        }


def test_fixed_policy_uses_advertised_semantics_and_levels_and_ends():
    env = DeviceEnv()
    # IDs are opaque: replacing them must not alter the chosen semantic action.
    public = project_descriptor(env.descriptor)
    for cap in public["capabilities"]:
        cap["capability_id"] = "opaque-" + str(len(cap["capability_id"]))
    first = FixedRulePolicy().select_action(public, env.observe(), step=0)
    assert first["source"] == "water_source"
    assert first["parameters"] == {"volume": {"value": 200, "unit": "mL"}}
    result = run_episode(FixedRulePolicy(), env, max_steps=30)
    assert result["stop_reason"] == "ended"
    assert len(env.requests) == 13
    assert env.requests[-1]["operation"] == "end_experiment"
    for private in (
        "evaluation",
        "ground_truth",
        "reward",
        "provider_action",
        "gym_action",
    ):
        assert private not in json.dumps(result)
    assert all(receipt.status.value != "rejected" for receipt in env.receipts)
    assert not any(
        receipt.error
        and receipt.error["code"]
        in {"invalid_parameters", "unsupported_action", "unit_mismatch"}
        for receipt in env.receipts
    )
    # Capability levels, not quantities from the known fake, drive the rule.
    public["capabilities"] = [
        (
            {
                **cap,
                "parameters": {"volume": {"unit": "mL", "allowed": [17, 33]}},
            }
            if cap["operation"] == "transfer_liquid"
            else cap
        )
        for cap in public["capabilities"]
    ]
    changed = FixedRulePolicy().select_action(public, {}, step=0)
    assert changed["parameters"]["volume"]["value"] == 17
    manifests = (
        Path(__file__).resolve().parents[1]
        / "openai4s_lab_provider/chemgymrl/manifests"
    )
    for profile, solvent in (
        ("WaterOilExtract-v0", "h2o_vessel"),
        ("GenWurtzExtract-v2", "diethyl_ether_vessel"),
    ):
        descriptor = DeviceDescriptor.from_dict(
            json.loads((manifests / f"{profile}.json").read_text())
        )
        for step in range(13):
            action = FixedRulePolicy().select_action(
                project_descriptor(descriptor), {}, step=step
            )
            normalized = match_command(
                descriptor,
                CommandRequest.from_dict(
                    {
                        **action,
                        "run_id": "labrun-manifest-policy",
                        "expected_revision": step,
                        "idempotency_key": f"manifest-step-{step}",
                    }
                ),
            )
            if step == 0:
                assert normalized.source == solvent
            if step == 12:
                assert normalized.operation == "end_experiment"


def test_seeded_random_policy_samples_capabilities_and_levels_uniformly():
    descriptor = project_descriptor(DeviceEnv().descriptor)
    global_state = random.getstate()

    def sample(seed):
        policy = RandomValidPolicy(seed)
        return [policy.select_action(descriptor, {}, step=i) for i in range(4000)]

    first = sample(11)
    assert sample(11) == first
    assert sample(12) != first
    assert random.getstate() == global_state
    counts = Counter((a["operation"], a["source"], a["target"]) for a in first)
    expected = len(first) / len(descriptor["capabilities"])
    for capability in descriptor["capabilities"]:
        key = (capability["operation"], capability["source"], capability["target"])
        assert expected * 0.70 < counts[key] < expected * 1.30
        for name, spec in capability["parameters"].items():
            levels = Counter(
                a["parameters"][name]["value"]
                for a in first
                if (a["operation"], a["source"], a["target"]) == key
            )
            assert set(levels) == set(spec["allowed"])
            assert max(levels.values()) < min(levels.values()) * 2.5
    env = DeviceEnv()
    result = run_episode(RandomValidPolicy(11), env, max_steps=100)
    assert result["stop_reason"] == "ended"
    assert not any(r.status.value == "rejected" for r in env.receipts)


def test_attempt_limit_counts_precondition_failures_and_keys_do_not_replay():
    env = DeviceEnv()
    for _ in range(2):
        result = run_episode(EmptyTransfer(), env, max_steps=4)
        assert result["stop_reason"] == "max_steps"
        assert result["steps"] == 4
    assert len(env.requests) == 8
    assert len({r["idempotency_key"] for r in env.requests}) == 8
    assert env.run["revision"] == 0
    assert all(r.error["code"] == "precondition_failed" for r in env.receipts)
    assert run_episode(EmptyTransfer(), env, max_steps=0)["steps"] == 0
    for invalid in (-1, True, 1.5):
        with pytest.raises(ValueError):
            run_episode(EmptyTransfer(), env, max_steps=invalid)


@pytest.mark.parametrize("stage", ["before", "after", "timeout"])
def test_unknown_outcome_stops_without_another_dispatch(stage):
    class UnknownEnv(DeviceEnv):
        def execute(self, request):
            result = super().execute(request)
            if stage == "timeout":
                raise LabError(ErrorCode.PROVIDER_TIMEOUT, "private simulator payload")
            result["command"]["state"] = "outcome_unknown"
            return result

    env = UnknownEnv()
    if stage == "before":
        env.command = {"state": "outcome_unknown"}
    result = run_episode(EmptyTransfer(), env, max_steps=7)
    assert result["stop_reason"] == "outcome_unknown"
    assert len(env.requests) == (0 if stage == "before" else 1)
    assert "private simulator payload" not in json.dumps(result)


@pytest.mark.stubbed_backend
@pytest.mark.parametrize("named", [True, False])
def test_a_write_failure_after_dispatch_is_an_unknown_outcome(named):
    # The manager names the command when the device may have executed.
    class FailedWriteEnv(DeviceEnv):
        def execute(self, request):
            super().execute(request)
            raise LabError(
                ErrorCode.PERSISTENCE_UNAVAILABLE,
                "Device may have executed",
                {"command_id": "labcmd-000000000001"} if named else None,
            )

    env = FailedWriteEnv()
    result = run_episode(EmptyTransfer(), env, max_steps=7)
    assert result["stop_reason"] == ("outcome_unknown" if named else "error")
    assert len(env.requests) == 1


def test_manager_adapter_calls_only_manager_and_projects_all_public_results():
    raw = DeviceEnv()
    calls = []
    caller = LabCaller("root", "frame", None, CommandOrigin.SYSTEM, None, "agent")

    class Manager:
        def status(self, received_caller, run_id):
            calls.append(("status", received_caller, run_id))
            return raw.status()

        def observe(self, received_caller, run_id, *, full=False):
            calls.append(("observe", received_caller, run_id, full))
            return {"observation": raw.observe(), "evaluation": raw.opened.evaluation}

        def execute(self, received_caller, request):
            calls.append(("execute", received_caller, request))
            return raw.execute(request)

        def observations(
            self, received_caller, run_id, *, after_sequence=-1, limit=20, full=False
        ):
            calls.append(("observations", received_caller, run_id, full))
            latest = raw.observe()
            return {
                "observations": (
                    [latest] if latest["sequence"] > after_sequence else []
                )[:limit],
                "next_after_sequence": latest["sequence"],
            }

    env = ManagerPolicyEnv(
        Manager(), caller, raw.run["run_id"], descriptor=raw.descriptor
    )
    initial = env.status()
    observation = env.observe()
    request = {
        **FixedRulePolicy().select_action(initial["descriptor"], observation, step=0),
        "run_id": raw.run["run_id"],
        "expected_revision": 0,
        "idempotency_key": "adapter-first",
    }
    executed = env.execute(request)
    result = run_episode(FixedRulePolicy(), env, max_steps=20)
    public = json.dumps([initial, observation, executed, result])
    for private in (
        "evaluation",
        "ground_truth",
        "reward",
        "moles",
        "gym_action",
        "provider_action",
        "fencing_token",
        "config",
    ):
        assert private not in public
    assert {item[0] for item in calls} == {
        "status",
        "observe",
        "execute",
        "observations",
    }
    assert all(item[1] == caller for item in calls)
    # Policies read full sensors: the agent view may summarize large arrays.
    assert all(
        item[3] is True for item in calls if item[0] in {"observe", "observations"}
    )
    before = len(calls)
    with pytest.raises(LabError) as caught:
        env.execute({**request, "run_id": "different-run"})
    assert caught.value.code is ErrorCode.RUN_NOT_FOUND
    assert len(calls) == before
    raw.run["capability_revision"] = "wrong"
    with pytest.raises(LabError) as caught:
        env.status()
    assert caught.value.code is ErrorCode.ADAPTER_MISMATCH


def test_fixed_policy_ends_safely_when_toy_lacks_planned_capability():
    env = DeviceEnv()
    capabilities = tuple(
        cap
        for cap in env.descriptor.capabilities
        if cap.operation == "end_experiment"
        or (cap.operation, cap.source, cap.target)
        == ("transfer_liquid", "beaker_1", "extraction_vessel")
    )
    env.descriptor = replace(
        env.descriptor,
        capabilities=capabilities,
        capability_revision=capability_revision(capabilities),
    )
    env.run["capability_revision"] = env.descriptor.capability_revision
    result = run_episode(FixedRulePolicy(), env, max_steps=3)
    assert result["stop_reason"] == "ended"
    assert [request["operation"] for request in env.requests] == ["end_experiment"]
