"""Public progress and completion projections never depend on hidden reasoning."""

from openai4s.agent.actions import CodeCell, NativeToolBatch, NativeToolCall
from openai4s.agent.loop import SYSTEM_PROMPT
from openai4s.agent.models import ExecutionOutcome
from openai4s.server.completions import (
    action_narration,
    completion_message,
    outcome_narration,
    response_language,
)


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
    assert "[results.csv](/api/artifacts/a-1)" in text
    assert "%E6%8A%A5%E5%91%8A.md" not in text
    assert "](/api/artifacts/a-2)" in text


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

    assert completion_message(None, language="zh", require_fallback=True) == "任务已完成。"
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
    assert "2 stdout line(s)" in text
    assert "running this analysis stage" not in text
    # Pre-action prose describes intent, not the execution result, so it must
    # not suppress the short post-Cell status.
    assert "2 stdout line(s)" in outcome_narration(
        action, outcome, had_public_prose=True
    )


def test_agent_prompt_never_claims_post_fence_prose_runs_after_submit():
    assert "After it succeeds you may add" not in SYSTEM_PROMPT
    assert "Only prose BEFORE the action fence is user-visible" in SYSTEM_PROMPT
    assert "NEVER `import host`" in SYSTEM_PROMPT
    assert "1-4 completed" in SYSTEM_PROMPT
    assert "complete repair cell" in SYSTEM_PROMPT
    assert "only the tail" in SYSTEM_PROMPT
    assert all(
        key in SYSTEM_PROMPT
        for key in ("summary", "findings", "metrics", "limitations")
    )


def test_completion_action_narration_does_not_expose_submit_source():
    text = action_narration(
        CodeCell("python", "host.submit_output({'ok': True}, ['Completed it'])")
    )

    assert text == ""
    assert "submit_output" not in text
