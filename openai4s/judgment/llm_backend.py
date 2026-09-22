"""Optional LLM JudgmentBackend. Explicit ``provider=llm`` only.

The configured main model answers the same typed questions that TypeSafe Jev
would. Results are uncalibrated. This backend is never selected automatically
when TypeSafe fails.
"""

from __future__ import annotations

import dataclasses
import json
import math
from typing import Any, Callable, Mapping

from openai4s.config import Config, LLMConfig
from openai4s.judgment.port import BackendError
from openai4s.judgment.types import BackendReply, Choice, Noul, Question, Score
from openai4s.judgment.validate import parse_response
from openai4s.llm.models import (
    LLMDeadlineExceeded,
    MissingCredentialError,
    StreamTimeoutError,
)
from openai4s.llm.usage import charge_call

SYSTEM_PROMPT = """\
You are a typed judgment engine. Read the state and answer every question.
Return one JSON object and nothing else: no markdown, no commentary, no
trailing text.

The object must be:
{"answers": {"<id>": <answer>, ...}}

Answer shapes:
- Noul: {"noul": <P(yes) in [0, 1]>}
- Choice: {"probabilities": {<option>: <p>, ...}}
  Every requested option must appear. Values in [0, 1], sum to 1.
- Score: {"probabilities": {"0": <p>, "1": <p>, ...}}
  One key per level index as a string. Values in [0, 1], sum to 1.

Question ids in the user message are the keys of "answers".
"""

_DROP_KEYS = frozenset(
    {"type", "choice", "confidence", "noul", "score", "legend", "value"}
)


def distribution_confidence(probabilities: Mapping[str, Any], n: int) -> float:
    """TypeSafe Confidence-explorer formula: ``(n * p_max - 1) / (n - 1)``.

    The public Confidence page does not publish a production closed form. Its
    interactive explorer uses this linear peak statistic (for three options,
    ``(3 * p_max - 1) / 2``) and labels it an approximation of how spread the
    distribution is. Uniform ``p_max = 1/n`` yields 0; a single peak of 1
    yields 1. Noul has no confidence.
    """

    if n <= 1:
        return 1.0
    peak = 0.0
    for raw in probabilities.values():
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(raw, bool):
            continue
        if value > peak:
            peak = value
    return max(0.0, min(1.0, (n * peak - 1.0) / (n - 1.0)))


class LlmBackend:
    """``JudgmentBackend`` that asks the configured main model via ``chat()``."""

    def __init__(
        self,
        cfg_provider: Config | Callable[[], Config],
        *,
        usage_sink: Callable[[Any], None] | None = None,
        chat_call: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.cfg_provider = cfg_provider
        self.usage_sink = usage_sink
        self.chat_call = chat_call

    def _config(self) -> Config:
        provider = self.cfg_provider
        return provider() if callable(provider) else provider

    def _chat(
        self,
        messages: list[dict[str, Any]],
        llm_cfg: LLMConfig,
        **options: Any,
    ) -> dict[str, Any]:
        if self.chat_call is not None:
            return self.chat_call(messages, llm_cfg, **options)
        from openai4s.llm import chat

        return chat(messages, llm_cfg, **options)

    def evaluate(
        self,
        *,
        state: object,
        questions: Mapping[str, Question],
        model: str,
        timeout: float,
    ) -> BackendReply:
        del model
        if not isinstance(questions, Mapping) or not questions:
            raise BackendError(
                "invalid_request", "questions must be a non-empty mapping"
            )
        budget = _timeout_budget(timeout)
        llm_cfg = _llm_cfg_for_call(self._config().llm, budget)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _render_user(state, questions)},
        ]
        try:
            reply = self._chat(
                messages,
                llm_cfg,
                max_tokens=llm_cfg.max_tokens,
                temperature=0.0,
            )
        except BaseException as error:
            charge_call(self.usage_sink, error)
            if not isinstance(error, Exception):
                raise
            raise _map_chat_error(error) from error
        charge_call(self.usage_sink, reply)
        payload = _payload_from_chat(reply, questions, llm_cfg)
        return parse_response(payload, questions)


def _timeout_budget(timeout: object) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise BackendError("invalid_request", "timeout must be a finite number")
    budget = float(timeout)
    if not math.isfinite(budget) or budget <= 0:
        raise BackendError("timeout", "llm judgment request timed out")
    return budget


def _llm_cfg_for_call(llm_cfg: LLMConfig, timeout: float) -> LLMConfig:
    """Honor ``evaluate(..., timeout=)`` without changing ``chat()``."""

    idle = timeout
    total = min(3600.0, max(1.0, idle))
    return dataclasses.replace(llm_cfg, timeout_s=idle, total_timeout_s=total)


def _render_user(state: object, questions: Mapping[str, Question]) -> str:
    payload = {
        "state": state,
        "questions": {qid: question.to_api() for qid, question in questions.items()},
    }
    return json.dumps(payload, ensure_ascii=False, default=str, indent=2)


def _payload_from_chat(
    reply: Mapping[str, Any],
    questions: Mapping[str, Question],
    llm_cfg: LLMConfig,
) -> dict[str, Any]:
    raw_answers = _parse_answers_object(reply.get("content"))
    adapted: dict[str, Any] = {}
    for qid, question in questions.items():
        if qid not in raw_answers:
            raise BackendError(
                "invalid_response", "answers must match the requested question ids"
            )
        adapted[qid] = _adapt_answer(raw_answers[qid], question)
    extra = set(raw_answers) - set(questions)
    if extra:
        raise BackendError(
            "invalid_response", "answers must match the requested question ids"
        )
    model_id = _reply_model(reply, llm_cfg)
    return {
        "model": model_id,
        "usage": _usage_counters(reply.get("usage")),
        "answers": adapted,
    }


def _parse_answers_object(content: object) -> dict[str, Any]:
    parsed = _load_json_object(content)
    answers = parsed.get("answers")
    if isinstance(answers, dict):
        return answers
    if all(key in ("answers", "model", "usage") for key in parsed):
        raise BackendError("invalid_response", "answers must be an object")
    return parsed


def _load_json_object(content: object) -> dict[str, Any]:
    if isinstance(content, dict):
        return dict(content)
    if not isinstance(content, str):
        raise BackendError("invalid_response", "response is not JSON")
    text = _strip_fence(content.strip())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BackendError("invalid_response", "response is not JSON") from exc
    if not isinstance(parsed, dict):
        raise BackendError("invalid_response", "response must be an object")
    return parsed


def _strip_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _adapt_answer(raw: object, question: Question) -> dict[str, Any]:
    if isinstance(question, Noul):
        return {"type": "noul", "noul": _noul_value(raw)}
    if isinstance(question, Choice):
        return _adapt_choice(raw, question)
    if isinstance(question, Score):
        return _adapt_score(raw, question)
    raise BackendError("invalid_response", "unknown question type")


def _noul_value(raw: object) -> object:
    if isinstance(raw, Mapping):
        if "noul" in raw:
            return raw["noul"]
        if "value" in raw:
            return raw["value"]
        raise BackendError("invalid_response", "noul answer is missing noul")
    return raw


def _adapt_choice(raw: object, question: Choice) -> dict[str, Any]:
    probs = _probability_mapping(raw)
    names = list(question.options)
    selected = _argmax(probs, names)
    return {
        "type": "choice",
        "choice": selected,
        "probabilities": probs,
        "confidence": distribution_confidence(probs, len(names)),
    }


def _adapt_score(raw: object, question: Score) -> dict[str, Any]:
    n_levels = len(question.levels)
    probs = _score_probabilities(raw, question)
    weighted = 0.0
    for index in range(n_levels):
        key = str(index)
        if key not in probs:
            # parse_response will reject a missing key; keep the payload
            # intact rather than inventing mass.
            continue
        try:
            weighted += index * float(probs[key])
        except (TypeError, ValueError):
            pass
    legend = {str(index): level for index, level in enumerate(question.levels)}
    return {
        "type": "score",
        "score": weighted,
        "probabilities": probs,
        "legend": legend,
        "confidence": distribution_confidence(probs, n_levels),
    }


def _probability_mapping(raw: object) -> dict[str, Any]:
    if isinstance(raw, Mapping) and isinstance(raw.get("probabilities"), Mapping):
        return {str(key): value for key, value in raw["probabilities"].items()}
    if isinstance(raw, Mapping) and isinstance(raw.get("probabilities"), list):
        values = raw["probabilities"]
        return {str(index): values[index] for index in range(len(values))}
    if isinstance(raw, Mapping):
        return {str(key): value for key, value in raw.items() if key not in _DROP_KEYS}
    if isinstance(raw, list):
        return {str(index): raw[index] for index in range(len(raw))}
    raise BackendError("invalid_response", "probabilities must be an object")


def _score_probabilities(raw: object, question: Score) -> dict[str, Any]:
    probs = _probability_mapping(raw)
    levels = list(question.levels)
    if set(probs) == set(levels):
        return {str(index): probs[level] for index, level in enumerate(levels)}
    return {str(key): value for key, value in probs.items()}


def _argmax(probabilities: Mapping[str, Any], order: list[str]) -> str:
    best_name = ""
    best_value = None
    for name in order:
        if name not in probabilities:
            continue
        try:
            value = float(probabilities[name])
        except (TypeError, ValueError):
            continue
        if isinstance(probabilities[name], bool):
            continue
        if best_value is None or value > best_value:
            best_name = name
            best_value = value
    if best_name:
        return best_name
    for name, raw in probabilities.items():
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(raw, bool):
            continue
        if best_value is None or value > best_value:
            best_name = str(name)
            best_value = value
    if not best_name:
        raise BackendError("invalid_response", "choice has no probabilities")
    return best_name


def _reply_model(reply: Mapping[str, Any], llm_cfg: LLMConfig) -> str:
    model = reply.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    configured = getattr(llm_cfg, "model", "") or ""
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    raise BackendError("invalid_response", "model must be a non-empty string")


def _usage_counters(raw: object) -> dict[str, int]:
    if not isinstance(raw, Mapping):
        return {"input_tokens": 0, "output_tokens": 0}

    def _nonneg(primary: str, alias: str) -> int:
        for key in (primary, alias):
            value = raw.get(key)
            if type(value) is int and value >= 0:
                return value
        return 0

    return {
        "input_tokens": _nonneg("input_tokens", "prompt_tokens"),
        "output_tokens": _nonneg("output_tokens", "completion_tokens"),
    }


def _map_chat_error(error: BaseException) -> BackendError:
    if isinstance(error, BackendError):
        return error
    if isinstance(error, MissingCredentialError):
        return BackendError("unconfigured", "llm backend is unconfigured")
    if isinstance(error, (LLMDeadlineExceeded, StreamTimeoutError)):
        return BackendError("timeout", "llm judgment request timed out")
    status = getattr(error, "status", None)
    if status in (401, 403):
        return BackendError("auth", "llm backend authentication failed")
    if status == 429:
        return BackendError("rate_limited", "llm backend rate limited")
    if status in (503, 529):
        return BackendError("overloaded", "llm backend overloaded")
    name = type(error).__name__.lower()
    if "timeout" in name:
        return BackendError("timeout", "llm judgment request timed out")
    message = str(error) or type(error).__name__
    return BackendError("unavailable", message)


__all__ = ["LlmBackend", "SYSTEM_PROMPT", "distribution_confidence"]
