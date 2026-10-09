"""What `GET /frames/{fid}/session/export` answers when the package refuses.

`SessionPackageService.export` is a fail-closed boundary: state it cannot
package faithfully raises `SessionPackageError` instead of being published in
part. Import and share translated that error; the export route did not, so a
deliberate refusal fell through to the dispatcher's catch-all and answered
`500 internal_error` with its reason withheld. It is now `409
session_not_exportable` carrying the refusal's own message.

Everything here drives `_route`, not `_api`: a raised error is turned into a
response in `_route`, so a test that calls `_api` and catches the exception
says nothing about the status a client receives.

The last test pins why the first refusal below is not a production state: a
real turn writes its `user` action group before `begin_turn_run` starts the
Auto Run, so even a turn that ends with a sole `finalize_response` exports.
"""

from __future__ import annotations

import io
import json
import zipfile

from openai4s.config import AutoModeConfig, Config, LLMConfig, RoadmapFeatureFlags
from openai4s.llm import normalize_usage
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.storage.snapshots import revert_recovery_setting_key

_PACKAGE_TYPE = "application/vnd.openai4s.session+zip"


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


def _runner(tmp_path, **config) -> gateway_mod.SessionRunner:
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        **config,
    )
    return gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)


def _export(runner: gateway_mod.SessionRunner, frame_id: str) -> dict:
    """The real dispatcher with only its byte sink replaced."""

    handler = object.__new__(gateway_mod.make_handler(runner.cfg, runner.hub, runner))
    sent: dict = {}

    def _send(code, body, ctype, extra=None, security=None):
        sent.update(code=code, body=body, ctype=ctype)

    handler._send = _send
    handler.command = "GET"
    handler.path = f"/api/v1/frames/{frame_id}/session/export"
    handler.headers = {
        "Content-Length": "0",
        local_auth.TOKEN_HEADER: local_auth.read_token(runner.cfg.data_dir) or "",
    }
    handler._route("GET")
    return sent


def _package(body: bytes) -> dict[str, dict]:
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        return {
            name: json.loads(archive.read(name))
            for name in ("ledger.json", "review.json")
        }


def _session(runner: gateway_mod.SessionRunner) -> tuple[str, str]:
    project_id = runner.store.create_project(name="export refusal")["project_id"]
    root = runner.create_session(project_id)
    runner.store.ensure_session_branch(root_frame_id=root, branch_id=root)
    return project_id, root


def test_an_auto_run_whose_turn_is_not_in_the_ledger_is_a_409(tmp_path):
    runner = _runner(tmp_path)
    try:
        _project_id, root = _session(runner)
        # The Store accepts a run for any turn id; the package refuses to
        # publish one its own ledger cannot resolve.
        runner.store.start_auto_mode_run(
            run_id="auto-orphan",
            idempotency_key="auto-orphan:start",
            root_frame_id=root,
            branch_id=root,
            turn_id="turn-source",
            execution_id="execution-orphan",
            mode="review_only",
            selection={
                "preset": "off",
                "result_review_mode": "review_only",
                "approvals_reviewer": "user",
            },
            budgets={},
            owner_instance_id="export-refusal-test",
        )
        runner.store.terminate_auto_mode_run(
            "auto-orphan",
            idempotency_key="auto-orphan:terminal",
            status="review_unavailable",
            reason="reviewer_inference_failed",
            stop_reason="review_unavailable",
        )

        refused = _export(runner, root)

        assert refused["code"] == 409
        body = json.loads(refused["body"])
        assert body["code"] == "session_not_exportable"
        assert body["error"] == "Auto Mode turn references an unknown identity"
        assert body["status"] == 409
        assert body["request_id"]
        # The export held the session's FIFO lease when it refused.
        execution = runner.executions.snapshot(root)
        assert (execution["active_count"], execution["queued_count"]) == (0, 0)

        # The refusal is about that one missing anchor: give the turn its
        # group and the same session exports, run included.
        runner.store.append_action_group(
            root_frame_id=root,
            branch_id=root,
            turn_id="turn-source",
            kind="user",
            assistant_message={"role": "user", "content": "Review this"},
        )
        exported = _export(runner, root)

        assert exported["code"] == 200
        assert exported["ctype"] == _PACKAGE_TYPE
        runs = _package(exported["body"])["review.json"]["auto_mode"]["runs"]
        assert [run["turn_id"] for run in runs] == ["turn-source"]
    finally:
        runner.close()


def test_a_revert_awaiting_recovery_is_a_409(tmp_path):
    runner = _runner(tmp_path)
    try:
        _project_id, root = _session(runner)
        # What a daemon that dies between a revert's barrier and its outcome
        # leaves behind. Recovery clears it; export must not publish around it.
        runner.store.set_setting(
            revert_recovery_setting_key(root),
            json.dumps(
                {
                    "schema_version": 1,
                    "state": "recovery_required",
                    "operation_id": "so-interrupted",
                    "branch_id": root,
                }
            ),
        )

        refused = _export(runner, root)

        assert refused["code"] == 409
        body = json.loads(refused["body"])
        assert body["code"] == "session_not_exportable"
        assert body["error"] == (
            "session workspace revert requires recovery before export"
        )
    finally:
        runner.close()


def test_a_real_auto_mode_turn_ended_by_finalize_response_exports(
    tmp_path, monkeypatch
):
    runner = _runner(
        tmp_path,
        max_turns=3,
        roadmap_features=RoadmapFeatureFlags(stage2_auto_run_storage=True),
        # Guardian review owns the turn, so `begin_turn_run` starts the Auto
        # Run before the model's first action: the earliest a run can exist.
        auto_mode=AutoModeConfig(
            enabled=False,
            result_review_mode="off",
            approvals_reviewer="auto_review",
            deployment_explicit=True,
            deployment_explicit_fields=("approvals_reviewer",),
        ),
    )
    finalize = {
        "id": "finalize-1",
        "wire_id": "finalize-1",
        "name": "finalize_response",
        "ordinal": 0,
        "raw_arguments": json.dumps(
            {"summary": "Done.", "completion_bullets": ["Answered"]}
        ),
        "arguments": {"summary": "Done.", "completion_bullets": ["Answered"]},
        "parse_error": None,
        "provider_meta": {"provider": "test"},
    }

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del messages, cfg, on_delta, kwargs
        return {
            "content": "",
            "tool_calls": [finalize],
            "assistant_message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [finalize],
            },
            # Attested as `chat()` attests it; the Auto Budget refuses a
            # reply whose token spend it cannot measure.
            "usage": normalize_usage(
                {"prompt_tokens": 12, "completion_tokens": 4}, "chatgpt"
            ),
        }

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *args, **kw: None)
    try:
        project_id, root = _session(runner)

        result = runner.run_message(root, project_id, "answer in one line")
        exported = _export(runner, root)

        assert result["status"] == "completed"
        assert exported["code"] == 200
        package = _package(exported["body"])
        (run,) = package["review.json"]["auto_mode"]["runs"]
        kinds = [
            group["kind"]
            for group in package["ledger.json"]["groups"]
            if group["turn_id"] == run["turn_id"]
            and group["branch_id"] == run["branch_id"]
        ]
        assert kinds == ["user", "finalize", "terminal"]
    finally:
        runner.close()
