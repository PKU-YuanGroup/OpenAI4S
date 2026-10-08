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
    "difference", ["config", "assumption", "initial", "goal", "cohort", "missing"]
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
