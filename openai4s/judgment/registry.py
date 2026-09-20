"""Registered judgment templates. Kernel code can only name a template id."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping

from openai4s.judgment.types import Answer, Noul, Question

PolicyVerdict = Literal["ok", "uncertain"]

BuildQuestions = Callable[[Mapping[str, Any]], Mapping[str, Question]]
PolicyFn = Callable[[Mapping[str, Answer], Mapping[str, Any]], PolicyVerdict]

PROBE_TEMPLATE_ID = "system.probe"
PROBE_TEMPLATE_VERSION = "1"
PROBE_PURPOSE = "probe"
PROBE_STATE: dict[str, str] = {"text": "hello"}

_CUSTOM_KINDS = frozenset(("noul", "score"))


@dataclass(frozen=True)
class CustomQuestionSpec:
    """Limits for templates that may synthesize questions (e.g. features.custom)."""

    kinds: tuple[str, ...]
    max_questions: int
    max_question_chars: int
    max_state_chars: int

    def __post_init__(self) -> None:
        kinds = tuple(self.kinds)
        if not kinds:
            raise ValueError("allow_custom.kinds must be non-empty")
        for kind in kinds:
            if kind not in _CUSTOM_KINDS:
                raise ValueError(
                    "allow_custom.kinds may contain only 'noul' and 'score'"
                )
        if self.max_questions < 1:
            raise ValueError("allow_custom.max_questions must be >= 1")
        if self.max_question_chars < 1:
            raise ValueError("allow_custom.max_question_chars must be >= 1")
        if self.max_state_chars < 1:
            raise ValueError("allow_custom.max_state_chars must be >= 1")
        object.__setattr__(self, "kinds", kinds)


@dataclass(frozen=True)
class Template:
    """One registered judgment template."""

    template_id: str
    version: str
    policy_version: str
    purpose: str
    build_questions: BuildQuestions
    policy: PolicyFn
    allow_custom: CustomQuestionSpec | None = None


_LOCK = threading.Lock()
_TEMPLATES: dict[str, Template] = {}


def _coerce_allow_custom(value: object) -> CustomQuestionSpec | None:
    if value is None:
        return None
    if isinstance(value, CustomQuestionSpec):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("allow_custom must be a mapping or None")
    kinds_raw = value.get("kinds", ("noul", "score"))
    if isinstance(kinds_raw, str):
        kinds: tuple[str, ...] = (kinds_raw,)
    else:
        kinds = tuple(str(item) for item in kinds_raw)
    return CustomQuestionSpec(
        kinds=kinds,
        max_questions=int(value.get("max_questions", 8)),
        max_question_chars=int(value.get("max_question_chars", 2000)),
        max_state_chars=int(value.get("max_state_chars", 32_768)),
    )


def register_template(
    template_id: str,
    version: str,
    build_questions: BuildQuestions,
    policy: PolicyFn,
    *,
    purpose: str,
    allow_custom: CustomQuestionSpec | Mapping[str, Any] | None = None,
    policy_version: str | None = None,
) -> Template:
    """Register a template. Duplicate ids raise ValueError."""

    if not isinstance(template_id, str) or not template_id.strip():
        raise ValueError("template_id must be a non-empty string")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("version must be a non-empty string")
    if not isinstance(purpose, str) or not purpose.strip():
        raise ValueError("purpose must be a non-empty string")
    if not callable(build_questions):
        raise ValueError("build_questions must be callable")
    if not callable(policy):
        raise ValueError("policy must be callable")
    resolved_policy = (
        policy_version.strip()
        if isinstance(policy_version, str) and policy_version.strip()
        else version
    )
    template = Template(
        template_id=template_id,
        version=version,
        policy_version=resolved_policy,
        purpose=purpose,
        build_questions=build_questions,
        policy=policy,
        allow_custom=_coerce_allow_custom(allow_custom),
    )
    with _LOCK:
        if template_id in _TEMPLATES:
            raise ValueError(f"template already registered: {template_id}")
        _TEMPLATES[template_id] = template
    return template


def get_template(template_id: str) -> Template:
    """Return a registered template or raise KeyError."""

    with _LOCK:
        try:
            return _TEMPLATES[template_id]
        except KeyError:
            raise KeyError(f"unknown template: {template_id}") from None


def unregister_template(template_id: str) -> None:
    """Drop a non-builtin template. Used by tests to keep the registry clean."""

    if template_id == PROBE_TEMPLATE_ID:
        raise ValueError("cannot unregister builtin system.probe")
    with _LOCK:
        _TEMPLATES.pop(template_id, None)


def _probe_questions(_params: Mapping[str, Any]) -> Mapping[str, Question]:
    return {
        "alive": Noul(
            instructions=(
                "This is a connection probe. The state field text is a fixed "
                "greeting. Is the judging service reachable and answering?"
            )
        )
    }


def _probe_policy(
    answers: Mapping[str, Answer], _params: Mapping[str, Any]
) -> PolicyVerdict:
    answer = answers.get("alive")
    if answer is None or answer.kind != "noul":
        return "uncertain"
    try:
        value = float(answer.value)
    except (TypeError, ValueError):
        return "uncertain"
    if 0.0 <= value <= 1.0:
        return "ok"
    return "uncertain"


def _register_builtins() -> None:
    with _LOCK:
        if PROBE_TEMPLATE_ID in _TEMPLATES:
            return
    register_template(
        PROBE_TEMPLATE_ID,
        PROBE_TEMPLATE_VERSION,
        _probe_questions,
        _probe_policy,
        purpose=PROBE_PURPOSE,
    )


_register_builtins()


__all__ = [
    "PROBE_PURPOSE",
    "PROBE_STATE",
    "PROBE_TEMPLATE_ID",
    "PROBE_TEMPLATE_VERSION",
    "CustomQuestionSpec",
    "Template",
    "get_template",
    "register_template",
    "unregister_template",
]
