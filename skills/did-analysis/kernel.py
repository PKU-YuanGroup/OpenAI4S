"""Prespecified, unconditional panel DiD with auditable stdlib inference.

The common-date estimator regresses each entity's post-minus-pre outcome mean
on an intercept and its eventually-treated indicator. CR1 covariance clusters
these entity changes at the explicitly named assignment cluster. Event-study,
DDD and unadjusted staggered group-time contrasts retain joint cluster scores.
This is not conditional/doubly robust Callaway--Sant'Anna or a bootstrap.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

VERSION = "did-analysis/v1"
MAX_ROWS = 100_000
MAX_ENTITIES = 20_000
MAX_PERIODS = 200
MAX_DRAWS = 1_000
MAX_CONTRASTS = 100
_IMPUTED = "_imputed_columns"


def _finite_json(value: Any, *, missing_nan: bool = False) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise ValueError("JSON/provenance mapping keys must be strings")
        return {k: _finite_json(v, missing_nan=missing_nan) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(v, missing_nan=missing_nan) for v in value]
    if missing_nan and isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite values or computation overflow are unsupported")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("inputs must contain JSON-compatible values")


def _canonical(value: Any) -> str:
    return json.dumps(
        _finite_json(value), sort_keys=True, ensure_ascii=False, allow_nan=False
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _missing(value: Any) -> bool:
    return (
        value is None
        or isinstance(value, str)
        and not value.strip()
        or (isinstance(value, float) and math.isnan(value))
    )


def _id(value: Any, column: str) -> str | int:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or _missing(value):
        raise ValueError(f"{column}: IDs must be nonempty strings or integers")
    return value


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"-?\d+", str(value).strip()):
        raise ValueError(f"{name}: use integer periods or upstream _period_index")
    return int(value)


def _binary(value: Any, name: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if value in (0, 1, "0", "1"):
        return int(value)
    raise ValueError(f"{name}: treatment/subgroup must be binary 0/1")


def _number(value: Any, name: str) -> float | None:
    if _missing(value):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name}: boolean is not an outcome measurement")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}: invalid numeric measurement") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name}: outcome must be finite")
    return result


def _periods(values: Sequence[int], name: str) -> list[int]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{name}: prespecify a nonempty list of periods")
    if not values or len(values) > MAX_PERIODS:
        raise ValueError(f"{name}: select between 1 and {MAX_PERIODS} periods")
    result = [_integer(v, name) for v in values]
    if len(set(result)) != len(result):
        raise ValueError(f"{name}: periods must be unique")
    return sorted(result)


def _design(research_design: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(research_design, Mapping):
        raise ValueError("ask the scientist for the research_design mapping")
    for key in ("event_source", "treatment_assignment", "parallel_trends"):
        if (
            not isinstance(research_design.get(key), str)
            or not research_design[key].strip()
        ):
            raise ValueError(f"ask the scientist for research_design.{key}")
    return _finite_json(dict(research_design))


def read_csv(path: str | Path) -> list[dict[str, Any]]:
    """Preserve CSV strings/IDs; strictly decode only reserved provenance lists.

    Upstream ``panel.csv`` serializes ``_source_rows`` and ``_imputed_columns``
    as JSON. Empty cells in those columns become empty lists. No other column
    is decoded or subjected to numeric/type inference.
    """
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        names = reader.fieldnames
        if not names or len(set(names)) != len(names) or any(not v for v in names):
            raise ValueError("CSV requires unique nonempty headers")
        rows: list[dict[str, Any]] = []
        for raw in reader:
            if len(rows) >= MAX_ROWS or None in raw or None in raw.values():
                raise ValueError("CSV exceeds row cap or has inconsistent row width")
            row: dict[str, Any] = dict(raw)
            for column in ("_source_rows", _IMPUTED):
                if column not in row:
                    continue
                try:
                    value = json.loads(row[column]) if row[column].strip() else []
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{column}: malformed reserved JSON metadata"
                    ) from exc
                if not isinstance(value, list):
                    raise ValueError(f"{column}: reserved metadata must be a JSON list")
                if column == "_source_rows" and any(
                    isinstance(v, bool) or not isinstance(v, int) or v < 1
                    for v in value
                ):
                    raise ValueError(
                        "_source_rows: expected positive integer row positions"
                    )
                if column == _IMPUTED and any(not isinstance(v, str) for v in value):
                    raise ValueError(
                        "_imputed_columns: expected a list of column names"
                    )
                row[column] = value
            rows.append(row)
        return rows


def _load(
    rows: Sequence[Mapping[str, Any]],
    *,
    entity: str,
    time: str,
    outcome: str,
    cluster: str,
    research_design: Mapping[str, Any],
    missing_policy: str,
    covariates: Sequence[str] = (),
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows:
        raise ValueError("rows must be a nonempty sequence of mappings")
    if len(rows) > MAX_ROWS:
        raise ValueError(f"row cap is {MAX_ROWS}")
    if missing_policy not in {"error", "drop_entity"}:
        raise ValueError("missing_policy must be 'error' or explicit 'drop_entity'")
    if covariates:
        raise ValueError("unsupported: covariate adjustment is not implemented in v1")
    for name in (entity, time, outcome, cluster):
        if not isinstance(name, str) or not name:
            raise ValueError("entity, time, outcome and cluster columns are explicit")
    if len({entity, time, outcome}) != 3 or cluster in {time, outcome}:
        raise ValueError(
            "entity/time/outcome must differ; cluster cannot be time/outcome"
        )
    design = _design(research_design)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, int]] = set()
    copied = []
    for position, raw in enumerate(rows, 1):
        if not isinstance(raw, Mapping) or any(not isinstance(k, str) for k in raw):
            raise ValueError("each row must be a mapping with string columns")
        row = _finite_json(dict(raw), missing_nan=True)
        copied.append(row)
        label, cluster_label = _id(row.get(entity), entity), _id(
            row.get(cluster), cluster
        )
        period = _integer(row.get(time), time)
        key = _canonical(label)
        if (key, period) in seen:
            raise ValueError(
                "duplicate entity-period rows; aggregate explicitly upstream"
            )
        seen.add((key, period))
        flags = row.get(_IMPUTED, [])
        if not isinstance(flags, list) or any(not isinstance(v, str) for v in flags):
            raise ValueError("_imputed_columns must be a list of column names")
        if set(flags).intersection({entity, time, cluster}):
            raise ValueError("entity/time/cluster identifiers cannot be imputed")
        upstream = row.get("_source_rows", [position])
        if not isinstance(upstream, list) or any(
            isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in upstream
        ):
            raise ValueError("_source_rows must contain positive row positions")
        groups[key].append(
            {
                "entity": label,
                "cluster": cluster_label,
                "period": period,
                "outcome": _number(row.get(outcome), outcome),
                "imputed": outcome in flags,
                "source_row": position,
                "source_rows": sorted(set(upstream)),
                "raw": row,
            }
        )
    if len(groups) > MAX_ENTITIES:
        raise ValueError(f"entity cap is {MAX_ENTITIES}")
    if len({r["period"] for rs in groups.values() for r in rs}) > MAX_PERIODS:
        raise ValueError(f"input period cap is {MAX_PERIODS}")
    for rs in groups.values():
        rs.sort(key=lambda r: r["period"])
        if len({_canonical(r["cluster"]) for r in rs}) != 1:
            raise ValueError("an entity must remain in one assignment cluster")
    config = {
        "entity": entity,
        "time": time,
        "outcome": outcome,
        "cluster": cluster,
        "research_design": design,
        "missing_policy": missing_policy,
        "covariates": [],
    }
    return dict(groups), {
        "config": config,
        "source_records_sha256": _digest(copied),
        "source_row_count": len(rows),
        "source_entity_count": len(groups),
    }


def _common(
    groups: Mapping[str, list[dict[str, Any]]],
    treatment: str,
    treatment_time: int,
    *,
    descriptive_staggered_benchmark: bool = False,
) -> dict[str, int]:
    membership = {}
    for key, rs in groups.items():
        if any(treatment in r["raw"].get(_IMPUTED, []) for r in rs):
            raise ValueError("treatment assignment/exposure cannot be imputed")
        exposures = [_binary(r["raw"].get(treatment), treatment) for r in rs]
        if any(a > b for a, b in zip(exposures, exposures[1:])):
            raise ValueError(
                "unsupported: treatment reversal; exposure must be absorbing"
            )
        group = int(any(exposures))
        if not descriptive_staggered_benchmark and any(
            d != int(group and r["period"] >= treatment_time)
            for r, d in zip(rs, exposures)
        ):
            raise ValueError(
                "unsupported: exposure does not match the common treatment date"
            )
        membership[key] = group
    if set(membership.values()) != {0, 1}:
        raise ValueError(
            "no comparison: need genuinely treated and never-treated entities"
        )
    _assignment(groups, membership)
    return membership


def _assignment(
    groups: Mapping[str, list[dict[str, Any]]], membership: Mapping[str, int]
) -> None:
    assigned: dict[str, int] = {}
    for key, group in membership.items():
        c = _canonical(groups[key][0]["cluster"])
        if c in assigned and assigned[c] != group:
            raise ValueError(
                "treatment assignment splits an explicit assignment cluster"
            )
        assigned[c] = group


def _select(
    groups: Mapping[str, list[dict[str, Any]]],
    periods: Sequence[int],
    missing_policy: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    included, excluded = {}, []
    selected = set(periods)
    for key in sorted(groups):
        rs = groups[key]
        by_time = {r["period"]: r for r in rs}
        absent = sorted(selected - by_time.keys())
        missing = sorted(
            p for p in selected & by_time.keys() if by_time[p]["outcome"] is None
        )
        imputed = sorted(p for p in selected & by_time.keys() if by_time[p]["imputed"])
        if absent or missing or imputed:
            excluded.append(
                {
                    "entity": rs[0]["entity"],
                    "absent_periods": absent,
                    "missing_outcome_periods": missing,
                    "imputed_outcome_periods": imputed,
                    "source_rows": sorted({n for r in rs for n in r["source_rows"]}),
                    "input_row_positions": [r["source_row"] for r in rs],
                }
            )
        else:
            included[key] = [by_time[p] for p in sorted(selected)]
    if excluded and missing_policy == "error":
        raise ValueError(
            f"incomplete or imputed outcome for {len(excluded)} entities; "
            "prespecify missing_policy='drop_entity' to exclude whole entities"
        )
    if not included:
        raise ValueError("no comparison: no complete observed entities")
    return included, {
        "required_periods": sorted(selected),
        "included_entities": [included[k][0]["entity"] for k in sorted(included)],
        "excluded_entities": excluded,
        "included_entity_count": len(included),
        "included_input_row_positions": sorted(
            r["source_row"] for rs in included.values() for r in rs
        ),
        "included_source_rows": sorted(
            {n for rs in included.values() for r in rs for n in r["source_rows"]}
        ),
        "selection_rule": "Prespecified periods; complete originally observed outcome; no outcome-break selection or imputation.",
    }


def _inverse(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    n = len(matrix)
    scale = max((abs(v) for row in matrix for v in row), default=0.0)
    if not n or scale == 0:
        raise ValueError("rank deficient comparison/design")
    a = [list(row) + [float(i == j) for j in range(n)] for i, row in enumerate(matrix)]
    for i in range(n):
        pivot = max(range(i, n), key=lambda j: abs(a[j][i]))
        if abs(a[pivot][i]) <= scale * 1e-12:
            raise ValueError("rank deficient comparison/design")
        a[i], a[pivot] = a[pivot], a[i]
        divisor = a[i][i]
        a[i] = [v / divisor for v in a[i]]
        for j in range(n):
            if j != i:
                factor = a[j][i]
                a[j] = [x - factor * y for x, y in zip(a[j], a[i])]
    return [row[n:] for row in a]


def _mv(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    return [sum(a * b for a, b in zip(row, vector)) for row in matrix]


def _fit(
    units: list[dict[str, Any]],
    *,
    subgroup: bool = False,
) -> dict[str, Any]:
    x = [
        (
            [
                1.0,
                float(u["group"]),
                float(u["subgroup"]),
                float(u["group"] * u["subgroup"]),
            ]
            if subgroup
            else [1.0, float(u["group"])]
        )
        for u in units
    ]
    k, n = len(x[0]), len(x)
    xx = [[sum(row[i] * row[j] for row in x) for j in range(k)] for i in range(k)]
    bread = _inverse(xx)
    beta = _mv(
        bread, [sum(row[i] * u["change"] for row, u in zip(x, units)) for i in range(k)]
    )
    scores: dict[str, list[float]] = defaultdict(lambda: [0.0] * k)
    cluster_labels = {}
    residuals = []
    for row, u in zip(x, units):
        c = _canonical(u["cluster"])
        cluster_labels[c] = u["cluster"]
        residual = u["change"] - sum(a * b for a, b in zip(row, beta))
        residuals.append(residual)
        scores[c] = [a + residual * b for a, b in zip(scores[c], row)]
    g = len(scores)
    correction = g / (g - 1) * (n - 1) / (n - k) if g > 1 and n > k else None
    influence = {c: _mv(bread, score)[-1] for c, score in scores.items()}
    covariance = None
    if correction is not None:
        vectors = [_mv(bread, score) for score in scores.values()]
        covariance = [
            [correction * sum(v[i] * v[j] for v in vectors) for j in range(k)]
            for i in range(k)
        ]
    groups = {
        str(v): len({_canonical(u["cluster"]) for u in units if u["group"] == v})
        for v in (0, 1)
    }
    cells = (
        {
            f"{a},{b}": len(
                {
                    _canonical(u["cluster"])
                    for u in units
                    if u["group"] == a and u["subgroup"] == b
                }
            )
            for a in (0, 1)
            for b in (0, 1)
        }
        if subgroup
        else {}
    )
    reasons = []
    if correction is None:
        reasons.append("residual degrees of freedom or cluster count insufficient")
    if min(groups.values()) < 2:
        reasons.append("fewer than two treated or control assignment clusters")
    if cells and min(cells.values()) < 2:
        reasons.append("fewer than two clusters in one or more DDD cells")
    change_scale = max(abs(u["change"]) for u in units)
    if max(abs(v) for v in residuals) <= change_scale * 1e-12:
        reasons.append(
            "numerically degenerate residual variation; no uncertainty claim"
        )
    result = {
        "estimate": beta[-1],
        "coefficient_names": (
            ["intercept", "treated", "subgroup", "treated:subgroup"]
            if subgroup
            else ["intercept", "treated"]
        ),
        "coefficients": beta,
        "coefficient_covariance": covariance,
        "variance": covariance[-1][-1] if covariance is not None else None,
        "influence": influence,
        "cluster_labels": cluster_labels,
        "cr1_factor": correction,
        "entity_count": n,
        "parameter_count": k,
        "cluster_count": g,
        "group_cluster_counts": groups,
        "cell_cluster_counts": cells,
        "unavailable_reasons": reasons,
    }
    _canonical(result)
    return result


def _beta_fraction(a: float, b: float, x: float) -> float:
    # Modified Lentz continued fraction for the regularized incomplete beta.
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    tiny, c = 1e-300, 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 501):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = d if abs(d) > tiny else tiny
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = d if abs(d) > tiny else tiny
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        d = 1.0 / d
        step = d * c
        h *= step
        if abs(step - 1.0) < 3e-14:
            return h
    raise ArithmeticError("incomplete-beta inference did not converge")


def _ibeta(a: float, b: float, x: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return max(0.0, min(1.0, front * _beta_fraction(a, b, x) / a))
    return max(0.0, min(1.0, 1.0 - front * _beta_fraction(b, a, 1.0 - x) / b))


def _t_tail(t: float, df: int) -> float:
    return _ibeta(df / 2.0, 0.5, df / (df + t * t))


def _t_critical(df: int, alpha: float) -> float:
    low, high = 0.0, 1.0
    while _t_tail(high, df) > alpha:
        high *= 2
    for _ in range(90):
        middle = (low + high) / 2.0
        if _t_tail(middle, df) > alpha:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _inference(fit: Mapping[str, Any], alpha: float = 0.05) -> dict[str, Any]:
    if isinstance(alpha, bool) or not 0 < alpha < 1:
        raise ValueError("alpha must be between zero and one")
    reasons = list(fit["unavailable_reasons"])
    variance = fit["variance"]
    if variance is not None and variance <= 0.0:
        reasons.append("zero/degenerate cluster score variance; no uncertainty claim")
    result = {
        "se": None,
        "ci_low": None,
        "ci_high": None,
        "p_value": None,
        "alpha": alpha,
        "df": fit["cluster_count"] - 1,
        "status": "unavailable" if reasons else "available",
        "unavailable_reasons": reasons,
        "method": "cluster CR1 sandwich; Student-t G-1 reference; asymptotic approximation",
        "cr1_factor": fit["cr1_factor"],
        "cluster_count": fit["cluster_count"],
        "group_cluster_counts": fit["group_cluster_counts"],
        "cell_cluster_counts": fit.get("cell_cluster_counts", {}),
        "limitations": "Many independent assignment clusters are needed; two clusters per group is a refusal floor, not a reliability guarantee.",
    }
    if not reasons:
        se = math.sqrt(max(0.0, variance))
        critical = _t_critical(result["df"], alpha)
        result.update(
            se=se,
            ci_low=fit["estimate"] - critical * se,
            ci_high=fit["estimate"] + critical * se,
            p_value=_t_tail(abs(fit["estimate"] / se), result["df"]),
        )
    return result


def _units(
    sample: Mapping[str, list[dict[str, Any]]],
    membership: Mapping[str, int],
    pre: Sequence[int],
    post: Sequence[int],
) -> list[dict[str, Any]]:
    units = []
    for key in sorted(sample):
        rs = sample[key]
        before = sum(r["outcome"] for r in rs if r["period"] in pre) / len(pre)
        after = sum(r["outcome"] for r in rs if r["period"] in post) / len(post)
        units.append(
            {
                "entity": rs[0]["entity"],
                "cluster": rs[0]["cluster"],
                "group": membership[key],
                "pre_mean": before,
                "post_mean": after,
                "change": after - before,
                "source_rows": sorted({n for r in rs for n in r["source_rows"]}),
                "input_row_positions": [r["source_row"] for r in rs],
            }
        )
    return units


def _finish(
    method: str,
    fit: Mapping[str, Any],
    info: dict[str, Any],
    audit: dict[str, Any],
    units: list[dict[str, Any]],
    *,
    alpha: float = 0.05,
    causal_status: str = "conditional_on_identifying_assumptions",
) -> dict[str, Any]:
    inference = _inference(fit, alpha)
    result = {
        "method": method,
        "status": "estimated",
        "estimate": fit["estimate"],
        **{k: inference[k] for k in ("se", "ci_low", "ci_high", "p_value")},
        "inference": inference,
        "audit": audit,
        "config": info["config"],
        "unit_changes": units,
        "coefficients": dict(zip(fit["coefficient_names"], fit["coefficients"])),
        "coefficient_covariance": fit["coefficient_covariance"],
        "causal_status": causal_status,
        "limitations": [
            "Unconditional equal-entity-weighted contrasts; no covariate adjustment.",
            "Parallel trends, no anticipation and no interference require substantive justification.",
            "Pretrend non-rejection and placebo null results do not prove identification.",
        ],
        "manifest": {
            "schema_version": 1,
            "method": VERSION,
            "estimator": method,
            "source_records_sha256": info["source_records_sha256"],
            "source_row_count": info["source_row_count"],
            "source_entity_count": info["source_entity_count"],
            "configuration_sha256": _digest(info["config"]),
            "sample_sha256": _digest(audit),
            "formula": "OLS entity change; CR1=G/(G-1)*(N-1)/(N-K); Student-t df=G-1",
            "software": "Python standard library; no external DID package or bootstrap",
        },
    }
    _canonical(result)
    return result


def _setup_common(
    rows: Sequence[Mapping[str, Any]],
    *,
    entity: str,
    time: str,
    outcome: str,
    treatment: str,
    treatment_time: int,
    pre_periods: Sequence[int],
    post_periods: Sequence[int],
    cluster: str,
    research_design: Mapping[str, Any],
    missing_policy: str = "error",
    covariates: Sequence[str] = (),
    descriptive_staggered_benchmark: bool = False,
) -> tuple[Any, ...]:
    pre, post = _periods(pre_periods, "pre_periods"), _periods(
        post_periods, "post_periods"
    )
    date = _integer(treatment_time, "treatment_time")
    if set(pre) & set(post) or max(pre) >= date or min(post) < date:
        raise ValueError(
            "pre periods must precede treatment_time; post periods must follow it"
        )
    groups, info = _load(
        rows,
        entity=entity,
        time=time,
        outcome=outcome,
        cluster=cluster,
        research_design=research_design,
        missing_policy=missing_policy,
        covariates=covariates,
    )
    if treatment in {entity, time, outcome, cluster}:
        raise ValueError("treatment column must differ from design identifiers/outcome")
    membership = _common(
        groups,
        treatment,
        date,
        descriptive_staggered_benchmark=descriptive_staggered_benchmark,
    )
    sample, audit = _select(groups, pre + post, missing_policy)
    info["config"].update(
        treatment=treatment,
        treatment_time=date,
        pre_periods=pre,
        post_periods=post,
        descriptive_staggered_benchmark=descriptive_staggered_benchmark,
    )
    audit["group_entity_counts"] = {
        str(v): sum(membership[k] == v for k in sample) for v in (0, 1)
    }
    return groups, membership, sample, info, audit, pre, post


def audit_design(rows: Sequence[Mapping[str, Any]], **config: Any) -> dict[str, Any]:
    """Validate common-adoption support and freeze design/sample before estimation."""
    _, membership, sample, info, audit, pre, post = _setup_common(rows, **config)
    fit = _fit(_units(sample, membership, pre, post))
    return {
        "config": info["config"],
        "audit": audit,
        "inference_support": _inference(fit),
        "source_records_sha256": info["source_records_sha256"],
    }


def traditional_did(
    rows: Sequence[Mapping[str, Any]],
    *,
    entity: str,
    time: str,
    outcome: str,
    treatment: str,
    treatment_time: int,
    pre_periods: Sequence[int],
    post_periods: Sequence[int],
    cluster: str,
    research_design: Mapping[str, Any],
    missing_policy: str = "error",
    covariates: Sequence[str] = (),
    alpha: float = 0.05,
    descriptive_staggered_benchmark: bool = False,
) -> dict[str, Any]:
    """Common-adoption DiD of explicit period means; exposure is absorbing 0/1.

    The optional staggered benchmark is only a descriptive ever-treated versus
    never-treated change. It must not be interpreted as a staggered causal ATT.
    """
    _, membership, sample, info, audit, pre, post = _setup_common(
        rows,
        entity=entity,
        time=time,
        outcome=outcome,
        treatment=treatment,
        treatment_time=treatment_time,
        pre_periods=pre_periods,
        post_periods=post_periods,
        cluster=cluster,
        research_design=research_design,
        missing_policy=missing_policy,
        covariates=covariates,
        descriptive_staggered_benchmark=descriptive_staggered_benchmark,
    )
    info["config"]["alpha"] = alpha
    units = _units(sample, membership, pre, post)
    return _finish(
        "traditional_did",
        _fit(units),
        info,
        audit,
        units,
        alpha=alpha,
        causal_status=(
            "descriptive_staggered_benchmark"
            if descriptive_staggered_benchmark
            else "conditional_on_identifying_assumptions"
        ),
    )


def _joint(fits: Sequence[Mapping[str, Any]]) -> list[list[float | None]]:
    """Cross-cell covariance of each cell's own sqrt(CR1)-scaled influence."""
    return [
        [
            (
                math.sqrt(a["cr1_factor"] * b["cr1_factor"])
                * sum(v * b["influence"].get(c, 0.0) for c, v in a["influence"].items())
                if a["cr1_factor"] is not None and b["cr1_factor"] is not None
                else None
            )
            for b in fits
        ]
        for a in fits
    ]


def event_study(
    rows: Sequence[Mapping[str, Any]],
    *,
    entity: str,
    time: str,
    outcome: str,
    treatment: str,
    treatment_time: int,
    periods: Sequence[int],
    reference_period: int,
    cluster: str,
    research_design: Mapping[str, Any],
    missing_policy: str = "error",
    covariates: Sequence[str] = (),
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Group-difference changes relative to an explicit pre-treatment base.

    All effects use one complete entity sample across every requested period
    and the reference. The joint pretrend test uses the joint cluster covariance.
    Its non-rejection cannot establish parallel trends.
    """
    selected = _periods(periods, "periods")
    reference = _integer(reference_period, "reference_period")
    date = _integer(treatment_time, "treatment_time")
    if reference >= date:
        raise ValueError("reference_period must precede the real treatment date")
    comparison = [p for p in selected if p != reference]
    if not comparison or len(comparison) > MAX_CONTRASTS:
        raise ValueError("request between one and 100 non-reference event contrasts")
    groups, info = _load(
        rows,
        entity=entity,
        time=time,
        outcome=outcome,
        cluster=cluster,
        research_design=research_design,
        missing_policy=missing_policy,
        covariates=covariates,
    )
    membership = _common(groups, treatment, date)
    sample, audit = _select(groups, sorted(set(selected + [reference])), missing_policy)
    info["config"].update(
        treatment=treatment,
        treatment_time=date,
        periods=selected,
        reference_period=reference,
        alpha=alpha,
    )
    fits, effects = [], []
    for p in comparison:
        fit = _fit(_units(sample, membership, [reference], [p]))
        fits.append(fit)
        inf = _inference(fit, alpha)
        effects.append(
            {
                "period": p,
                "event_time": p - date,
                "estimate": fit["estimate"],
                **{k: inf[k] for k in ("se", "ci_low", "ci_high", "p_value")},
                "inference": inf,
            }
        )
    covariance = _joint(fits)
    leads = [i for i, p in enumerate(comparison) if p < date]
    joint = {
        "status": "unavailable",
        "wald": None,
        "f_statistic": None,
        "df_num": len(leads),
        "df_den": fits[0]["cluster_count"] - 1,
        "p_value": None,
        "reason": "no non-reference pre-treatment contrasts",
        "interpretation": "Non-rejection does not prove parallel trends; rejection can reflect design failures or chance.",
    }
    if leads:
        if any(fits[i]["unavailable_reasons"] for i in leads):
            joint["reason"] = (
                "insufficient assignment clusters/residual degrees of freedom"
            )
        else:
            try:
                inverse = _inverse([[covariance[i][j] for j in leads] for i in leads])
                estimates = [fits[i]["estimate"] for i in leads]
                wald = sum(a * b for a, b in zip(estimates, _mv(inverse, estimates)))
                f = max(0.0, wald) / len(leads)
                df = joint["df_den"]
                pvalue = _ibeta(df / 2, len(leads) / 2, df / (df + len(leads) * f))
                joint.update(
                    status="available",
                    wald=wald,
                    f_statistic=f,
                    p_value=pvalue,
                    reason=None,
                )
            except ValueError:
                joint["reason"] = "rank-deficient joint pretrend covariance"
    result = _finish(
        "event_study",
        fits[0],
        info,
        audit,
        _units(sample, membership, [reference], [comparison[0]]),
        alpha=alpha,
    )
    for key in (
        "estimate",
        "se",
        "ci_low",
        "ci_high",
        "p_value",
        "coefficients",
        "coefficient_covariance",
        "unit_changes",
        "inference",
    ):
        result.pop(key)
    result.update(
        effects=effects,
        covariance=covariance,
        covariance_periods=comparison,
        joint_pretrend=joint,
    )
    result["manifest"][
        "formula"
    ] = "For each period p: (treated mean Yp-Yref)-(control mean Yp-Yref); same complete sample; joint CR1 cluster score covariance"
    return result


def _placebo_setup(
    rows: Sequence[Mapping[str, Any]],
    *,
    entity: str,
    time: str,
    outcome: str,
    treatment: str,
    treatment_time: int,
    pre_periods: Sequence[int],
    post_periods: Sequence[int],
    cluster: str,
    research_design: Mapping[str, Any],
    missing_policy: str = "error",
    covariates: Sequence[str] = (),
) -> tuple[Any, ...]:
    pre, post = _periods(pre_periods, "pre_periods"), _periods(
        post_periods, "post_periods"
    )
    if set(pre) & set(post):
        raise ValueError("placebo pre/post period windows cannot overlap")
    date = _integer(treatment_time, "treatment_time")
    groups, info = _load(
        rows,
        entity=entity,
        time=time,
        outcome=outcome,
        cluster=cluster,
        research_design=research_design,
        missing_policy=missing_policy,
        covariates=covariates,
    )
    real = _common(groups, treatment, date)
    info["config"].update(
        treatment=treatment, treatment_time=date, pre_periods=pre, post_periods=post
    )
    return groups, real, info, pre, post, date


def placebo_time(
    rows: Sequence[Mapping[str, Any]],
    *,
    entity: str,
    time: str,
    outcome: str,
    treatment: str,
    treatment_time: int,
    fake_treatment_time: int,
    pre_periods: Sequence[int],
    post_periods: Sequence[int],
    cluster: str,
    research_design: Mapping[str, Any],
    missing_policy: str = "error",
    covariates: Sequence[str] = (),
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Fake-date DiD; both windows must be strictly before known real adoption."""
    groups, real, info, pre, post, date = _placebo_setup(
        rows,
        entity=entity,
        time=time,
        outcome=outcome,
        treatment=treatment,
        treatment_time=treatment_time,
        pre_periods=pre_periods,
        post_periods=post_periods,
        cluster=cluster,
        research_design=research_design,
        missing_policy=missing_policy,
        covariates=covariates,
    )
    fake = _integer(fake_treatment_time, "fake_treatment_time")
    if max(pre) >= fake or min(post) < fake or fake >= date or max(pre + post) >= date:
        raise ValueError(
            "time-placebo windows must straddle fake date strictly before real treatment"
        )
    sample, audit = _select(groups, pre + post, missing_policy)
    info["config"].update(fake_treatment_time=fake, alpha=alpha)
    units = _units(sample, real, pre, post)
    return _finish(
        "placebo_time",
        _fit(units),
        info,
        audit,
        units,
        alpha=alpha,
        causal_status="falsification_diagnostic",
    )


def _never_sample(
    groups: Mapping[str, list[dict[str, Any]]],
    real: Mapping[str, int],
    pre: list[int],
    post: list[int],
    missing_policy: str,
) -> tuple[Any, ...]:
    never = {k: rs for k, rs in groups.items() if real[k] == 0}
    sample, audit = _select(never, pre + post, missing_policy)
    audit["excluded_genuine_treated_entities"] = [
        groups[k][0]["entity"] for k in sorted(groups) if real[k] == 1
    ]
    audit["genuine_status_rule"] = (
        "Classified on all original supplied exposure histories before window filtering; all-zero histories declared genuinely never-treated by research design."
    )
    return never, sample, audit


def placebo_group(
    rows: Sequence[Mapping[str, Any]],
    *,
    entity: str,
    time: str,
    outcome: str,
    treatment: str,
    treatment_time: int,
    fake_treated_entities: Sequence[str | int],
    pre_periods: Sequence[int],
    post_periods: Sequence[int],
    cluster: str,
    research_design: Mapping[str, Any],
    missing_policy: str = "error",
    covariates: Sequence[str] = (),
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Fake treated entities from genuine never-treated controls, whole clusters."""
    groups, real, info, pre, post, date = _placebo_setup(
        rows,
        entity=entity,
        time=time,
        outcome=outcome,
        treatment=treatment,
        treatment_time=treatment_time,
        pre_periods=pre_periods,
        post_periods=post_periods,
        cluster=cluster,
        research_design=research_design,
        missing_policy=missing_policy,
        covariates=covariates,
    )
    if max(pre) >= date or min(post) < date:
        raise ValueError("group-placebo periods must straddle real treatment_time")
    if (
        isinstance(fake_treated_entities, (str, bytes))
        or not isinstance(fake_treated_entities, Sequence)
        or not fake_treated_entities
    ):
        raise ValueError(
            "fake_treated_entities must be a nonempty explicit ID sequence"
        )
    keys = [_canonical(_id(v, entity)) for v in fake_treated_entities]
    if len(set(keys)) != len(keys):
        raise ValueError("fake treated entity IDs must be unique")
    if any(k not in groups or real[k] != 0 for k in keys):
        raise ValueError("fake treated IDs must be genuine never-treated entities")
    never, sample, audit = _never_sample(groups, real, pre, post, missing_policy)
    membership = {k: int(k in keys) for k in never}
    _assignment(never, membership)
    if any(k not in sample for k in keys):
        raise ValueError(
            "a fake treated entity lacks complete observed placebo windows"
        )
    units = _units(sample, membership, pre, post)
    info["config"].update(
        fake_treated_entities=[groups[k][0]["entity"] for k in sorted(keys)],
        alpha=alpha,
    )
    return _finish(
        "placebo_group",
        _fit(units),
        info,
        audit,
        units,
        alpha=alpha,
        causal_status="falsification_diagnostic",
    )


def placebo_distribution(
    rows: Sequence[Mapping[str, Any]],
    *,
    entity: str,
    time: str,
    outcome: str,
    treatment: str,
    treatment_time: int,
    pre_periods: Sequence[int],
    post_periods: Sequence[int],
    cluster: str,
    research_design: Mapping[str, Any],
    seed: int,
    draws: int,
    fake_cluster_count: int,
    missing_policy: str = "error",
    covariates: Sequence[str] = (),
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Seeded whole-cluster placebo distribution within never-treated controls.

    The absolute tail fraction against the real estimate is descriptive. It is
    not a randomization p-value without an actual exchangeable assignment design.
    """
    seed = _integer(seed, "seed")
    count, fake_count = _integer(draws, "draws"), _integer(
        fake_cluster_count, "fake_cluster_count"
    )
    if not 1 <= count <= MAX_DRAWS:
        raise ValueError(f"draws must be between 1 and {MAX_DRAWS}")
    groups, real, info, pre, post, date = _placebo_setup(
        rows,
        entity=entity,
        time=time,
        outcome=outcome,
        treatment=treatment,
        treatment_time=treatment_time,
        pre_periods=pre_periods,
        post_periods=post_periods,
        cluster=cluster,
        research_design=research_design,
        missing_policy=missing_policy,
        covariates=covariates,
    )
    if max(pre) >= date or min(post) < date:
        raise ValueError("group-placebo periods must straddle real treatment_time")
    never, sample, audit = _never_sample(groups, real, pre, post, missing_policy)
    labels = {_canonical(rs[0]["cluster"]): rs[0]["cluster"] for rs in sample.values()}
    clusters = sorted(labels)
    if not 1 <= fake_count < len(clusters):
        raise ValueError("fake_cluster_count must leave at least one control cluster")
    if count * len(sample) > 5_000_000:
        raise ValueError("placebo draw-by-entity workload exceeds 5,000,000")
    real_sample, real_audit = _select(groups, pre + post, missing_policy)
    actual = _fit(_units(real_sample, real, pre, post))
    rng = random.Random(seed)
    distribution = []
    template = _units(sample, {k: 0 for k in sample}, pre, post)
    for index in range(count):
        fake = set(rng.sample(clusters, fake_count))
        units = [
            {**u, "group": int(_canonical(u["cluster"]) in fake)} for u in template
        ]
        fit = _fit(units)
        distribution.append(
            {
                "draw": index + 1,
                "estimate": fit["estimate"],
                "fake_treated_clusters": [labels[c] for c in sorted(fake)],
            }
        )
    info["config"].update(
        seed=seed, draws=count, fake_cluster_count=fake_count, alpha=alpha
    )
    result = _finish(
        "placebo_distribution",
        actual,
        info,
        audit,
        [],
        alpha=alpha,
        causal_status="descriptive_falsification_distribution",
    )
    result.update(
        distribution=distribution,
        real_estimate=actual["estimate"],
        real_sample_audit=real_audit,
        descriptive_tail_fraction=sum(
            abs(d["estimate"]) >= abs(actual["estimate"]) for d in distribution
        )
        / count,
        tail_fraction_interpretation="Descriptive fraction of sampled placebo absolute effects >= real absolute estimate; not a randomization p-value.",
    )
    return result


def triple_difference(
    rows: Sequence[Mapping[str, Any]],
    *,
    entity: str,
    time: str,
    outcome: str,
    treatment: str,
    treatment_time: int,
    subgroup: str,
    pre_periods: Sequence[int],
    post_periods: Sequence[int],
    cluster: str,
    research_design: Mapping[str, Any],
    missing_policy: str = "error",
    covariates: Sequence[str] = (),
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Unconditional common-date 2x2x2 DDD with a joint cluster sandwich.

    Saturated entity-change OLS [1,T,S,T*S] estimates DiD(S=1)-DiD(S=0).
    Clusters shared across subgroups retain cross-covariance.
    """
    groups, membership, sample, info, audit, pre, post = _setup_common(
        rows,
        entity=entity,
        time=time,
        outcome=outcome,
        treatment=treatment,
        treatment_time=treatment_time,
        pre_periods=pre_periods,
        post_periods=post_periods,
        cluster=cluster,
        research_design=research_design,
        missing_policy=missing_policy,
        covariates=covariates,
    )
    if subgroup in {entity, time, outcome, treatment, cluster}:
        raise ValueError("subgroup must be a separate time-invariant binary column")
    strata = {}
    for key, rs in groups.items():
        if any(subgroup in r["raw"].get(_IMPUTED, []) for r in rs):
            raise ValueError("subgroup assignment cannot be imputed")
        values = {_binary(r["raw"].get(subgroup), subgroup) for r in rs}
        if len(values) != 1:
            raise ValueError("subgroup must be time invariant within entity")
        strata[key] = values.pop()
    units = _units(sample, membership, pre, post)
    for u in units:
        u["subgroup"] = strata[_canonical(u["entity"])]
    info["config"].update(
        subgroup=subgroup,
        alpha=alpha,
        identifying_contrast="DiD(subgroup=1)-DiD(subgroup=0); requires differential parallel-trends assumption",
    )
    fit = _fit(units, subgroup=True)
    result = _finish("triple_difference", fit, info, audit, units, alpha=alpha)
    result["manifest"][
        "formula"
    ] = "Saturated delta OLS [1,T,S,T*S]; T*S coefficient is DDD; full joint cluster CR1 covariance"
    return result


def _aggregate(
    fits: Sequence[Mapping[str, Any]],
    covariance: Sequence[Sequence[float | None]],
    indices: Sequence[int],
    weights: Sequence[float],
    *,
    alpha: float,
) -> dict[str, Any]:
    estimate = sum(weights[j] * fits[i]["estimate"] for j, i in enumerate(indices))
    complete_covariance = all(
        covariance[i][j] is not None for i in indices for j in indices
    )
    variance = (
        sum(
            weights[a] * weights[b] * covariance[i][j]
            for a, i in enumerate(indices)
            for b, j in enumerate(indices)
        )
        if complete_covariance
        else None
    )
    clusters = set().union(*(fits[i]["influence"] for i in indices))
    reasons = sorted({r for i in indices for r in fits[i]["unavailable_reasons"]})
    if not complete_covariance:
        reasons.append("one or more joint covariance entries unavailable")
    aggregate = {
        "estimate": estimate,
        "variance": variance,
        "cluster_count": len(clusters),
        "unavailable_reasons": reasons,
        "group_cluster_counts": {},
        "cell_cluster_counts": {},
        "cr1_factor": None,
    }
    inference = _inference(aggregate, alpha)
    inference["method"] = (
        "Linear aggregation of joint per-cell CR1 cluster influence covariance; Student-t union-cluster G-1 reference"
    )
    return {
        "estimate": estimate,
        **{k: inference[k] for k in ("se", "ci_low", "ci_high", "p_value")},
        "inference": inference,
        "cell_indices": list(indices),
        "weights": list(weights),
        "variance": variance,
    }


def staggered_did(
    rows: Sequence[Mapping[str, Any]],
    *,
    entity: str,
    time: str,
    outcome: str,
    cohort: str,
    periods: Sequence[int],
    cluster: str,
    research_design: Mapping[str, Any],
    control_group: str = "never_treated",
    treatment: str | None = None,
    missing_policy: str = "error",
    covariates: Sequence[str] = (),
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Unadjusted ATT(g,t), t>=g, relative to g-1 with declared cohort=0 never.

    Cohort is an invariant first-adoption date encoding absorbing exposure.
    Optional observed ``treatment`` additionally verifies this exposure history.
    All entities use one balanced complete sample across prespecified periods.
    Never-treated or not-yet-treated controls are chosen explicitly. Cross-cell
    covariance uses each cell's own sqrt(CR1)-scaled cluster influence scores.
    This is an unconditional group-time contrast, not full conditional/DR C&S.
    """
    selected = _periods(periods, "periods")
    if control_group not in {"never_treated", "not_yet_treated"}:
        raise ValueError("control_group must be never_treated or not_yet_treated")
    groups, info = _load(
        rows,
        entity=entity,
        time=time,
        outcome=outcome,
        cluster=cluster,
        research_design=research_design,
        missing_policy=missing_policy,
        covariates=covariates,
    )
    if cohort in {entity, time, outcome, cluster}:
        raise ValueError(
            "cohort must be a separate column; integer date with never sentinel 0"
        )
    cohorts, assigned = {}, {}
    for key, rs in groups.items():
        if any(cohort in r["raw"].get(_IMPUTED, []) for r in rs):
            raise ValueError("cohort assignment cannot be imputed")
        values = {_integer(r["raw"].get(cohort), cohort) for r in rs}
        if len(values) != 1:
            raise ValueError("unsupported: cohort must be time invariant within entity")
        g = values.pop()
        cohorts[key] = g
        c = _canonical(rs[0]["cluster"])
        if c in assigned and assigned[c] != g:
            raise ValueError("cohort assignment splits an explicit assignment cluster")
        assigned[c] = g
        if treatment is not None:
            if treatment in {entity, time, outcome, cluster, cohort}:
                raise ValueError(
                    "observed treatment must be a separate exposure column"
                )
            if any(treatment in r["raw"].get(_IMPUTED, []) for r in rs):
                raise ValueError("observed treatment cannot be imputed")
            if any(
                _binary(r["raw"].get(treatment), treatment)
                != int(g != 0 and r["period"] >= g)
                for r in rs
            ):
                raise ValueError(
                    "unsupported: observed exposure reverses or disagrees with declared absorbing cohort"
                )
    sample, audit = _select(groups, selected, missing_policy)
    active = sorted(
        {cohorts[k] for k in sample if cohorts[k] != 0 and cohorts[k] <= max(selected)}
    )
    if not active:
        raise ValueError("no comparison: no treated cohorts in selected periods")
    if any(g - 1 not in selected for g in active):
        raise ValueError("each active cohort requires prespecified baseline period g-1")
    if sum(sum(t >= g for t in selected) for g in active) > MAX_CONTRASTS:
        raise ValueError("staggered design exceeds 100 group-time contrasts")
    fits, cells, unsupported = [], [], []
    sizes = {g: sum(cohorts[k] == g for k in sample) for g in active}
    for g in active:
        for t in [p for p in selected if p >= g]:
            base = g - 1
            controls = {
                k
                for k in sample
                if cohorts[k] == 0
                or control_group == "not_yet_treated"
                and cohorts[k] > max(t, base)
            }
            treated = {k for k in sample if cohorts[k] == g}
            controls -= treated
            if not controls:
                unsupported.append(
                    {
                        "cohort": g,
                        "period": t,
                        "reason": "no eligible comparison entities at this group-time cell",
                    }
                )
                continue
            subset = {k: sample[k] for k in sorted(treated | controls)}
            membership = {k: int(k in treated) for k in subset}
            units = _units(subset, membership, [base], [t])
            fit = _fit(units)
            fits.append(fit)
            inf = _inference(fit, alpha)
            cells.append(
                {
                    "cohort": g,
                    "period": t,
                    "event_time": t - g,
                    "baseline_period": base,
                    "cohort_size": sizes[g],
                    "control_entity_count": len(controls),
                    "control_entities": [
                        sample[k][0]["entity"] for k in sorted(controls)
                    ],
                    "estimate": fit["estimate"],
                    **{k: inf[k] for k in ("se", "ci_low", "ci_high", "p_value")},
                    "inference": inf,
                    "unit_changes": units,
                }
            )
    if not cells:
        raise ValueError("no comparison: no estimable staggered group-time cells")
    covariance = _joint(fits)
    dynamic = []
    for event_time in sorted({cell["event_time"] for cell in cells}):
        indices = [
            i for i, cell in enumerate(cells) if cell["event_time"] == event_time
        ]
        total = sum(cells[i]["cohort_size"] for i in indices)
        weights = [cells[i]["cohort_size"] / total for i in indices]
        dynamic.append(
            {
                "event_time": event_time,
                "supported_cohorts": [cells[i]["cohort"] for i in indices],
                **_aggregate(fits, covariance, indices, weights, alpha=alpha),
            }
        )
    # Every supported ATT(g,t) receives its cohort size weight. This estimand
    # weights cohorts with longer observed post histories more; record it.
    denominator = sum(cell["cohort_size"] for cell in cells)
    weights = [cell["cohort_size"] / denominator for cell in cells]
    aggregate = _aggregate(
        fits, covariance, list(range(len(cells))), weights, alpha=alpha
    )
    info["config"].update(
        cohort=cohort,
        never_treated_sentinel=0,
        periods=selected,
        control_group=control_group,
        treatment=treatment,
        alpha=alpha,
        assumption="Absorbing first adoption; no anticipation; unconditional group-time parallel trends",
        covariance_rule="Per-cell sqrt(CR1_j*CR1_k) times cross-cluster influence products",
        aggregation="Cohort-size weights over supported group-time cells; longer post histories receive more overall weight",
    )
    audit["cohort_entity_counts"] = {
        str(g): sum(cohorts[k] == g for k in sample)
        for g in sorted(set(cohorts.values()))
    }
    audit["exposure_validation"] = (
        "observed exposure checked against cohort"
        if treatment
        else "cohort encodes declared absorbing exposure; no observed exposure column was verified"
    )
    result = _finish("staggered_did", fits[0], info, audit, [], alpha=alpha)
    for key in (
        "estimate",
        "se",
        "ci_low",
        "ci_high",
        "p_value",
        "coefficients",
        "coefficient_covariance",
        "unit_changes",
        "inference",
    ):
        result.pop(key)
    result.update(
        cells=cells,
        cell_covariance=covariance,
        cell_covariance_order=[
            {"cohort": c["cohort"], "period": c["period"]} for c in cells
        ],
        dynamic_effects=dynamic,
        aggregate=aggregate,
        unsupported_cells=unsupported,
    )
    result["manifest"][
        "formula"
    ] = "ATT(g,t)=mean(Yt-Yg-1|G=g)-mean(Yt-Yg-1|eligible controls); joint per-cell CR1 score covariance; explicit cohort-size linear aggregation"
    result["limitations"].extend(
        [
            "No covariate adjustment, doubly robust scores, propensity weights, multiplier bootstrap or simultaneous confidence bands.",
            "Dynamic support may change across event times; inspect supported_cohorts and unsupported_cells.",
        ]
    )
    return result


def analyze_did(
    rows: Sequence[Mapping[str, Any]], *, plan: Mapping[str, Any]
) -> dict[str, Any]:
    """Execute a frozen plan: traditional first, placebos next, derived DiD last.

    ``plan`` contains ``traditional`` kwargs and optional lists ``event_study``,
    ``placebo_time``, ``placebo_group``, ``placebo_distribution``, ``derived``.
    Each derived entry contains ``method`` and ``config``; methods are
    ``triple_difference`` or ``staggered_did``. No design/periods are inferred.
    """
    if not isinstance(plan, Mapping) or not isinstance(
        plan.get("traditional"), Mapping
    ):
        raise ValueError("prespecified plan requires a traditional configuration")
    allowed = {
        "traditional",
        "event_study",
        "placebo_time",
        "placebo_group",
        "placebo_distribution",
        "derived",
    }
    if set(plan) - allowed:
        raise ValueError("unknown prespecified plan stage")
    frozen = _finite_json(dict(plan))
    staged = []
    counts = 1
    for key in allowed - {"traditional"}:
        values = frozen.get(key, [])
        if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
            raise ValueError(f"plan.{key} must be a list of explicit configurations")
        counts += len(values)
    if counts > 20:
        raise ValueError("plan exceeds 20 estimators/diagnostics")
    derived = {"triple_difference": triple_difference, "staggered_did": staggered_did}
    for entry in frozen.get("derived", []):
        if (
            set(entry) != {"method", "config"}
            or entry["method"] not in derived
            or not isinstance(entry["config"], dict)
        ):
            raise ValueError(
                "derived entries require supported method and explicit config"
            )
    staged.append(traditional_did(rows, **frozen["traditional"]))
    source_hash = staged[0]["manifest"]["source_records_sha256"]

    def record(method: str, function: Any, config: Mapping[str, Any]) -> None:
        try:
            staged.append(function(rows, **config))
        except (ValueError, TypeError, ArithmeticError) as exc:
            staged.append(
                {
                    "method": method,
                    "status": "refused",
                    "reason": str(exc),
                    "config": dict(config),
                    "manifest": {
                        "schema_version": 1,
                        "method": VERSION,
                        "estimator": method,
                        "source_records_sha256": source_hash,
                        "configuration_sha256": _digest(config),
                        "status": "refused",
                        "reason": str(exc),
                    },
                }
            )

    functions = {
        "event_study": event_study,
        "placebo_time": placebo_time,
        "placebo_group": placebo_group,
        "placebo_distribution": placebo_distribution,
    }
    for key in ("event_study", "placebo_time", "placebo_group", "placebo_distribution"):
        for config in frozen.get(key, []):
            record(key, functions[key], config)
    for entry in frozen.get("derived", []):
        record(entry["method"], derived[entry["method"]], entry["config"])
    result = {
        "method": "prespecified_did_pipeline",
        "plan": frozen,
        "results": staged,
        "execution_order": [r["method"] for r in staged],
        "manifest": {
            "schema_version": 1,
            "method": VERSION,
            "source_records_sha256": _digest(
                _finite_json(list(rows), missing_nan=True)
            ),
            "plan_sha256": _digest(frozen),
            "execution_order": [r["method"] for r in staged],
            "result_sha256": _digest(staged),
        },
    }
    _canonical(result)
    return result


def _table(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    if "results" in result:
        return [
            {"stage": i + 1, **row}
            for i, entry in enumerate(result["results"])
            for row in _table(entry)
        ]
    if result.get("method") == "placebo_distribution" and "distribution" in result:
        reference = {
            "method": result["method"],
            "role": "real_treatment_reference",
            "estimate": result["real_estimate"],
            **{k: result.get(k) for k in ("se", "ci_low", "ci_high", "p_value")},
            "seed": result["config"]["seed"],
            "descriptive_tail_fraction": result["descriptive_tail_fraction"],
        }
        draws = [
            {
                "method": result["method"],
                "role": "fake_group_draw",
                "draw": row["draw"],
                "estimate": row["estimate"],
                "fake_treated_clusters": row["fake_treated_clusters"],
                "seed": result["config"]["seed"],
                "se": None,
                "ci_low": None,
                "ci_high": None,
                "p_value": None,
            }
            for row in result["distribution"]
        ]
        return [reference, *draws]
    if "effects" in result:
        return [
            {
                "method": result["method"],
                **{k: v for k, v in row.items() if k != "inference"},
            }
            for row in result["effects"]
        ]
    if "cells" in result:
        return [
            {
                "method": result["method"],
                **{
                    k: v
                    for k, v in row.items()
                    if k not in {"inference", "unit_changes", "control_entities"}
                },
            }
            for row in result["cells"]
        ]
    return [
        {
            "method": result["method"],
            "status": result.get("status", "estimated"),
            "reason": result.get("reason"),
            **{
                k: result.get(k)
                for k in ("estimate", "se", "ci_low", "ci_high", "p_value")
            },
        }
    ]


def write_outputs(result: Mapping[str, Any], directory: str | Path) -> dict[str, str]:
    """Write a deterministic JSON/CSV bundle exclusively; never overwrite files."""
    frozen = _finite_json(dict(result))
    if not isinstance(frozen.get("manifest"), dict):
        raise ValueError("result requires a manifest")
    root = Path(directory)
    names = ("results.json", "estimates.csv", "manifest.json")
    if any((root / name).exists() for name in names):
        raise FileExistsError("output bundle exists; choose a new directory")
    root.mkdir(parents=True, exist_ok=True)
    with (root / "results.json").open("x", encoding="utf-8") as handle:
        json.dump(
            frozen,
            handle,
            sort_keys=True,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        handle.write("\n")
    table = _table(frozen)
    fields = sorted({k for row in table for k in row})
    with (root / "estimates.csv").open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in table:
            writer.writerow(
                {
                    k: _canonical(v) if isinstance(v, (dict, list)) else v
                    for k, v in row.items()
                }
            )
    manifest = {
        **frozen["manifest"],
        "outputs": {
            name: {
                "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
                "size": (root / name).stat().st_size,
            }
            for name in names
            if name != "manifest.json"
        },
    }
    with (root / "manifest.json").open("x", encoding="utf-8") as handle:
        json.dump(
            manifest,
            handle,
            sort_keys=True,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        handle.write("\n")
    return {name: str((root / name).resolve()) for name in names}


def render_diagnostics(
    result: Mapping[str, Any], directory: str | Path
) -> dict[str, str]:
    """Plot explicit event timing, real contrasts and sampled placebo effects.

    Panels distinguish real-reference values from fake-group draws. Event-study
    reference points are normalization constraints, not estimated zero effects.
    Intervals are pointwise; hollow points indicate unavailable inference.
    """
    from textwrap import fill

    try:
        import matplotlib
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
    except ImportError as exc:
        raise RuntimeError("diagnostic plots require optional matplotlib") from exc
    root = Path(directory)
    names = ("did-diagnostics.png", "did-diagnostics.svg", "visualization.json")
    if any((root / name).exists() for name in names):
        raise FileExistsError("diagnostic bundle exists; choose a new directory")
    entries = result.get("results", [result])
    if not isinstance(entries, list) or not 1 <= len(entries) <= 20:
        raise ValueError("diagnostic plots require between 1 and 20 stages")
    refused = [
        {"stage": i + 1, "method": entry["method"], "reason": entry["reason"]}
        for i, entry in enumerate(entries)
        if entry.get("status") == "refused"
    ]
    completed = [
        (i + 1, entry)
        for i, entry in enumerate(entries)
        if entry.get("status") != "refused"
    ]
    forest = [
        (stage, entry)
        for stage, entry in completed
        if entry["method"]
        not in {"event_study", "staggered_did", "placebo_distribution"}
        and entry.get("estimate") is not None
    ]
    panels: list[tuple[str, Any]] = [("contrasts", forest)] if forest else []
    point_count = len(forest)
    for stage, entry in completed:
        method = entry["method"]
        if method == "event_study":
            panels.append((method, (stage, entry)))
            point_count += len(entry["effects"]) + 1
        elif method == "staggered_did":
            panels.append((method, (stage, entry)))
            point_count += len(entry["dynamic_effects"])
        elif method == "placebo_distribution":
            if not 1 <= len(entry["distribution"]) <= MAX_DRAWS:
                raise ValueError("placebo plot exceeds the bounded draw count")
            panels.append((method, (stage, entry)))
    if refused:
        panels.append(("refusals", refused))
    if not panels or point_count > 100:
        raise ValueError("diagnostic plots require at most 100 contrast points")
    root.mkdir(parents=True, exist_ok=True)
    panel_metadata = []

    def point(axis: Any, x: float, row: Mapping[str, Any]) -> None:
        low, high = row.get("ci_low"), row.get("ci_high")
        available = low is not None and high is not None
        axis.plot(
            x,
            row["estimate"],
            "o",
            color="#246b8e",
            fillstyle="full" if available else "none",
        )
        if available:
            axis.plot([x, x], [low, high], color="#246b8e", linewidth=1.5)

    with matplotlib.rc_context({"svg.hashsalt": VERSION}):
        figure = Figure(figsize=(12, max(4, len(panels) * 3.8)), layout="constrained")
        axes = figure.subplots(len(panels), 1, squeeze=False)[:, 0]
        figure.suptitle(
            "Prespecified DiD diagnostics: pointwise intervals; hollow points have unavailable inference",
            fontsize=12,
        )
        for axis, (kind, content) in zip(axes, panels):
            metadata: dict[str, Any] = {"kind": kind}
            if kind == "contrasts":
                labels = []
                for index, (stage, entry) in enumerate(content):
                    estimate, low, high = (
                        entry["estimate"],
                        entry.get("ci_low"),
                        entry.get("ci_high"),
                    )
                    available = low is not None and high is not None
                    axis.plot(
                        estimate,
                        index,
                        "o",
                        color="#246b8e",
                        fillstyle="full" if available else "none",
                    )
                    if available:
                        axis.plot([low, high], [index, index], color="#246b8e")
                    labels.append(f"Stage {stage}: {entry['method']}")
                axis.set_yticks(range(len(labels)), labels)
                axis.axvline(0, color="#666666", linestyle="--", linewidth=1)
                axis.set_xlabel("Outcome contrast")
                axis.set_title("Traditional, DDD and individual placebo contrasts")
                metadata["stages"] = [stage for stage, _ in content]
            elif kind == "event_study":
                stage, entry = content
                config = entry["config"]
                reference = config["reference_period"] - config["treatment_time"]
                effects = entry["effects"]
                for row in effects:
                    point(axis, row["event_time"], row)
                axis.plot(
                    reference,
                    0,
                    "s",
                    color="#777777",
                    label="Reference normalization (fixed at zero)",
                )
                axis.axvline(reference, color="#777777", linestyle=":", linewidth=1)
                axis.axvline(
                    0,
                    color="#c67c16",
                    linestyle="--",
                    label="Real adoption (event time 0)",
                )
                axis.axhline(0, color="#666666", linewidth=0.8)
                axis.set_xticks(
                    sorted({reference, 0, *(row["event_time"] for row in effects)})
                )
                axis.set_xlabel("Periods relative to real adoption")
                axis.set_ylabel("Change relative to reference")
                axis.set_title(
                    f"Stage {stage}: event study; pretrend non-rejection does not prove parallel trends"
                )
                axis.legend(fontsize=8, loc="best")
                metadata.update(
                    stage=stage,
                    reference_event_time=reference,
                    adoption_event_time=0,
                    reference_period=config["reference_period"],
                    treatment_time=config["treatment_time"],
                    plotted_periods=[
                        {"period": row["period"], "event_time": row["event_time"]}
                        for row in effects
                    ],
                    joint_pretrend=entry["joint_pretrend"],
                )
            elif kind == "staggered_did":
                stage, entry = content
                dynamic = entry["dynamic_effects"]
                for row in dynamic:
                    point(axis, row["event_time"], row)
                axis.axvline(
                    0, color="#c67c16", linestyle="--", label="Each cohort's adoption"
                )
                axis.axhline(0, color="#666666", linewidth=0.8)
                axis.set_xticks([row["event_time"] for row in dynamic])
                axis.set_xlabel(
                    "Periods relative to cohort adoption; cohort-size weighted"
                )
                axis.set_ylabel("Dynamic ATT contrast")
                counts = ", ".join(
                    f"e={row['event_time']}: {len(row['supported_cohorts'])} cohorts"
                    for row in dynamic
                )
                axis.set_title(
                    f"Stage {stage}: staggered dynamic effects; support varies across event times"
                )
                axis.text(
                    0.01,
                    0.02,
                    fill(counts, 110),
                    transform=axis.transAxes,
                    fontsize=8,
                    va="bottom",
                )
                metadata.update(
                    stage=stage,
                    dynamic_support=[
                        {
                            "event_time": row["event_time"],
                            "supported_cohorts": row["supported_cohorts"],
                            "cell_indices": row["cell_indices"],
                            "weights": row["weights"],
                        }
                        for row in dynamic
                    ],
                    unsupported_cells=entry["unsupported_cells"],
                    aggregate=entry["aggregate"],
                )
            elif kind == "placebo_distribution":
                stage, entry = content
                values = [row["estimate"] for row in entry["distribution"]]
                actual = entry["real_estimate"]
                bins = min(30, max(5, int(math.sqrt(len(values)))))
                axis.hist(
                    values,
                    bins=bins,
                    color="#246b8e",
                    alpha=0.8,
                    label="Sampled fake-group estimates (never-treated clusters)",
                )
                axis.axvline(
                    actual,
                    color="#c67c16",
                    linestyle="--",
                    linewidth=1.5,
                    label="Real DiD estimate: comparison reference",
                )
                axis.axvline(0, color="#666666", linewidth=0.8)
                axis.set_xlabel(
                    "Outcome contrast; real reference is not a placebo draw"
                )
                axis.set_ylabel("Sampled draws")
                axis.set_title(
                    f"Stage {stage}: fake-group distribution; seed {entry['config']['seed']}, {len(values)} draws; descriptive tail fraction {entry['descriptive_tail_fraction']:.3f}"
                )
                axis.legend(fontsize=8, loc="upper left")
                if actual < min(values) or actual > max(values):
                    inset = axis.inset_axes([0.49, 0.46, 0.48, 0.45])
                    inset.hist(values, bins=bins, color="#246b8e", alpha=0.8)
                    inset.set_title("Zoom: sampled fake-group effects", fontsize=8)
                    inset.tick_params(labelsize=7)
                metadata.update(
                    stage=stage,
                    real_reference=actual,
                    seed=entry["config"]["seed"],
                    draws=len(values),
                    fake_cluster_count=entry["config"]["fake_cluster_count"],
                    descriptive_tail_fraction=entry["descriptive_tail_fraction"],
                    interpretation=entry["tail_fraction_interpretation"],
                    distribution_sha256=_digest(entry["distribution"]),
                )
            else:
                axis.axis("off")
                text = "\n".join(
                    fill(
                        f"Stage {row['stage']} ({row['method']}): {row['reason']}", 110
                    )
                    for row in content
                )
                axis.set_title(
                    f"{len(content)} prespecified stage(s) refused; no estimate was produced"
                )
                axis.text(
                    0.01,
                    0.95,
                    text,
                    transform=axis.transAxes,
                    va="top",
                    fontsize=max(6, min(10, 80 / len(content))),
                )
                metadata["refusals"] = content
            if kind != "refusals":
                axis.grid(alpha=0.15)
            panel_metadata.append(metadata)
        FigureCanvasAgg(figure)
        for suffix in ("png", "svg"):
            figure.savefig(
                root / f"did-diagnostics.{suffix}",
                dpi=150,
                metadata=(
                    {"Date": None}
                    if suffix == "svg"
                    else {"Software": "OpenAI4S did-analysis"}
                ),
            )
    svg = root / "did-diagnostics.svg"
    svg.write_text(
        "\n".join(
            line.rstrip() for line in svg.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )
    visualization = {
        "method": VERSION,
        "result_sha256": _digest(result),
        "panels": panel_metadata,
        "refused_stage_count": len(refused),
        "interpretation": "Pointwise cluster intervals; no simultaneous bands. Placebo/pretrend non-rejection does not establish parallel trends.",
        "outputs": {
            name: {"sha256": hashlib.sha256((root / name).read_bytes()).hexdigest()}
            for name in names
            if name != "visualization.json"
        },
    }
    with (root / "visualization.json").open("x", encoding="utf-8") as handle:
        json.dump(
            visualization,
            handle,
            sort_keys=True,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        handle.write("\n")
    return {name: str((root / name).resolve()) for name in names}
