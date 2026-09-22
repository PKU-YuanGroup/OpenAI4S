"""Host-side experimental judgment service.

Kernel cells reach this through ``host.judge`` / ``HostDispatcher._m_judge``.
Control-plane callers (W1-C connection test, later Skill/literature hooks)
construct or reuse a ``JudgmentService`` and call ``run`` / ``probe`` directly.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Callable, Literal, Mapping, Sequence

from openai4s.config import Config
from openai4s.judgment.disclosure import CAPABILITIES
from openai4s.judgment.flags import EffectiveJudgmentFlags, resolve
from openai4s.judgment.port import BackendError, JudgmentBackend, NullBackend
from openai4s.judgment.registry import (
    PROBE_STATE,
    PROBE_TEMPLATE_ID,
    CustomQuestionSpec,
    Template,
    get_template,
)
from openai4s.judgment.types import (
    Answer,
    BackendReply,
    Choice,
    JudgmentResult,
    Noul,
    Question,
    Score,
)

STATE_TOKEN_BUDGET = 32_768
CACHE_SIZE = 512
MAX_CONCURRENCY = 4
CHARS_PER_TOKEN = 4
SETTING_AUDIT_RAW_STATE = "experimental.judgment.audit_raw_state"

_TRUE_SETTINGS = frozenset(("1", "true", "yes", "on"))


@dataclass(frozen=True)
class JudgmentServiceResult(JudgmentResult):
    """JudgmentResult plus the service-layer truncation mark.

    W0's ``JudgmentResult`` has no ``truncated`` field. Adding it here keeps
    ``types.py`` untouched (W1-A owns a surgical edit there) while still
    surfacing the mark on ``to_dict()``.
    """

    truncated: bool = False
    fake: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["truncated"] = bool(self.truncated)
        payload["fake"] = bool(self.fake)
        return payload


class _LRU:
    """Process-local LRU. Capacity is bounded; not shared across processes."""

    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, int(capacity))
        self._data: OrderedDict[tuple[str, ...], JudgmentServiceResult] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[str, ...]) -> JudgmentServiceResult | None:
        with self._lock:
            value = self._data.get(key)
            if value is None:
                return None
            self._data.move_to_end(key)
            return value

    def put(self, key: tuple[str, ...], value: JudgmentServiceResult) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self.capacity:
                self._data.popitem(last=False)


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":")
    )


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _estimate_tokens(value: object) -> int:
    return len(_canonical(value)) // CHARS_PER_TOKEN


def _truncate_mapping(data: dict[str, Any], max_chars: int) -> dict[str, Any]:
    out = dict(data)
    while len(_canonical(out)) > max_chars:
        longest_key = None
        longest_len = -1
        for key, item in out.items():
            if isinstance(item, str) and len(item) > longest_len:
                longest_key = key
                longest_len = len(item)
        if longest_key is None or longest_len <= 0:
            encoded = _canonical(out)
            return {"_truncated": encoded[:max_chars]}
        overflow = len(_canonical(out)) - max_chars
        new_len = max(0, longest_len - overflow - 16)
        out[longest_key] = out[longest_key][:new_len]
    return out


def _truncate_state(state: object) -> tuple[object, bool]:
    max_chars = STATE_TOKEN_BUDGET * CHARS_PER_TOKEN
    if len(_canonical(state)) <= max_chars:
        return state, False
    if isinstance(state, str):
        return state[:max_chars], True
    if isinstance(state, Mapping):
        return _truncate_mapping(dict(state), max_chars), True
    return _canonical(state)[:max_chars], True


def _scope_id(scope: object) -> str:
    if scope is None:
        return ""
    if isinstance(scope, str):
        return scope
    return _digest(scope)


def _candidate_hash(params: Mapping[str, Any]) -> str:
    if "candidate_versions" in params:
        return _digest(params["candidate_versions"])
    return _digest(params)


def _cache_key(
    *,
    provider: str,
    model: str,
    template: Template,
    state_sha256: str,
    params: Mapping[str, Any],
    scope: object,
    backend_identity: str = "",
) -> tuple[str, ...]:
    return (
        str(provider),
        str(model),
        template.template_id,
        template.version,
        template.policy_version,
        state_sha256,
        _candidate_hash(params),
        _scope_id(scope),
        backend_identity,
    )


def _purpose_enabled(
    flags: EffectiveJudgmentFlags, purpose: str, *, ignore_capability: bool = False
) -> bool:
    if ignore_capability or purpose == "probe" or purpose not in CAPABILITIES:
        return bool(flags.master.enabled)
    resolved = getattr(flags, purpose)
    return bool(resolved.enabled)


def _question_kind(question: Question) -> str:
    if isinstance(question, Noul):
        return "noul"
    if isinstance(question, Choice):
        return "choice"
    if isinstance(question, Score):
        return "score"
    raise ValueError(f"unsupported question type: {type(question)!r}")


def _enforce_custom_limits(
    template: Template,
    questions: Mapping[str, Question],
    state: object,
    params: Mapping[str, Any],
) -> None:
    limits: CustomQuestionSpec | None = template.allow_custom
    if "questions" in params and limits is None:
        raise ValueError(
            f"template {template.template_id!r} does not allow custom questions"
        )
    if limits is None:
        return
    if len(questions) > limits.max_questions:
        raise ValueError(
            f"template {template.template_id!r} allows at most "
            f"{limits.max_questions} questions"
        )
    if len(_canonical(state)) > limits.max_state_chars:
        raise ValueError(
            f"template {template.template_id!r} state exceeds "
            f"{limits.max_state_chars} characters"
        )
    for question in questions.values():
        kind = _question_kind(question)
        if kind not in limits.kinds:
            raise ValueError(
                f"template {template.template_id!r} does not allow {kind} questions"
            )
        if len(question.instructions) > limits.max_question_chars:
            raise ValueError(
                f"template {template.template_id!r} question exceeds "
                f"{limits.max_question_chars} characters"
            )


def _coerce_answer(value: object) -> Answer:
    if isinstance(value, Answer):
        return value
    if not isinstance(value, Mapping):
        raise BackendError("invalid_response", "answer is not an object")
    kind = value.get("kind") or value.get("type")
    if kind not in ("noul", "choice", "score"):
        raise BackendError("invalid_response", "answer kind is missing")
    if "value" in value:
        raw_value: object = value["value"]
    elif kind == "noul":
        raw_value = value.get("noul")
    elif kind == "score":
        raw_value = value.get("score")
    else:
        raw_value = value.get("choice")
    try:
        return Answer(
            kind=kind,
            value=raw_value,  # type: ignore[arg-type]
            probabilities=value.get("probabilities"),
            confidence=value.get("confidence"),
        )
    except (TypeError, ValueError) as exc:
        raise BackendError("invalid_response", str(exc) or "invalid answer") from exc


def _coerce_answers(
    raw: Mapping[str, Any], questions: Mapping[str, Question]
) -> dict[str, Answer]:
    if set(raw) != set(questions):
        raise BackendError(
            "invalid_response", "backend answers do not match the requested questions"
        )
    return {key: _coerce_answer(raw[key]) for key in questions}


def _probability_map(answers: Mapping[str, Answer]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, answer in answers.items():
        if answer.probabilities is not None:
            out[key] = dict(answer.probabilities)
        elif answer.kind == "noul":
            try:
                yes = float(answer.value)
            except (TypeError, ValueError):
                yes = 0.0
            out[key] = {"true": yes, "false": 1.0 - yes}
        else:
            out[key] = {"value": answer.value}
    return out


def _store_setting_true(store: Any, key: str) -> bool:
    if store is None or not hasattr(store, "get_setting"):
        return False
    try:
        raw = store.get_setting(key)
    except Exception:  # noqa: BLE001 - audit flag must not fail the call
        return False
    if raw is None:
        return False
    return str(raw).strip().lower() in _TRUE_SETTINGS


@dataclass(frozen=True)
class _Prepared:
    purpose: str
    template: Template
    flags: EffectiveJudgmentFlags
    state: object
    truncated: bool
    params: dict[str, Any]
    questions: Mapping[str, Question]
    cache_key: tuple[str, ...]
    state_sha256: str
    disabled: bool


class JudgmentService:
    """Flag-gated, cached, metered judgment runner."""

    def __init__(
        self,
        cfg_provider: Config | Callable[[], Config],
        store_provider: Callable[[], Any] | None,
        backend_factory: Callable[[], JudgmentBackend] | None = None,
        usage_sink: Callable[[Any], None] | None = None,
        *,
        quota_gate: Callable[..., Any] | None = None,
        audit_sink: Callable[[Mapping[str, Any]], None] | None = None,
        cache_size: int = CACHE_SIZE,
        max_concurrency: int = MAX_CONCURRENCY,
        executor_factory: Callable[..., Any] = ThreadPoolExecutor,
    ) -> None:
        self.cfg_provider = cfg_provider
        self.store_provider = store_provider
        self.backend_factory = backend_factory
        self.usage_sink = usage_sink
        self.quota_gate = quota_gate
        self.audit_sink = audit_sink
        self._cache = _LRU(cache_size)
        self._max_concurrency = max(1, int(max_concurrency))
        self.executor_factory = executor_factory

    def _config(self) -> Config:
        provider = self.cfg_provider
        return provider() if callable(provider) else provider

    def _store(self) -> Any:
        provider = self.store_provider
        if provider is None:
            return None
        # A Store instance is as valid as a factory here, exactly as
        # ``_config`` accepts a Config or a callable. The gateway settings
        # surface hands over the request's Store; the dispatcher hands over a
        # lambda. Calling one of them unconditionally is a TypeError, and it
        # is raised inside the route rather than in any package's own tests.
        return provider() if callable(provider) else provider

    def _flags(self) -> EffectiveJudgmentFlags:
        return resolve(self._config(), self._store())

    def _default_backend(self, flags: EffectiveJudgmentFlags) -> JudgmentBackend:
        provider = flags.provider
        if provider == "typesafe":
            try:
                from openai4s.judgment.typesafe import TypeSafeBackend
            except ImportError as exc:
                raise BackendError(
                    "unconfigured", "TypeSafeBackend is not available"
                ) from exc
            return TypeSafeBackend(self._store)
        if provider == "llm":
            try:
                from openai4s.judgment.llm_backend import LlmBackend
            except ImportError as exc:
                raise BackendError(
                    "unconfigured", "llm judgment backend is not available"
                ) from exc
            return LlmBackend(self.cfg_provider, usage_sink=self.usage_sink)
        return NullBackend()

    def _backend(self, flags: EffectiveJudgmentFlags) -> JudgmentBackend:
        factory = self.backend_factory
        if factory is not None:
            return factory()
        return self._default_backend(flags)

    def _admit(
        self, projected_input: float, flags: EffectiveJudgmentFlags
    ) -> Callable[[], None] | None:
        if self.quota_gate is None:
            return None
        release = self.quota_gate(
            projected_input=float(projected_input),
            projected_output=0.0,
            ttl_s=flags.timeout_s,
        )
        return release if callable(release) else None

    def _release(self, release: Callable[[], None] | None) -> None:
        if release is None:
            return
        try:
            release()
        except Exception:  # noqa: BLE001 - a failed release must not fail the call
            pass

    def _meter(self, usage: Any) -> None:
        if self.usage_sink is None:
            return
        try:
            self.usage_sink(usage)
        except Exception:  # noqa: BLE001 - metering never breaks the call
            pass

    def _audit(
        self,
        result: JudgmentResult,
        state: object,
        *,
        truncated: bool,
    ) -> None:
        payload: dict[str, Any] = {
            "purpose": result.purpose,
            "template_id": result.template_id,
            "template_version": result.template_version,
            "policy_version": result.policy_version,
            "status": result.status,
            "error_code": result.error_code,
            "usage": dict(result.usage),
            "latency_ms": result.latency_ms,
            "state_sha256": result.state_sha256,
            "probabilities": _probability_map(result.answers),
            "cache_hit": result.cache_hit,
            "truncated": truncated,
            "fake": bool(getattr(result, "fake", False)),
            "provider": result.provider,
            "model": result.model,
        }
        if _store_setting_true(self._store(), SETTING_AUDIT_RAW_STATE):
            payload["state"] = state
        if self.audit_sink is not None:
            try:
                self.audit_sink(payload)
            except Exception:  # noqa: BLE001 - auditing must not fail the call
                pass
        try:
            from openai4s.observability import log_event

            log_event("judgment", **payload)
        except Exception:  # noqa: BLE001 - auditing must not fail the call
            pass

    def _result(
        self,
        *,
        status: Literal["ok", "uncertain", "unavailable", "disabled"],
        prepared: _Prepared,
        answers: Mapping[str, Answer] | None = None,
        usage: Mapping[str, int] | None = None,
        latency_ms: int = 0,
        cache_hit: bool = False,
        error_code: str | None = None,
        model: str | None = None,
        fake: bool = False,
    ) -> JudgmentServiceResult:
        flags = prepared.flags
        template = prepared.template
        result = JudgmentServiceResult(
            status=status,
            purpose=prepared.purpose,
            template_id=template.template_id,
            template_version=template.version,
            policy_version=template.policy_version,
            provider=flags.provider,
            model=model or flags.model,
            calibrated=flags.provider == "typesafe",
            state_sha256=prepared.state_sha256,
            answers=dict(answers or {}),
            usage=dict(usage or {}),
            latency_ms=int(latency_ms),
            cache_hit=cache_hit,
            error_code=error_code,
            truncated=prepared.truncated,
            fake=fake,
        )
        return result

    def _prepare(
        self,
        *,
        purpose: str,
        template_id: str,
        state: object,
        params: Mapping[str, Any] | None,
        scope: object,
        ignore_capability: bool = False,
    ) -> _Prepared:
        try:
            template = get_template(template_id)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
        resolved_purpose = purpose or template.purpose
        cfg = self._config()
        flags = resolve(cfg, self._store())
        enabled = _purpose_enabled(
            flags, resolved_purpose, ignore_capability=ignore_capability
        )
        raw_params = dict(params or {})
        if not enabled:
            truncated_state, truncated = state, False
            questions: Mapping[str, Question] = {}
        else:
            questions = dict(template.build_questions(raw_params) or {})
            _enforce_custom_limits(template, questions, state, raw_params)
            truncated_state, truncated = _truncate_state(state)
        state_sha256 = _digest(truncated_state)
        return _Prepared(
            purpose=resolved_purpose,
            template=template,
            flags=flags,
            state=truncated_state,
            truncated=truncated,
            params=raw_params,
            questions=questions,
            cache_key=_cache_key(
                provider=flags.provider,
                model=flags.model,
                template=template,
                state_sha256=state_sha256,
                params=raw_params,
                scope=scope,
                backend_identity=(
                    _digest((cfg.llm.provider, cfg.llm.base_url))
                    if flags.provider == "llm"
                    else ""
                ),
            ),
            state_sha256=state_sha256,
            disabled=not enabled,
        )

    def _finish(
        self, result: JudgmentServiceResult, prepared: _Prepared, *, cache: bool
    ) -> JudgmentServiceResult:
        if cache and result.status in ("ok", "uncertain"):
            stored = (
                result if not result.cache_hit else replace(result, cache_hit=False)
            )
            self._cache.put(prepared.cache_key, stored)
        self._audit(result, prepared.state, truncated=prepared.truncated)
        return result

    def _evaluate_backend(
        self, prepared: _Prepared, questions: Mapping[str, Question]
    ) -> tuple[BackendReply, int]:
        flags = prepared.flags
        backend = self._backend(flags)
        projected = _estimate_tokens(prepared.state) + _estimate_tokens(
            {key: question.to_api() for key, question in questions.items()}
        )
        reservation = self._admit(projected, flags)
        started = time.monotonic()
        try:
            reply = backend.evaluate(
                state=prepared.state,
                questions=questions,
                model=flags.model,
                timeout=float(flags.timeout_s),
            )
        finally:
            self._release(reservation)
        latency_ms = max(0, int(round((time.monotonic() - started) * 1000)))
        if not isinstance(reply, BackendReply):
            raise BackendError(
                "invalid_response", "backend did not return BackendReply"
            )
        return reply, latency_ms

    def _from_reply(
        self,
        prepared: _Prepared,
        reply: BackendReply,
        questions: Mapping[str, Question],
        latency_ms: int,
        *,
        meter: bool = True,
    ) -> JudgmentServiceResult:
        answers = _coerce_answers(reply.answers, questions)
        usage = dict(reply.usage)
        if meter:
            self._meter(usage)
        verdict = prepared.template.policy(answers, prepared.params)
        if verdict not in ("ok", "uncertain"):
            raise ValueError(
                f"template policy must return 'ok' or 'uncertain', got {verdict!r}"
            )
        return self._result(
            status=verdict,
            prepared=prepared,
            answers=answers,
            usage=usage,
            latency_ms=latency_ms,
            model=reply.model or prepared.flags.model,
            fake=bool(getattr(reply, "fake", False)),
        )

    def _unavailable(
        self, prepared: _Prepared, exc: BackendError, latency_ms: int = 0
    ) -> JudgmentServiceResult:
        return self._result(
            status="unavailable",
            prepared=prepared,
            latency_ms=latency_ms,
            error_code=exc.code,
        )

    def _execute_one(self, prepared: _Prepared) -> JudgmentServiceResult:
        if prepared.disabled:
            return self._finish(
                self._result(status="disabled", prepared=prepared),
                prepared,
                cache=False,
            )
        cached = self._cache.get(prepared.cache_key)
        if cached is not None:
            hit = replace(cached, cache_hit=True)
            return self._finish(hit, prepared, cache=False)
        try:
            reply, latency_ms = self._evaluate_backend(prepared, prepared.questions)
            result = self._from_reply(prepared, reply, prepared.questions, latency_ms)
        except BackendError as exc:
            result = self._unavailable(prepared, exc)
        except Exception as exc:  # noqa: BLE001 - never invent answers
            result = self._unavailable(
                prepared, BackendError("unavailable", str(exc) or type(exc).__name__)
            )
        return self._finish(result, prepared, cache=True)

    def _execute_group(self, items: Sequence[_Prepared]) -> list[JudgmentServiceResult]:
        if len(items) == 1:
            return [self._execute_one(items[0])]
        live: list[_Prepared] = []
        results: list[JudgmentServiceResult | None] = [None] * len(items)
        for index, prepared in enumerate(items):
            if prepared.disabled:
                results[index] = self._finish(
                    self._result(status="disabled", prepared=prepared),
                    prepared,
                    cache=False,
                )
                continue
            cached = self._cache.get(prepared.cache_key)
            if cached is not None:
                results[index] = self._finish(
                    replace(cached, cache_hit=True), prepared, cache=False
                )
                continue
            live.append(prepared)
        if not live:
            return [item for item in results if item is not None]
        combined: dict[str, Question] = {}
        owners: list[tuple[int, str, str]] = []
        for live_index, prepared in enumerate(live):
            for qid, question in prepared.questions.items():
                merged_id = f"{live_index}::{qid}"
                combined[merged_id] = question
                owners.append((live_index, qid, merged_id))
        shared = live[0]
        try:
            reply, latency_ms = self._evaluate_backend(shared, combined)
            raw_answers = dict(reply.answers)
            grouped: list[dict[str, Any]] = [{} for _ in live]
            for live_index, qid, merged_id in owners:
                if merged_id not in raw_answers:
                    raise BackendError(
                        "invalid_response",
                        "backend answers do not match the requested questions",
                    )
                grouped[live_index][qid] = raw_answers[merged_id]
            produced: list[JudgmentServiceResult] = []
            for live_index, prepared in enumerate(live):
                split = BackendReply(
                    answers=grouped[live_index],
                    usage=reply.usage,
                    model=reply.model,
                    request_id=reply.request_id,
                    fake=reply.fake,
                )
                item_result = self._from_reply(
                    prepared,
                    split,
                    prepared.questions,
                    latency_ms,
                    meter=live_index == 0,
                )
                produced.append(self._finish(item_result, prepared, cache=True))
            live_results = produced
        except BackendError as exc:
            live_results = [
                self._finish(self._unavailable(prepared, exc), prepared, cache=False)
                for prepared in live
            ]
        except Exception as exc:  # noqa: BLE001 - never invent answers
            wrapped = BackendError("unavailable", str(exc) or type(exc).__name__)
            live_results = [
                self._finish(
                    self._unavailable(prepared, wrapped), prepared, cache=False
                )
                for prepared in live
            ]
        live_iter = iter(live_results)
        out: list[JudgmentServiceResult] = []
        for existing in results:
            if existing is not None:
                out.append(existing)
            else:
                out.append(next(live_iter))
        return out

    def run(
        self,
        *,
        purpose: str,
        template_id: str,
        state: object,
        params: Mapping[str, Any] | None = None,
        scope: object = None,
        ignore_capability: bool = False,
    ) -> JudgmentResult:
        prepared = self._prepare(
            purpose=purpose,
            template_id=template_id,
            state=state,
            params=params,
            scope=scope,
            ignore_capability=ignore_capability,
        )
        return self._execute_one(prepared)

    def run_many(self, requests: Sequence[Mapping[str, Any]]) -> list[JudgmentResult]:
        if not requests:
            return []
        prepared_list = [
            self._prepare(
                purpose=str(item.get("purpose") or ""),
                template_id=str(item.get("template_id") or item.get("template") or ""),
                state=item.get("state"),
                params=item.get("params"),
                scope=item.get("scope"),
                ignore_capability=bool(item.get("ignore_capability", False)),
            )
            for item in requests
        ]
        groups: OrderedDict[str, list[int]] = OrderedDict()
        for index, prepared in enumerate(prepared_list):
            groups.setdefault(_canonical(prepared.state), []).append(index)
        group_items = [
            [prepared_list[index] for index in indexes] for indexes in groups.values()
        ]
        workers = max(1, min(self._max_concurrency, len(group_items)))
        if len(group_items) == 1 or workers == 1:
            produced_groups = [self._execute_group(group) for group in group_items]
        else:
            with self.executor_factory(max_workers=workers) as executor:
                produced_groups = list(executor.map(self._execute_group, group_items))
        by_index: dict[int, JudgmentResult] = {}
        for indexes, results in zip(groups.values(), produced_groups):
            for index, result in zip(indexes, results):
                by_index[index] = result
        return [by_index[index] for index in range(len(prepared_list))]

    def probe(self) -> JudgmentResult:
        """Connection test. Ignores sub-capability flags; master must be on."""

        return self.run(
            purpose="probe",
            template_id=PROBE_TEMPLATE_ID,
            state=dict(PROBE_STATE),
            params=None,
            scope=None,
            ignore_capability=True,
        )

    def dispatch(self, spec: Mapping[str, Any] | None) -> dict[str, Any]:
        """Thin RPC adapter used by ``HostDispatcher._m_judge``."""

        if spec is None or not isinstance(spec, Mapping):
            return {"error": "judge spec must be an object"}
        template_id = spec.get("template")
        if not isinstance(template_id, str) or not template_id.strip():
            return {"error": "judge requires a template id"}
        if "state" not in spec:
            return {"error": "judge requires state"}
        params = spec.get("params")
        if params is None:
            params_map: Mapping[str, Any] = {}
        elif isinstance(params, Mapping):
            params_map = params
        else:
            return {"error": "params must be an object"}
        try:
            template = get_template(template_id)
        except KeyError:
            return {"error": f"unknown template: {template_id}"}
        try:
            if template_id == PROBE_TEMPLATE_ID:
                # Master consent permits a connection greeting only. Kernel
                # callers cannot use the probe to disclose arbitrary state
                # while every data-bearing capability is disabled.
                result = self.probe()
            else:
                result = self.run(
                    purpose=template.purpose,
                    template_id=template_id,
                    state=spec.get("state"),
                    params=params_map,
                    scope=spec.get("scope"),
                )
        except ValueError as exc:
            return {"error": str(exc) or "invalid judge arguments"}
        return result.to_dict()


__all__ = [
    "CACHE_SIZE",
    "MAX_CONCURRENCY",
    "SETTING_AUDIT_RAW_STATE",
    "STATE_TOKEN_BUDGET",
    "JudgmentService",
    "JudgmentServiceResult",
]
