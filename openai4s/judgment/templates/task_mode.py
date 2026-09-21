"""task_mode.classify: three-way shadow label for resolve_task_mode.

Questions are English. Each ``instructions`` names ``state.request`` because
question keys are not sent to the model. Criteria are structured
``{what, not_for, examples}`` taken from ``openai4s.agent.task_modes``
docstrings. The template is recording-only: it never binds completion
evidence and never changes the rule-based return value.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from openai4s.judgment.registry import get_template, register_template
from openai4s.judgment.types import Answer, Choice, Question

# Tunable. Shadow never binds delivery; policy is uncertain below this
# confidence. 0.0 means any well-formed choice is ``ok``.
CLASSIFY_CONF = 0.0
# Tunable. ``agree`` is True only when the shadow choice equals the rule
# mode and (when present) confidence is at least this value.
AGREE_CONF = 0.0

TEMPLATE_VERSION = "1"
TEMPLATE_ID = "task_mode.classify"
PURPOSE = "task_mode_shadow"

OPTIONS: tuple[str, ...] = (
    "analysis_run",
    "reusable_pipeline",
    "codebase_change",
)

_OPTION_CRITERIA: dict[str, dict[str, str]] = {
    "analysis_run": {
        "what": (
            "The default and the historical behaviour, byte for byte. "
            "A one-shot analysis turn: keep cells small, produce figures "
            "and a report. No extra completion requirements."
        ),
        "not_for": (
            "A request whose deliverable is a reusable pipeline (source "
            "modules, a thin entry point, tests) or saved source code in "
            "an existing project plus evidence that it still works."
        ),
        "examples": (
            "Analyze this CSV and plot the residual distribution; run the "
            "ADMET pipeline on seed molecules and report the top hits; "
            "run this code and explain the error; summarise the findings "
            "in an attached report."
        ),
    },
    "reusable_pipeline": {
        "what": (
            "The deliverable runs again: source modules, a thin entry "
            "point, and tests. The user is asking to engineer a pipeline "
            "or workflow so it can be re-run, not to produce one report."
        ),
        "not_for": (
            "A one-shot analysis whose output is a figure or report inside "
            "the kernel namespace, or a change to an existing codebase "
            "where the saved source files themselves are the deliverable."
        ),
        "examples": (
            "Build a reusable pipeline I can rerun next week on new "
            "samples; turn this into a repeatable workflow with a CLI "
            "entry point; make the pipeline reproducible so we can "
            "re-run it monthly."
        ),
    },
    "codebase_change": {
        "what": (
            "The deliverable is saved source code in an existing project, "
            "plus the evidence that it still works. The stricter of the "
            "two engineering kinds: guidance is a superset of a reusable "
            "pipeline."
        ),
        "not_for": (
            "A new standalone pipeline that is not editing an existing "
            "project, or an analysis that only discusses or runs code "
            "without asking to refactor, restructure, or modularize it."
        ),
        "examples": (
            "Refactor the repository so the loaders live in their own "
            "module; restructure this codebase and update pyproject; "
            "read AGENTS.md first, then modularize the package."
        ),
    },
}


def policy_version() -> str:
    """Cache-key component. Threshold edits must miss the in-process LRU."""

    return (
        f"{TEMPLATE_VERSION}:"
        f"classify_conf={CLASSIFY_CONF:.2f}:"
        f"agree_conf={AGREE_CONF:.2f}"
    )


POLICY_VERSION = policy_version()


def _classify_questions(_params: Mapping[str, Any]) -> Mapping[str, Question]:
    return {
        "mode": Choice(
            instructions=(
                "Which kind of task is the user request in state.request? "
                "Choose analysis_run when the user wants a one-shot analysis, "
                "figures, or a report, with no extra completion requirements. "
                "Choose reusable_pipeline when the deliverable must run again "
                "as source modules, a thin entry point, and tests. "
                "Choose codebase_change when the deliverable is saved source "
                "code in an existing project plus evidence that it still "
                "works. Judge only the kind of task; do not decide whether "
                "completion evidence should be required."
            ),
            options=dict(_OPTION_CRITERIA),
        )
    }


def _classify_policy(
    answers: Mapping[str, Answer], _params: Mapping[str, Any]
) -> Literal["ok", "uncertain"]:
    answer = answers.get("mode")
    if answer is None or answer.kind != "choice":
        return "uncertain"
    if str(answer.value) not in OPTIONS:
        return "uncertain"
    confidence = answer.confidence
    if confidence is None:
        return "ok" if CLASSIFY_CONF <= 0.0 else "uncertain"
    try:
        if float(confidence) < CLASSIFY_CONF:
            return "uncertain"
    except (TypeError, ValueError):
        return "uncertain"
    return "ok"


def shadow_agree(
    rule_mode: str,
    choice: str | None,
    confidence: float | None,
) -> bool | None:
    """Whether the shadow label matches the rule-based mode.

    ``None`` means there was no usable choice (unavailable / malformed).
    The numeric floor is :data:`AGREE_CONF` and is safe to retune.
    """

    if choice is None or choice not in OPTIONS:
        return None
    if rule_mode not in OPTIONS:
        return None
    if confidence is not None:
        try:
            if float(confidence) < AGREE_CONF:
                return False
        except (TypeError, ValueError):
            return None
    return choice == rule_mode


def _register() -> None:
    try:
        get_template(TEMPLATE_ID)
        return
    except KeyError:
        pass
    register_template(
        TEMPLATE_ID,
        TEMPLATE_VERSION,
        _classify_questions,
        _classify_policy,
        purpose=PURPOSE,
        policy_version=policy_version(),
    )


_register()


__all__ = [
    "AGREE_CONF",
    "CLASSIFY_CONF",
    "OPTIONS",
    "POLICY_VERSION",
    "PURPOSE",
    "TEMPLATE_ID",
    "TEMPLATE_VERSION",
    "policy_version",
    "shadow_agree",
]
