"""Private evaluation of complete ledger snapshots, never an agent projection.

Reward, terminal flags, transport success and an explicitly defined goal are
independent. Missing evidence remains unknown (JSON null metrics; the string
"unknown" for the three-valued goal). No runtime imports of simulator packages.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from openai4s.lab.models import canonical_json, sha256_hex

UPSTREAM_SHA = "ab8227b6b33f13617b7e551bdf6b894df7eec68d"
WURTZ_TARGETS = (
    "dodecane",
    "5-methylundecane",
    "4-ethyldecane",
    "5,6-dimethyldecane",
    "4-ethyl-5-methylnonane",
    "4,5-diethyloctane",
    "NaCl",
)


@dataclass(frozen=True)
class Goal:
    """Amount/purity in one named collection vessel; mole basis, not volume.

    dissolve_salt folds paired Na/Cl into NaCl equivalents in this vessel only.
    exclude_materials affects the purity denominator, never target amount.
    Thresholds are benchmark choices, not upstream success criteria.
    """

    profile: str
    target: str
    resource_id: str
    min_amount_mol: float
    min_purity: float
    exclude_materials: tuple[str, ...] = ()
    dissolve_salt: bool = False
    definition: str = "explicit benchmark goal"
    upstream_evidence: tuple[str, ...] = ()
    source_sha: str | None = None

    def __post_init__(self):
        if (
            not all(
                isinstance(x, str) and x
                for x in (self.profile, self.target, self.resource_id)
            )
            or not _number(self.min_amount_mol)
            or self.min_amount_mol <= 0
            or not _number(self.min_purity)
            or not 0 <= self.min_purity <= 1
            or type(self.dissolve_salt) is not bool
            or (self.dissolve_salt and self.target != "NaCl")
            or self.target in self.exclude_materials
            or not isinstance(self.exclude_materials, tuple)
            or any(not isinstance(x, str) for x in self.exclude_materials)
        ):
            raise ValueError("Invalid evaluation goal")


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def default_goal(profile: str, *, target: str | None = None) -> Goal:
    """GenWurtz's target MUST come from initial public targets or the caller.

    Both defaults require >=0.5 mol at >=0.9 purity in beaker_1. WaterOil's
    purity counts oil contamination but excludes water; GenWurtz uses upstream
    non-solvent purity. These thresholds and collection vessel are our choices.
    """
    common = ("chemistrylab/util/reward.py:16-39,74-95",)
    if profile == "WaterOilExtract-v0" and target in (None, "NaCl"):
        return Goal(
            profile,
            "NaCl",
            "beaker_1",
            0.5,
            0.9,
            ("H2O",),
            True,
            "WaterOil collection goal: paired salt equivalents; water excluded, oil counted. Upstream sums salt minus oil over both work vessels; thresholds/vessel/purity are benchmark choices.",
            common + ("chemistrylab/benches/extract_bench.py:115-124,202-231",),
            UPSTREAM_SHA,
        )
    if profile == "GenWurtzExtract-v2" and target in WURTZ_TARGETS:
        return Goal(
            profile,
            target,
            "beaker_1",
            0.5,
            0.9,
            ("C6H14", "diethyl ether"),
            target == "NaCl",
            "GenWurtz collection goal: upstream target-equivalent non-solvent purity; upstream sums amount times purity over three work vessels. Thresholds/vessel are benchmark choices; target is selected at reset.",
            common
            + (
                "chemistrylab/benches/extract_bench.py:69-86,158-165",
                "chemistrylab/benches/general_bench.py:235-240",
            ),
            UPSTREAM_SHA,
        )
    raise ValueError("Unsupported profile or missing/invalid explicit target")


def _vessels(evaluation):
    if not isinstance(evaluation, Mapping):
        return None
    truth = evaluation.get("ground_truth")
    rows = truth.get("vessels") if isinstance(truth, Mapping) else None
    if not isinstance(rows, list) or not rows:
        return None
    result = {}
    for row in rows:
        if not isinstance(row, Mapping):
            return None
        resource, moles = row.get("resource_id"), row.get("moles")
        if (
            not isinstance(resource, str)
            or resource in result
            or not isinstance(moles, Mapping)
        ):
            return None
        if any(
            not isinstance(k, str) or not _number(v) or v < 0 for k, v in moles.items()
        ):
            return None
        if not _number(row.get("volume_L")) or row["volume_L"] < 0:
            return None
        result[resource] = dict(row)
    return result


def _fingerprint(value):
    return sha256_hex(canonical_json(value))


def _goal_metrics(vessels, goal):
    if vessels is None or goal.resource_id not in vessels:
        return {"goal_met": "unknown", "target_amount_mol": None, "purity": None}
    moles = vessels[goal.resource_id]["moles"]
    dissolved = min(moles.get("Na", 0), moles.get("Cl", 0)) if goal.dissolve_salt else 0
    amount = moles.get(goal.target, 0) + dissolved
    denominator = (
        sum(v for k, v in moles.items() if k not in goal.exclude_materials) - dissolved
    )
    purity = amount / denominator if denominator > 0 else None
    return {
        "goal_met": bool(
            amount >= goal.min_amount_mol
            and purity is not None
            and purity >= goal.min_purity
        ),
        "target_amount_mol": amount,
        "purity": purity,
    }


def evaluate_run(run, commands, observations, evaluations, *, goal: Goal) -> dict:
    """Evaluate one full run snapshot (decoded LabLedger rows).

    Callers must exhaust ledger pagination. Snapshot rows cannot recover prior
    unknown states after reconciliation, or count actual dispatch attempts:
    those metrics intentionally remain unavailable, not a fabricated zero.
    """
    if not isinstance(goal, Goal):
        raise ValueError("An explicit Goal is required")
    commands, observations, evaluations = (
        list(commands),
        list(observations),
        list(evaluations),
    )
    run_id = run.get("run_id")
    problems = []
    if not run_id or any(
        row.get("run_id") != run_id for row in commands + observations + evaluations
    ):
        problems.append("rows do not all belong to the run")
    ids = [c.get("command_id") for c in commands]
    if None in ids or len(ids) != len(set(ids)):
        problems.append("command ids are missing or duplicated")
    sequences = [e.get("sequence") for e in evaluations]
    if any(type(s) is not int or s < 0 for s in sequences) or len(sequences) != len(
        set(sequences)
    ):
        problems.append("evaluation sequences are missing or duplicated")
    steps = run.get("step_count")
    if type(steps) is not int or steps < 0:
        problems.append("run step count is unavailable")
    success = [c for c in commands if c.get("state") == "succeeded"]
    if type(steps) is int and steps != len(success):
        problems.append("successful command snapshot does not cover run steps")
    if commands and sorted(c.get("seq", -1) for c in commands) != list(
        range(1, len(commands) + 1)
    ):
        problems.append("command sequence has gaps")
    if run.get("command_count") is not None and run["command_count"] != len(commands):
        problems.append("command snapshot does not cover command count")
    initial_rows = [
        e for e in evaluations if e.get("sequence") == 0 and e.get("command_id") is None
    ]
    initial = initial_rows[0] if len(initial_rows) == 1 else None
    final_rows = [e for e in evaluations if e.get("sequence") == steps]
    final = final_rows[0] if len(final_rows) == 1 else None
    by_command = {c.get("command_id"): c for c in success}
    for e in evaluations:
        if e.get("sequence") == 0:
            continue
        c = by_command.get(e.get("command_id"))
        if c is None or c.get("applied_revision") != e.get("sequence"):
            problems.append("evaluation is not bound to the applied command revision")
            break
    if type(steps) is int and set(sequences) != set(range(steps + 1)):
        problems.append("evaluation sequence is incomplete")
    if run.get("profile") != goal.profile:
        problems.append("goal profile differs from run")
    if goal.source_sha and run.get("backend_source_sha") != goal.source_sha:
        problems.append("goal upstream source differs from run")
    initial_vessels, final_vessels = _vessels(initial), _vessels(final)
    if initial_vessels is None:
        problems.append("initial state evidence is unavailable")
    if final_vessels is None:
        problems.append("final state evidence is unavailable")
    if any(
        c.get("state")
        in {"dispatching", "running", "stop_requested", "outcome_unknown"}
        for c in commands
    ):
        problems.append("a command may have changed the final state without evidence")
    if final_vessels is not None and goal.resource_id not in final_vessels:
        problems.append("goal collection vessel evidence is unavailable")
    result = _goal_metrics(final_vessels if not problems else None, goal)

    covered = sum(
        any(
            o.get("observation_id") == c.get("observation_id")
            and o.get("command_id") == c.get("command_id")
            and o.get("sequence") == c.get("applied_revision")
            and isinstance(o.get("channels"), list)
            and bool(o["channels"])
            for o in observations
        )
        for c in success
    )
    rewards = [
        e.get("reward")
        for e in evaluations
        if type(e.get("sequence")) is int and e["sequence"] > 0
    ]
    reward_complete = (
        not problems and len(rewards) == steps and all(_number(r) for r in rewards)
    )
    # Feed consumption = positive net depletion of named source vessels, not
    # disappearance from the whole system. Material returned to stock offsets it.
    descriptor = run.get("descriptor", {})
    caps = descriptor.get("capabilities", [])
    sources = {c.get("source") for c in caps if c.get("operation") == "transfer_liquid"}
    sinks = {c.get("target") for c in caps if c.get("target") is not None}
    stock_ids = sorted(sources - sinks - {None})
    consumption = None
    if (
        not problems
        and stock_ids
        and all(r in initial_vessels and r in final_vessels for r in stock_ids)
    ):
        consumption = {
            r: {
                "volume_L": max(
                    0, initial_vessels[r]["volume_L"] - final_vessels[r]["volume_L"]
                ),
                "moles": {
                    k: max(0, v - final_vessels[r]["moles"].get(k, 0))
                    for k, v in initial_vessels[r]["moles"].items()
                },
            }
            for r in stock_ids
        }
    created, end = run.get("created_at"), run.get("ended_at")
    # For live runs updated_at only marks a ledger write, not elapsed wall time.
    wall = (
        end - created if _number(end) and _number(created) and end >= created else None
    )
    usage = {
        "steps": steps,
        "commands": len(commands),
        "wall_ms": wall,
        "consecutive_failures": run.get("consecutive_failures"),
    }
    limits = run.get("budgets", {})
    keys = {
        "steps": "max_steps",
        "commands": "max_commands",
        "wall_ms": "max_wall_ms",
        "consecutive_failures": "max_consecutive_failures",
    }
    budget = {
        key: {
            "used": used,
            "limit": limits.get(keys[key]),
            "fraction": (
                used / limits[keys[key]]
                if _number(used)
                and _number(limits.get(keys[key]))
                and limits[keys[key]] > 0
                else None
            ),
        }
        for key, used in usage.items()
    }
    illegal = {"invalid_parameters", "unsupported_action", "unit_mismatch"}
    unknown_ids = [
        c["command_id"] for c in commands if c.get("state") == "outcome_unknown"
    ]
    config_evidence = {
        k: run.get(k)
        for k in (
            "mode",
            "backend",
            "device_id",
            "profile",
            "adapter_version",
            "backend_source_sha",
            "capability_revision",
            "config_hash",
            "config",
            "seed",
            "budgets",
        )
    }
    config_evidence["descriptor"] = descriptor
    result.update(
        {
            "source": "simulator_ground_truth",
            "run_id": run_id,
            "goal": asdict(goal),
            "evidence_issues": list(dict.fromkeys(problems)),
            "action_count": len(commands),
            "applied_action_count": len(success),
            "rejected_illegal_action_count": sum(
                c.get("state") in {"rejected", "failed", "not_dispatched"}
                and c.get("error_code") in illegal
                for c in commands
            ),
            "duplicate_dispatch_count": None,
            "duplicate_dispatch_reason": "Command snapshots contain no dispatch-attempt history; zero cannot be attested.",
            "outcome_unknown": {
                "unresolved_count": len(unknown_ids),
                "command_ids": unknown_ids,
                "historical_count": None,
                "subsequent_results": None,
                "reason": "Reconciliation overwrites command state; historical outcomes require event evidence.",
            },
            "material_consumption": {
                "basis": "positive net stock depletion; not total transfer or chemical destruction",
                "by_resource": consumption,
            },
            "budget_usage": budget,
            "evidence_completeness": {
                "successful_commands": len(success),
                "with_observation": covered,
                "fraction": covered / len(success) if success else None,
            },
            "rewards": {
                "initial_baseline": (
                    initial.get("reward")
                    if initial and _number(initial.get("reward"))
                    else None
                ),
                "step_sum": sum(rewards) if reward_complete else None,
                "step_count": len(rewards),
                "complete": reward_complete,
            },
            "comparability": {
                "configuration": _fingerprint(config_evidence),
                "initial_state": (
                    _fingerprint(
                        {
                            "vessels": sorted(
                                initial_vessels.values(), key=lambda v: v["resource_id"]
                            )
                        }
                    )
                    if initial_vessels
                    else None
                ),
                "goal": _fingerprint(asdict(goal)),
                "complete": run.get("status") in {"ended", "failed"}
                and not problems
                and all(
                    run.get(k) is not None
                    for k in (
                        "config_hash",
                        "config",
                        "budgets",
                        "capability_revision",
                        "backend_source_sha",
                        "adapter_version",
                    )
                )
                and bool(descriptor),
            },
        }
    )
    return result


def compare(results_by_policy: Mapping[str, Sequence[Mapping] | Mapping]) -> dict:
    """Paired comparisons require identical configuration AND initial truth.

    A missing pair, unknown goal, or incompatible goal/wrapper/version refuses
    the whole comparison; no averages over silently different cohorts.
    """
    cohorts = {
        name: [rows] if isinstance(rows, Mapping) else list(rows)
        for name, rows in results_by_policy.items()
    }
    reasons = []
    if len(cohorts) < 2 or any(not rows for rows in cohorts.values()):
        reasons.append("at least two nonempty policy cohorts are required")
    keys = []
    for name, rows in cohorts.items():
        cohort = []
        for row in rows:
            evidence = row.get("comparability", {})
            if not evidence.get("complete") or row.get("goal_met") not in (True, False):
                reasons.append(
                    f"{name}: complete episode, goal and initial/configuration evidence is required"
                )
            cohort.append(
                tuple(
                    evidence.get(k) for k in ("configuration", "initial_state", "goal")
                )
            )
        keys.append(Counter(cohort))
    if keys and any(k != keys[0] for k in keys[1:]):
        reasons.append("configuration, goal or paired initial states differ")
    if reasons:
        return {"comparable": False, "reasons": list(dict.fromkeys(reasons))}
    return {
        "comparable": True,
        "policies": {
            name: {
                "episodes": len(rows),
                "goal_met_rate": sum(r["goal_met"] is True for r in rows) / len(rows),
                "mean_action_count": sum(r["action_count"] for r in rows) / len(rows),
            }
            for name, rows in cohorts.items()
        },
    }
