"""Text-feature helpers. Auto-loaded into the python kernel when the skill loads:

 propose_questions, featurize, run_feature_study

Module top level is definition-only. Optional numpy / pandas / sklearn are
imported inside functions. Sibling Skills (audit-dataset, plan-ml-experiment,
evaluate-model) are reused rather than reimplemented.

No ``from __future__``: kernel-cell tests exec this file after ``sys.modules``
setup, and a future import must be the first statement.
"""

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

TEMPLATE_ID = "features.custom"
TEMPLATE_VERSION = "1"
MAX_QUESTIONS = 24
MAX_QUESTION_CHARS = 400
MAX_STATE_CHARS = 8000
MAX_SCORE_LEVELS = 10
ALLOWED_KINDS = ("noul", "score")
DEFAULT_N = 12
FLAT_SPREAD = 1e-6
CV_FOLDS = 5
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20260920
SPLIT_SEED = 20260920
DEV_TEST_FRACTIONS = (0.5, 0.0, 0.5)

DEFAULT_NOUL_CRITERIA = {
    "true": "The text in state.text states this or clearly implies it.",
    "false": "The text in state.text gives no indication of this.",
}

DEFAULT_SCORE_LEVELS = (
    "Not present in this text at all",
    "Barely present - mentioned once, in passing",
    "Present at a moderate level",
    "Present strongly - the text dwells on it",
    "Dominant - the text is largely about this",
)

_NAN = float("nan")
_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def tf_sdk():
    """Rebind-proof SDK handle — see literature-review/kernel.py:lr_sdk."""
    import host

    return host


def _load_sibling(name: str):
    from importlib import import_module

    return import_module(f"{name}.kernel")


def _try_numpy():
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError:
        return None
    return np


def _try_pandas():
    try:
        import pandas as pd  # type: ignore[import-not-found]
    except ImportError:
        return None
    return pd


def _try_sklearn_linear():
    try:
        from sklearn.linear_model import LinearRegression, LogisticRegression
        from sklearn.model_selection import KFold
    except ImportError:
        return None
    return LinearRegression, LogisticRegression, KFold


def _disabled_payload(**extra: Any) -> dict[str, Any]:
    payload = {
        "status": "disabled",
        "questions": [],
        "table": None,
        "metrics": None,
        "frozen": False,
        "error_code": None,
    }
    payload.update(extra)
    return payload


def _judge(template: str, state: object, **params: Any) -> dict[str, Any]:
    try:
        result = tf_sdk().judge(template, state, **params)
    except Exception as exc:
        return {
            "status": "unavailable",
            "error_code": "unavailable",
            "answers": {},
            "error": str(exc) or type(exc).__name__,
        }
    if isinstance(result, dict) and result.get("error") and "status" not in result:
        return {
            "status": "unavailable",
            "error_code": "invalid_request",
            "answers": {},
            "error": str(result.get("error")),
        }
    if not isinstance(result, dict):
        return {
            "status": "unavailable",
            "error_code": "invalid_response",
            "answers": {},
        }
    return result


def _is_nan(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return not math.isfinite(float(value))
    return False


def _slug(name: str, taken: set[str]) -> str:
    base = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")
    if not base or not base[0].isalpha():
        base = "feature_" + (base or "q")
    candidate = base
    n = 2
    while candidate in taken:
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def _canonical_question(spec: Mapping[str, Any]) -> dict[str, Any]:
    kind_raw = spec.get("kind") or spec.get("type") or ""
    kind = str(kind_raw).strip().lower()
    if kind in {"presence"}:
        kind = "noul"
    if kind in {"intensity"}:
        kind = "score"
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"question kind must be noul or score, not {kind_raw!r}")
    instructions = spec.get("instructions") or spec.get("question") or ""
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("instructions must be a non-empty string")
    text = instructions.strip()
    if len(text) > MAX_QUESTION_CHARS:
        raise ValueError(f"instructions exceed {MAX_QUESTION_CHARS} characters")
    qid = spec.get("id") or spec.get("name") or "feature"
    if not isinstance(qid, str) or not qid.strip():
        qid = "feature"
    qid = qid.strip()
    out: dict[str, Any] = {
        "id": qid,
        "kind": kind,
        "instructions": text,
    }
    if kind == "noul":
        criteria = spec.get("criteria")
        if isinstance(criteria, Mapping) and set(criteria) >= {"true", "false"}:
            out["criteria"] = {
                "true": str(criteria["true"]),
                "false": str(criteria["false"]),
            }
        else:
            out["criteria"] = dict(DEFAULT_NOUL_CRITERIA)
        return out
    raw_levels = spec.get("levels")
    if raw_levels is None:
        raw_levels = spec.get("criteria")
    if isinstance(raw_levels, (list, tuple)) and len(raw_levels) >= 2:
        levels = tuple(str(item).strip() for item in raw_levels if str(item).strip())
    else:
        levels = DEFAULT_SCORE_LEVELS
    if len(levels) < 2 or len(levels) > MAX_SCORE_LEVELS:
        raise ValueError(f"Score must have between 2 and {MAX_SCORE_LEVELS} levels")
    out["levels"] = list(levels)
    return out


def _dedupe_questions(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    taken: set[str] = set()
    seen_instr: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, Mapping):
            continue
        try:
            spec = _canonical_question(raw)
        except ValueError:
            continue
        spec["id"] = _slug(str(spec["id"]), taken)
        taken.add(spec["id"])
        key = " ".join(spec["instructions"].casefold().split())
        if key in seen_instr:
            continue
        seen_instr.add(key)
        out.append(spec)
        if len(out) >= MAX_QUESTIONS:
            break
    return out


def _extract_json(text: str) -> Any:
    blob = text.strip()
    fenced = _FENCE_RE.search(blob)
    if fenced:
        blob = fenced.group(1)
    else:
        match = _JSON_OBJECT_RE.search(blob)
        if match:
            blob = match.group(0)
    return json.loads(blob)


def _actions_to_questions(
    actions: Sequence[Mapping[str, Any]], previous: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    live = {str(item.get("id")): dict(item) for item in previous if item.get("id")}
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        op = str(action.get("op") or action.get("action") or "add").strip().lower()
        target = str(action.get("target") or "")
        if op == "drop":
            live.pop(target, None)
            continue
        try:
            spec = _canonical_question(action)
        except ValueError:
            continue
        if op == "revise" and target in live:
            spec["id"] = live[target]["id"]
            live[target] = spec
            continue
        live[spec["id"]] = spec
    return list(live.values())


def propose_questions(
    task_description: str,
    examples: Sequence[Any],
    *,
    n: int = DEFAULT_N,
    previous: Sequence[Mapping[str, Any]] | None = None,
    feedback: str | None = None,
) -> list[dict[str, Any]]:
    """Ask the main model for operationalizable Noul/Score questions."""

    count = max(1, min(int(n), MAX_QUESTIONS))
    accepted = [dict(item) for item in (previous or [])]
    example_lines = []
    for item in list(examples)[:12]:
        if isinstance(item, Mapping):
            text = item.get("text", item)
            label = item.get("target", item.get("label", ""))
            example_lines.append(f"- label={label!r}: {text}")
        else:
            example_lines.append(f"- {item}")
    previous_block = ""
    if accepted:
        previous_block = (
            "Existing questions (do not duplicate; revise or drop by id):\n"
            + "\n".join(
                f"- {item.get('id')} ({item.get('kind')}): {item.get('instructions')}"
                for item in accepted
            )
        )
    prompt = (
        "You design numeric features for a supervised model that reads only "
        "those features, never the raw text.\n"
        f"Task: {task_description}\n"
        f"Return at most {count} questions as JSON. Use kind noul for a yes/no "
        "fact that can be judged from the text, and kind score for a graded "
        "property with 2-10 written levels. Each question must be answerable "
        "from one row of text, must vary across rows, and must not be a gold "
        "label. Instructions must be at most "
        f"{MAX_QUESTION_CHARS} characters and must refer to the text in "
        "state.text.\n"
        'Schema: {"questions":[{"id":"snake_case","kind":"noul"|"score",'
        '"instructions":"...","levels":["..."]}]}. For noul omit levels. '
        'You may instead return {"actions":[{"op":"add"|"revise"|"drop",'
        '"target":"","id":"...","kind":"...","instructions":"..."}]}.\n'
    )
    if example_lines:
        prompt += "Examples:\n" + "\n".join(example_lines) + "\n"
    if previous_block:
        prompt += previous_block + "\n"
    if feedback:
        prompt += "Model feedback on the current questions:\n" + str(feedback) + "\n"
    try:
        raw = tf_sdk().llm(prompt, temperature=0.2, max_tokens=2048)
    except Exception:
        return _dedupe_questions(accepted) if accepted else []
    if not isinstance(raw, str) or not raw.strip():
        return _dedupe_questions(accepted) if accepted else []
    try:
        parsed = _extract_json(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _dedupe_questions(accepted) if accepted else []
    items: list[Mapping[str, Any]] = []
    if isinstance(parsed, Mapping):
        if isinstance(parsed.get("actions"), list):
            items = _actions_to_questions(parsed["actions"], accepted)
        else:
            blob = (
                parsed.get("questions") or parsed.get("features") or parsed.get("specs")
            )
            if isinstance(blob, list):
                items = list(blob)
            elif isinstance(blob, Mapping):
                items = [
                    (
                        {**dict(value), "id": key}
                        if isinstance(value, Mapping)
                        else {"id": key, "instructions": str(value), "kind": "noul"}
                    )
                    for key, value in blob.items()
                ]
    elif isinstance(parsed, list):
        items = list(parsed)
    try:
        cleaned = _dedupe_questions(items)[:count]
    except ValueError:
        cleaned = []
    return cleaned or _dedupe_questions(accepted)


def _noul_p(payload: Mapping[str, Any], key: str) -> float | None:
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        return None
    answer = answers.get(key)
    if not isinstance(answer, Mapping):
        return None
    raw = answer.get("value")
    if raw is None:
        raw = answer.get("noul")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _score_columns(
    payload: Mapping[str, Any], key: str, n_levels: int
) -> tuple[float, float] | None:
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        return None
    answer = answers.get(key)
    if not isinstance(answer, Mapping):
        return None
    top = n_levels - 1
    if top < 1:
        return None
    probs_raw = answer.get("probabilities")
    expected: float | None = None
    if isinstance(probs_raw, Mapping) and probs_raw:
        total = 0.0
        mass = 0.0
        for index in range(n_levels):
            try:
                p = float(probs_raw.get(str(index), 0.0))
            except (TypeError, ValueError):
                p = 0.0
            total += p * float(index)
            mass += p
        if mass > 0:
            expected = total / mass if abs(mass - 1.0) > 1e-6 else total
    if expected is None:
        raw = answer.get("value")
        if raw is None:
            raw = answer.get("score")
        try:
            expected = float(raw)
        except (TypeError, ValueError):
            return None
    if not math.isfinite(expected):
        return None
    variance = 0.0
    if isinstance(probs_raw, Mapping) and probs_raw:
        for index in range(n_levels):
            try:
                p = float(probs_raw.get(str(index), 0.0))
            except (TypeError, ValueError):
                p = 0.0
            delta = float(index) - expected
            variance += p * delta * delta
    sd = math.sqrt(max(0.0, variance))
    return expected / float(top), sd / float(top)


def _column_plan(questions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for spec in questions:
        qid = str(spec["id"])
        kind = str(spec["kind"])
        source = {
            "question_id": qid,
            "kind": kind,
            "instructions": spec.get("instructions"),
            "template_id": TEMPLATE_ID,
            "template_version": TEMPLATE_VERSION,
        }
        if kind == "noul":
            plan.append({**source, "column": qid, "role": "p_yes"})
            continue
        levels = spec.get("levels") or DEFAULT_SCORE_LEVELS
        n_levels = len(levels)
        plan.append(
            {
                **source,
                "column": qid,
                "role": "mean",
                "n_levels": n_levels,
                "levels": list(levels),
            }
        )
        plan.append(
            {
                **source,
                "column": f"{qid}_sd",
                "role": "sd",
                "n_levels": n_levels,
                "levels": list(levels),
            }
        )
    return plan


def _state_for_row(row_id: object, text: object) -> dict[str, Any]:
    return {"id": row_id, "text": "" if text is None else str(text)}


def _usage_add(total: dict[str, int], payload: Mapping[str, Any]) -> None:
    usage = (
        payload.get("usage") if "answers" in payload or "status" in payload else payload
    )
    if not isinstance(usage, Mapping):
        return
    for key in ("input_tokens", "output_tokens", "requests"):
        try:
            total[key] = int(total.get(key, 0)) + int(usage.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
    if "requests" not in (usage or {}) and payload.get("status"):
        total["requests"] = int(total.get("requests", 0)) + 1


def featurize(
    rows: Sequence[Any],
    questions: Sequence[Mapping[str, Any]],
    *,
    text_field: str,
    id_field: str,
) -> dict[str, Any]:
    """Turn each row's text into calibrated numeric columns via host.judge."""

    specs = _dedupe_questions(questions)
    plan = _column_plan(specs)
    ids: list[Any] = []
    statuses: list[str] = []
    columns: dict[str, list[float]] = {item["column"]: [] for item in plan}
    unavailable = 0
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0}
    disabled = False
    error_code = None
    row_list = list(rows or [])
    for row in row_list:
        mapping = dict(row) if isinstance(row, Mapping) else {text_field: row}
        row_id = mapping.get(id_field)
        text = mapping.get(text_field, "")
        ids.append(row_id)
        judged = _judge(
            TEMPLATE_ID,
            _state_for_row(row_id, text),
            specs=specs,
        )
        status = str(judged.get("status") or "unavailable")
        statuses.append(status)
        _usage_add(usage, judged)
        if judged.get("error_code"):
            error_code = judged.get("error_code")
        if status == "disabled":
            disabled = True
        if status in {"unavailable", "disabled"} or judged.get("error"):
            if status != "disabled":
                unavailable += 1
            for item in plan:
                columns[item["column"]].append(_NAN)
            continue
        for item in plan:
            qid = item["question_id"]
            if item["kind"] == "noul":
                value = _noul_p(judged, qid)
                columns[item["column"]].append(_NAN if value is None else value)
                continue
            pair = _score_columns(judged, qid, int(item["n_levels"]))
            if pair is None:
                columns[item["column"]].append(_NAN)
                continue
            mean_norm, sd_norm = pair
            columns[item["column"]].append(
                mean_norm if item["role"] == "mean" else sd_norm
            )
    table: dict[str, Any] = {
        "status": "disabled" if disabled else "ok",
        "ids": ids,
        "id_field": id_field,
        "row_status": statuses,
        "columns": columns,
        "unavailable_count": unavailable,
        "questions": specs,
        "feature_sources": plan,
        "template_id": TEMPLATE_ID,
        "template_version": TEMPLATE_VERSION,
        "usage": usage,
        "error_code": error_code,
    }
    frame = _frame_from_table(table, id_field)
    if frame is not None:
        table["frame"] = frame
    return table


def _frame_from_table(table: Mapping[str, Any], id_field: str):
    pd = _try_pandas()
    if pd is None:
        return None
    data: dict[str, Any] = {
        id_field: list(table["ids"]),
        "status": list(table["row_status"]),
    }
    for name, values in table["columns"].items():
        data[name] = list(values)
    return pd.DataFrame(data)


def _as_rows(df: Any) -> list[dict[str, Any]]:
    pd = _try_pandas()
    if pd is not None and isinstance(df, pd.DataFrame):
        return df.to_dict(orient="records")
    if isinstance(df, Mapping):
        keys = list(df)
        length = len(next(iter(df.values()))) if df else 0
        return [{key: df[key][index] for key in keys} for index in range(length)]
    return [dict(row) if isinstance(row, Mapping) else {"text": row} for row in df]


def _column_values(rows: Sequence[Mapping[str, Any]], name: str) -> list[Any]:
    return [row.get(name) for row in rows]


def _split_indices(
    rows: Sequence[Mapping[str, Any]],
    *,
    split_by: str | None,
    time_col: str | None,
    seed: int,
) -> dict[str, list[int]]:
    plan = _load_sibling("plan-ml-experiment")
    size = len(rows)
    if time_col:
        return plan.chronological_split(
            _column_values(rows, time_col), fractions=DEV_TEST_FRACTIONS
        )
    if split_by:
        return plan.grouped_split(
            _column_values(rows, split_by),
            fractions=DEV_TEST_FRACTIONS,
            seed=seed,
        )
    return plan.random_split(size, fractions=DEV_TEST_FRACTIONS, seed=seed)


def _is_binary(values: Sequence[Any]) -> bool:
    present = {value for value in values if not _is_nan(value)}
    if len(present) != 2:
        return False
    coerced: set[Any] = set()
    for value in present:
        if isinstance(value, bool):
            coerced.add(int(value))
            continue
        if isinstance(value, (int, float)) and float(value) in (0.0, 1.0):
            coerced.add(int(value))
            continue
        coerced.add(value)
    return coerced <= {0, 1} or len(coerced) == 2


def _finite_pairs(
    y: Sequence[Any], pred: Sequence[Any]
) -> tuple[list[float], list[float]]:
    truth: list[float] = []
    guess: list[float] = []
    for actual, estimate in zip(y, pred):
        if _is_nan(actual) or _is_nan(estimate):
            continue
        try:
            truth.append(float(actual))
            guess.append(float(estimate))
        except (TypeError, ValueError):
            continue
    return truth, guess


def _matrix(
    table: Mapping[str, Any], indices: Sequence[int]
) -> tuple[list[list[float]], list[int]]:
    names = list(table["columns"])
    kept: list[int] = []
    matrix: list[list[float]] = []
    for index in indices:
        row = []
        skip = False
        for name in names:
            value = table["columns"][name][index]
            if _is_nan(value):
                skip = True
                break
            row.append(float(value))
        if skip or not row:
            continue
        kept.append(index)
        matrix.append(row)
    return matrix, kept


def _mean(values: Sequence[float]) -> float:
    return sum(values) / float(len(values)) if values else 0.0


def _predict_constant(value: float, n: int) -> list[float]:
    return [value] * n


def _fit_linear(
    train_x: Sequence[Sequence[float]],
    train_y: Sequence[float],
    test_x: Sequence[Sequence[float]],
    *,
    classification: bool,
) -> list[float]:
    sklearn = _try_sklearn_linear()
    np = _try_numpy()
    if sklearn is not None and np is not None:
        LinearRegression, LogisticRegression = sklearn[0], sklearn[1]
        x_train = np.asarray(train_x, dtype=float)
        y_train = np.asarray(train_y, dtype=float)
        x_test = np.asarray(test_x, dtype=float)
        if classification:
            model = LogisticRegression(max_iter=200, solver="liblinear")
            model.fit(x_train, y_train.astype(int))
            if hasattr(model, "predict_proba"):
                return [float(p[1]) for p in model.predict_proba(x_test)]
            return [float(v) for v in model.predict(x_test)]
        model = LinearRegression()
        model.fit(x_train, y_train)
        return [float(v) for v in model.predict(x_test)]
    if not train_x or not test_x:
        return _predict_constant(_mean(train_y), len(test_x))
    # Least-squares with a bias column; falls back to the mean if singular.
    n_features = len(train_x[0])
    xtx = [[0.0] * (n_features + 1) for _ in range(n_features + 1)]
    xty = [0.0] * (n_features + 1)
    for row, target in zip(train_x, train_y):
        extended = [1.0, *row]
        for i, left in enumerate(extended):
            xty[i] += left * target
            for j, right in enumerate(extended):
                xtx[i][j] += left * right
    try:
        weights = _solve(xtx, xty)
    except ValueError:
        return _predict_constant(_mean(train_y), len(test_x))
    out = []
    for row in test_x:
        value = weights[0] + sum(w * x for w, x in zip(weights[1:], row))
        if classification:
            value = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, value))))
        out.append(float(value))
    return out


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    aug = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-12:
            raise ValueError("singular")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        scale = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= scale
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            for j in range(col, n + 1):
                aug[row][j] -= factor * aug[col][j]
    return [aug[i][n] for i in range(n)]


def _cross_validate(
    table: Mapping[str, Any],
    y: Sequence[Any],
    indices: Sequence[int],
    *,
    classification: bool,
) -> tuple[list[float], dict[str, Any]]:
    matrix, kept = _matrix(table, indices)
    loc = {index: pos for pos, index in enumerate(kept)}
    oof = [_NAN] * len(indices)
    if len(kept) < 4 or not matrix:
        return oof, {"n": 0, "metric": None}
    sklearn = _try_sklearn_linear()
    np = _try_numpy()
    folds: list[list[int]]
    if sklearn is not None and np is not None:
        _lin, _log, KFold = sklearn
        splitter = KFold(
            n_splits=min(CV_FOLDS, len(kept)), shuffle=True, random_state=SPLIT_SEED
        )
        folds = [list(test) for _train, test in splitter.split(np.arange(len(kept)))]
    else:
        k = min(CV_FOLDS, len(kept))
        folds = [list(range(i, len(kept), k)) for i in range(k)]
    preds = [_NAN] * len(kept)
    targets = [float(y[index]) for index in kept]
    for fold in folds:
        train = [i for i in range(len(kept)) if i not in set(fold)]
        if not train or not fold:
            continue
        guess = _fit_linear(
            [matrix[i] for i in train],
            [targets[i] for i in train],
            [matrix[i] for i in fold],
            classification=classification,
        )
        for local, value in zip(fold, guess):
            preds[local] = value
    for pos, index in enumerate(indices):
        if index in loc:
            oof[pos] = preds[loc[index]]
    return oof, _score_predictions(
        [y[i] for i in indices], oof, classification=classification
    )


def _score_predictions(
    y_true: Sequence[Any], predictions: Sequence[Any], *, classification: bool
) -> dict[str, Any]:
    metrics_mod = _load_sibling("evaluate-model")
    truth, guess = _finite_pairs(y_true, predictions)
    if len(truth) < 2:
        return {"n": len(truth), "metric": None, "primary": None}
    if classification:
        labels = [1 if value >= 0.5 else 0 for value in truth]
        scored = metrics_mod.binary_classification_metrics(
            labels, scores=guess, threshold=0.5, positive=1
        )
        return {
            "n": scored["n"],
            "task": "classification",
            "primary": "roc_auc",
            "metric": scored.get("roc_auc"),
            "details": scored,
        }
    scored = metrics_mod.regression_metrics(truth, guess)
    return {
        "n": scored["n"],
        "task": "regression",
        "primary": "rmse",
        "metric": scored.get("rmse"),
        "details": scored,
    }


def _baseline_predictions(
    y_dev: Sequence[Any], n_test: int, *, classification: bool
) -> list[float]:
    finite = [float(value) for value in y_dev if not _is_nan(value)]
    if classification:
        rate = _mean(finite) if finite else 0.5
        return _predict_constant(rate, n_test)
    return _predict_constant(_mean(finite) if finite else 0.0, n_test)


def _lift(
    y_true: Sequence[Any],
    baseline: Sequence[Any],
    model: Sequence[Any],
    *,
    classification: bool,
) -> dict[str, Any]:
    metrics_mod = _load_sibling("evaluate-model")
    base = _score_predictions(y_true, baseline, classification=classification)
    feat = _score_predictions(y_true, model, classification=classification)
    truth, base_hat = _finite_pairs(y_true, baseline)
    _, model_hat = _finite_pairs(y_true, model)
    higher_is_better = bool(classification)
    if base["metric"] is None or feat["metric"] is None or len(truth) < 2:
        return {
            "baseline": base,
            "model": feat,
            "lift": None,
            "higher_is_better": higher_is_better,
            "bootstrap": None,
        }
    if classification:
        lift = float(feat["metric"]) - float(base["metric"])
    else:
        lift = float(base["metric"]) - float(feat["metric"])

    def statistic(indices: Sequence[float]) -> float:
        picked = [int(i) for i in indices]
        y_s = [truth[i] for i in picked]
        b_s = [base_hat[i] for i in picked]
        m_s = [model_hat[i] for i in picked]
        b_metric = _score_predictions(y_s, b_s, classification=classification)["metric"]
        m_metric = _score_predictions(y_s, m_s, classification=classification)["metric"]
        if b_metric is None or m_metric is None:
            return 0.0
        if classification:
            return float(m_metric) - float(b_metric)
        return float(b_metric) - float(m_metric)

    interval = metrics_mod.bootstrap_ci(
        [float(i) for i in range(len(truth))],
        statistic=statistic,
        resamples=BOOTSTRAP_RESAMPLES,
        seed=BOOTSTRAP_SEED,
    )
    return {
        "baseline": base,
        "model": feat,
        "lift": lift,
        "higher_is_better": higher_is_better,
        "bootstrap": interval,
    }


def _question_set_version(questions: Sequence[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        {
            "template_id": TEMPLATE_ID,
            "template_version": TEMPLATE_VERSION,
            "questions": list(questions),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _spread(values: Sequence[float]) -> float:
    finite = [float(v) for v in values if not _is_nan(v)]
    if len(finite) < 2:
        return 0.0
    mean = _mean(finite)
    var = sum((v - mean) ** 2 for v in finite) / float(len(finite))
    return math.sqrt(var)


def _select_examples(
    rows: Sequence[Mapping[str, Any]],
    indices: Sequence[int],
    y: Sequence[Any],
    oof: Sequence[float] | None,
    *,
    n: int,
    text_field: str,
    id_field: str,
) -> list[dict[str, Any]]:
    if not indices:
        return []
    count = min(n, len(indices))
    if oof is None:
        order = list(indices)
        step = max(1, len(order) // count)
        picked = order[::step][:count]
    else:
        errors = []
        for pos, index in enumerate(indices):
            if pos >= len(oof) or _is_nan(oof[pos]) or _is_nan(y[index]):
                continue
            errors.append((abs(float(y[index]) - float(oof[pos])), index, pos))
        errors.sort(reverse=True)
        worst = [item[1] for item in errors[: count // 2]]
        best = [item[1] for item in errors[-(count - len(worst)) :]] if errors else []
        picked = worst + best
    out = []
    for index in picked:
        row = rows[index]
        item = {
            "id": row.get(id_field),
            "text": row.get(text_field),
            "target": y[index],
        }
        out.append(item)
    return out


def run_feature_study(
    df: Any,
    target: str,
    *,
    split_by: str | None = None,
    time_col: str | None = None,
    rounds: int = 3,
    text_field: str = "text",
    id_field: str = "id",
    n_questions: int = DEFAULT_N,
    task_description: str | None = None,
) -> dict[str, Any]:
    """Propose, featurize, and evaluate on a frozen test split used once."""

    rows = _as_rows(df)
    if not rows:
        return _disabled_payload(error="empty dataset")
    audit = _load_sibling("audit-dataset").audit_rows(
        rows,
        target=target,
        id_columns=(id_field,) if id_field else (),
        group_columns=(split_by,) if split_by else (),
    )
    y = _column_values(rows, target)
    classification = _is_binary(y)
    splits = _split_indices(rows, split_by=split_by, time_col=time_col, seed=SPLIT_SEED)
    dev_idx = list(splits.get("train") or []) + list(splits.get("validation") or [])
    test_idx = list(splits.get("test") or [])
    judged_ids: list[Any] = []
    probe = _judge(
        TEMPLATE_ID,
        _state_for_row("capability-probe", "probe"),
        specs=[
            {
                "id": "probe_noul",
                "kind": "noul",
                "instructions": "Is state.text a non-empty string?",
            }
        ],
    )
    if str(probe.get("status") or "") == "disabled":
        return _disabled_payload(audit=audit, split={"dev": dev_idx, "test": test_idx})

    description = task_description or (
        "Predict the supervised target from free-text rows. "
        f"Target column: {target}."
    )
    accepted: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "requests": 0}
    oof: list[float] | None = None
    feedback = ""
    last_table: dict[str, Any] | None = None
    n_rounds = max(1, int(rounds))
    for round_index in range(1, n_rounds + 1):
        examples = _select_examples(
            rows,
            dev_idx,
            y,
            oof,
            n=min(8, len(dev_idx)),
            text_field=text_field,
            id_field=id_field,
        )
        proposed = propose_questions(
            description,
            examples,
            n=n_questions,
            previous=accepted,
            feedback=feedback or None,
        )
        trial = proposed or accepted
        if not trial:
            history.append({"round": round_index, "n_questions": 0, "cv": None})
            continue
        dev_rows = [rows[i] for i in dev_idx]
        table = featurize(dev_rows, trial, text_field=text_field, id_field=id_field)
        judged_ids.extend(table.get("ids") or [])
        _usage_add(usage, table.get("usage") or {})
        if table.get("status") == "disabled":
            return _disabled_payload(
                audit=audit, split={"dev": dev_idx, "test": test_idx}
            )
        # Drop flat features on the development rows only.
        kept = []
        for spec in trial:
            col = table["columns"].get(spec["id"])
            if col is None:
                kept.append(spec)
                continue
            if _spread(col) < FLAT_SPREAD:
                continue
            kept.append(spec)
        trial = kept or trial
        last_table = table
        oof_map, cv = _cross_validate(
            table,
            [y[i] for i in dev_idx],
            list(range(len(dev_idx))),
            classification=classification,
        )
        oof = oof_map
        accepted = trial
        history.append(
            {
                "round": round_index,
                "n_questions": len(accepted),
                "cv": cv,
                "question_ids": [item["id"] for item in accepted],
            }
        )
        feedback = (
            f"round {round_index} cv {cv.get('primary')}={cv.get('metric')!r} "
            f"on {cv.get('n')} development rows"
        )

    frozen = True
    test_rows = [rows[i] for i in test_idx]
    test_table = None
    test_pred: list[float] = []
    baseline: list[float] = []
    lift: dict[str, Any] | None = None
    if accepted and test_idx:
        # Test rows are judged only after the question set is frozen.
        test_table = featurize(
            test_rows, accepted, text_field=text_field, id_field=id_field
        )
        judged_ids.extend(test_table.get("ids") or [])
        _usage_add(usage, test_table.get("usage") or {})
        dev_table = last_table or featurize(
            [rows[i] for i in dev_idx],
            accepted,
            text_field=text_field,
            id_field=id_field,
        )
        train_x, train_kept = _matrix(dev_table, list(range(len(dev_idx))))
        test_x, test_kept = _matrix(test_table, list(range(len(test_idx))))
        train_y = [float(y[dev_idx[i]]) for i in train_kept]
        if train_x and test_x:
            fitted = _fit_linear(
                train_x, train_y, test_x, classification=classification
            )
            pred_map = {test_kept[i]: fitted[i] for i in range(len(test_kept))}
            test_pred = [pred_map.get(i, _NAN) for i in range(len(test_idx))]
        else:
            test_pred = [_NAN] * len(test_idx)
        baseline = _baseline_predictions(
            [y[i] for i in dev_idx], len(test_idx), classification=classification
        )
        lift = _lift(
            [y[i] for i in test_idx],
            baseline,
            test_pred,
            classification=classification,
        )

    return {
        "status": "ok",
        "frozen": frozen,
        "audit": audit,
        "split": {
            "dev": [rows[i].get(id_field) for i in dev_idx],
            "test": [rows[i].get(id_field) for i in test_idx],
            "dev_indices": dev_idx,
            "test_indices": test_idx,
        },
        "questions": accepted,
        "question_set_version": _question_set_version(accepted),
        "feature_sources": _column_plan(accepted),
        "history": history,
        "test": {
            "table": test_table,
            "predictions": test_pred,
            "baseline": baseline,
        },
        "metrics": lift,
        "usage": usage,
        "judged_ids": judged_ids,
        "classification": classification,
        "template_id": TEMPLATE_ID,
        "template_version": TEMPLATE_VERSION,
    }


__all__ = [
    "ALLOWED_KINDS",
    "MAX_QUESTION_CHARS",
    "MAX_QUESTIONS",
    "MAX_SCORE_LEVELS",
    "MAX_STATE_CHARS",
    "TEMPLATE_ID",
    "TEMPLATE_VERSION",
    "featurize",
    "propose_questions",
    "run_feature_study",
    "tf_sdk",
]
