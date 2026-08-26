"""Task-mode resolution and the prompt fragments it selects.

The system had exactly one shape of guidance: analysis. A request to build a
reusable pipeline or change a codebase got the same "keep cells small, produce
figures and a report" instructions, and the deliverable stayed inside the
kernel namespace. These tests pin the classifier's *conservative* contract —
two independent structural signals or it stays `analysis_run` — plus the
explicit override that always wins, and the fragment registry both surfaces
splice.
"""

from __future__ import annotations

import pytest

from openai4s import prompts
from openai4s.agent.task_modes import (
    TASK_MODE_PROMPT_NAMES,
    TaskMode,
    resolve_task_mode,
    task_mode_prompt,
)

# --------------------------------------------------------------------------
# the enum and the explicit door
# --------------------------------------------------------------------------


def test_the_vocabulary_is_exactly_three_modes_with_analysis_as_default():
    assert [mode.value for mode in TaskMode] == [
        "analysis_run",
        "reusable_pipeline",
        "codebase_change",
    ]
    assert resolve_task_mode("") is TaskMode.ANALYSIS_RUN
    assert resolve_task_mode(None) is TaskMode.ANALYSIS_RUN


@pytest.mark.parametrize(
    "explicit,expected",
    [
        ("analysis_run", TaskMode.ANALYSIS_RUN),
        ("reusable_pipeline", TaskMode.REUSABLE_PIPELINE),
        ("codebase_change", TaskMode.CODEBASE_CHANGE),
        (TaskMode.CODEBASE_CHANGE, TaskMode.CODEBASE_CHANGE),
    ],
)
def test_an_explicit_selection_beats_every_detected_signal(explicit, expected):
    """The user picked. Detection must not second-guess a chosen mode."""
    text = "重构这个仓库的模块划分并写一个可复用的管线"
    assert resolve_task_mode(text, explicit=explicit) is expected


def test_an_explicit_selection_can_force_the_default_back_on():
    assert (
        resolve_task_mode(
            "refactor the repository into modules", explicit="analysis_run"
        )
        is TaskMode.ANALYSIS_RUN
    )


def test_an_unknown_explicit_mode_is_loud_rather_than_silently_detected():
    """A typo that silently fell through to detection would make the explicit
    door a suggestion. The caller (HTTP body / CLI flag) must hear about it."""
    with pytest.raises(ValueError) as excinfo:
        resolve_task_mode("anything", explicit="codebase-change")
    assert "codebase-change" in str(excinfo.value)
    assert "analysis_run" in str(excinfo.value)


def test_blank_explicit_values_mean_absent_not_invalid():
    assert resolve_task_mode("plot the data", explicit="") is TaskMode.ANALYSIS_RUN
    assert resolve_task_mode("plot the data", explicit="  ") is TaskMode.ANALYSIS_RUN


# --------------------------------------------------------------------------
# detection: two independent signals, or the default
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Build a reusable pipeline I can rerun next week on new samples",
        "Turn this into a repeatable workflow with a CLI entry point",
        "please make the pipeline reproducible so we can re-run it monthly",
        "写一个可复用的分析管线，以后每个月都能重跑",
        "把这套流程工程化成一个可以复用的工作流",
    ],
)
def test_reusable_pipeline_phrasings(text):
    assert resolve_task_mode(text) is TaskMode.REUSABLE_PIPELINE


@pytest.mark.parametrize(
    "text",
    [
        "Refactor the repository so the loaders live in their own module",
        "restructure this codebase and update pyproject accordingly",
        "Read AGENTS.md first, then modularize the package",
        "重构一下这个仓库的模块划分",
        "把源码里的这几个模块拆分开",
    ],
)
def test_codebase_change_phrasings(text):
    assert resolve_task_mode(text) is TaskMode.CODEBASE_CHANGE


@pytest.mark.parametrize(
    "text",
    [
        "Analyze this CSV and plot the distribution of the residuals",
        "Run the ADMET pipeline on these seed molecules and report the top hits",
        "run this code and tell me what the error means",
        "跑一下这段代码，看看为什么报错",
        "用这个管线跑一遍数据，给我一份报告",
        "What does this module do?",
        "Summarise the findings in the attached report",
    ],
)
def test_a_single_weak_signal_stays_on_the_default(text):
    """One signal is a topic word, not a request to engineer something. The
    documented bias is toward `analysis_run`: a false pipeline/codebase mode
    imposes required source/entry-point/test evidence on an analysis turn."""
    assert resolve_task_mode(text) is TaskMode.ANALYSIS_RUN


def test_matching_is_word_bounded_rather_than_substring():
    """`recode`/`packaged goods` must not read as `code`/`package`, and a
    signal inside another word is not a signal."""
    assert (
        resolve_task_mode("recode the ordinal columns and rewrite the labels")
        is TaskMode.ANALYSIS_RUN
    )
    assert (
        resolve_task_mode("restructure the packaged-goods sales table")
        is TaskMode.ANALYSIS_RUN
    )


def test_both_families_matching_resolves_to_the_stricter_codebase_mode():
    text = "Refactor the repository into modules and give me a reusable pipeline I can rerun"
    assert resolve_task_mode(text) is TaskMode.CODEBASE_CHANGE


def test_detection_is_case_insensitive():
    assert (
        resolve_task_mode("REFACTOR THE CODEBASE INTO PACKAGES")
        is TaskMode.CODEBASE_CHANGE
    )


# --------------------------------------------------------------------------
# the prompt fragments
# --------------------------------------------------------------------------


def test_the_default_mode_injects_nothing():
    """`analysis_run` is today's behaviour, byte for byte."""
    assert task_mode_prompt(TaskMode.ANALYSIS_RUN) == ""
    assert TaskMode.ANALYSIS_RUN.value not in TASK_MODE_PROMPT_NAMES


@pytest.mark.parametrize("mode", [TaskMode.REUSABLE_PIPELINE, TaskMode.CODEBASE_CHANGE])
def test_each_non_default_mode_has_a_registered_fragment(mode):
    name = TASK_MODE_PROMPT_NAMES[mode.value]
    assert prompts.build(name) == task_mode_prompt(mode)
    assert task_mode_prompt(mode).strip()


@pytest.mark.parametrize("mode", [TaskMode.REUSABLE_PIPELINE, TaskMode.CODEBASE_CHANGE])
def test_the_fragment_states_the_source_deliverable_contract(mode):
    body = task_mode_prompt(mode)
    # The defect this whole mode exists to fix: cells were the deliverable.
    assert "source file" in body.lower()
    assert "entry point" in body.lower()
    assert "test" in body.lower()
    # The completion contract the Host will actually enforce.
    for field in (
        "source_files",
        "entry_points",
        "architecture_summary",
        "test_evidence",
    ):
        assert field in body


@pytest.mark.parametrize("mode", [TaskMode.REUSABLE_PIPELINE, TaskMode.CODEBASE_CHANGE])
def test_the_fragment_carries_its_own_working_directory_override(mode):
    """The Web prompt says the working dir is for deliverables only and is
    NEVER a repo checkout. Source modules ARE the deliverable in these modes,
    so the fragment must scope that clause rather than silently contradict it."""
    body = task_mode_prompt(mode)
    assert "working directory" in body.lower()


@pytest.mark.parametrize("mode", [TaskMode.REUSABLE_PIPELINE, TaskMode.CODEBASE_CHANGE])
def test_the_fragment_forbids_gaming_the_structure_check(mode):
    body = task_mode_prompt(mode).lower()
    assert "empty" in body
    assert "single file" in body or "one file" in body


def test_the_codebase_fragment_opens_with_read_only_inspection():
    body = task_mode_prompt(TaskMode.CODEBASE_CHANGE)
    assert "AGENTS.md" in body and "CLAUDE.md" in body
    assert "README" in body and "pyproject" in body


def test_task_mode_prompt_accepts_the_string_spelling_too():
    assert task_mode_prompt("codebase_change") == task_mode_prompt(
        TaskMode.CODEBASE_CHANGE
    )
    assert task_mode_prompt("analysis_run") == ""


def test_an_unknown_mode_name_is_an_error_not_an_empty_fragment():
    with pytest.raises(ValueError):
        task_mode_prompt("nonsense_mode")


# --------------------------------------------------------------------------
# the two doors: Web per-turn injection and the CLI flag
# --------------------------------------------------------------------------


class _Hub:
    def __init__(self):
        self.events = []

    def emitter(self, root_frame_id):
        def emit(event):
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id, event):
        event.setdefault("root_frame_id", root_frame_id)
        self.events.append(event)


def _web_cfg(tmp_path):
    from openai4s.config import Config, LLMConfig

    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=2,
    )


def _drive_web_turn(monkeypatch, tmp_path, text, **kwargs):
    """One real `run_message` with the provider and runtime faked out."""
    from types import SimpleNamespace

    from openai4s.server import gateway as gateway_mod
    from openai4s.store import get_store

    cfg = _web_cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")
    calls = []
    modes = []

    def fake_chat(messages, cfg, on_delta=None, **kw):
        calls.append([dict(m) for m in messages])
        return {"content": "no action here.", "usage": {}}

    def fake_ensure(st):
        st.dispatcher = SimpleNamespace(
            last_output=None,
            set_task_mode=lambda mode: modes.append(mode),
        )
        st.messages = [{"role": "system", "content": "sys"}]
        st.booted = True

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_ensure_runtime", fake_ensure)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
    runner.run_message(fid, "default", text, **kwargs)
    return store, fid, calls, modes


def test_web_turn_appends_the_detected_fragment_to_the_user_message_only(
    monkeypatch, tmp_path
):
    """Same seam `_EXPLORE_PROTOCOL` uses: the in-conversation user message
    carries the fragment, the durable message row stays exactly what was typed
    (mode is a per-turn decision, and the system prompt is seeded once).

    A DETECTED mode guides and never arms: the dispatcher is stamped with no
    binding mode, so the completion contract stays exactly analysis_run. A
    two-signal classifier over prose is not consent to refuse an honest
    completion — `rerun the pipeline` and `restructure the plot code` are
    ordinary requests in this product's own domain."""
    text = "Refactor the repository so the loaders live in their own module"
    store, fid, calls, modes = _drive_web_turn(monkeypatch, tmp_path, text)

    sent = calls[0][-1]["content"]
    assert "[TASK MODE: codebase_change]" in sent
    assert sent.startswith(text)
    assert store.list_messages(fid)[0]["content"] == text
    assert modes and modes[-1] is None


def test_web_turn_on_an_analysis_request_injects_nothing(monkeypatch, tmp_path):
    text = "Plot the residual distribution for this CSV"
    _store, _fid, calls, modes = _drive_web_turn(monkeypatch, tmp_path, text)

    assert calls[0][-1]["content"] == text
    assert "[TASK MODE" not in calls[0][-1]["content"]
    assert modes and modes[-1] is None


@pytest.mark.parametrize(
    "text",
    [
        # Realistic phrasings that DO trip the two-signal detector. The
        # completion contract must stay unarmed for every one of them,
        # because none of these users asked for verified code evidence.
        "rerun the pipeline with the new seeds",
        "Please refactor my plotting code so the figure is cleaner "
        "and split the helpers into their own module",
    ],
)
def test_a_detection_false_positive_never_arms_the_web_completion_contract(
    monkeypatch, tmp_path, text
):
    _store, _fid, _calls, modes = _drive_web_turn(monkeypatch, tmp_path, text)
    assert modes and modes[-1] is None


def test_web_body_task_mode_overrides_detection(monkeypatch, tmp_path):
    text = "Plot the residual distribution for this CSV"
    _store, _fid, calls, modes = _drive_web_turn(
        monkeypatch, tmp_path, text, task_mode="reusable_pipeline"
    )

    assert "[TASK MODE: reusable_pipeline]" in calls[0][-1]["content"]
    assert modes[-1] == "reusable_pipeline"


def test_an_invalid_web_task_mode_is_a_400_not_a_500(tmp_path):
    from openai4s.server import gateway as gateway_mod
    from openai4s.store import get_store

    cfg = _web_cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    store = get_store(cfg.db_path)
    fid = store.new_frame(kind="turn", project_id="default", status="ready")

    with pytest.raises(gateway_mod.GatewayError) as excinfo:
        runner.submit_message(fid, "default", "task", None, task_mode="nope")
    assert excinfo.value.code == 400
    assert excinfo.value.error_code == "invalid_task_mode"


def test_task_mode_survives_the_queue_boundary(tmp_path):
    from openai4s.server import gateway as gateway_mod

    cfg = _web_cfg(tmp_path)
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    seen = {}

    def fake_run(
        root_frame_id,
        project_id,
        user_text,
        model=None,
        plan=False,
        annos=None,
        explore=False,
        frozen_binding=None,
        task_mode=None,
    ):
        seen["task_mode"] = task_mode
        return {"status": "completed", "frame_id": root_frame_id}

    runner.run_message = fake_run
    job = runner.submit_message(
        "f-x", "default", "task", None, task_mode="codebase_change"
    )
    assert job.wait_result()["status"] == "completed"
    assert seen["task_mode"] == "codebase_change"


def _cli_agent(monkeypatch, tmp_path, seen, **kwargs):
    """A real `Agent` and a real dispatcher; only the kernel and provider fake."""
    from openai4s.agent import loop as loop_mod

    class _Kernel:
        def __init__(self, *a, **k):
            pass

        def execute(self, *a, **k):
            return {"stdout": "", "error": None}

        def shutdown(self):
            pass

    def fake_chat(messages, *a, **k):
        seen.append([dict(m) for m in messages])
        return {"content": "done.", "usage": {}}

    monkeypatch.setattr(loop_mod, "Kernel", _Kernel)
    monkeypatch.setattr(loop_mod, "chat", fake_chat)

    return loop_mod.Agent(
        cfg=_web_cfg(tmp_path),
        max_turns=1,
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
        **kwargs,
    )


def test_the_cli_agent_augments_its_own_user_message(monkeypatch, tmp_path):
    """The CLI/child seam had no per-turn augmentation at all — `Agent.run`
    built `[{system}, {user: task}]` with nothing in between."""
    seen: list = []
    agent = _cli_agent(monkeypatch, tmp_path, seen)
    agent.run("Build a reusable pipeline I can rerun next month")

    conversation = [m for m in seen if isinstance(m, list)][-1]
    user = [m for m in conversation if m.get("role") == "user"]
    assert "[TASK MODE: reusable_pipeline]" in user[0]["content"]
    assert "[TASK MODE: codebase_change]" not in user[0]["content"]
    # Detected, not selected: the fragment guides, the completion contract
    # stays unarmed. Nothing about this run's text is consent to refuse an
    # honest completion for missing source/entry-point/test evidence.
    assert agent.dispatcher._task_mode is None


def test_an_explicit_cli_mode_beats_the_agents_own_detection(monkeypatch, tmp_path):
    seen: list = []
    agent = _cli_agent(monkeypatch, tmp_path, seen, task_mode="analysis_run")
    agent.run("refactor the repository into modules")

    conversation = [m for m in seen if isinstance(m, list)][-1]
    user = [m for m in conversation if m.get("role") == "user"]
    assert "[TASK MODE" not in user[0]["content"]
    assert agent.dispatcher._task_mode == "analysis_run"


def test_the_cli_run_subcommand_exposes_the_flag():
    from openai4s.cli.main import build_parser

    parser = build_parser()
    args = parser.parse_args(["run", "do a thing", "--mode", "codebase_change"])
    assert args.mode == "codebase_change"
    assert parser.parse_args(["run", "do a thing"]).mode is None
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "x", "--mode", "nonsense"])


def test_the_cli_mode_choices_are_derived_from_the_enum():
    """A fourth mode must reach the CLI surface without anyone remembering a
    hardcoded list."""
    import argparse

    from openai4s.cli.main import build_parser

    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    run_sub = subparsers.choices["run"]
    mode_action = next(a for a in run_sub._actions if a.dest == "mode")
    assert list(mode_action.choices) == [m.value for m in TaskMode]


# --------------------------------------------------------------------------
# detection guides; only explicit selection arms the completion contract
# --------------------------------------------------------------------------


def test_a_detected_code_mode_guides_but_never_arms_the_completion_contract(
    monkeypatch, tmp_path
):
    """The regression that made ordinary turns uncompletable: a request whose
    prose trips the detector must still be able to finish with a plain
    analysis-shaped completion, on BOTH doors."""
    seen: list = []
    agent = _cli_agent(monkeypatch, tmp_path, seen)
    agent.run("Refactor the parsing module so the helpers live in their own file.")

    assert agent.dispatcher._task_mode is None
    # submit_output door: an advice-only completion is accepted.
    result = agent.dispatcher._completion_service.submit(
        {
            "output": {"summary": "Move the helpers into parsing/helpers.py."},
            "completion_bullets": ["Explained the refactor"],
        }
    )
    assert result == {"status": "ok"}
    # finalize door: the shared verifier demands nothing either.
    assert agent.dispatcher.verify_code_evidence({"summary": "advice"}) is None


def test_an_explicit_code_mode_arms_the_contract_on_both_doors(monkeypatch, tmp_path):
    seen: list = []
    agent = _cli_agent(monkeypatch, tmp_path, seen, task_mode="codebase_change")
    agent.run("tidy this up")

    assert agent.dispatcher._task_mode == "codebase_change"
    refusal = agent.dispatcher.verify_code_evidence({"summary": "done"})
    assert refusal is not None
    for field in ("source_files", "entry_points", "test_evidence"):
        assert field in refusal
    result = agent.dispatcher._completion_service.submit(
        {"output": {"summary": "done"}, "completion_bullets": ["Tidied the code"]}
    )
    assert "error" in result and "source_files" in result["error"]


def test_the_detected_fragment_carries_an_advisory_note_and_the_explicit_one_does_not():
    """The fragment must not promise Host verification the turn will not run.
    An explicit selection gets the verified-contract wording (and the registry
    pin above stays byte-for-byte); a detected one is told, honestly, that the
    declarations stay advisory and a misread never blocks completion."""
    explicit = task_mode_prompt(TaskMode.CODEBASE_CHANGE)
    detected = task_mode_prompt(TaskMode.CODEBASE_CHANGE, explicit=False)
    assert explicit == prompts.build("task_mode_codebase_change")
    assert detected.startswith(explicit)
    note = detected[len(explicit) :]
    assert "inferred" in note
    assert "advisory" in note
    assert "never blocks" in note
    assert task_mode_prompt(TaskMode.ANALYSIS_RUN, explicit=False) == ""


# --------------------------------------------------------------------------
# an explicit code-mode CLI run records its cells — and can therefore finish
# --------------------------------------------------------------------------


def _recording_cli_agent(monkeypatch, tmp_path, replies, kernel_results, **kwargs):
    """A real `Agent`/dispatcher/store; scripted provider; a kernel double
    whose per-cell results (with stable ids) we control."""
    from openai4s.agent import loop as loop_mod

    queue = list(kernel_results)

    class _Kernel:
        def __init__(self, *a, **k):
            pass

        def execute(self, *a, **k):
            return dict(queue.pop(0)) if queue else {"stdout": "", "error": None}

        def shutdown(self):
            pass

    calls = {"n": 0}

    def scripted_chat(messages, cfg, **kw):
        del messages, cfg, kw
        index = min(calls["n"], len(replies) - 1)
        calls["n"] += 1
        return {"content": replies[index], "usage": {}}

    monkeypatch.setattr(loop_mod, "Kernel", _Kernel)
    monkeypatch.setattr(loop_mod, "chat", scripted_chat)
    return loop_mod.Agent(
        cfg=_web_cfg(tmp_path),
        max_turns=2,
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
        **kwargs,
    )


def test_an_explicit_code_mode_run_records_cells_under_the_agents_frame(
    monkeypatch, tmp_path
):
    """The blocking defect: a root CLI Agent wrote no execution_log rows, so
    `test_evidence` could NEVER verify and `openai4s run --mode codebase_change`
    was a flag that could not succeed."""
    agent = _recording_cli_agent(
        monkeypatch,
        tmp_path,
        replies=["```python\nprint('3 passed')\n```", "done."],
        kernel_results=[
            {"id": "cell-known-1", "stdout": "3 passed in 0.01s\n", "error": None}
        ],
        task_mode="codebase_change",
    )
    agent.run("tidy the helpers into a module")

    row = agent.dispatcher.store.cell_detail("cell-known-1")
    assert row is not None
    assert row["status"] == "ok"
    assert row["root_frame_id"] == agent.frame_id
    assert row["origin"] == "agent"
    assert row["stdout"] == "3 passed in 0.01s\n"


def test_a_detected_mode_run_stays_unrecorded_like_every_cli_run_before_it(
    monkeypatch, tmp_path
):
    """Recording rides the explicit contract only — an ordinary CLI run keeps
    its historical no-rows behaviour byte for byte."""
    agent = _recording_cli_agent(
        monkeypatch,
        tmp_path,
        replies=["```python\nprint('x')\n```", "done."],
        kernel_results=[{"id": "cell-unrec-1", "stdout": "x\n", "error": None}],
    )
    agent.run("refactor the parsing module so the helpers live in their own file")
    assert agent.dispatcher.store.cell_detail("cell-unrec-1") is None


def test_the_recorded_cell_backs_a_full_circle_codebase_completion(
    monkeypatch, tmp_path
):
    import hashlib

    agent = _recording_cli_agent(
        monkeypatch,
        tmp_path,
        replies=["```python\nprint('2 passed')\n```", "done."],
        kernel_results=[
            {"id": "cell-tests-1", "stdout": "2 passed in 0.02s\n", "error": None}
        ],
        task_mode="codebase_change",
    )
    agent.run("move the helpers into their own module")

    source = tmp_path / "helpers.py"
    source.write_text("def helper(x):\n    return x + 1\n", encoding="utf-8")
    agent.dispatcher.store.save_artifact(
        path=str(source),
        filename="helpers.py",
        content_type="text/x-python",
        size_bytes=source.stat().st_size,
        checksum=hashlib.sha256(source.read_bytes()).hexdigest(),
        frame_id=agent.frame_id,
        root_frame_id=agent.frame_id,
        project_id="default",
    )
    result = agent.dispatcher(
        "submit_output",
        [
            {
                "output": {"summary": "helpers.py owns the shared helpers."},
                "completion_bullets": ["Wrote helpers.py and ran its tests"],
                "source_files": [{"path": "helpers.py"}],
                "entry_points": ["helpers.py"],
                "architecture_summary": "helpers.py owns the shared helpers.",
                "test_evidence": [
                    {
                        "command": "python -m pytest tests/",
                        "producing_cell_id": "cell-tests-1",
                    }
                ],
            }
        ],
    )
    assert result == {"status": "ok"}
    assert agent.dispatcher.last_output is not None
    assert agent.dispatcher.last_output["entry_points"] == ["helpers.py"]


def test_a_codebase_change_run_completes_end_to_end_with_a_real_kernel(
    monkeypatch, tmp_path
):
    """The reviewer's exact scenario, now green: a run that writes the module,
    saves the artifact, runs the test in a real recorded cell, and submits
    naming that cell's REAL id must be accepted — not told the cell it just
    ran never executed."""
    from openai4s.agent import loop as loop_mod

    reply_write = (
        "```python\n"
        "from pathlib import Path\n"
        "Path('convert.py').write_text(\n"
        "    'def convert(value):\\n    return int(value) * 2\\n',\n"
        "    encoding='utf-8',\n"
        ")\n"
        "host.save_artifact('convert.py', 'convert.py')\n"
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('convert', 'convert.py')\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "assert module.convert('3') == 6\n"
        "print('convert smoke: 1 test passed')\n"
        "```"
    )
    reply_submit = (
        "```python\n"
        "rows = host.query(\n"
        '    "SELECT producing_cell_id FROM my_execution_log '
        "WHERE status='ok' ORDER BY cell_seq DESC LIMIT 1\"\n"
        ")\n"
        "cell_id = rows[0]['producing_cell_id']\n"
        "host.submit_output(\n"
        "    {'summary': 'convert.py owns the conversion helper; "
        "its smoke test ran in a recorded cell.'},\n"
        "    ['Wrote convert.py and ran its smoke test'],\n"
        "    source_files=[{'path': 'convert.py'}],\n"
        "    entry_points=['convert.py'],\n"
        "    architecture_summary='convert.py owns the numeric conversion helper.',\n"
        "    test_evidence=[{'command': 'python convert smoke test', "
        "'producing_cell_id': cell_id}],\n"
        ")\n"
        "```"
    )
    replies = [reply_write, reply_submit]
    calls = {"n": 0}

    def scripted_chat(messages, cfg, **kw):
        del messages, cfg, kw
        index = min(calls["n"], len(replies) - 1)
        calls["n"] += 1
        return {"content": replies[index], "usage": {}}

    monkeypatch.setattr(loop_mod, "chat", scripted_chat)
    agent = loop_mod.Agent(
        cfg=_web_cfg(tmp_path),
        max_turns=3,
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
        task_mode="codebase_change",
    )
    result = agent.run("Refactor the conversion helper into its own module file.")

    assert result["stop_reason"] == "submitted", result
    submitted = result["submitted_output"]
    assert submitted["output"]["summary"].startswith("convert.py owns")
    assert submitted["source_files"] == [{"path": "convert.py"}]
    # and the evidence really is durable: the named cell is a stored row
    cell_id = submitted["test_evidence"][0]["producing_cell_id"]
    row = agent.dispatcher.store.cell_detail(cell_id)
    assert row is not None and row["status"] == "ok"
    assert "1 test passed" in (row["stdout"] or "")
