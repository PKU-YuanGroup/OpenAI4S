"""What KIND of task this turn is, and the prompt fragment that says so.

The system had exactly one shape of guidance — analysis — so a request to
build a reusable pipeline or change a codebase was answered with "keep cells
small, produce figures and a report", and the implementation stayed inside the
kernel namespace where nobody could run it again.

Three modes:

``analysis_run``
    The default and the historical behaviour, byte for byte. No fragment, no
    extra completion requirements.
``reusable_pipeline``
    The deliverable runs again: source modules, a thin entry point, tests.
``codebase_change``
    The deliverable is saved source code in an existing project, plus the
    evidence that it still works.

Selection has two doors. An **explicit** selection always wins — the Web body
field ``task_mode`` and the CLI ``--mode`` flag — and an unrecognised explicit
value raises rather than falling through to detection, so the door cannot
degrade into a suggestion. Otherwise the request text is classified by a
deliberately conservative rule set: a mode needs a *target* signal (the thing
being engineered) AND an *action* signal (the request to engineer it), both
matched on word boundaries. One signal alone is a topic word, not a request,
and stays ``analysis_run`` — a false pipeline/codebase mode imposes required
source/entry-point/test evidence on a turn that has none of it, which refuses
an honest completion. The bias is documented, tested, and one-directional.
"""

from __future__ import annotations

import re
from enum import Enum

from openai4s import prompts

__all__ = [
    "TASK_MODE_PROMPT_NAMES",
    "TaskMode",
    "resolve_task_mode",
    "task_mode_prompt",
]


class TaskMode(str, Enum):
    """The task kinds the outer loop distinguishes."""

    ANALYSIS_RUN = "analysis_run"
    REUSABLE_PIPELINE = "reusable_pipeline"
    CODEBASE_CHANGE = "codebase_change"


#: Mode value -> ``openai4s.prompts`` registry key. ``analysis_run`` is absent
#: on purpose: the default mode injects nothing.
TASK_MODE_PROMPT_NAMES: dict[str, str] = {
    TaskMode.REUSABLE_PIPELINE.value: "task_mode_reusable_pipeline",
    TaskMode.CODEBASE_CHANGE.value: "task_mode_codebase_change",
}


def _signals(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


# CJK has no word boundaries, so the Chinese signals are matched literally;
# every ASCII signal carries an explicit \b so `recode` is not `code` and
# `packaged` is not `package`.
_PIPELINE_TARGETS = _signals(
    r"\bpipelines?\b",
    r"\bworkflows?\b",
    r"\bentry[- ]?points?\b",
    r"\bCLI\b",
    r"\bcommand[- ]line\b",
    r"管线",
    r"流水线",
    r"工作流",
    r"流程",
)

_PIPELINE_ACTIONS = _signals(
    r"\breusable\b",
    r"\bre-?runnable\b",
    r"\bre-?run\b",
    r"\bre-?runs\b",
    r"\brerunning\b",
    r"\brepeatable\b",
    r"\breproducible\b",
    r"\bproductioni[sz]e[d]?\b",
    r"\bparameteri[sz]e[d]?\b",
    r"可复用",
    r"可重复",
    r"复用",
    r"工程化",
    r"重跑",
)

_CODEBASE_TARGETS = _signals(
    r"\bcodebase\b",
    r"\bcode\b",
    r"\brepo\b",
    r"\brepository\b",
    r"\bpackages?\b",
    r"\bmodules?\b",
    r"\bsource files?\b",
    r"\bpyproject(\.toml)?\b",
    r"\bsetup\.py\b",
    r"\bAGENTS\.md\b",
    r"\bCLAUDE\.md\b",
    r"代码库",
    r"仓库",
    r"模块",
    r"源码",
    r"源代码",
)

_CODEBASE_ACTIONS = _signals(
    r"\brefactor(ing|ed|s)?\b",
    r"\brestructur(e|ed|ing)\b",
    r"\breorgani[sz]e[d]?\b",
    r"\bmodulari[sz]e[d]?\b",
    r"\bsplit\s+(?:\w+\s+){0,3}?into\b",
    r"重构",
    r"拆分",
    r"模块化",
    r"工程化",
)


def _matches(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _qualifies(
    text: str,
    targets: tuple[re.Pattern[str], ...],
    actions: tuple[re.Pattern[str], ...],
) -> bool:
    """Two independent structural signals, or it does not qualify."""

    return _matches(text, targets) and _matches(text, actions)


def _coerce(value: object) -> TaskMode:
    if isinstance(value, TaskMode):
        return value
    text = str(value or "").strip()
    try:
        return TaskMode(text)
    except ValueError as exc:  # noqa: TRY003 - the caller shows this to a user
        raise ValueError(
            f"unknown task_mode {text!r}; known: "
            + ", ".join(mode.value for mode in TaskMode)
        ) from exc


def resolve_task_mode(
    text: str | None, explicit: str | TaskMode | None = None
) -> TaskMode:
    """Resolve the mode for one turn: explicit selection first, else detection.

    ``explicit`` of ``None`` or a blank string means "not selected"; anything
    else must name a real mode. Detection needs a target AND an action signal
    and biases to :attr:`TaskMode.ANALYSIS_RUN` otherwise. When both code-shaped
    families match, :attr:`TaskMode.CODEBASE_CHANGE` wins — it is the stricter
    of the two and its guidance is a superset.
    """

    if explicit is not None and str(explicit).strip():
        return _coerce(explicit)
    body = str(text or "")
    if not body.strip():
        return TaskMode.ANALYSIS_RUN
    if _qualifies(body, _CODEBASE_TARGETS, _CODEBASE_ACTIONS):
        return TaskMode.CODEBASE_CHANGE
    if _qualifies(body, _PIPELINE_TARGETS, _PIPELINE_ACTIONS):
        return TaskMode.REUSABLE_PIPELINE
    return TaskMode.ANALYSIS_RUN


def task_mode_prompt(mode: str | TaskMode) -> str:
    """The per-turn prompt fragment for ``mode`` (``""`` for the default)."""

    resolved = _coerce(mode)
    name = TASK_MODE_PROMPT_NAMES.get(resolved.value)
    if name is None:
        return ""
    return prompts.build(name)
