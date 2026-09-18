"""`openai4s run --auto`: autonomous preset plus a real post-run verdict."""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest

from openai4s.agent.loop import (
    AUTO_RUN_ENVIRONMENT,
    enable_auto_run_environment,
    review_cli_result,
)
from openai4s.config import Config, LLMConfig, RoadmapFeatureFlags


def test_auto_sets_the_autonomous_environment_without_overruling_an_operator():
    """--auto asks for autonomous; it does not overwrite an explicit choice."""

    environ: dict[str, str] = {}
    applied = enable_auto_run_environment(environ)
    assert applied == dict(AUTO_RUN_ENVIRONMENT)
    assert environ["OPENAI4S_AUTO_MODE"] == "autonomous"

    chosen = {"OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT": "0"}
    applied = enable_auto_run_environment(chosen)
    assert "OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT" not in applied
    assert chosen["OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT"] == "0"


def test_auto_is_not_a_blanket_grant():
    """The flag's whole claim is that it is not full access."""

    assert "OPENAI4S_UNATTENDED_APPROVAL" not in AUTO_RUN_ENVIRONMENT
    assert AUTO_RUN_ENVIRONMENT["OPENAI4S_AUTO_MODE"] == "autonomous"


def _cfg():
    return Config(
        roadmap_features=RoadmapFeatureFlags(stage3_scientific_review_shadow=True)
    )


def _reply(payload):
    def chat(*_args, **_kwargs):
        return {"content": json.dumps(payload), "usage": {}}

    return chat


@pytest.fixture
def run_store(tmp_path):
    """A real Store and the root frame a CLI run records its evidence under."""

    from openai4s.store import Store

    store = Store(tmp_path / "review.db")
    try:
        yield store, store.new_frame(kind="turn")
    finally:
        store.close()


def test_a_clean_answer_reports_verified(run_store):
    store, frame = run_store
    review = review_cli_result(
        "what is 6*7",
        {"final_message": "6 multiplied by 7 is 42.", "submitted_output": None},
        cfg=_cfg(),
        store=store,
        root_frame_id=frame,
        chat_call=_reply({"verdict": "pass", "summary": "ok", "findings": []}),
    )
    assert review["terminal"] == "verified"
    assert review["unverified"] is False


def test_findings_report_issues_not_verified(run_store):
    store, frame = run_store
    review = review_cli_result(
        "count the rows",
        {"final_message": "There are 100 rows.", "submitted_output": None},
        cfg=_cfg(),
        store=store,
        root_frame_id=frame,
        chat_call=_reply(
            {
                "verdict": "issues",
                "summary": "wrong",
                "findings": [
                    {
                        "severity": "high",
                        "category": "claim_mismatch",
                        "claim_ref": "100 rows",
                        "evidence_refs": ["source:candidate_answer"],
                        "reproduction": "the table has 97",
                    }
                ],
            }
        ),
    )
    assert review["terminal"] == "completed_with_issues"
    assert review["unverified"] is True
    assert review["findings"][0]["severity"] == "high"


def test_a_reviewer_that_fails_is_unavailable_not_a_pass():
    def boom(*_args, **_kwargs):
        raise RuntimeError("provider down")

    review = review_cli_result(
        "anything",
        {"final_message": "an answer", "submitted_output": None},
        cfg=_cfg(),
        chat_call=boom,
    )
    assert review["terminal"] == "review_unavailable"
    assert review["unverified"] is True


def test_the_cli_run_carries_an_identity_so_provenance_is_not_flagged(run_store):
    """Four blank ids read as missing provenance and produced a finding about
    the harness rather than the answer."""

    store, frame = run_store
    seen: dict[str, object] = {}

    def capture(messages, *_args, **_kwargs):
        seen["packet"] = messages[-1]["content"]
        return {"content": json.dumps({"verdict": "pass", "findings": []}), "usage": {}}

    review = review_cli_result(
        "q",
        {"final_message": "a"},
        cfg=_cfg(),
        store=store,
        root_frame_id=frame,
        chat_call=capture,
    )
    assert review["terminal"] == "verified"
    assert '"root_frame_id": ""' not in str(seen["packet"])
    packet = _packet(seen["packet"])
    assert packet["identity"]["root_frame_id"] == frame
    assert packet["environment"]["runtime"] == "cli"


def _packet(content) -> dict:
    return json.loads(str(content).split("\n", 1)[1])


def test_a_review_with_no_execution_evidence_is_never_verified():
    """The fail-open half: with nothing to read the run from, a lenient
    reviewer's pass used to report `verified` over an answer no evidence
    backed. Missing evidence is an omission, and an omission is not a pass."""

    seen: dict[str, object] = {}

    def lenient(messages, *_args, **_kwargs):
        seen["packet"] = messages[-1]["content"]
        return {"content": json.dumps({"verdict": "pass", "findings": []}), "usage": {}}

    review = review_cli_result(
        "compute r", {"final_message": "r = 0.9990"}, cfg=_cfg(), chat_call=lenient
    )
    assert review["terminal"] != "verified"
    assert review["unverified"] is True
    packet = _packet(seen["packet"]) if "packet" in seen else None
    if packet is not None:
        assert packet["complete"] is False
        assert any(
            item.get("kind") == "execution_evidence_unavailable"
            for item in packet["omissions"]
        )


def _llm_cfg(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )


def test_an_auto_run_hands_the_reviewer_the_cells_it_executed(
    tmp_path, monkeypatch, capsys
):
    """The live defect: a correct one-cell run was reviewed from a packet with
    `cells: []`, so the reviewer flagged the task's own instructions as
    unbacked claims. `--auto` must record the run's cells and hand the
    reviewer their code and output."""

    from openai4s import scientific_reviewer
    from openai4s.agent import loop as loop_mod

    for key in AUTO_RUN_ENVIRONMENT:
        # Registered with monkeypatch so the process environment `--auto`
        # widens is restored after the test.
        monkeypatch.setenv(key, "")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    cli = importlib.import_module("openai4s.cli.main")
    monkeypatch.setattr(cli, "get_config", lambda: _llm_cfg(tmp_path))
    replies = [
        "```python\nr = 0.9990\nprint(f'r={r:.4f}')\n```",
        "```python\nhost.submit_output({'summary': 'Pearson r = 0.9990'}, "
        "['Computed the Pearson correlation'])\n```",
    ]

    def agent_chat(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        return {"content": replies.pop(0) if replies else "Done.", "usage": {}}

    seen: dict[str, object] = {}

    def reviewer_chat(messages, *_args, **_kwargs):
        seen["packet"] = messages[-1]["content"]
        return {
            "content": json.dumps({"verdict": "pass", "summary": "ok", "findings": []}),
            "usage": {},
        }

    monkeypatch.setattr(loop_mod, "chat", agent_chat)
    monkeypatch.setattr(scientific_reviewer, "chat", reviewer_chat)

    status = cli.cmd_run(
        SimpleNamespace(
            task="Compute the Pearson correlation and report r.",
            json=True,
            verbose=False,
            mode=None,
            auto=True,
            allow_test_command=None,
        )
    )
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{") :])

    assert status == 0
    assert payload["stop_reason"] == "submitted", payload
    packet = _packet(seen["packet"])
    cells = packet["cells"]
    assert cells, packet
    printed = [cell for cell in cells if "r=0.9990" in (cell.get("stdout") or "")]
    assert printed, cells
    assert "print(f'r={r:.4f}')" in printed[0]["source"]
    assert f"cell:{printed[0]['cell_id']}" in {
        ref["ref_id"] for ref in packet["evidence_refs"]
    }
    assert packet["complete"] is True
    assert payload["auto_mode"]["terminal"] == "verified"


def test_a_tool_only_run_hands_the_reviewer_its_tool_ledger(tmp_path, monkeypatch):
    from openai4s.agent import loop as loop_mod

    replies = [
        ("list_dir", {"path": "."}),
        (
            "finalize_response",
            {
                "summary": "The workspace listing was read.",
                "completion_bullets": ["Listed the workspace"],
            },
        ),
    ]

    def control_chat(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        name, arguments = replies.pop(0)
        call = {
            "id": f"call-{name}",
            "wire_id": f"wire-{name}",
            "name": name,
            "ordinal": 0,
            "raw_arguments": json.dumps(arguments),
            "arguments": arguments,
            "parse_error": None,
            "provider_meta": {"provider": "test"},
        }
        return {
            "content": "",
            "tool_calls": [call],
            "assistant_message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [call],
            },
        }

    monkeypatch.setattr(loop_mod, "chat", control_chat)
    agent = loop_mod.Agent(
        cfg=_llm_cfg(tmp_path),
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
        record_cells=True,
    )
    result = agent.run("List the workspace.")
    assert result["stop_reason"] == "submitted", result

    seen: dict[str, object] = {}

    def capture(messages, *_args, **_kwargs):
        seen["packet"] = messages[-1]["content"]
        return {"content": json.dumps({"verdict": "pass", "findings": []}), "usage": {}}

    review_cli_result(
        "List the workspace.",
        result,
        cfg=_cfg(),
        store=agent.dispatcher.store,
        root_frame_id=agent.frame_id,
        chat_call=capture,
    )
    ledger = _packet(seen["packet"])["tool_ledger"]
    assert [item["title"] for item in ledger] == ["list_dir"]
    assert ledger[0]["status"] == "done"


@pytest.mark.parametrize("record_cells", [False, True], ids=["unrecorded", "recorded"])
def test_executed_cells_missing_from_the_record_are_declared_not_complete(
    tmp_path, monkeypatch, record_cells
):
    """A caller that hands the reviewer a Store and frame but never armed the
    cell recorder has execution attempts with no `execution_log` rows. That
    packet would read `complete` with `cells: []` -- the fail-open the adapter
    closes -- so the gap is a `cells_unrecorded` omission and never
    `verified`. The recorded control proves the omission tracks the gap."""

    from openai4s.agent import loop as loop_mod

    class _Kernel:
        def __init__(self, *a, **k):
            pass

        def execute(self, *a, **k):
            return {"stdout": "r=0.9990\n", "error": None}

        def shutdown(self):
            pass

    replies = ["```python\nprint('r=0.9990')\n```", "r = 0.9990."]

    def agent_chat(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        return {"content": replies.pop(0) if replies else "Done.", "usage": {}}

    monkeypatch.setattr(loop_mod, "Kernel", _Kernel)
    monkeypatch.setattr(loop_mod, "chat", agent_chat)
    agent = loop_mod.Agent(
        cfg=_llm_cfg(tmp_path),
        max_turns=2,
        use_skills=False,
        allow_delegate=False,
        workspace=str(tmp_path),
        record_cells=record_cells,
    )
    result = agent.run("Compute r.")
    store = agent.dispatcher.store
    assert store.list_execution_attempts(root_frame_id=agent.frame_id), result

    seen: dict[str, object] = {}

    def lenient(messages, *_args, **_kwargs):
        seen["packet"] = messages[-1]["content"]
        return {"content": json.dumps({"verdict": "pass", "findings": []}), "usage": {}}

    review = review_cli_result(
        "Compute r.",
        result,
        cfg=_cfg(),
        store=store,
        root_frame_id=agent.frame_id,
        chat_call=lenient,
    )
    packet = _packet(seen["packet"]) if "packet" in seen else None
    gaps = [
        item
        for item in (packet or {}).get("omissions", [])
        if item.get("kind") == "cells_unrecorded"
    ]
    if record_cells:
        assert packet is not None and not gaps and packet["complete"] is True
        assert review["terminal"] == "verified"
    else:
        assert review["terminal"] != "verified"
        assert review["unverified"] is True
        assert packet is not None and packet["complete"] is False, packet
        assert [gap.get("count") for gap in gaps] == [1], packet["omissions"]
