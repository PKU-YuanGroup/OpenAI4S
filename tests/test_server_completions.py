"""Public progress and completion projections never depend on hidden reasoning."""

import re

from openai4s.agent.actions import CodeCell, NativeToolBatch, NativeToolCall
from openai4s.agent.loop import SYSTEM_PROMPT
from openai4s.agent.models import ExecutionOutcome
from openai4s.server.completions import (
    action_narration,
    bookkeeping_calls,
    cancellation_marker,
    completion_message,
    is_progress_narration,
    narration_paths,
    outcome_narration,
    response_language,
)
from openai4s.server.contract import API_ROOT
from openai4s.server.gateway import _GATEWAY_PROMPT_EXTRA


def _call(name: str) -> NativeToolCall:
    return NativeToolCall(
        id="call-1",
        wire_id="call-1",
        name=name,
        ordinal=0,
        raw_arguments='{"secret":"must-not-leak"}',
        arguments={"secret": "must-not-leak"},
    )


def test_action_narration_is_safe_localized_and_hides_raw_arguments():
    text = action_narration(NativeToolBatch((_call("web_search"),)), "zh")

    assert "检索" in text
    assert "must-not-leak" not in text
    assert "secret" not in text
    assert response_language("分析这个结果") == "zh"
    assert response_language("Analyze this result") == "en"


def test_completion_message_projects_summary_bullets_and_real_artifacts():
    text = completion_message(
        {
            "output": {"summary": "已完成真实数据分析。"},
            "completion_bullets": ["生成了结果表", "撰写了分析报告"],
        },
        [
            {"artifact_id": "a-1", "filename": "results.csv"},
            {"artifact_id": "a-2", "filename": "报告.md"},
        ],
        language="zh",
    )

    assert text.startswith("已完成真实数据分析。")
    assert "- 生成了结果表" in text
    assert f"[results.csv]({API_ROOT}/artifacts/a-1)" in text
    assert "%E6%8A%A5%E5%91%8A.md" not in text
    assert f"]({API_ROOT}/artifacts/a-2)" in text
    # Every link the projection emits lives under the versioned root: the
    # gateway answers any other `/api/...` path with a 404.
    targets = re.findall(r"\]\(([^)\s]+)\)", text)
    assert len(targets) == 2
    assert all(target.startswith(API_ROOT + "/") for target in targets), targets


def test_trusted_completion_links_exact_encoded_versions_and_skips_mutable_heads():
    text = completion_message(
        {"output": {"summary": "Delivered verified bytes."}},
        [
            {
                "artifact_id": "mutable-head",
                "latest_version_id": "version/报告 1?#",
                "filename": "报告 [final].csv",
            },
            {"artifact_id": "head-without-version", "filename": "unstable.csv"},
        ],
        trusted_delivery=True,
    )

    assert "/api/v1/artifacts/versions/version%2F%E6%8A%A5%E5%91%8A%201%3F%23" in text
    assert "mutable-head" not in text
    assert "unstable.csv" not in text
    assert "报告 \\[final\\].csv" in text


def test_completion_message_deduplicates_existing_closing_prose():
    text = completion_message(
        {
            "output": {"answer": "The answer is 42."},
            "completion_bullets": ["Computed the answer"],
        },
        previous_text="The answer is 42.\n\nComputed the answer",
        require_fallback=False,
    )

    assert text == ""


def test_completion_message_has_bounded_json_fallback_and_completion_fallback():
    rendered = completion_message(
        {"output": {"metrics": {"accuracy": 0.93}}}, language="en"
    )
    assert "Metrics:\n- accuracy: 0.93" in rendered

    assert (
        completion_message(None, language="zh", require_fallback=True) == "任务已完成。"
    )
    assert completion_message(None, require_fallback=False) == ""


def test_completion_message_renders_scientific_public_fields_as_sections():
    rendered = completion_message(
        {
            "output": {
                "summary": "Compared the measured variants.",
                "findings": ["Variant A scored highest", "Variant C was unstable"],
                "metrics": {"accuracy": 0.93, "n": 120},
                "limitations": ["Single assay batch"],
            },
            "completion_bullets": ["Compared all measured variants"],
        }
    )

    assert rendered.startswith("Compared the measured variants.")
    assert "Key findings:\n- Variant A scored highest" in rendered
    assert "Metrics:\n- accuracy: 0.93" in rendered
    assert "Limitations:\n- Single assay batch" in rendered
    assert '"findings"' not in rendered


def test_a_direct_answer_is_one_paragraph_not_a_report():
    """CHAT-5: "just tell me whether F and p agree" came back as five headed
    sections. A summary with nothing structured and no files is the answer;
    a "Completed work" list under it only restates it."""
    answer = "交互项 F=50.29、p=0.000103，与手工计算完全一致。"
    spec = {
        "output": {"summary": answer},
        "completion_bullets": ["用 statsmodels 复核了交互项"],
    }
    assert completion_message(spec, language="zh") == answer
    english = {
        "output": {"summary": "Yes: F and p agree with the manual ANOVA."},
        "completion_bullets": ["Answered the question"],
    }
    assert completion_message(english) == "Yes: F and p agree with the manual ANOVA."

    # A list that says more than the paragraph is not dropped.
    terse = completion_message(
        {
            "output": {"summary": "Done."},
            "completion_bullets": ["Recomputed the ANOVA with statsmodels"],
        }
    )
    assert terse == "Done.\n\nCompleted work:\n- Recomputed the ANOVA with statsmodels"

    # Files or structured sections make it a report again, in the same order.
    with_file = completion_message(
        spec, [{"artifact_id": "a-1", "filename": "fig3.png"}], language="zh"
    )
    assert with_file.index("完成内容：") < with_file.index("产物：")
    with_findings = completion_message(
        {**spec, "output": {"summary": answer, "findings": ["两种算法一致"]}},
        language="zh",
    )
    assert with_findings.index("关键发现：") < with_findings.index("完成内容：")

    # The model is told the same thing, and to label metrics for a reader.
    from openai4s.agent.finalize import finalize_response_tool_spec

    assert "answer it directly in `summary`" in _GATEWAY_PROMPT_EXTRA
    assert "would only repeat that answer" in _GATEWAY_PROMPT_EXTRA
    assert "plain-language labels" in _GATEWAY_PROMPT_EXTRA
    assert "plain-language labels" in str(finalize_response_tool_spec())


def test_code_outcome_narration_uses_real_error_and_redacts_secrets():
    action = CodeCell("python", "raise RuntimeError('hidden source')")
    failed = ExecutionOutcome(
        observation=(
            "[Observation]\nERROR (cell line 1):\nTraceback...\n"
            "RuntimeError: api_key=very-secret-value"
        )
    )

    text = outcome_narration(action, failed, "en", had_public_prose=True)

    assert "This cell failed" in text
    assert "RuntimeError" in text
    assert "very-secret-value" not in text
    assert "hidden source" not in text


def test_the_failure_headline_is_the_exception_line_not_the_last_line():
    """CHAT-3: the chat said "这个 Cell 执行失败：`^^^^^^^`" because a patsy
    error ends with the formula and a caret line under its exception header."""
    action = CodeCell("python", "smf.ols('lag_h ~ C(rpoS) * C(relA)', data=params)")
    patsy = (
        "Traceback (most recent call last):\n"
        '  File "site-packages/patsy/compat.py", line 40, in call_and_wrap_exc\n'
        "    return f(*args, **kwargs)\n"
        '  File "<string>", line 1, in <module>\n'
        "TypeError: 'Series' object is not callable\n"
        "\n"
        "The above exception was the direct cause of the following exception:\n"
        "\n"
        "Traceback (most recent call last):\n"
        '  File "<kernel:47>", line 7, in <module>\n'
        '    model = smf.ols("lag_h ~ C(rpoS) * C(relA)", data=sub).fit()\n'
        "            ~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
        '  File "site-packages/patsy/compat.py", line 43, in call_and_wrap_exc\n'
        "    raise new_exc from e\n"
        "patsy.PatsyError: Error evaluating factor: TypeError: 'Series' object "
        "is not callable\n"
        "    lag_h ~ C(rpoS) * C(relA)\n"
        "            ^^^^^^^"
    )

    def narrate(error: str, language: str = "zh") -> str:
        outcome = ExecutionOutcome(
            observation=f"[Observation]\nERROR (cell line 7):\n{error}\n"
            "[system] The cell stopped at the first exception."
        )
        return outcome_narration(action, outcome, language)

    zh = narrate(patsy)
    assert "^^^" not in zh and "lag_h ~" not in zh
    assert (
        "`patsy.PatsyError: Error evaluating factor: TypeError: 'Series' object "
        "is not callable`" in zh
    )
    assert "`patsy.PatsyError: Error evaluating factor" in narrate(patsy, "en")

    # No traceback header (a compile error): the last ``Name: message`` line.
    syntax = '  File "<kernel:3>", line 1\n    x = = 1\n        ^\nSyntaxError: invalid syntax'
    assert "`SyntaxError: invalid syntax`" in narrate(syntax)
    # An exception with no message is still the headline.
    bare = (
        "Traceback (most recent call last):\n"
        '  File "<kernel:3>", line 1, in <module>\n    assert ok\n           ^^\n'
        "AssertionError"
    )
    assert "`AssertionError`" in narrate(bare)
    # Nothing recognisable: the last line that is not a caret/underline line.
    r_error = (
        "Error in library(ggplot2) :\n  there is no package called 'ggplot2'\n  ^~~~"
    )
    assert "there is no package called" in narrate(r_error)
    assert "^~~~" not in narrate(r_error)


def test_a_refused_host_call_is_narrated_as_a_refusal_not_a_failure():
    # A denial used to read "这个 Cell 执行失败：RuntimeError: bash: host
    # authorization unavailable; command was not executed": the user's own
    # "no" as a crash, in English internals.
    action = CodeCell("python", "host.bash('ls')")

    def outcome(last_line: str) -> ExecutionOutcome:
        return ExecutionOutcome(
            observation=(
                "[Observation]\nERROR (cell line 1):\nTraceback (most recent call last):\n"
                f'  File "<kernel:3>", line 1, in <module>\n{last_line}'
            )
        )

    declined = outcome(
        "RuntimeError: bash: Permission denied: 不需要定义工具; command was not executed"
    )
    zh = outcome_narration(action, declined, "zh")
    assert "运行出错" not in zh and "RuntimeError" not in zh
    assert "未获批准" in zh and "不需要定义工具" in zh
    en = outcome_narration(action, declined, "en")
    assert "failed" not in en.lower() and "Permission denied" not in en
    assert "not approved" in en and "不需要定义工具" in en

    no_reason = outcome(
        "RuntimeError: host.web_fetch error: Permission denied: denied by user"
    )
    assert "被你拒绝了" in outcome_narration(action, no_reason, "zh")
    assert "declined by you" in outcome_narration(action, no_reason, "en")

    stopped = outcome(
        "RuntimeError: bash: Permission denied: turn cancelled; command was not executed"
    )
    assert outcome_narration(action, stopped, "zh") == ""

    timed_out = outcome(
        "RuntimeError: host.mcp_call error: Permission denied: approval timed out"
    )
    assert "超时" in outcome_narration(action, timed_out, "zh")

    # An OS PermissionError is a real failure and is still narrated as one.
    os_error = outcome("PermissionError: [Errno 13] Permission denied: '/etc/shadow'")
    assert "运行出错" in outcome_narration(action, os_error, "zh")

    for line in (
        outcome_narration(action, declined, "zh"),
        outcome_narration(action, declined, "en"),
        outcome_narration(action, no_reason, "zh"),
        outcome_narration(action, timed_out, "en"),
    ):
        assert is_progress_narration(line), line


def test_code_only_success_has_honest_running_and_actual_output_status():
    action = CodeCell("python", "print('a')\nprint('b')")
    outcome = ExecutionOutcome(
        observation="[Observation]\nstdout:\na\nb\n[usage wall=0.1s cpu=0.1s rss=1kb]"
    )

    running = action_narration(action)
    assert "running it now" in running
    assert "actual output" in running
    assert "print('a')" not in running
    text = outcome_narration(action, outcome, "en", had_public_prose=False)
    assert text == "The cell finished. Output:\n```text\na\nb\n```"
    assert "running this analysis stage" not in text
    # Pre-action prose describes intent, not the execution result, so it must
    # not suppress the short post-Cell status.
    assert "```text\na\nb\n```" in outcome_narration(
        action, outcome, had_public_prose=True
    )


def test_post_cell_status_shows_output_and_never_calls_a_cell_successful():
    """RUN-1: the status said "completed successfully with N stdout line(s);
    the actual output is recorded in the Notebook" 40+ times a session --
    including for a cell whose pip subprocess exited 1. It now shows the first
    lines the cell printed, says the cell *finished*, and flags stderr."""
    action = CodeCell("python", "run()")
    many = "\n".join(f"row {i}" for i in range(1, 11))
    outcome = ExecutionOutcome(
        observation=(
            "[Observation]\nstdout:\n" + many + "\nstderr:\nWARNING: pip exited 1\n"
            "[system] This looks like blocked network access.\n"
            "[usage wall=1s cpu=1s rss=1kb]"
        )
    )
    en = outcome_narration(action, outcome, "en")
    assert "successfully" not in en
    assert en.startswith("The cell finished. First lines of its output:")
    assert "row 6" in en and "row 7" not in en
    assert "(10 lines in total; the full output is in the Notebook.)" in en
    assert "1 stderr line(s), which may contain warnings or errors" in en
    assert "blocked network" not in en  # a [system] hint is not stdout

    zh = outcome_narration(action, outcome, "zh")
    assert "成功" not in zh
    assert zh.startswith("单元已运行结束，输出的前几行：")
    assert "（共 10 行，完整输出在 Notebook。）" in zh
    assert "另有 stderr 1 行" in zh

    quiet = ExecutionOutcome(observation="[Observation]\n(no output)")
    assert outcome_narration(action, quiet, "zh") == "单元已运行结束，没有输出。"

    secret = ExecutionOutcome(
        observation="[Observation]\nstdout:\napi_key=sk-live-123\nok ``` fence"
    )
    shown = outcome_narration(action, secret, "en")
    assert "sk-live-123" not in shown and "api_key=<redacted>" in shown
    assert shown.count("```") == 2  # the output's own fence cannot close ours


def test_agent_prompt_documents_the_delegation_task_status_contract():
    """D5 parent-side guidance: the prompt tells the parent to read the
    machine-readable task_status instead of parsing child prose — additive
    fragment, the pinned literals around it survive verbatim."""
    assert "`task_status` (completed | partial | blocked | failed)" in SYSTEM_PROMPT
    assert "parse the child's prose" in SYSTEM_PROMPT
    assert "anything other than `completed`" in SYSTEM_PROMPT
    assert 'task_status="partial"' in SYSTEM_PROMPT


def test_agent_prompt_never_claims_post_fence_prose_runs_after_submit():
    assert "After it succeeds you may add" not in SYSTEM_PROMPT
    assert "Only prose BEFORE the action fence is user-visible" in SYSTEM_PROMPT
    assert "NEVER `import host`" in SYSTEM_PROMPT
    assert "A foreground Cell is not a native tool call" in SYSTEM_PROMPT
    assert "`run_python_cell`" in SYSTEM_PROMPT
    assert "exact name present in the current tool declarations" in SYSTEM_PROMPT
    assert "must never replace an ordinary foreground Cell" in SYSTEM_PROMPT
    assert "exact native `list_skills`" in SYSTEM_PROMPT
    assert "zero-argument overview returns the exact total count" in SYSTEM_PROMPT
    assert "`collection` id and `offset=0`" in SYSTEM_PROMPT
    assert "returned `next_offset`" in SYSTEM_PROMPT
    assert "`list_dir` lists workspace files only" in SYSTEM_PROMPT
    assert "`host.skills.list()` only inside" in SYSTEM_PROMPT
    assert "1-4 completed" in SYSTEM_PROMPT
    assert "complete repair cell" in SYSTEM_PROMPT
    assert "only the tail" in SYSTEM_PROMPT
    assert "probes local hardware first" in SYSTEM_PROMPT
    assert "ask whether they already have it locally" in SYSTEM_PROMPT
    assert "download weights while that question is unanswered" in SYSTEM_PROMPT
    assert "A staged model asset is not admitted" in SYSTEM_PROMPT
    assert "terminal record succeeds" in SYSTEM_PROMPT
    assert "Missing code or weights is a bring-up condition" in SYSTEM_PROMPT
    assert all(
        key in SYSTEM_PROMPT
        for key in ("summary", "findings", "metrics", "limitations")
    )


def test_gateway_prompt_keeps_native_cells_and_host_rpcs_distinct():
    assert "exact declared native JSON tool" in _GATEWAY_PROMPT_EXTRA
    assert "`host.*` syntax is Python source" in _GATEWAY_PROMPT_EXTRA
    assert "a foreground Cell has no runner function" in _GATEWAY_PROMPT_EXTRA
    assert "exact native `list_skills`" in _GATEWAY_PROMPT_EXTRA
    assert "overview gives the exact total, curated names" in _GATEWAY_PROMPT_EXTRA
    assert "returned `next_offset`" in _GATEWAY_PROMPT_EXTRA
    assert "Only inside a fenced Python Cell use `host.skills.list()`" in (
        _GATEWAY_PROMPT_EXTRA
    )
    assert "do not use `list_dir` or `write_file`" in _GATEWAY_PROMPT_EXTRA
    assert "fall back to `exec_background`" in _GATEWAY_PROMPT_EXTRA


def test_completion_action_narration_does_not_expose_submit_source():
    text = action_narration(
        CodeCell("python", "host.submit_output({'ok': True}, ['Completed it'])")
    )

    assert text == ""
    assert "submit_output" not in text


def test_every_progress_line_this_module_writes_is_recognised():
    """The clean notebook export (NB-1) drops progress lines and keeps the
    model's narration. Recognition lives next to the templates; this walks
    every template so a reworded line cannot silently leak into exports."""
    actions = [
        CodeCell("python", "print(1)"),
        CodeCell("r", "print(1)"),
        NativeToolBatch(calls=(_call("web_search"), _call("web_fetch"))),
        *(
            NativeToolBatch(calls=(_call(name),))
            for name in (
                "web_search",
                "web_fetch",
                "write_file",
                "read_text_file",
                "env_use",
                "something_else",
            )
        ),
    ]
    outcomes = [
        ExecutionOutcome(observation="[Observation]\nstdout:\nok"),
        ExecutionOutcome(observation="[Observation]\n(no output)"),
        ExecutionOutcome(observation="[Observation]\nstderr:\nwarn"),
        ExecutionOutcome(
            observation="[Observation]\nERROR (cell line 1):\nTraceback\nValueError: x"
        ),
    ]
    bookkeeping = [
        ("host.todo_write([])", "{'ok': True, 'count': 0, 'todos': []}"),
        ("host.todo_write([])", "not a literal"),
        ("host.plan_update('s1', 'completed')", "{'ok': True}"),
        ("host.plan_read()", "{'plan': None}"),
        ("host.env.list()", "{'environments': [{'name': 'base'}]}"),
        ("host.env.list()", ""),
        ("host.env.use('lab_env')", "{'ok': True, 'env': {'name': 'lab_env'}}"),
        ("host.env.use('lab_env')", ""),
        (
            "host.env.create('work', ['statsmodels', 'nope'])",
            "{'ok': False, 'installed': ['statsmodels'], 'failed': ['nope'], "
            "'log': ' + statsmodels==0.15.0'}",
        ),
        ("host.env.create('work', ['x'])", "{'ok': False, 'installed': []}"),
        ("host.env.create('work', [])", "{'ok': True, 'installed': []}"),
        ("host.todo_write([])\nhost.env.list()", "{'environments': []}"),
    ]
    for language in ("en", "zh"):
        lines = [action_narration(action, language) for action in actions]
        lines += [
            action_narration(action, language, opening=False) for action in actions
        ]
        lines.append(
            action_narration(NativeToolBatch(calls=(_call("write_todos"),)), language)
        )
        lines += [
            outcome_narration(CodeCell("python", "x"), outcome, language)
            for outcome in outcomes
        ]
        lines += [
            outcome_narration(
                CodeCell("python", code),
                ExecutionOutcome(observation=f"[Observation]\nstdout:\n{echo}"),
                language,
            )
            for code, echo in bookkeeping
        ]
        lines += [cancellation_marker(r, language) for r in ("user", "auto_budget")]
        for line in lines:
            assert line and is_progress_narration(line), line
    for prose in (
        "Ages are right-skewed (median 61).",
        "I am running a sensitivity analysis next because the effect is small.",
        "我先看一下缺失值的分布。",
        "更新了计划：先做 QC，再拟合生长模型。",
        "Installed packages are enough; the fit converged for every well.",
    ):
        assert not is_progress_narration(prose), prose
    # Lines earlier versions wrote are still in stored sessions.
    for stored in (
        "Cell 已运行结束，没有输出。",
        "这个 Cell 执行失败：`ValueError: x`。下一轮会依据真实 traceback 修复。",
        "这个 Cell 里需要批准的操作被你拒绝了，没有执行。智能体会据此调整下一步。",
        "我已经准备好一个 Python Cell，正在执行；真实输出会持续记录到 Notebook。",
    ):
        assert is_progress_narration(stored), stored


def test_bookkeeping_cells_get_one_plain_line_not_their_raw_return_value():
    """CHAT-4: plan updates, environment listings and installs were narrated as
    "Cell 已运行结束，输出：" plus the call's raw return dict -- for an install,
    the interpreter's absolute path included."""

    def narrate(code: str, echo: str, language: str = "zh") -> str:
        outcome = ExecutionOutcome(
            observation=f"[Observation]\nstdout:\n{echo}\n[usage wall=0.1s cpu=0.1s rss=1kb]"
        )
        return outcome_narration(CodeCell("python", code), outcome, language)

    todos = (
        "{'ok': True, 'count': 3, 'todos': ["
        "{'id': 't1', 'content': 'QC', 'status': 'completed', 'priority': 'high'}, "
        "{'id': 't2', 'content': 'fit', 'status': 'completed', 'priority': 'high'}, "
        "{'id': 't3', 'content': 'report', 'status': 'in_progress', 'priority': 'low'}]}"
    )
    plan = 'host.todo_write([{"content": "QC", "status": "completed"}])'
    assert narrate(plan, todos) == "更新了计划（3 步，完成 2 步）。"
    assert narrate(plan, todos, "en") == "Updated the plan (3 steps, 2 done)."

    listing = (
        "{'environments': [{'name': 'base', 'current': True}, {'name': 'lab_env'}, "
        "{'name': 'struct'}, {'name': 'r'}], 'current': 'base', 'recommend': None}"
    )
    assert narrate('host.env.list(["openpyxl"])', listing) == "列出了 4 个环境。"
    assert narrate("print(host.env.list())", listing, "en") == "Listed 4 environments."

    install = (
        "{'ok': True, 'installed': ['statsmodels'], 'failed': [], 'log': 'Using "
        "Python 3.13 environment at: /home/someone/work/.venv\\nInstalled 2 "
        "packages in 54ms\\n + patsy==1.0.3\\n + statsmodels==0.15.0\\n', "
        "'interpreter': '/home/someone/work/.venv/bin/python', 'name': 'base'}"
    )
    for language, expected in (
        ("zh", "已安装 statsmodels 0.15.0。"),
        ("en", "Installed statsmodels 0.15.0."),
    ):
        text = narrate('host.env.create("base", ["statsmodels"])', install, language)
        assert text == expected
        assert "/home/someone" not in text and "interpreter" not in text

    # An echo that cannot be read back (cut by the output budget) still gets
    # a plain line, never the raw text.
    cut = "{'ok': True, 'count': 6, 'todos': [{'id': 't1', 'content': '…"
    assert narrate(plan, cut) == "更新了计划。"
    # Assigned and echoed is the same cell; anything else is real output.
    assert bookkeeping_calls("r = host.env.list()\nr") == ("env.list",)
    assert bookkeeping_calls("r = host.env.list()\nprint(r)") == ("env.list",)
    assert bookkeeping_calls("host.todo_write(todos)\nprint(df.head())") == ()
    assert bookkeeping_calls("host.web_fetch('https://example.org')") == ()
    assert bookkeeping_calls("not python (") == ()
    mixed = narrate("host.todo_write(todos)\nprint(len(df))", "48")
    assert mixed == "单元已运行结束，输出：\n```text\n48\n```"
    # A bookkeeping call that raised is still a failure, said as one.
    failed = ExecutionOutcome(
        observation="[Observation]\nERROR (cell line 1):\nTraceback (most recent call "
        "last):\nRuntimeError: host.plan_update error: no active plan for this session"
    )
    assert "运行出错" in outcome_narration(CodeCell("python", plan), failed, "zh")


def test_a_cell_that_only_registers_an_artifact_gets_one_line_not_its_path():
    """CHAT-4 follow-up: `print(host.save_artifact(...))` was narrated as
    "单元已运行结束，输出：" plus the returned dict, whose `path` is the
    snapshot's absolute location in this machine's data directory."""

    def narrate(code: str, echo: str, language: str = "zh") -> str:
        outcome = ExecutionOutcome(observation=f"[Observation]\nstdout:\n{echo}")
        return outcome_narration(CodeCell("python", code), outcome, language)

    saved = (
        "{'artifact_id': 'a-3d114679ee0d', 'version_id': 'v-4e29182b8516', "
        "'filename': 'fig_growth.png', 'path': "
        "'/home/someone/.openai4s/artifacts/v-e5d72409264e__fig_growth.png', "
        "'content_type': None, 'size_bytes': 472632, 'created_at': 1795167627993}"
    )
    code = 'art = host.save_artifact("fig_growth.png", "fig_growth.png")\nprint(art)'
    assert narrate(code, saved) == "已登记产物 fig_growth.png。"
    assert narrate(code, saved, "en") == "Registered the artifact fig_growth.png."
    assert narrate('host.save_artifact("demo.csv")', "") == "已登记产物。"
    assert narrate('host.save_artifact("demo.csv")', "", "en") == (
        "Registered an artifact."
    )
    # A name that is not one plain path segment is not repeated.
    odd = saved.replace("'fig_growth.png', 'path'", "'../x`y.png', 'path'")
    assert narrate(code, odd) == "已登记产物。"
    for language in ("zh", "en"):
        assert is_progress_narration(narrate(code, saved, language))
        assert is_progress_narration(narrate('host.save_artifact("a")', "", language))
    # The cleaned notebook export still keeps the cell that registered it.
    assert bookkeeping_calls(code) == ()


def test_cell_output_narration_shows_workspace_and_data_dir_paths_relative(
    tmp_path,
):
    """CHAT-4 follow-up: whatever a cell printed, the chat never repeats an
    absolute path under the session workspace or the data directory -- even
    when the line is long enough that the preview cuts it."""
    data_dir = tmp_path / "data"
    workspace = data_dir / "agent-workspaces" / "f-1"
    workspace.mkdir(parents=True)
    paths = narration_paths(workspace, data_dir)
    snapshot = f"{data_dir}/artifacts/v-e5d72409264e__fig_growth.png"
    printed = (
        f"{{'artifact_id': 'a-1', 'filename': 'fig_growth.png', "
        f"'path': '{snapshot}', 'content_type': None, 'size_bytes': 472632}}"
        + " " * 200
        + f"\nwrote {workspace}/fig_growth.png\ncwd {workspace}\n"
        f"{tmp_path}/datafile\n{data_dir}-other/x"
    )
    code = 'art = host.save_artifact("fig_growth.png")\nprint(art)\nshow(art)'
    outcome = ExecutionOutcome(observation=f"[Observation]\nstdout:\n{printed}")

    text = outcome_narration(CodeCell("python", code), outcome, "zh", local_paths=paths)

    assert str(data_dir) + "/" not in text and str(workspace) not in text
    assert "'path': '<data_dir>/artifacts/v-e5d72409264e__fig_growth.png'" in text
    assert "wrote ./fig_growth.png" in text
    assert "cwd .\n" in text
    # Only those two roots: a sibling or a longer name is left alone.
    assert f"{tmp_path}/datafile" in text and f"{data_dir}-other/x" in text

    failed = ExecutionOutcome(
        observation="[Observation]\nERROR (cell line 1):\nTraceback (most recent "
        "call last):\nFileNotFoundError: [Errno 2] No such file or directory: "
        f"'{workspace}/demo.csv'"
    )
    error = outcome_narration(CodeCell("python", "x"), failed, "en", local_paths=paths)
    assert "No such file or directory: './demo.csv'" in error
    assert str(workspace) not in error


def test_mid_task_narration_does_not_read_like_starting_over():
    """CHAT-4: "我先检查相关文件和数据，再…" reappeared mid-task as if the
    analysis had started again, and a failure promised a fix "from the real
    traceback"."""
    read = NativeToolBatch((_call("read_text_file"),))
    search = NativeToolBatch((_call("web_search"),))
    for language, first in (("zh", "先"), ("en", "first")):
        assert first in action_narration(search, language)
        assert first not in action_narration(search, language, opening=False)
    assert "先" in action_narration(read, "zh")
    assert action_narration(read, "zh", opening=False) == "我正在查看相关文件和数据。"
    assert (
        action_narration(read, "en", opening=False)
        == "I am looking at the relevant files and data."
    )

    failed = ExecutionOutcome(
        observation="[Observation]\nERROR (cell line 1):\nTraceback\nValueError: x"
    )
    for language in ("zh", "en"):
        text = outcome_narration(CodeCell("python", "x"), failed, language)
        assert "traceback" not in text.lower() and "下一轮" not in text
        assert "`ValueError: x`" in text


def test_a_truncated_output_reports_its_real_size_not_the_preview_lines():
    """A long stdout reaches the model as head + omission marker + tail; the
    status counted that preview ("160 stdout line(s)" for 3001 lines)."""
    from openai4s.agent.runtime import format_observation

    out = "\n".join(f"row {i} " + "x" * 40 for i in range(3001))
    observation = format_observation(
        {"stdout": out, "stderr": "", "error": None, "cwd": None}
    )
    outcome = ExecutionOutcome(observation=observation)
    en = outcome_narration(CodeCell("python", "x"), outcome, "en")
    assert en.startswith("The cell finished. First lines of its output:")
    assert f"{len(out):,} characters in total" in en
    assert "lines in total" not in en and "omitted" not in en
    zh = outcome_narration(CodeCell("python", "x"), outcome, "zh")
    assert f"共 {len(out):,} 个字符" in zh


def test_the_summary_separates_deliverables_and_reads_metrics_plainly():
    """ART-8: 14 "artifacts" mixed figures and tables with helper scripts and
    copies of inputs, and metrics read as raw keys (cox_tmb_HR_low: 0.42712)."""
    artifacts = [
        {"artifact_id": "a1", "filename": "fig1_survival.png", "version_id": "v-1"},
        {"artifact_id": "a2", "filename": "cox_results.csv", "version_id": "v-2"},
        {"artifact_id": "a3", "filename": "survival_lib.py", "version_id": "v-3"},
        {
            "artifact_id": "a4",
            "filename": "project-inputs/cohort.csv",
            "version_id": "v-4",
        },
    ]
    text = completion_message(
        {
            "output": {
                "summary": "Done.",
                "metrics": {"cox_tmb_HR_low": 0.427123, "n": 120},
            }
        },
        artifacts,
        language="en",
    )
    deliverables = text.split("Artifacts:\n", 1)[1].split("\n\n", 1)[0]
    assert "fig1_survival.png" in deliverables and "cox_results.csv" in deliverables
    assert "survival_lib.py" not in deliverables
    assert "Working files (scripts, logs, input copies; 2):" in text
    assert "[survival_lib.py](" in text and "[project-inputs/cohort.csv](" in text
    assert "- cox tmb HR low: 0.4271" in text
    assert "- n: 120" in text

    zh = completion_message(
        {"output": {"summary": "完成"}}, artifacts[2:], language="zh"
    )
    assert "产物：" not in zh
    assert "过程文件（脚本、日志、输入副本，2 个）：" in zh


def test_the_summary_groups_deliverables_by_kind():
    """ART-1: 26 links in one flat newest-first list; the report was item 3."""
    artifacts = [
        {"artifact_id": f"a{n}", "filename": name, "version_id": f"v-{n}"}
        for n, name in enumerate(
            (
                "Fig2_parameters.svg",
                "qc_log.csv",
                "growth_report.md",
                "Fig1_curves.png",
                "model.pt",
                "scratch_inspect.py",
            )
        )
    ]
    text = completion_message({"output": {"summary": "完成"}}, artifacts, language="zh")
    block = text.split("产物：\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert [line for line in block if not line.startswith("  ")] == [
        "- 报告",
        "- 图",
        "- 表与数据",
        "- 其他",
    ]
    assert block[1].startswith("  - [growth_report.md](")
    # Within a group the order the caller gave is kept.
    assert [line.split("]")[0] for line in block[3:5]] == [
        "  - [Fig2_parameters.svg",
        "  - [Fig1_curves.png",
    ]
    assert "scratch_inspect.py" not in "\n".join(block)
    assert "过程文件（脚本、日志、输入副本，1 个）：" in text

    # One kind only: nothing to tell apart, so no group line.
    flat = completion_message(
        {"output": {"summary": "Done."}}, artifacts[:1] + artifacts[3:4], language="en"
    )
    assert "Artifacts:\n- [Fig2_parameters.svg](" in flat
    assert "- Figures" not in flat


def test_the_summary_never_links_the_platforms_own_spill_files():
    """ART-4: a context-output-*.json spill is the platform's bookkeeping. It is
    stored hidden, but a row that reaches the summary anyway (un-hidden, or an
    older caller) must not be offered as a result or as a working file."""
    text = completion_message(
        {"output": {"summary": "Done."}},
        [
            {"artifact_id": "a1", "filename": "fit.csv", "version_id": "v-1"},
            {
                "artifact_id": "a2",
                "filename": "context-output-0123456789abcdef.json",
                "version_id": "v-2",
            },
        ],
        language="en",
    )
    assert "[fit.csv](" in text
    assert "context-output" not in text
    assert "Working files" not in text
