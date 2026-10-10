"""Offline identification, numerical inference and export contracts for DiD.

Numerical expectations come from small contrasts with hand-computable cluster
scores. No optional statistics package is used to verify the stdlib sidecar.
"""

from __future__ import annotations

import builtins
import copy
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.evals import did_analysis as did_evals
from openai4s.skills_loader import SkillLoader

ROOT = Path(__file__).resolve().parents[1]
DESIGN = {
    "event_source": "Researcher supplied policy registry; date recorded before analysis.",
    "treatment_assignment": "Policy assigned at the named district cluster.",
    "parallel_trends": "Researcher specifies comparison districts and parallel trends assumption.",
}


@pytest.fixture(scope="module")
def did():
    spec = importlib.util.spec_from_file_location(
        "did_analysis_test", ROOT / "skills/did-analysis/kernel.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(**changes):
    return {
        "entity": "id",
        "time": "t",
        "outcome": "y",
        "treatment": "d",
        "treatment_time": 1,
        "pre_periods": [0],
        "post_periods": [1],
        "cluster": "district",
        "research_design": copy.deepcopy(DESIGN),
        **changes,
    }


def _panel(treated=(2, 4, 6), controls=(0, 1, 2), *, times=(0, 1), date=1):
    rows = []
    for prefix, changes, group in (("T", treated, 1), ("C", controls, 0)):
        for i, change in enumerate(changes):
            entity = f"{prefix}{i:03d}"
            baseline = 10 * i + 7
            for t in times:
                rows.append(
                    {
                        "id": entity,
                        "district": entity,
                        "t": t,
                        "d": int(group and t >= date),
                        "y": baseline + (change if t >= date else 0),
                    }
                )
    return rows


def _run(did, rows=None, **changes):
    return did.traditional_did(_panel() if rows is None else rows, **_config(**changes))


def _event_panel():
    # Relative to period 2, the two pretrend contrasts are 1 and 2. The six
    # cluster influence vectors have V=[[5/3,5/6],[5/6,2/3]] after CR1=3/2.
    values = {
        "T000": [-1, 2, 0, 2, 3],
        "T001": [1, 3, 0, 4, 6],
        "T002": [3, 4, 0, 6, 9],
        "C000": [-1, 0, 0, 0, 0],
        "C001": [0, 2, 0, 1, 2],
        "C002": [1, 1, 0, 2, 4],
    }
    return [
        {
            "id": unit,
            "district": unit,
            "t": t,
            "y": y,
            "d": int(unit.startswith("T") and t >= 3),
        }
        for unit, series in values.items()
        for t, y in enumerate(series)
    ]


def _event_run(did, rows=None, **changes):
    config = _config(treatment_time=3)
    config.pop("pre_periods")
    config.pop("post_periods")
    return did.event_study(
        _event_panel() if rows is None else rows,
        **{**config, "periods": [0, 1, 3, 4], "reference_period": 2, **changes},
    )


def test_skill_discovery_and_sidecar_compilation():
    skill = SkillLoader().discover()["did-analysis"]
    assert skill.read_only and skill.has_kernel
    assert skill.sidecar_gate() == {"ok": True, "error": None}


def test_sidecar_has_no_required_science_dependencies(monkeypatch):
    original = builtins.__import__

    def deny_science(name, *args, **kwargs):
        if name.split(".")[0] in {"numpy", "pandas", "scipy", "statsmodels"}:
            raise ImportError("science package unavailable in stdlib runtime")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny_science)
    spec = importlib.util.spec_from_file_location(
        "did_without_science", ROOT / "skills/did-analysis/kernel.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert _run(module)["estimate"] == pytest.approx(3)


def test_common_date_cluster_cr1_student_t_matches_closed_form(did):
    result = _run(did)
    # Treated mean change=4; control=1. Cluster score sum of squares=10/9;
    # six independent clusters and N=6,K=2 give CR1=3/2, variance=5/3.
    assert result["estimate"] == pytest.approx(3)
    assert result["se"] == pytest.approx(math.sqrt(5 / 3))
    assert result["inference"]["cr1_factor"] == pytest.approx(1.5)
    assert result["inference"]["df"] == 5
    assert result["inference"]["group_cluster_counts"] == {"0": 3, "1": 3}
    assert result["p_value"] == pytest.approx(0.06773301776557172, abs=1e-12)
    # t_{5,.975}=2.570581835636314, rather than a normal-reference 1.96.
    assert result["ci_low"] == pytest.approx(-0.31860687982757874, abs=1e-10)
    assert result["ci_high"] == pytest.approx(6.318606879827579, abs=1e-10)


def test_units_sharing_assignment_clusters_do_not_inflate_independence(did):
    rows = []
    for row in _panel():
        for sibling in ("a", "b"):
            rows.append({**row, "id": row["id"] + sibling})
    result = _run(did, rows)
    # Duplicated units still have six clusters. N=12,K=2 gives CR1=33/25.
    assert result["estimate"] == pytest.approx(3)
    assert result["inference"]["cluster_count"] == 6
    assert result["inference"]["df"] == 5
    assert result["inference"]["cr1_factor"] == pytest.approx(33 / 25)
    assert result["se"] ** 2 == pytest.approx(22 / 15)


def test_multiple_periods_are_means_with_original_rows_and_provenance(did):
    rows = _panel(times=(-2, -1, 1, 2, 3))
    for position, row in enumerate(rows, 1):
        row["y"] += row["t"] * 2
        row["_source_rows"] = [1000 + position]
    before = copy.deepcopy(rows)
    result = _run(did, rows, pre_periods=[-2, -1], post_periods=[1, 2, 3])
    assert rows == before
    assert result["estimate"] == pytest.approx(3)
    changes = {r["entity"]: r for r in result["unit_changes"]}
    assert changes["T000"]["change"] == pytest.approx(9)
    assert changes["C000"]["change"] == pytest.approx(7)
    assert changes["T000"]["source_rows"] == [1001, 1002, 1003, 1004, 1005]
    assert result["audit"]["included_source_rows"] == list(range(1001, 1031))


@pytest.mark.parametrize(
    "key", ["event_source", "treatment_assignment", "parallel_trends"]
)
def test_researcher_supplied_identification_cannot_be_omitted(did, key):
    design = copy.deepcopy(DESIGN)
    design.pop(key)
    with pytest.raises(ValueError, match=key):
        _run(did, research_design=design)


@pytest.mark.parametrize("value", [None, {}, "infer the policy date from y"])
def test_invalid_research_design_refuses(did, value):
    with pytest.raises(ValueError, match="research_design"):
        _run(did, research_design=value)


@pytest.mark.parametrize("time", [True, 0.5, "2024-01", "2024Q1"])
def test_ambiguous_time_cannot_be_silently_ranked(did, time):
    rows = _panel()
    rows[0]["t"] = time
    with pytest.raises(ValueError, match="integer periods|_period_index"):
        _run(did, rows)


def test_upstream_period_index_accepts_calendar_labels_and_preserves_ids(did):
    rows = _panel()
    for row in rows:
        row["_period_index"] = row["t"]
        row["t"] = "2024Q1" if row["t"] == 0 else "2024Q2"
    result = _run(did, rows, time="_period_index")
    assert result["estimate"] == pytest.approx(3)
    assert result["config"]["time"] == "_period_index"
    assert "T000" in result["audit"]["included_entities"]


@pytest.mark.parametrize("missing", [None, "", float("nan"), "imputed", "absent"])
def test_missing_or_imputed_outcomes_require_explicit_whole_entity_exclusion(
    did, missing
):
    rows = _panel()
    if missing == "absent":
        rows.pop(1)
    elif missing == "imputed":
        rows[1]["y"] = 10000
        rows[1]["_imputed_columns"] = ["y"]
    else:
        rows[1]["y"] = missing
    with pytest.raises(ValueError, match="drop_entity"):
        _run(did, rows)
    result = _run(did, rows, missing_policy="drop_entity")
    assert result["estimate"] == pytest.approx(4)
    assert result["audit"]["included_entity_count"] == 5
    assert result["audit"]["excluded_entities"][0]["entity"] == "T000"
    assert "T000" not in result["audit"]["included_entities"]
    assert not any(r["entity"] == "T000" for r in result["unit_changes"])
    reason = {"absent": "absent_periods", "imputed": "imputed_outcome_periods"}.get(
        missing if isinstance(missing, str) else "", "missing_outcome_periods"
    )
    assert result["audit"]["excluded_entities"][0][reason] == [1]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("column", ["id", "t", "district", "d"])
def test_imputed_design_columns_always_refuse_even_with_drop_entity(did, column):
    rows = _panel()
    rows[0]["_imputed_columns"] = [column]
    with pytest.raises(ValueError, match="imputed"):
        _run(did, rows, missing_policy="drop_entity")


@pytest.mark.parametrize("value", [True, "invalid", float("inf"), -float("inf")])
def test_invalid_measurements_refuse(did, value):
    rows = _panel()
    rows[0]["y"] = value
    with pytest.raises(ValueError):
        _run(did, rows)


def test_duplicate_entity_period_requires_upstream_resolution(did):
    rows = _panel()
    rows.append(copy.deepcopy(rows[0]))
    with pytest.raises(ValueError, match="duplicate entity-period"):
        _run(did, rows)


def test_entity_cannot_move_between_assignment_clusters(did):
    rows = _panel()
    rows[1]["district"] = "other"
    with pytest.raises(ValueError, match="one assignment cluster"):
        _run(did, rows)


def test_treated_and_control_units_cannot_split_assignment_cluster(did):
    rows = _panel()
    for row in rows:
        if row["id"] in {"T000", "C000"}:
            row["district"] = "shared"
    with pytest.raises(ValueError, match="assignment cluster"):
        _run(did, rows)


def test_treatment_reversal_and_mixed_adoption_refuse_causal_common_date(did):
    rows = _panel(times=(0, 1, 2))
    next(r for r in rows if r["id"] == "T000" and r["t"] == 2)["d"] = 0
    with pytest.raises(ValueError, match="reversal"):
        _run(did, rows)
    rows = _panel(times=(0, 1, 2))
    next(r for r in rows if r["id"] == "T000" and r["t"] == 1)["d"] = 0
    with pytest.raises(ValueError, match="common treatment date"):
        _run(did, rows)
    descriptive = _run(did, rows, descriptive_staggered_benchmark=True)
    assert descriptive["causal_status"] == "descriptive_staggered_benchmark"


@pytest.mark.parametrize("pre,post", [([0, 1], [1]), ([0], [0, 1]), ([0], [1, 1])])
def test_contaminated_or_duplicated_prespecified_windows_refuse(did, pre, post):
    with pytest.raises(ValueError):
        _run(did, pre_periods=pre, post_periods=post)


def test_covariate_adjustment_is_not_claimed_without_implementation(did):
    with pytest.raises(ValueError, match="covariate adjustment"):
        _run(did, covariates=["income"])


def test_missing_policy_is_an_explicit_supported_choice(did):
    with pytest.raises(ValueError, match="missing_policy"):
        _run(did, missing_policy="fill_zero")


def test_one_cluster_per_arm_with_many_units_cannot_claim_valid_inference(did):
    rows = _panel()
    for row in rows:
        row["district"] = row["id"][0]
    result = _run(did, rows)
    assert result["estimate"] == pytest.approx(3)
    assert result["inference"]["status"] == "unavailable"
    assert result["inference"]["group_cluster_counts"] == {"0": 1, "1": 1}
    assert all(result[k] is None for k in ("se", "p_value", "ci_low", "ci_high"))
    assert result["inference"]["unavailable_reasons"]
    json.dumps(result, allow_nan=False)


def test_perfectly_constant_cluster_changes_report_degenerate_uncertainty(did):
    result = _run(did, _panel(treated=(5, 5, 5), controls=(1, 1, 1)))
    assert result["estimate"] == pytest.approx(4)
    assert result["inference"]["status"] == "unavailable"
    assert all(result[k] is None for k in ("se", "p_value", "ci_low", "ci_high"))
    json.dumps(result, allow_nan=False)


def test_joint_event_pretrend_uses_correlated_cluster_influence(did):
    result = _event_run(did)
    effects = {r["period"]: r for r in result["effects"]}
    assert effects[0]["event_time"] == -3
    assert effects[0]["estimate"] == pytest.approx(1)
    assert effects[1]["estimate"] == pytest.approx(2)
    assert effects[3]["estimate"] == pytest.approx(3)
    covariance = result["covariance"]
    assert covariance[0][:2] == pytest.approx([5 / 3, 5 / 6])
    assert covariance[1][:2] == pytest.approx([5 / 6, 2 / 3])
    joint = result["joint_pretrend"]
    assert joint["status"] == "available"
    assert joint["wald"] == pytest.approx(9.6)
    assert joint["f_statistic"] == pytest.approx(4.8)
    assert (joint["df_num"], joint["df_den"]) == (2, 5)
    # F(2,5) has survival (5/(5+2F))^(5/2), giving an independent check.
    assert joint["p_value"] == pytest.approx(0.06863456276748132, abs=1e-12)


def test_event_study_uses_one_complete_sample_for_joint_covariance(did):
    rows = _event_panel()
    next(r for r in rows if r["id"] == "T000" and r["t"] == 0)["y"] = None
    with pytest.raises(ValueError, match="drop_entity"):
        _event_run(did, rows)
    result = _event_run(did, rows, missing_policy="drop_entity")
    assert result["audit"]["included_entity_count"] == 5
    assert "T000" not in result["audit"]["included_entities"]
    assert {r["inference"]["cluster_count"] for r in result["effects"]} == {5}


def test_event_study_requires_explicit_pre_treatment_reference(did):
    with pytest.raises(ValueError, match="reference"):
        _event_run(did, reference_period=3)


def test_rank_deficient_joint_pretrends_do_not_report_a_wald_p_value(did):
    rows = _event_panel()
    first_lead = {r["id"]: r["y"] for r in rows if r["t"] == 0}
    for row in rows:
        if row["t"] == 1:
            row["y"] = 2 * first_lead[row["id"]]
    result = _event_run(did, rows)
    assert result["joint_pretrend"]["status"] == "unavailable"
    assert result["joint_pretrend"]["p_value"] is None
    assert "rank" in result["joint_pretrend"]["reason"]


def test_fake_date_placebo_has_no_real_treatment_in_either_window(did):
    rows = _panel(times=(-2, -1, 0, 1))
    result = did.placebo_time(
        rows,
        **_config(pre_periods=[-2], post_periods=[-1]),
        fake_treatment_time=-1,
    )
    assert result["estimate"] == pytest.approx(0)
    assert result["causal_status"] == "falsification_diagnostic"
    assert result["audit"]["required_periods"] == [-2, -1]
    with pytest.raises(ValueError, match="before real treatment"):
        did.placebo_time(
            rows,
            **_config(pre_periods=[-2], post_periods=[-1, 1]),
            fake_treatment_time=-1,
        )
    with pytest.raises(ValueError, match="before real treatment"):
        did.placebo_time(rows, **_config(), fake_treatment_time=1)


def test_group_placebo_estimates_only_genuinely_never_treated_entities(did):
    rows = _panel(treated=(100, 110, 120), controls=(0, 1, 2, 3, 4, 5))
    # Missing genuine-treated outcomes must not determine the never-treated
    # placebo sample. Exposure remains observed and genuine treatment is kept.
    for row in rows:
        if row["id"].startswith("T"):
            row["y"] = None
    result = did.placebo_group(
        rows, **_config(), fake_treated_entities=["C000", "C001"]
    )
    assert result["estimate"] == pytest.approx(-3)
    assert result["se"] ** 2 == pytest.approx(21 / 32)
    assert result["audit"]["included_entities"] == [f"C{i:03d}" for i in range(6)]
    assert result["audit"]["excluded_genuine_treated_entities"] == [
        "T000",
        "T001",
        "T002",
    ]
    assert result["causal_status"] == "falsification_diagnostic"


@pytest.mark.parametrize("fake", [["T000", "C000"], ["unknown"], ["C000", "C000"]])
def test_group_placebo_fake_ids_are_explicit_unique_and_never_treated(did, fake):
    with pytest.raises(ValueError, match="never-treated|unique"):
        did.placebo_group(
            _panel(controls=(0, 1, 2, 3)), **_config(), fake_treated_entities=fake
        )


def test_group_placebo_never_splits_a_whole_assignment_cluster(did):
    rows = _panel(controls=(0, 1, 2, 3, 4, 5))
    for row in rows:
        if row["id"] in {"C000", "C001"}:
            row["district"] = "shared-control-district"
    with pytest.raises(ValueError, match="assignment cluster"):
        did.placebo_group(rows, **_config(), fake_treated_entities=["C000", "C002"])


def test_seeded_placebo_distribution_is_reproducible_and_descriptive(did):
    rows = _panel(treated=(100, 110, 120), controls=(0, 1, 2, 3, 4, 5))
    config = {**_config(), "draws": 32, "seed": 419, "fake_cluster_count": 2}
    result = did.placebo_distribution(rows, **config)
    assert result == did.placebo_distribution(rows, **config)
    assert result["causal_status"] == "descriptive_falsification_distribution"
    assert len(result["distribution"]) == 32
    assert result["real_estimate"] == pytest.approx(107.5)
    assert result["descriptive_tail_fraction"] == 0
    assert "not a randomization p-value" in result["tail_fraction_interpretation"]
    for draw in result["distribution"]:
        assert len(draw["fake_treated_clusters"]) == 2
        assert all(c.startswith("C") for c in draw["fake_treated_clusters"])
        assert -3 <= draw["estimate"] <= 3
    json.dumps(result, allow_nan=False)


def test_randomized_placebos_respect_joint_unit_assignment_within_clusters(did):
    rows = []
    for row in _panel(controls=(0, 1, 2, 3, 4, 5)):
        for sibling in ("a", "b"):
            rows.append({**row, "id": row["id"] + sibling})
    result = did.placebo_distribution(
        rows, **_config(), draws=10, seed=11, fake_cluster_count=2
    )
    # The extreme fake assignment is two entire districts, not two units out
    # of twelve controls. Every recorded cluster label has two original units.
    for draw in result["distribution"]:
        assigned = set(draw["fake_treated_clusters"])
        assert len(assigned) == 2
        selected = {r["id"] for r in rows if r["district"] in assigned}
        assert len(selected) == 4


@pytest.mark.parametrize("draws,count", [(0, 2), (1001, 2), (2, 0), (2, 6)])
def test_placebo_distribution_bounds_and_control_support_refuse(did, draws, count):
    with pytest.raises(ValueError, match="draws|fake_cluster_count"):
        did.placebo_distribution(
            _panel(controls=(0, 1, 2, 3, 4, 5)),
            **_config(),
            draws=draws,
            seed=11,
            fake_cluster_count=count,
        )


def _ddd_panel(*, shared_subgroup_clusters=False):
    # Cell DiDs: S0=(2-1)=1; S1=(6-2)=4, so DDD=3.
    values = {(0, 0): [0, 2], (1, 0): [1, 3], (0, 1): [1, 3], (1, 1): [4, 8]}
    rows = []
    for (group, subgroup), changes in values.items():
        for i, delta in enumerate(changes):
            unit = f"{group}:{subgroup}:{i}"
            cluster = f"{group}:{i}" if shared_subgroup_clusters else unit
            for t in (0, 1):
                rows.append(
                    {
                        "id": unit,
                        "district": cluster,
                        "s": subgroup,
                        "t": t,
                        "y": 10 + delta * t,
                        "d": group * t,
                    }
                )
    return rows


def test_ddd_saturated_contrast_has_full_cluster_cr1_covariance(did):
    result = did.triple_difference(_ddd_panel(), **_config(), subgroup="s")
    assert result["estimate"] == pytest.approx(3)
    assert result["se"] ** 2 == pytest.approx(7)
    assert result["inference"]["cr1_factor"] == pytest.approx(2)
    assert result["inference"]["df"] == 7
    assert result["coefficients"]["treated:subgroup"] == pytest.approx(3)
    assert result["inference"]["cell_cluster_counts"] == {
        "0,0": 2,
        "0,1": 2,
        "1,0": 2,
        "1,1": 2,
    }


def test_ddd_shared_subgroup_clusters_preserve_cross_covariance(did):
    result = did.triple_difference(
        _ddd_panel(shared_subgroup_clusters=True), **_config(), subgroup="s"
    )
    assert result["estimate"] == pytest.approx(3)
    # Correlated C subgroup residuals cancel exactly; treated cluster DDD
    # scores are +/-1/2. CR1=7/3 gives V=7/6, with four clusters, df=3.
    assert result["inference"]["cluster_count"] == 4
    assert result["inference"]["df"] == 3
    assert result["se"] ** 2 == pytest.approx(7 / 6)


def test_ddd_requires_four_supported_binary_time_invariant_cells(did):
    rows = _ddd_panel()
    rows[1]["s"] = 1
    with pytest.raises(ValueError, match="time invariant"):
        did.triple_difference(rows, **_config(), subgroup="s")
    missing_cell = [
        r for r in _ddd_panel() if not (r["s"] == 1 and r["id"].startswith("1:"))
    ]
    with pytest.raises(ValueError, match="rank"):
        did.triple_difference(missing_cell, **_config(), subgroup="s")
    rows = _ddd_panel()
    rows[0]["s"] = 2
    with pytest.raises(ValueError, match="binary"):
        did.triple_difference(rows, **_config(), subgroup="s")


def test_ddd_with_one_cluster_in_a_cell_reports_no_inference(did):
    rows = [r for r in _ddd_panel() if r["id"] != "1:1:1"]
    result = did.triple_difference(rows, **_config(), subgroup="s")
    assert result["inference"]["status"] == "unavailable"
    assert result["se"] is None and result["p_value"] is None
    assert any(
        "DDD cells" in reason for reason in result["inference"]["unavailable_reasons"]
    )


def _staggered_panel():
    series = {
        "A000": (2, [0, 0, 2, 4]),
        "A001": (2, [0, 0, 4, 8]),
        "B000": (3, [0, 0, 0, 3]),
        "B001": (3, [0, 0, 0, 5]),
        "C000": (0, [0, 0, 0, 1]),
        "C001": (0, [0, 0, 2, 3]),
    }
    return [
        {"id": unit, "district": unit, "g": cohort, "t": t, "y": y}
        for unit, (cohort, values) in series.items()
        for t, y in enumerate(values)
    ]


def _staggered_run(did, rows=None, **changes):
    config = {
        "entity": "id",
        "time": "t",
        "outcome": "y",
        "cohort": "g",
        "periods": [1, 2, 3],
        "cluster": "district",
        "research_design": copy.deepcopy(DESIGN),
    }
    return did.staggered_did(
        _staggered_panel() if rows is None else rows, **{**config, **changes}
    )


def test_staggered_group_time_and_joint_aggregate_covariance(did):
    result = _staggered_run(did)
    assert [(c["cohort"], c["period"]) for c in result["cells"]] == [
        (2, 2),
        (2, 3),
        (3, 3),
    ]
    assert [c["estimate"] for c in result["cells"]] == pytest.approx([2, 4, 3])
    assert [c["se"] ** 2 for c in result["cells"]] == pytest.approx([2, 5, 1])
    expected = [[2, 3, 0], [3, 5, 0], [0, 0, 1]]
    for actual, row in zip(result["cell_covariance"], expected):
        assert actual == pytest.approx(row)
    aggregate = result["aggregate"]
    assert aggregate["estimate"] == pytest.approx(3)
    assert aggregate["weights"] == pytest.approx([1 / 3] * 3)
    # Shared control histories induce positive covariance: the variance is
    # (2+5+1+2*3)/9=14/9, rather than the independence approximation 8/9.
    assert aggregate["variance"] == pytest.approx(14 / 9)
    assert aggregate["se"] ** 2 == pytest.approx(14 / 9)
    assert aggregate["inference"]["df"] == 5
    dynamic = {r["event_time"]: r for r in result["dynamic_effects"]}
    assert dynamic[0]["estimate"] == pytest.approx(2.5)
    assert dynamic[0]["variance"] == pytest.approx(3 / 4)
    assert dynamic[0]["supported_cohorts"] == [2, 3]
    assert dynamic[1]["supported_cohorts"] == [2]
    assert dynamic[1]["variance"] == pytest.approx(5)
    assert result["unsupported_cells"] == []
    json.dumps(result, allow_nan=False)


def test_staggered_not_yet_treated_controls_exit_before_own_adoption(did):
    result = _staggered_run(did, control_group="not_yet_treated")
    cells = {(c["cohort"], c["period"]): c for c in result["cells"]}
    assert cells[2, 2]["control_entities"] == ["B000", "B001", "C000", "C001"]
    assert cells[2, 2]["estimate"] == pytest.approx(2.5)
    assert cells[2, 3]["control_entities"] == ["C000", "C001"]
    assert cells[3, 3]["control_entities"] == ["C000", "C001"]
    assert not any("A000" in c["control_entities"] for c in result["cells"])


def test_staggered_unsupported_cells_are_audited_without_fabricated_att(did):
    rows = [r for r in _staggered_panel() if not r["id"].startswith("C")]
    result = _staggered_run(did, rows, control_group="not_yet_treated")
    assert [(c["cohort"], c["period"]) for c in result["cells"]] == [(2, 2)]
    assert {(c["cohort"], c["period"]) for c in result["unsupported_cells"]} == {
        (2, 3),
        (3, 3),
    }
    with pytest.raises(ValueError, match="no comparison"):
        _staggered_run(did, rows, control_group="never_treated")


def test_staggered_baselines_cannot_be_inferred_from_available_rows(did):
    with pytest.raises(ValueError, match="baseline period g-1"):
        _staggered_run(did, periods=[2, 3])


def test_staggered_cohorts_must_be_invariant_and_not_imputed(did):
    rows = _staggered_panel()
    rows[0]["g"] = 3
    with pytest.raises(ValueError, match="time invariant"):
        _staggered_run(did, rows)
    rows = _staggered_panel()
    rows[0]["_imputed_columns"] = ["g"]
    with pytest.raises(ValueError, match="cohort assignment cannot be imputed"):
        _staggered_run(did, rows, missing_policy="drop_entity")


def test_staggered_assignment_cluster_cannot_contain_different_cohorts(did):
    rows = _staggered_panel()
    for row in rows:
        if row["id"] in {"A000", "B000"}:
            row["district"] = "mixed-cohort-district"
    with pytest.raises(ValueError, match="assignment cluster"):
        _staggered_run(did, rows)


def test_staggered_observed_exposure_checks_all_history_before_sampling(did):
    rows = _staggered_panel()
    for row in rows:
        row["d"] = int(row["g"] != 0 and row["t"] >= row["g"])
    result = _staggered_run(did, rows, treatment="d")
    assert "observed exposure checked" in result["audit"]["exposure_validation"]
    # Period zero is outside the selected [1,2,3] analysis window. Observed
    # exposure incompatible with cohort must still refuse before filtering.
    rows[0]["d"] = 1
    with pytest.raises(ValueError, match="observed exposure"):
        _staggered_run(did, rows, treatment="d")


def test_staggered_complete_sample_excludes_whole_entities_across_all_cells(did):
    rows = _staggered_panel()
    next(r for r in rows if r["id"] == "C000" and r["t"] == 1)["y"] = None
    with pytest.raises(ValueError, match="drop_entity"):
        _staggered_run(did, rows)
    result = _staggered_run(did, rows, missing_policy="drop_entity")
    assert result["audit"]["excluded_entities"][0]["entity"] == "C000"
    assert result["audit"]["included_entity_count"] == 5
    assert all("C000" not in c["control_entities"] for c in result["cells"])
    assert all(c["inference"]["status"] == "unavailable" for c in result["cells"])
    assert result["aggregate"]["p_value"] is None


def test_unimplemented_staggered_comparisons_and_adjustments_refuse(did):
    with pytest.raises(ValueError, match="control_group"):
        _staggered_run(did, control_group="already_treated")
    with pytest.raises(ValueError, match="covariate adjustment"):
        _staggered_run(did, covariates=["x"])


def test_pipeline_freezes_plan_and_executes_baseline_placebos_derived_in_order(did):
    rows = _ddd_panel()
    baseline = [r for r in rows if r["t"] == 0]
    for row in baseline:
        rows.extend([{**row, "t": -2}, {**row, "t": -1}])
    event_config = _config()
    event_config.pop("pre_periods")
    event_config.pop("post_periods")
    event_config.update(periods=[-1, 1], reference_period=0)
    plan = {
        "traditional": _config(),
        "event_study": [event_config],
        "placebo_time": [
            {**_config(pre_periods=[-2], post_periods=[-1]), "fake_treatment_time": -1}
        ],
        "placebo_group": [{**_config(), "fake_treated_entities": ["0:0:0", "0:0:1"]}],
        "derived": [
            {"method": "triple_difference", "config": {**_config(), "subgroup": "s"}}
        ],
    }
    before = copy.deepcopy((rows, plan))
    result = did.analyze_did(rows, plan=plan)
    assert (rows, plan) == before
    assert result["execution_order"] == [
        "traditional_did",
        "event_study",
        "placebo_time",
        "placebo_group",
        "triple_difference",
    ]
    assert result["results"][0]["estimate"] == pytest.approx(2.5)
    assert result["results"][-1]["estimate"] == pytest.approx(3)
    assert result == did.analyze_did(rows, plan=plan)


def test_pipeline_requires_a_prespecified_traditional_baseline(did):
    with pytest.raises(ValueError, match="traditional"):
        did.analyze_did(_panel(), plan={"event_study": []})
    with pytest.raises(ValueError, match="unknown prespecified plan stage"):
        did.analyze_did(_panel(), plan={"traditional": _config(), "choose_dates": True})


def test_exports_are_finite_deterministic_exclusive_and_hash_verifiable(did, tmp_path):
    result = _staggered_run(did)
    first = did.write_outputs(result, tmp_path / "first")
    second = did.write_outputs(result, tmp_path / "second")
    assert set(first) == {"results.json", "estimates.csv", "manifest.json"}
    for name in first:
        assert Path(first[name]).is_absolute()
        assert Path(first[name]).read_bytes() == Path(second[name]).read_bytes()
    restored = json.loads(Path(first["results.json"]).read_text())
    assert restored == result
    manifest = json.loads(Path(first["manifest.json"]).read_text())
    for name, info in manifest["outputs"].items():
        path = Path(first[name])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == info["sha256"]
        assert path.stat().st_size == info["size"]
    with Path(first["estimates.csv"]).open(newline="") as handle:
        cells = list(csv.DictReader(handle))
    assert [(int(c["cohort"]), int(c["period"])) for c in cells] == [
        (2, 2),
        (2, 3),
        (3, 3),
    ]
    assert [float(c["estimate"]) for c in cells] == pytest.approx([2, 4, 3])
    saved = {name: Path(path).read_bytes() for name, path in first.items()}
    with pytest.raises(FileExistsError):
        did.write_outputs(result, tmp_path / "first")
    assert saved == {name: Path(path).read_bytes() for name, path in first.items()}


def test_export_preflights_every_target_before_writing_any_file(did, tmp_path):
    target = tmp_path / "existing"
    target.mkdir()
    sentinel = target / "estimates.csv"
    sentinel.write_text("researcher result; preserve these bytes\n")
    with pytest.raises(FileExistsError):
        did.write_outputs(_run(did), target)
    assert sentinel.read_text() == "researcher result; preserve these bytes\n"
    assert not (target / "results.json").exists()
    assert not (target / "manifest.json").exists()


def test_missing_inference_exports_blank_csv_cells_instead_of_zero(did, tmp_path):
    result = _run(did, _panel(treated=(5, 5, 5), controls=(1, 1, 1)))
    paths = did.write_outputs(result, tmp_path / "degenerate")
    with Path(paths["estimates.csv"]).open(newline="") as handle:
        row = next(csv.DictReader(handle))
    assert float(row["estimate"]) == 4
    assert all(row[k] == "" for k in ("se", "ci_low", "ci_high", "p_value"))


def test_plotting_dependency_remains_optional_with_actionable_failure(
    did, monkeypatch, tmp_path
):
    original = builtins.__import__

    def deny_plotting(name, *args, **kwargs):
        if name.startswith("matplotlib"):
            raise ImportError("optional plotting unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny_plotting)
    with pytest.raises(RuntimeError, match="optional matplotlib"):
        did.render_diagnostics(_run(did), tmp_path / "plots")
    assert not (tmp_path / "plots").exists()


def test_declared_replay_cases_run_the_production_sidecar():
    report = did_evals.evaluate_did_cases()
    assert report["production_backed"] is True
    assert report["total"] == 12
    assert report["passed"] == 12 and report["failed"] == 0


@pytest.mark.stubbed_backend
def test_replay_expected_refusal_cannot_pass_on_success(monkeypatch, tmp_path):
    record = json.loads(did_evals.DEFAULT_CASES_PATH.read_text())
    refusal = next(c for c in record["cases"] if "expected_error" in c)
    record["cases"] = [refusal]
    tape = tmp_path / "expected-refusal.json"
    tape.write_text(json.dumps(record))
    fake = SimpleNamespace(**{refusal["operation"]: lambda *a, **k: {"estimate": 0}})
    monkeypatch.setattr(did_evals, "_kernel", lambda: fake)
    report = did_evals.evaluate_did_cases(tape)
    assert report["total"] == 1 and report["failed"] == 1
    assert report["cases"][0]["passed"] is False
    assert report["cases"][0]["detail"] == "declared outcome did not match"


def test_pipeline_retains_refused_diagnostics_and_continues_to_valid_derivative(
    did, tmp_path
):
    rows = _ddd_panel()
    plan = {
        "traditional": _config(),
        "placebo_time": [{**_config(), "fake_treatment_time": -1}],
        "placebo_group": [{**_config(), "fake_treated_entities": ["1:0:0"]}],
        "derived": [
            {"method": "triple_difference", "config": {**_config(), "subgroup": "s"}}
        ],
    }
    result = did.analyze_did(rows, plan=plan)
    assert result["execution_order"] == [
        "traditional_did",
        "placebo_time",
        "placebo_group",
        "triple_difference",
    ]
    stages = result["results"]
    assert stages[0]["estimate"] == pytest.approx(2.5)
    assert stages[-1]["estimate"] == pytest.approx(3)
    for entry, key in zip(stages[1:3], ("placebo_time", "placebo_group")):
        assert entry["status"] == "refused"
        assert entry["config"] == plan[key][0]
        assert entry["reason"]
        assert (
            entry["manifest"]["source_records_sha256"]
            == stages[0]["manifest"]["source_records_sha256"]
        )
    files = did.write_outputs(result, tmp_path / "retained")
    with Path(files["estimates.csv"]).open(newline="") as handle:
        table = list(csv.DictReader(handle))
    assert [r["status"] for r in table] == [
        "estimated",
        "refused",
        "refused",
        "estimated",
    ]
    assert all(r["estimate"] == "" and r["reason"] for r in table[1:3])


def test_read_csv_preserves_ids_and_restores_upstream_panel_metadata(did, tmp_path):
    rows = _panel()
    for n, row in enumerate(rows, 1):
        row["_source_rows"] = [n + 100]
        row["_imputed_columns"] = []
    path = tmp_path / "panel.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(
            {k: json.dumps(v) if isinstance(v, list) else v for k, v in row.items()}
            for row in rows
        )
    restored = did.read_csv(path)
    assert restored[0]["id"] == rows[0]["id"]
    assert isinstance(restored[0]["t"], str)
    assert restored[0]["_source_rows"] == [101]
    assert restored[0]["_imputed_columns"] == []
    assert _run(did, restored)["estimate"] == pytest.approx(3)
    assert _run(did, restored)["audit"]["included_source_rows"] == list(
        range(101, 101 + len(rows))
    )


@pytest.mark.parametrize(
    "column,value",
    [
        ("_source_rows", "null"),
        ("_source_rows", "1"),
        ("_source_rows", "[true]"),
        ("_source_rows", "[0]"),
        ("_source_rows", '["1"]'),
        ("_source_rows", "[1.5]"),
        ("_imputed_columns", "null"),
        ("_imputed_columns", "{}"),
        ("_imputed_columns", "[1]"),
        ("_imputed_columns", "not JSON"),
    ],
)
def test_read_csv_refuses_malformed_reserved_metadata(did, tmp_path, column, value):
    path = tmp_path / "bad-metadata.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", column])
        writer.writeheader()
        writer.writerow({"id": "001", column: value})
    with pytest.raises(ValueError, match=column):
        did.read_csv(path)


def test_placebo_csv_distinguishes_actual_reference_from_every_fake_draw(did, tmp_path):
    rows = _panel(treated=(100, 110, 120), controls=(0, 1, 2, 3, 4, 5))
    result = did.placebo_distribution(
        rows, **_config(), draws=12, seed=419, fake_cluster_count=2
    )
    files = did.write_outputs(result, tmp_path / "draws")
    with Path(files["estimates.csv"]).open(newline="") as handle:
        table = list(csv.DictReader(handle))
    references = [r for r in table if r["role"] == "real_treatment_reference"]
    draws = [r for r in table if r["role"] == "fake_group_draw"]
    assert len(references) == 1
    assert float(references[0]["estimate"]) == pytest.approx(107.5)
    assert len(draws) == len(result["distribution"]) == 12
    assert [float(r["estimate"]) for r in draws] == pytest.approx(
        [r["estimate"] for r in result["distribution"]]
    )
    assert all(r["ci_low"] == r["ci_high"] == r["p_value"] == "" for r in draws)
