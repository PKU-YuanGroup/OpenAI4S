"""Evaluation uses composition evidence, never reward or Gym done as success."""

from copy import deepcopy
from dataclasses import replace

import pytest

from openai4s.lab.evaluation import (
    UPSTREAM_SHA,
    Goal,
    compare,
    default_goal,
    evaluate_run,
)


def rows(profile="WaterOilExtract-v0"):
    run = {
        "run_id": "run",
        "profile": profile,
        "mode": "simulation",
        "backend": "chemgymrl",
        "device_id": "device",
        "backend_source_sha": UPSTREAM_SHA,
        "adapter_version": "1",
        "capability_revision": "revision",
        "config_hash": "config",
        "config": {},
        "seed": 7,
        "step_count": 1,
        "status": "ended",
        "created_at": 100,
        "ended_at": 400,
        "budgets": {
            "max_steps": 50,
            "max_commands": 200,
            "max_wall_ms": 1000,
            "max_consecutive_failures": 3,
        },
        "consecutive_failures": 0,
        "descriptor": {
            "assumptions": [],
            "capabilities": [
                {
                    "operation": "transfer_liquid",
                    "source": "h2o_vessel",
                    "target": "beaker_1",
                },
            ],
        },
    }
    commands = [
        {
            "run_id": "run",
            "command_id": "cmd",
            "seq": 1,
            "state": "succeeded",
            "applied_revision": 1,
            "observation_id": "obs",
            "dispatched_at": 200,
            "receipt": {"applied": True},
        }
    ]
    observations = [
        {
            "run_id": "run",
            "command_id": "cmd",
            "sequence": 1,
            "observation_id": "obs",
            "channels": [{"name": "layers"}],
        }
    ]

    def vessel(resource, moles, volume=1):
        return {
            "resource_id": resource,
            "moles": moles,
            "volume_L": volume,
            "temperature_K": 297,
        }

    evaluations = [
        {
            "run_id": "run",
            "command_id": None,
            "sequence": 0,
            "reward": 17,
            "ground_truth": {
                "vessels": [
                    vessel("beaker_1", {}),
                    vessel("extraction_vessel", {"Na": 1, "Cl": 1, "C6H14": 1}),
                    vessel("h2o_vessel", {"H2O": 10}, 2),
                ]
            },
        },
        {
            "run_id": "run",
            "command_id": "cmd",
            "sequence": 1,
            "reward": -3,
            "ground_truth": {
                "vessels": [
                    vessel("beaker_1", {"Na": 0.6, "Cl": 0.6, "H2O": 3, "C6H14": 0.02}),
                    vessel("extraction_vessel", {"Na": 0.4, "Cl": 0.4, "C6H14": 0.98}),
                    vessel("h2o_vessel", {"H2O": 7}, 1.5),
                ]
            },
        },
    ]
    return run, commands, observations, evaluations


def evaluate(data=None, goal=None):
    return evaluate_run(
        *(rows() if data is None else data),
        goal=default_goal("WaterOilExtract-v0") if goal is None else goal,
    )


def test_goal_uses_dissolved_composition_not_reward_terminal_or_tool_success():
    data = rows()
    original = deepcopy(data)
    good = evaluate(data)
    assert good["source"] == "simulator_ground_truth"
    assert good["goal_met"] is True
    assert good["target_amount_mol"] == pytest.approx(0.6)
    assert good["purity"] == pytest.approx(0.6 / 0.62)
    assert good["goal"]["source_sha"] == UPSTREAM_SHA
    assert good["goal"]["upstream_evidence"]
    assert data == original
    data[3][1]["reward"] = 999
    data[3][1]["ground_truth"]["vessels"][0]["moles"]["C6H14"] = 1
    bad = evaluate(data)
    assert bad["goal_met"] is False
    assert bad["purity"] == pytest.approx(0.6 / 1.6)
    data[3][1]["ground_truth"]["vessels"][0]["moles"] = {"Na": 0.1, "Cl": 0.1}
    assert evaluate(data)["goal_met"] is False  # Pure but insufficient quantity.


@pytest.mark.parametrize("target", ["dodecane", "NaCl"])
def test_wurtz_default_requires_public_target_and_uses_target_specific_purity(target):
    goal = default_goal("GenWurtzExtract-v2", target=target)
    data = rows(goal.profile)
    data[3][1]["ground_truth"]["vessels"][0]["moles"] = (
        {
            "dodecane": 0.6,
            "Na": 0.1,
            "Cl": 0.1,
            "C6H14": 100,
            "diethyl ether": 100,
        }
        if target != "NaCl"
        else {"Na": 0.6, "Cl": 0.6, "C6H14": 100, "diethyl ether": 100}
    )
    result = evaluate(data, goal)
    assert result["target_amount_mol"] == pytest.approx(0.6)
    assert result["purity"] == pytest.approx(0.75 if target == "dodecane" else 1)
    assert result["goal_met"] is (target == "NaCl")
    with pytest.raises(ValueError):
        default_goal("GenWurtzExtract-v2")


@pytest.mark.parametrize(
    "missing",
    [
        "vessel",
        "moles",
        "nan",
        "negative",
        "final",
        "initial",
        "unknown",
        "wrong_run",
        "wrong_revision",
        "wrong_sha",
        "wrong_profile",
        "gap",
    ],
)
def test_incomplete_or_invalid_evidence_is_unknown(missing):
    data = rows()
    if missing == "vessel":
        data[3][1]["ground_truth"]["vessels"].pop(0)
    elif missing == "moles":
        del data[3][1]["ground_truth"]["vessels"][0]["moles"]
    elif missing in {"nan", "negative"}:
        data[3][1]["ground_truth"]["vessels"][0]["moles"]["Na"] = (
            float("nan") if missing == "nan" else -1
        )
    elif missing == "final":
        data[3].pop()
    elif missing == "initial":
        data[3].pop(0)
    elif missing == "unknown":
        data[1].append(
            {"run_id": "run", "command_id": "u", "seq": 2, "state": "outcome_unknown"}
        )
    elif missing == "wrong_run":
        data[3][1]["run_id"] = "another"
    elif missing == "wrong_revision":
        data[1][0]["applied_revision"] = 2
    elif missing == "wrong_sha":
        data[0]["backend_source_sha"] = "unverified"
    elif missing == "wrong_profile":
        data[0]["profile"] = "elsewhere"
    else:
        data[0]["step_count"] = 2
    assert evaluate(data)["goal_met"] == "unknown"


def test_ledger_metrics_rewards_stock_depletion_budgets_and_missing_history():
    data = rows()
    data[1].extend(
        [
            {
                "run_id": "run",
                "command_id": "bad",
                "seq": 2,
                "state": "rejected",
                "error_code": "unsupported_action",
            },
            {
                "run_id": "run",
                "command_id": "busy",
                "seq": 3,
                "state": "failed",
                "error_code": "resource_busy",
            },
        ]
    )
    result = evaluate(data)
    assert result["action_count"] == 3 and result["applied_action_count"] == 1
    assert result["rejected_illegal_action_count"] == 1
    assert result["rewards"] == {
        "initial_baseline": 17,
        "step_sum": -3,
        "step_count": 1,
        "complete": True,
    }
    assert result["material_consumption"]["by_resource"]["h2o_vessel"] == {
        "volume_L": 0.5,
        "moles": {"H2O": 3},
    }
    assert result["budget_usage"]["wall_ms"] == {
        "used": 300,
        "limit": 1000,
        "fraction": 0.3,
    }
    assert result["budget_usage"]["steps"]["fraction"] == 0.02
    assert result["evidence_completeness"]["fraction"] == 1
    assert result["duplicate_dispatch_count"] is None
    assert result["outcome_unknown"]["historical_count"] is None
    data[1].append(
        {"run_id": "run", "command_id": "u", "seq": 4, "state": "outcome_unknown"}
    )
    assert evaluate(data)["outcome_unknown"]["unresolved_count"] == 1
    assert evaluate(data)["outcome_unknown"]["subsequent_results"] is None


def test_success_evidence_requires_its_own_observation_and_zero_commands_is_not_full_evidence():
    data = rows()
    data[2][0]["command_id"] = "wrong-command"
    assert evaluate(data)["evidence_completeness"]["fraction"] == 0
    data[0]["step_count"] = 0
    data[1].clear()
    data[2].clear()
    data[3].pop()
    result = evaluate(data)
    assert result["evidence_completeness"]["fraction"] is None
    assert result["rewards"]["step_sum"] == 0


@pytest.mark.parametrize(
    "difference",
    ["config", "assumption", "initial", "goal", "cohort", "missing", "unfinished"],
)
def test_comparison_refuses_unmatched_evidence_without_numbers(difference):
    first = evaluate()
    data = rows()
    goal = default_goal("WaterOilExtract-v0")
    if difference == "config":
        data[0]["config"] = {"different": True}
    elif difference == "assumption":
        data[0]["descriptor"]["assumptions"] = ["noise"]
    elif difference == "initial":
        data[3][0]["ground_truth"]["vessels"][1]["moles"]["Na"] = 0.5
    elif difference == "goal":
        goal = replace(goal, min_purity=0.5)
    elif difference == "unfinished":
        data[0]["status"] = "ready"
    elif difference == "missing":
        del data[0]["config_hash"]
    second = evaluate(data, goal)
    results = {
        "fixed": [first],
        "random": [second, second] if difference == "cohort" else [second],
    }
    refused = compare(results)
    assert refused["comparable"] is False
    assert set(refused) == {"comparable", "reasons"} and refused["reasons"]
    matched = compare({"fixed": [first], "random": [deepcopy(first)]})
    assert matched["comparable"] is True
    assert matched["policies"]["fixed"]["goal_met_rate"] == 1


def test_development_probe_runs_seeded_cohorts_and_counts_actual_dispatches(tmp_path):
    from scripts.lab_evaluate import run_probe

    def probe():
        return run_probe(work_dir=tmp_path, episodes=2, seed=7, max_steps=20)

    first, second = probe(), probe()
    assert first["execution_boundary"] == "device_contract_probe"
    assert first["comparison"]["comparable"] is True
    for policy, results in first["results_by_policy"].items():
        for index, result in enumerate(results):
            again = second["results_by_policy"][policy][index]
            assert result["goal_met"] == again["goal_met"]
            assert result["action_count"] == again["action_count"]
            assert result["comparability"] == again["comparability"]
            assert result["rejected_illegal_action_count"] == 0
            assert result["duplicate_dispatch_count"] == 0
            assert result["outcome_unknown"]["historical_count"] == 0
            assert result["evidence_completeness"]["fraction"] == 1
            assert not result["evidence_issues"]


@pytest.mark.external
@pytest.mark.parametrize("profile", ["WaterOilExtract-v0", "GenWurtzExtract-v2"])
def test_real_chemgymrl_fixed_policy_goal_matches_composition_accounting(profile):
    import json
    import os
    from pathlib import Path
    from uuid import uuid4

    from openai4s.lab.policies import FixedRulePolicy, run_episode
    from scripts.lab_evaluate import _ProbeEnv, _ProviderDevice

    python = os.environ.get("OPENAI4S_LAB_CHEMGYMRL_PYTHON")
    if not python:
        pytest.skip("Set OPENAI4S_LAB_CHEMGYMRL_PYTHON to the pinned isolated provider")
    directory = (
        Path(__file__).resolve().parents[2] / "_data/W2-C/external" / uuid4().hex
    )
    port = _ProviderDevice("chemgymrl", python, directory)
    env = None
    try:
        env = _ProbeEnv(port, profile, 42, 50)
        target = next(
            c["value"] for c in env.observe()["channels"] if c["name"] == "targets"
        )
        goal = default_goal(profile, target=target)
        episode = run_episode(FixedRulePolicy(), env, max_steps=50)
        env.finish()
        result = env.evaluate(goal)
        assert episode["stop_reason"] == "ended"
        assert episode["steps"] == 13
        assert result["rejected_illegal_action_count"] == 0
        assert not result["evidence_issues"]
        # Independent reconstruction of the pinned upstream reward accounting:
        # reward.py:16-39,74-95, scoped to the explicitly chosen collection vessel.
        vessels = env._evaluations[-1]["ground_truth"]["vessels"]
        moles = next(v["moles"] for v in vessels if v["resource_id"] == "beaker_1")
        paired = min(moles.get("Na", 0), moles.get("Cl", 0)) if target == "NaCl" else 0
        q = moles.get(target, 0) + paired
        excluded = (
            {"H2O"} if profile == "WaterOilExtract-v0" else {"C6H14", "diethyl ether"}
        )
        total = sum(v for k, v in moles.items() if k not in excluded) - paired
        expected_purity = q / total if total > 0 else None
        expected_met = (
            q >= 0.5 and expected_purity is not None and expected_purity >= 0.9
        )
        assert result["goal_met"] is expected_met
        assert result["target_amount_mol"] == pytest.approx(q)
        assert (
            result["purity"] == pytest.approx(expected_purity)
            if expected_purity is not None
            else result["purity"] is None
        )
        assert result["rewards"]["initial_baseline"] == env._evaluations[0]["reward"]
        assert result["rewards"]["step_sum"] == pytest.approx(
            sum(e["reward"] for e in env._evaluations[1:])
        )
        (directory / "evaluation.json").write_text(
            json.dumps(result, indent=2, allow_nan=False) + "\n"
        )
    finally:
        if env is not None:
            env.close()
        else:
            port.close()


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "entry",
    ["describe", "open", "execute", "query", "stop", "query_identity", "error_code"],
)
def test_probe_invalid_response_kills_provider_instead_of_claiming_live(
    entry, monkeypatch, tmp_path
):
    import sys

    import scripts.lab_evaluate as probe
    from openai4s.lab.fake import FakeExtractorDevice
    from openai4s.lab.manifest import match_command
    from openai4s.lab.models import (
        CommandRequest,
        Dispatch,
        ErrorCode,
        LabError,
        Quantity,
        SessionOpenRequest,
    )

    base = FakeExtractorDevice()
    descriptor = base.describe("toy-extract-v0")
    request = SessionOpenRequest(
        descriptor.profile, 7, {}, descriptor.capability_revision
    )
    o = base.open(request)
    capability = next(c for c in descriptor.capabilities if c.operation == "mix_model")
    command = match_command(
        descriptor,
        CommandRequest(
            "run",
            capability.operation,
            capability.source,
            capability.target,
            {
                k: Quantity(v.allowed[0], v.unit)
                for k, v in capability.parameters.items()
            },
            0,
            "key",
        ),
    )
    dispatch = Dispatch("command", command, {r: 1 for r in capability.resources})
    receipt = base.execute(o.session_id, dispatch)
    responses = {
        "hello": {"protocol": 1, "backend": "toy"},
        "describe": descriptor.to_dict(),
        "open": o.to_dict(),
        "execute": receipt.to_dict(),
        "query": receipt.to_dict(),
        "stop": {"stopped": True, "semantics": "end_session"},
    }
    if entry == "query_identity":
        responses["query"]["provider_command_id"] = "different"
    else:
        responses[entry] = {"malformed": True}

    class Client:
        live = True

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            return self

        def request(self, op, args, *, timeout):
            if entry == "error_code" and op == "execute":
                raise probe.ProviderError("future_invalid_code", "Invalid error frame")
            return deepcopy(responses[op])

        def close(self):
            self.live = False

        def alive(self):
            return self.live

    monkeypatch.setattr(probe, "ProviderClient", Client)
    port = probe._ProviderDevice("toy", sys.executable, tmp_path)
    with pytest.raises(LabError) as caught:
        port.describe(descriptor.profile)
        port.open(request)
        port.execute(o.session_id, dispatch)
        port.query(o.session_id, "command")
        port.stop(o.session_id, "test")
    assert caught.value.code is ErrorCode.PROVIDER_PROTOCOL_ERROR
    assert not port.alive(o.session_id) and not port._client.alive()
