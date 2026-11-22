"""Decision 30: the workbench says the server's progress lines again in the UI
language, so its table of them must not drift from the server's wording.

`openai4s/server/completions.py` writes deterministic status rows around the
agent's actions in the language of the turn's request. The frontend
(`frontend/src/features/messages/progress.ts`) recognises each of them in
either language and re-renders it from its own zh/en table. A server line
reworded without that table following would no longer be recognised and
would quietly stay in the language it was written in. So this generates the
real sentences from the server's own functions, puts `{0}`, `{1}`, ... where
their parameters are, and asserts each template appears verbatim, quoted, in
the frontend module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openai4s.agent.actions import CodeCell, NativeToolBatch, NativeToolCall
from openai4s.agent.models import ExecutionOutcome
from openai4s.agent.runtime import format_observation
from openai4s.server import completions
from openai4s.server.completions import (
    action_narration,
    cancellation_marker,
    outcome_narration,
)

COPY_MODULE = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "features"
    / "messages"
    / "progress.ts"
)
FENCE = "`" * 3


def _call(name: str) -> NativeToolCall:
    return NativeToolCall(
        id="call-1",
        wire_id="call-1",
        name=name,
        ordinal=0,
        raw_arguments="{}",
        arguments={},
    )


def _template(text: str, params: list[str]) -> str:
    """`text` with each parameter value (in order) replaced by `{n}`."""
    for index, value in enumerate(params):
        assert value in text, (value, text)
        text = text.replace(value, "{%d}" % index)
    return text


def _sentences(text: str) -> list[str]:
    """The template lines of a row: a cell's output block is not one."""
    lines, inside = [], False
    for line in text.split("\n"):
        if line.startswith(FENCE):
            inside = not inside
            continue
        if not inside:
            lines.append(line)
    return lines


def _failed(language: str, error_line: str, code_language: str = "python") -> str:
    observation = (
        "[Observation]\nERROR (cell line 1):\nTraceback (most recent call last):\n"
        f'  File "<kernel:3>", line 1, in <module>\n{error_line}'
    )
    return outcome_narration(
        CodeCell(code_language, "x"),
        ExecutionOutcome(observation=observation),
        language,
    )


def _ran(language: str, observation: str) -> str:
    return outcome_narration(
        CodeCell("python", "x"), ExecutionOutcome(observation=observation), language
    )


def _templates(language: str) -> list[str]:
    zh = language == "zh"
    out: list[str] = []

    # action_narration: every branch, opening and not.
    out.append(action_narration(CodeCell("python", "print(1)"), language))
    out.append(action_narration(CodeCell("r", "print(1)"), language))
    steps = NativeToolBatch(calls=tuple(_call("web_fetch") for _ in range(7)))
    out.append(_template(action_narration(steps, language), ["7"]))
    for name in (
        "web_search",
        "web_fetch",
        "write_file",
        "read_text_file",
        "write_todos",
        "env_use",
        "something_else",
    ):
        batch = NativeToolBatch(calls=(_call(name),))
        out.append(action_narration(batch, language))
        out.append(action_narration(batch, language, opening=False))

    # cancellation_marker.
    out += [cancellation_marker(reason, language) for reason in ("user", "auto_budget")]

    # outcome_narration: a failure, each missing-package hint, each refusal.
    error = "ZeroDivisionError: division by zero"
    out.append(_template(_failed(language, error), [error]))
    renamed = "ModuleNotFoundError: No module named 'Bio'"
    out.append(
        _template(_failed(language, renamed), [renamed, "`Bio`", "`biopython`"])
        .replace("{1}", "`{1}`")
        .replace("{2}", "`{2}`")
    )
    plain = "ModuleNotFoundError: No module named 'lifelines'"
    out.append(
        _template(_failed(language, plain), [plain, "`lifelines`"]).replace(
            "{1}", "`{1}`"
        )
    )
    r_missing = "Error in library(Seurat) : there is no package called ‘Seurat’"
    out.append(
        _template(_failed(language, r_missing, "r"), [r_missing, "`Seurat`"]).replace(
            "{1}", "`{1}`"
        )
    )
    for reason in ("approval timed out", "denied by user"):
        out.append(
            _failed(
                language,
                f"RuntimeError: host.web_fetch error: Permission denied: {reason}",
            )
        )
    reason = "blocked by rule net-deny"
    out.append(
        _template(
            _failed(
                language,
                f"RuntimeError: host.web_fetch error: Permission denied: {reason}",
            ),
            [reason],
        )
    )

    # A finished cell: each header and note around its output block.
    out += _sentences(_ran(language, "[Observation]\nstdout:\nalpha\nbeta"))
    eight = "\n".join(f"line {i}" for i in range(8))
    head, *notes = _sentences(_ran(language, f"[Observation]\nstdout:\n{eight}"))
    out.append(head)
    out += [_template(note, ["8"]) for note in notes]
    long_out = "\n".join(f"row {i} " + "x" * 40 for i in range(3001))
    long_text = _ran(
        language,
        format_observation(
            {"stdout": long_out, "stderr": "", "error": None, "cwd": None}
        ),
    )
    head, *notes = _sentences(long_text)
    out.append(head)
    out += [_template(note, [f"{len(long_out):,}"]) for note in notes]
    stderr_only = _sentences(_ran(language, "[Observation]\nstderr:\nwarn 1\nwarn 2"))
    out.append(stderr_only[0])
    out += [_template(note, ["2"]) for note in stderr_only[1:]]
    out.append(_ran(language, "[Observation]\n(no output)"))

    # A bookkeeping cell's sentences, one by one.
    line = completions._bookkeeping_line
    out.append(
        _template(
            line("save_artifact", {"filename": "volcano.png"}, zh), ["volcano.png"]
        )
    )
    out.append(line("save_artifact", {}, zh))
    todos = [{"status": "completed"}] * 2 + [{"status": "pending"}] * 3
    out.append(_template(line("todo_write", {"todos": todos}, zh), ["5", "2"]))
    for name in ("todo_write", "plan_update", "todo_read", "plan_read"):
        out.append(line(name, {}, zh))
    out.append(_template(line("env.list", {"environments": [{}] * 4}, zh), ["4"]))
    out.append(line("env.list", {}, zh))
    out.append(
        _template(line("env.use", {"env": {"name": "lab_env"}}, zh), ["lab_env"])
    )
    out.append(line("env.use", {}, zh))
    installed = {"installed": ["statsmodels"], "log": " + statsmodels==0.15.0"}
    out.append(_template(line("env.create", installed, zh), ["statsmodels 0.15.0"]))
    out.append(_template(line("env.create", {"failed": ["nope"]}, zh), ["nope"]))
    out.append(line("env.create", {"ok": False}, zh))
    out.append(line("env.create", {"ok": True}, zh))
    return out


@pytest.mark.parametrize("language", ["zh", "en"])
def test_every_server_progress_template_is_in_the_frontend_table(language):
    source = COPY_MODULE.read_text(encoding="utf-8")
    templates = _templates(language)
    assert len(templates) >= 40
    missing = [text for text in templates if f'"{text}"' not in source]
    assert not missing, (
        "server progress lines the frontend cannot say again in the UI language; "
        f"update {COPY_MODULE.name} with completions.py: {missing}"
    )


def test_the_frontend_joins_bookkeeping_sentences_as_the_server_does():
    """`progress.ts` reads a bookkeeping row as sentences joined with nothing
    in Chinese and with one space in English."""
    stdout = "{'environments': [{'name': 'base'}]}"
    for zh, joiner in ((True, ""), (False, " ")):
        row = completions._bookkeeping_narration(("todo_write", "env.list"), stdout, zh)
        first = completions._bookkeeping_line("todo_write", {}, zh)
        second = completions._bookkeeping_line(
            "env.list", {"environments": [{"name": "base"}]}, zh
        )
        assert row == first + joiner + second
