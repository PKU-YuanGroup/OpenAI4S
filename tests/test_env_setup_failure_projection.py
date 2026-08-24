"""A failed environment install must not project as "ready".

`preinstall.install` reports failure as a structured result —
``{"ok": False, "installed": [], "failed": [{name, error}], "log": ...}`` —
rather than the dispatcher's single-key ``{"error"}`` soft-fail shape. The
activity-step projection only understood the latter, so the "end" event for a
failed ``env_setup`` fell through to the success branch, and with nothing
``installed`` its summary was the bare word "ready". A user watching the Web
activity feed saw "Setting up the empirical-macro-env environment — ready"
directly above a Notebook cell whose raw result said ``ok=false``: two writers,
one truth, disagreeing.

The projection must read the declared ``ok`` flag, carry the real reason (the
``failed`` entry's error, e.g. "No module named pip") onto the card, and mark
the step's status as an error.
"""

from __future__ import annotations

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import _step_end, build_dispatcher

FAILED_INSTALL = {
    "name": "empirical-macro-env",
    "ok": False,
    "installed": [],
    "failed": [{"name": "pandas, statsmodels", "error": "No module named pip"}],
    "log": "/x/.venv/bin/python: No module named pip",
}


def test_step_end_projects_a_declared_failure_as_failed():
    output, summary = _step_end("env_setup", "env", dict(FAILED_INSTALL), True)

    assert summary.startswith("failed"), summary
    assert "No module named pip" in summary
    assert "ready" not in summary
    assert "No module named pip" in str(output.get("error"))


def test_step_end_still_reports_ready_on_an_empty_successful_install():
    result = {"name": "analysis", "ok": True, "installed": [], "failed": [], "log": ""}

    _output, summary = _step_end("env_setup", "env", result, True)

    assert summary == "ready"


def test_step_end_reports_installed_packages_on_success():
    result = {
        "name": "analysis",
        "ok": True,
        "installed": ["pandas"],
        "failed": [],
        "log": "ok",
    }

    _output, summary = _step_end("env_setup", "env", result, True)

    assert summary == "installed pandas"


def test_step_end_falls_back_to_the_log_tail_when_failed_is_empty():
    result = {"ok": False, "installed": [], "failed": [], "log": "resolver exploded"}

    _output, summary = _step_end("env_setup", "env", result, True)

    assert summary.startswith("failed"), summary
    assert "resolver exploded" in summary


@pytest.fixture
def dispatcher(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    dispatcher = build_dispatcher(cfg, frame_id="f-env", workspace=workspace)
    dispatcher.store.set_permission_rule(
        scope="conversation",
        scope_id="f-env",
        tool="env_setup",
        pattern="*",
        decision="allow",
    )
    return dispatcher


def test_a_failed_env_setup_step_carries_the_reason_end_to_end(dispatcher, monkeypatch):
    """Through the real dispatcher: the activity step a Web session renders."""

    from openai4s.kernel import preinstall

    monkeypatch.setattr(
        preinstall, "install", lambda packages, **kw: dict(FAILED_INSTALL)
    )
    steps: list[dict] = []
    dispatcher.on_step = steps.append

    result = dispatcher(
        "env_setup",
        [{"name": "empirical-macro-env", "packages": ["pandas", "statsmodels"]}],
    )

    assert result["ok"] is False  # the structured result itself is unchanged
    end = next(step for step in steps if step.get("phase") == "end")
    assert end["status"] == "error"
    assert end["summary"].startswith("failed"), end["summary"]
    assert "No module named pip" in end["summary"]
    assert "ready" not in end["summary"]


def test_a_successful_env_setup_step_still_ends_done(dispatcher, monkeypatch):
    from openai4s.kernel import preinstall

    monkeypatch.setattr(
        preinstall,
        "install",
        lambda packages, **kw: {
            "ok": True,
            "installed": list(packages),
            "failed": [],
            "log": "ok",
        },
    )
    steps: list[dict] = []
    dispatcher.on_step = steps.append

    result = dispatcher("env_setup", [{"packages": ["tabulate"]}])

    assert result["ok"] is True
    end = next(step for step in steps if step.get("phase") == "end")
    assert end["status"] == "done"
    assert end["summary"] == "installed tabulate"
