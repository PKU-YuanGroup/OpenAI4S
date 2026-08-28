"""Integration contracts for the AgentEngine-backed Web session runner.

These tests keep the concrete kernel offline.  They exercise the composition
boundary in ``SessionRunner._loop``: native control tools, cancellation, plan
mode, environment switches, and typed-delta projection onto the existing Web
event protocol.
"""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import openai4s.agent.loop as loop_mod
import openai4s.kernel.readiness as readiness_mod
from openai4s.agent.delegation import DelegationError
from openai4s.config import Config, LLMConfig, RoadmapFeatureFlags
from openai4s.server import gateway as gateway_mod
from openai4s.server.execution_views import ExecutionViewService
from openai4s.storage.snapshots import revert_recovery_setting_key


class _Hub:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emitter(self, root_frame_id: str):
        def emit(event: dict) -> None:
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id: str, event: dict) -> None:
        event.setdefault("root_frame_id", root_frame_id)
        self.events.append(event)


def _cfg(tmp_path, *, max_turns: int = 3, stage1: bool = False) -> Config:
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=max_turns,
        roadmap_features=RoadmapFeatureFlags(stage1_trusted_delivery=stage1),
    )


def _native_call(
    call_id: str,
    name: str,
    arguments: dict,
    *,
    ordinal: int = 0,
) -> dict:
    return {
        "id": call_id,
        "wire_id": call_id,
        "name": name,
        "ordinal": ordinal,
        "raw_arguments": json.dumps(arguments, separators=(",", ":")),
        "arguments": arguments,
        "parse_error": None,
        "provider_meta": {"provider": "test"},
    }


def _native_reply(content: str, calls: list[dict]) -> tuple[dict, dict]:
    assistant = {
        "role": "assistant",
        "content": content,
        "tool_calls": calls,
    }
    return (
        {
            "content": content,
            "usage": {},
            "tool_calls": calls,
            "assistant_message": assistant,
        },
        assistant,
    )


def _prepare_message_runner(monkeypatch, tmp_path, dispatcher):
    cfg = _cfg(tmp_path)
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    runner.store.update_frame(frame_id, name="Existing test session")

    def ensure_runtime(state):
        state.dispatcher = dispatcher
        state.messages = [{"role": "system", "content": "sys"}]
        return dispatcher

    monkeypatch.setattr(runner, "_ensure_runtime", ensure_runtime)
    monkeypatch.setattr(runner, "_spawn_title_summary", lambda *args, **kwargs: None)
    return runner, hub, frame_id


def test_native_file_control_calls_create_versioned_artifacts(tmp_path):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    events = []
    target = state.workspace / "analysis.md"

    def write_first():
        target.write_text("first", encoding="utf-8")
        return "ok", True

    def write_second():
        target.write_text("second", encoding="utf-8")
        return "ok", True

    first = runner._invoke_control_with_artifacts(
        state,
        SimpleNamespace(name="write_file"),
        events.append,
        write_first,
    )
    assert first == ("ok", True)
    artifact = runner.store.artifact_by_filename("analysis.md", frame_id, strict=True)
    assert artifact is not None
    first_version = artifact["latest_version_id"]

    second = runner._invoke_control_with_artifacts(
        state,
        SimpleNamespace(name="edit_file"),
        events.append,
        write_second,
    )
    assert second == ("ok", True)

    artifact = runner.store.artifact_by_filename("analysis.md", frame_id, strict=True)
    versions = runner.store.list_versions(artifact["artifact_id"])
    assert len(versions) == 2
    assert artifact["latest_version_id"] != first_version
    assert (
        Path(runner.store.version_meta(first_version)["snapshot_path"]).read_text(
            encoding="utf-8"
        )
        == "first"
    )
    assert any(event.get("type") == "artifact_created" for event in events)


def test_trusted_native_write_fails_with_retry_veto_when_capture_fails(
    tmp_path, monkeypatch
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    target = state.workspace / "uncaptured.txt"

    def write_file():
        target.write_text("side effect happened", encoding="utf-8")
        return "tool-success", True

    monkeypatch.setattr(
        runner.artifacts,
        "capture",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected capture failure")
        ),
    )

    with pytest.raises(RuntimeError, match="capture failed") as failure:
        runner._invoke_control_with_artifacts(
            state,
            SimpleNamespace(name="write_file"),
            lambda _event: None,
            write_file,
        )

    assert target.read_text(encoding="utf-8") == "side effect happened"
    assert failure.value.output_committed is True
    assert (
        runner.store.artifact_by_filename("uncaptured.txt", frame_id, strict=True)
        is None
    )


@pytest.mark.parametrize("explicit_save", [False, True])
def test_delegated_child_write_is_never_recaptured_as_parent_production(
    tmp_path, monkeypatch, explicit_save
):
    """A shared workspace must not turn a child's write into the parent's.

    This drives the real Web delegation composition and a real local Kernel.
    The parent takes the same before/after snapshots used around its Cell;
    between them a delegated Agent writes the file.  The explicit-save variant
    also proves that the parent's later sweep does not add a false parent
    capture observation to the child's already durable version.
    """

    runner = gateway_mod.SessionRunner(_cfg(tmp_path, max_turns=3, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    replies = []

    def fake_chat(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        replies.append(len(replies))
        if len(replies) == 1:
            save = (
                "host.save_artifact('delegated.txt', 'delegated.txt')\n"
                if explicit_save
                else ""
            )
            return {
                "content": (
                    "```python\n"
                    "from pathlib import Path\n"
                    "Path('delegated.txt').write_text('child bytes', encoding='utf-8')\n"
                    f"{save}"
                    "```"
                ),
                "tool_calls": [],
            }
        return {
            "content": (
                "```python\n"
                "host.submit_output({'summary': 'child done'}, ['wrote child file'])\n"
                "```"
            ),
            "tool_calls": [],
        }

    monkeypatch.setattr(loop_mod, "chat", fake_chat)
    monkeypatch.setattr(
        readiness_mod,
        "standard_profile_readiness",
        lambda **_kwargs: {"ready": True},
    )

    try:
        runner._ensure_runtime(state)
        delegated = state.delegation_runner
        assert delegated is not None
        parent_before = runner.artifacts.snapshot(state.workspace)
        child_result = delegated({"request": "write the delegated result"})
        assert child_result["stop_reason"] == "submitted"
        child_frame_id = child_result["frame_id"]

        runner.artifacts.capture(
            state,
            1,
            "parent-cell",
            parent_before,
            lambda _event: None,
            language="python",
        )

        artifact = runner.store.artifact_by_filename(
            "delegated.txt", frame_id, strict=True
        )
        assert artifact is not None
        versions = runner.store.list_versions(artifact["artifact_id"])
        assert len(versions) == 1
        version = runner.store.version_meta(versions[0]["version_id"])
        assert version["frame_id"] == child_frame_id
        assert version["producing_cell_id"] != "parent-cell"
        observations = runner.store.list_artifact_capture_observations(
            version_id=version["version_id"]
        )
        # The explicit save and the child's own sweep are two halves of one
        # Cell transaction. The observation repository merges them rather than
        # fabricating two captures by the same child Cell.
        assert len(observations) == 1
        assert {row["frame_id"] for row in observations} == {child_frame_id}
        assert "parent-cell" not in {row["producing_cell_id"] for row in observations}
        # The child Cell is durably recorded under its own delegate frame:
        # frame_id = root_frame_id = child, origin "delegate" — reachable via
        # cell_detail/frame_detail, never via the parent Notebook.
        recorded = runner.store.cell_detail(version["producing_cell_id"])
        assert recorded is not None, "the delegated child Cell was not recorded"
        assert recorded["frame_id"] == child_frame_id
        assert recorded["root_frame_id"] == child_frame_id
        assert recorded["origin"] == "delegate"
        assert recorded["status"] == "ok"
        assert "delegated.txt" in recorded["code"]
        assert (
            runner.store.list_cells(frame_id) == []
        ), "a child cell flattened into the parent session's Notebook log"
        lineage = ExecutionViewService(
            store=runner.store,
            format_timestamp=lambda value: str(value) if value is not None else None,
        ).artifact_lineage(artifact["artifact_id"])
        assert lineage["producer"] == {
            "kind": "cell",
            "frame_id": child_frame_id,
            "frame_kind": "delegate",
            "producing_cell_id": version["producing_cell_id"],
            "cell_recorded": True,
        }
        # No root-Notebook "cell" interaction is fabricated for a delegated
        # producer — the UI would link it to a root cell that does not exist.
        assert [item["kind"] for item in lineage["interactions"]] == ["save"]
        assert lineage["capture_observations"][0]["frame_id"] == child_frame_id
        assert lineage["capture_observations"][0]["frame_kind"] == "delegate"
        assert lineage["capture_observations"][0]["cell_recorded"] is True
        assert lineage["capture_observations"][0]["cell_index"] == 1
        assert lineage["capture_observations"][0]["language"] == "python"
    finally:
        runner.close()


def test_delegated_native_write_is_captured_under_the_child_frame(
    tmp_path, monkeypatch
):
    """A child's native writer uses the same producer boundary as its Cell."""

    runner = gateway_mod.SessionRunner(_cfg(tmp_path, max_turns=3, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    replies = []

    def fake_chat(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        replies.append(len(replies))
        if len(replies) == 1:
            reply, _assistant = _native_reply(
                "Writing the delegated result.",
                [
                    _native_call(
                        "child-write",
                        "write_file",
                        {
                            "path": "delegated-native.txt",
                            "content": "native child bytes",
                        },
                    )
                ],
            )
            return reply
        reply, _assistant = _native_reply(
            "",
            [
                _native_call(
                    "child-final",
                    "finalize_response",
                    {
                        "summary": "child done",
                        "completion_bullets": ["wrote child file"],
                    },
                )
            ],
        )
        return reply

    monkeypatch.setattr(loop_mod, "chat", fake_chat)
    runner.store.set_permission_rule(
        scope="conversation",
        scope_id=frame_id,
        tool="write_file",
        pattern="delegated-native.txt",
        decision="allow",
    )

    try:
        runner._ensure_runtime(state)
        delegated = state.delegation_runner
        assert delegated is not None
        parent_before = runner.artifacts.snapshot(state.workspace)
        child_result = delegated({"request": "write with the native tool"})
        assert child_result["stop_reason"] == "submitted"
        child_frame_id = child_result["frame_id"]

        runner.artifacts.capture(
            state,
            1,
            "parent-cell",
            parent_before,
            lambda _event: None,
            language="python",
        )

        artifact = runner.store.artifact_by_filename(
            "delegated-native.txt", frame_id, strict=True
        )
        assert artifact is not None
        versions = runner.store.list_versions(artifact["artifact_id"])
        assert len(versions) == 1
        version = runner.store.version_meta(versions[0]["version_id"])
        assert version["frame_id"] == child_frame_id
        assert version["producing_cell_id"] is None
        # A native action has no Cell identity; its exact producer is the
        # child frame on the version row. Do not fabricate a producing Cell.
        assert (
            runner.store.list_artifact_capture_observations(
                version_id=version["version_id"]
            )
            == []
        )
        lineage = ExecutionViewService(
            store=runner.store,
            format_timestamp=lambda value: str(value) if value is not None else None,
        ).artifact_lineage(artifact["artifact_id"])
        # A native writer has no Cell identity, so there is nothing to record
        # and cell_recorded stays honestly false even now that delegated
        # Cells are recorded.
        assert lineage["producer"] == {
            "kind": "non_cell",
            "frame_id": child_frame_id,
            "frame_kind": "delegate",
            "producing_cell_id": None,
            "cell_recorded": False,
        }
        assert "capture_observations" not in lineage
        assert (
            runner.store.list_cells(child_frame_id) == []
        ), "a native-only child fabricated a phantom execution_log row"
    finally:
        runner.close()


def test_trusted_delegation_refuses_an_active_root_background_writer(
    tmp_path, monkeypatch
):
    """A root background kernel cannot race a child's workspace snapshot."""

    monkeypatch.setattr(
        loop_mod.Agent,
        "run",
        lambda self, task: {
            "stop_reason": "submitted",
            "submitted_output": {"output": task, "completion_bullets": []},
            "final_message": None,
        },
    )
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    try:
        runner._ensure_runtime(state)
        state.dispatcher._bg_executor = SimpleNamespace(
            list_jobs=lambda: [{"exec_id": "bg-1", "status": "running"}]
        )
        delegated = state.delegation_runner
        assert delegated is not None

        with state.trusted_capture.background():
            with pytest.raises(
                DelegationError, match="background execution is running"
            ):
                delegated({"request": "must not race"})
        assert delegated.children() == []
        assert delegated.delegation_stats()["spawned_session"] == 0
    finally:
        runner.close()


def test_trusted_delegation_holds_capture_lease_against_background_launch(
    tmp_path, monkeypatch
):
    """A native delegation keeps the atomic lease for the whole child run."""

    attempted: list[gateway_mod.GatewayError] = []
    spawned = False
    state = None

    def child_run(self, task):
        assert state is not None
        try:
            state.dispatcher._m_exec_background({"code": "print('racing child')"})
        except gateway_mod.GatewayError as error:
            attempted.append(error)
        return {
            "stop_reason": "submitted",
            "submitted_output": {"output": task, "completion_bullets": []},
            "final_message": None,
        }

    monkeypatch.setattr(loop_mod.Agent, "run", child_run)
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    try:
        runner._ensure_runtime(state)

        def spawn_kernel():
            nonlocal spawned
            spawned = True
            raise AssertionError("delegation lease must reject before spawn")

        state.dispatcher.background_kernel_factory = spawn_kernel
        delegated = state.delegation_runner
        assert delegated is not None
        result = delegated({"request": "hold the capture boundary"})

        assert result["stop_reason"] == "submitted"
        assert [error.error_code for error in attempted] == ["trusted_capture_busy"]
        assert spawned is False
    finally:
        runner.close()


def test_trusted_foreground_cells_and_repl_refuse_active_background_before_identity(
    tmp_path,
):
    """Agent and user Cells share one pre-allocation capture admission."""

    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    starting_index = state.cell_index
    try:
        with state.trusted_capture.background():
            with pytest.raises(gateway_mod.GatewayError) as agent_failure:
                runner._execute_and_log(
                    state,
                    "print('agent')",
                    "agent",
                    lambda _event: None,
                )
            with pytest.raises(gateway_mod.GatewayError) as repl_failure:
                runner.run_repl(frame_id, "default", "print('repl')")

        assert agent_failure.value.error_code == "trusted_capture_busy"
        assert repl_failure.value.error_code == "trusted_capture_busy"
        assert state.cell_index == starting_index
        assert state.kernels.status("python")["alive"] is False
    finally:
        runner.close()


def test_trusted_native_writer_refuses_active_background_before_side_effect(tmp_path):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    target = state.workspace / "must-not-exist.txt"
    invoked = False

    def write_file():
        nonlocal invoked
        invoked = True
        target.write_text("wrong owner", encoding="utf-8")
        return "ok", True

    try:
        with state.trusted_capture.background():
            with pytest.raises(gateway_mod.GatewayError) as failure:
                runner._invoke_control_with_artifacts(
                    state,
                    SimpleNamespace(name="write_file"),
                    lambda _event: None,
                    write_file,
                )

        assert failure.value.error_code == "trusted_capture_busy"
        assert invoked is False
        assert not target.exists()
    finally:
        runner.close()


def test_background_launch_during_trusted_capture_refuses_before_kernel_spawn(
    tmp_path,
):
    """The launch-side lease closes the capture check/start TOCTOU."""

    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    spawned = False
    try:
        dispatcher = runner._ensure_runtime(state)

        def spawn_kernel():
            nonlocal spawned
            spawned = True
            raise AssertionError("admission must run before kernel creation")

        dispatcher.background_kernel_factory = spawn_kernel
        with state.trusted_capture.capture():
            with pytest.raises(gateway_mod.GatewayError) as failure:
                dispatcher._m_exec_background({"code": "print('late')"})

        assert failure.value.error_code == "trusted_capture_busy"
        assert spawned is False
        assert dispatcher._bg().list_jobs() == []
    finally:
        runner.close()


def test_trusted_capture_is_reentrant_only_for_its_owner_thread(tmp_path):
    """Two foreground snapshots may not both claim one shared workspace."""

    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    entered = threading.Event()
    release = threading.Event()
    owner_errors: list[BaseException] = []

    def hold_capture() -> None:
        try:
            with state.trusted_capture.capture():
                entered.set()
                if not release.wait(2):
                    raise AssertionError("test did not release capture owner")
        except BaseException as error:  # recorded and asserted on the parent
            owner_errors.append(error)

    owner = threading.Thread(target=hold_capture)
    owner.start()
    try:
        assert entered.wait(1)
        with pytest.raises(gateway_mod.GatewayError) as failure:
            with state.trusted_capture.capture():
                raise AssertionError("contending capture must not enter")
        assert failure.value.error_code == "trusted_capture_busy"
    finally:
        release.set()
        owner.join(2)
        runner.close()
    assert not owner.is_alive()
    assert owner_errors == []


def _external_mutation_snapshot(runner, state, hub) -> dict:
    artifacts = runner.store.list_artifacts({"root_frame_id": state.root_frame_id})
    versions = {
        artifact["artifact_id"]: runner.store.list_versions(artifact["artifact_id"])
        for artifact in artifacts
    }

    def files_under(root: Path) -> list[tuple[str, bytes]]:
        if not root.exists():
            return []
        return sorted(
            (str(path.relative_to(root)), path.read_bytes())
            for path in root.rglob("*")
            if path.is_file()
        )

    return {
        "live": files_under(state.workspace),
        "snapshots": files_under(runner.artifacts.versions_dir()),
        "artifacts": copy.deepcopy(artifacts),
        "versions": copy.deepcopy(versions),
        "observations": runner.store.list_artifact_capture_observations(),
        "events": copy.deepcopy(hub.events),
    }


@pytest.mark.parametrize("blocking_lease", ["capture", "background"])
@pytest.mark.parametrize(
    "mutation",
    ["upload", "edit", "restore", "promote", "rename", "delete"],
)
def test_external_artifact_mutations_refuse_active_workspace_writers_without_delta(
    tmp_path,
    mutation,
    blocking_lease,
):
    hub = _Hub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), hub)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    seeded = runner.artifacts.upload(
        {
            "frame_id": frame_id,
            "filename": "seed.txt",
            "content_text": "alpha",
        }
    )
    artifact_id = seeded["artifact_id"]
    source_version_id = runner.store.get_artifact(artifact_id)["latest_version_id"]
    runner.artifacts.edit(artifact_id, "beta")
    hub.events.clear()

    def invoke() -> None:
        if mutation == "upload":
            runner.upload_artifact(
                {
                    "frame_id": frame_id,
                    "filename": "external.txt",
                    "content_text": "must not land",
                },
                broadcast=hub.broadcast,
            )
        elif mutation == "edit":
            runner.edit_artifact(
                artifact_id,
                "must not replace beta",
                broadcast=hub.broadcast,
            )
        elif mutation == "restore":
            runner.restore_version(artifact_id, source_version_id)
        elif mutation == "promote":
            runner.promote_cell_artifact(
                gateway_mod.PromotionTarget(
                    root_frame_id=frame_id,
                    project_id="default",
                    workspace=state.workspace,
                ),
                {
                    "producing_cell_id": "cell-rejected",
                    "cell_index": 9,
                    "source": "print('must not promote')",
                    "stdout": "must not land",
                },
                hub.emitter(frame_id),
            )
        elif mutation == "rename":
            runner.rename_artifact(
                artifact_id,
                "rejected-name.txt",
                broadcast=hub.broadcast,
            )
        else:
            runner.delete_artifact(artifact_id, broadcast=hub.broadcast)

    before = _external_mutation_snapshot(runner, state, hub)
    try:
        lease = getattr(state.trusted_capture, blocking_lease)
        with lease():
            with pytest.raises(gateway_mod.GatewayError) as failure:
                invoke()

        assert failure.value.code == 409
        assert failure.value.error_code == "trusted_capture_busy"
        assert _external_mutation_snapshot(runner, state, hub) == before
    finally:
        runner.close()


def test_external_upload_resolves_child_frame_to_root_capture_gate(tmp_path):
    hub = _Hub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), hub)
    root_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    child_id = runner.store.new_frame(
        parent_id=root_id,
        kind="delegate",
        project_id="default",
        status="ready",
    )
    state = runner._state(root_id, "default")
    before = _external_mutation_snapshot(runner, state, hub)
    try:
        with state.trusted_capture.capture():
            with pytest.raises(gateway_mod.GatewayError) as failure:
                runner.upload_artifact(
                    {
                        "frame_id": child_id,
                        "filename": "child-race.txt",
                        "content_text": "must not land",
                    }
                )
        assert failure.value.error_code == "trusted_capture_busy"
        assert runner._existing_state(child_id) is None
        assert _external_mutation_snapshot(runner, state, hub) == before
    finally:
        runner.close()


def test_external_mutation_refuses_an_active_pure_turn_before_calling_manager(
    tmp_path,
    monkeypatch,
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    invoked = False

    def must_not_upload(*args, **kwargs):
        del args, kwargs
        nonlocal invoked
        invoked = True
        raise AssertionError("busy turn must refuse before ArtifactManager")

    monkeypatch.setattr(runner.artifacts, "upload", must_not_upload)
    state.turn_lock.acquire()
    try:
        with pytest.raises(gateway_mod.GatewayError) as failure:
            runner.upload_artifact(
                {
                    "frame_id": frame_id,
                    "filename": "pure-turn-race.txt",
                    "content_text": "must not land",
                }
            )
        assert failure.value.code == 409
        assert failure.value.error_code == "trusted_capture_busy"
        assert invoked is False
    finally:
        state.turn_lock.release()
        runner.close()


@pytest.mark.parametrize("blocking_writer", ["foreground", "background"])
@pytest.mark.parametrize("mutation", ["edit", "restore"])
def test_external_edit_restore_gate_is_always_on_when_stage1_is_disabled(
    tmp_path,
    monkeypatch,
    blocking_writer,
    mutation,
):
    hub = _Hub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=False), hub)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    seeded = runner.artifacts.upload(
        {
            "frame_id": frame_id,
            "filename": "always-gated.txt",
            "content_text": "alpha",
        }
    )
    artifact_id = seeded["artifact_id"]
    source_version_id = runner.store.get_artifact(artifact_id)["latest_version_id"]
    hub.events.clear()
    before = _external_mutation_snapshot(runner, state, hub)
    manager_invoked = False

    def must_not_mutate(*args, **kwargs):
        del args, kwargs
        nonlocal manager_invoked
        manager_invoked = True
        raise AssertionError("writer gate must refuse before ArtifactManager")

    monkeypatch.setattr(runner.artifacts, mutation, must_not_mutate)

    def invoke() -> None:
        if mutation == "edit":
            runner.edit_artifact(
                artifact_id,
                "must not replace beta",
                broadcast=hub.broadcast,
            )
        else:
            runner.restore_version(artifact_id, source_version_id)

    blocker = (
        state.turn_lock
        if blocking_writer == "foreground"
        else state.trusted_capture.background()
    )
    try:
        with blocker:
            with pytest.raises(gateway_mod.GatewayError) as failure:
                invoke()
        assert failure.value.code == 409
        assert failure.value.error_code == "trusted_capture_busy"
        assert manager_invoked is False
        assert _external_mutation_snapshot(runner, state, hub) == before
    finally:
        runner.close()


def test_frameless_project_mutation_barrier_is_always_on_when_stage1_is_disabled(
    tmp_path,
    monkeypatch,
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=False), _Hub())
    calls: list[dict] = []

    def compatible_upload(payload, *, broadcast=None):
        del broadcast
        calls.append(payload)
        return {"artifact_id": "legacy-project", "id": "legacy-project"}

    monkeypatch.setattr(runner.artifacts, "upload", compatible_upload)
    with runner._lock:
        runner._deleting_projects.add("legacy-project")
    try:
        with pytest.raises(gateway_mod.GatewayError) as failure:
            runner.upload_artifact(
                {
                    "project_id": "legacy-project",
                    "filename": "legacy-frameless.txt",
                    "content_text": "must be refused",
                }
            )
        assert failure.value.code == 409
        assert calls == []
    finally:
        with runner._lock:
            runner._deleting_projects.discard("legacy-project")
        runner.close()


def test_unknown_producer_is_refused_when_stage1_is_disabled(
    tmp_path,
    monkeypatch,
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=False), _Hub())
    calls: list[dict] = []

    def compatible_upload(payload, *, broadcast=None):
        del broadcast
        calls.append(payload)
        return {"artifact_id": "legacy-result", "id": "legacy-result"}

    monkeypatch.setattr(runner.artifacts, "upload", compatible_upload)
    try:
        with pytest.raises(gateway_mod.GatewayError) as failure:
            runner.upload_artifact(
                {
                    "frame_id": "legacy-frame",
                    "project_id": "legacy-project",
                    "filename": "legacy.txt",
                    "content_text": "must be refused",
                }
            )
        assert failure.value.code == 404
        assert calls == []
        assert runner._existing_state("legacy-frame") is None
    finally:
        runner.close()


def test_external_mutation_lease_spans_durable_write_through_final_event(tmp_path):
    class BlockingHub(_Hub):
        def __init__(self) -> None:
            super().__init__()
            self.event_entered = threading.Event()
            self.release_event = threading.Event()

        def broadcast(self, root_frame_id: str, event: dict) -> None:
            super().broadcast(root_frame_id, event)
            if event.get("type") == "artifact_created":
                self.event_entered.set()
                if not self.release_event.wait(2):
                    raise AssertionError("test did not release Artifact event")

    hub = BlockingHub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), hub)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    failures: list[BaseException] = []

    def upload() -> None:
        try:
            runner.upload_artifact(
                {
                    "frame_id": frame_id,
                    "filename": "event-boundary.txt",
                    "content_text": "durable before event returns",
                }
            )
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=upload)
    worker.start()
    try:
        assert hub.event_entered.wait(1)
        artifact = runner.store.artifact_by_filename(
            "event-boundary.txt", frame_id, strict=True
        )
        assert artifact is not None
        assert (state.workspace / "event-boundary.txt").read_text() == (
            "durable before event returns"
        )
        assert state.turn_lock.acquire(blocking=False) is False
        for lease in (state.trusted_capture.capture, state.trusted_capture.background):
            with pytest.raises(gateway_mod.GatewayError) as failure:
                with lease():
                    raise AssertionError("writer must not overlap final event")
            assert failure.value.error_code == "trusted_capture_busy"
    finally:
        hub.release_event.set()
        worker.join(2)
        runner.close()
    assert not worker.is_alive()
    assert failures == []


def test_datapro_link_failure_compensates_under_one_external_mutation(
    tmp_path,
    monkeypatch,
):
    hub = _Hub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), hub)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    background_failures: list[gateway_mod.GatewayError] = []

    def fail_link(batch_id, artifact_id):
        del batch_id, artifact_id
        try:
            with state.trusted_capture.background():
                raise AssertionError("background must not enter upload/link gap")
        except gateway_mod.GatewayError as error:
            background_failures.append(error)
        raise RuntimeError("injected DataPro link failure")

    monkeypatch.setattr(runner.store, "link_datapro_index_artifact", fail_link)
    result = {
        "structuredContent": {
            "code": 0,
            "records": [{"title": "gap-fault-sentinel"}],
        },
        "content": [],
        "is_error": False,
        "code": 0,
        "available": True,
    }
    try:
        with pytest.raises(RuntimeError, match="injected DataPro link failure"):
            runner.save_datapro_search_result(
                query="gap-fault-sentinel",
                result=result,
                frame_id=frame_id,
                secrets=(),
                source_result=result,
            )

        assert [error.error_code for error in background_failures] == [
            "trusted_capture_busy"
        ]
        assert runner.store.search_datapro_index("gap-fault-sentinel")["total"] == 0
        assert runner.store.list_artifacts({"root_frame_id": frame_id}) == []
        assert runner.store.list_artifact_capture_observations() == []
        assert not list(state.workspace.glob("datapro-search-*.json"))
        assert hub.events == []
        with state.trusted_capture.background():
            pass
    finally:
        runner.close()


def test_datapro_composite_publishes_one_event_after_success(tmp_path):
    hub = _Hub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), hub)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    result = {
        "structuredContent": {
            "code": 0,
            "records": [{"title": "one-final-event"}],
        },
        "content": [],
        "is_error": False,
        "code": 0,
        "available": True,
    }
    try:
        receipt, artifact = runner.save_datapro_search_result(
            query="one-final-event",
            result=result,
            frame_id=frame_id,
            secrets=(),
            source_result=result,
        )

        assert receipt is not None and receipt.get("batch_id")
        assert artifact is not None and artifact.get("id")
        indexed = runner.store.search_datapro_index("one-final-event")
        assert indexed["total"] == 1
        assert indexed["items"][0]["artifact_id"] == artifact["id"]
        created = [
            event for event in hub.events if event.get("type") == "artifact_created"
        ]
        assert len(created) == 1
        assert created[0]["artifact"]["id"] == artifact["id"]
    finally:
        runner.close()


def test_datapro_composite_preserves_flag_off_immediate_event_sequence(
    tmp_path,
    monkeypatch,
):
    hub = _Hub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=False), hub)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    result = {
        "structuredContent": {
            "code": 0,
            "records": [{"title": "legacy-event-sequence"}],
        },
        "content": [],
        "is_error": False,
        "code": 0,
        "available": True,
    }

    def fail_link(batch_id, artifact_id):
        del batch_id, artifact_id
        raise RuntimeError("legacy link failure")

    monkeypatch.setattr(runner.store, "link_datapro_index_artifact", fail_link)
    try:
        with pytest.raises(RuntimeError, match="legacy link failure"):
            runner.save_datapro_search_result(
                query="legacy-event-sequence",
                result=result,
                frame_id=frame_id,
                secrets=(),
                source_result=result,
            )

        created = [
            event for event in hub.events if event.get("type") == "artifact_created"
        ]
        assert len(created) == 2
        assert "artifact" in created[0]
        assert "artifact" not in created[1]
        assert runner.store.search_datapro_index("legacy-event-sequence")["total"] == 0
        assert runner.store.list_artifacts({"root_frame_id": frame_id}) == []
    finally:
        runner.close()


def test_session_domain_mutation_refuses_active_background_before_side_effect(
    tmp_path,
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    invoked = False

    def must_not_mutate():
        nonlocal invoked
        invoked = True
        raise AssertionError("domain mutation must refuse before its side effect")

    try:
        with state.trusted_capture.background():
            with pytest.raises(gateway_mod.GatewayError) as failure:
                runner.mutate_session_domain(
                    frame_id,
                    "default",
                    operation="create_checkpoint",
                    mutate=must_not_mutate,
                )
        assert failure.value.error_code == "trusted_capture_busy"
        assert invoked is False
    finally:
        runner.close()


def test_recovery_action_refuses_active_background_before_runtime_preparation(
    tmp_path,
    monkeypatch,
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    runtime_built = False

    def must_not_build_runtime(*args, **kwargs):
        del args, kwargs
        nonlocal runtime_built
        runtime_built = True
        raise AssertionError("recovery must refuse before runtime preparation")

    monkeypatch.setattr(runner, "_recovery_runtime", must_not_build_runtime)
    try:
        with state.trusted_capture.background():
            with pytest.raises(gateway_mod.GatewayError) as failure:
                runner.execute_recovery_action(
                    frame_id,
                    "default",
                    "restore",
                )
        assert failure.value.error_code == "trusted_capture_busy"
        assert runtime_built is False
    finally:
        runner.close()


def test_successful_restore_clears_revert_barrier_after_runtime_publication(
    tmp_path, monkeypatch
):
    hub = _Hub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), hub)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    runner._state(frame_id, "default")
    marker_key = revert_recovery_setting_key(frame_id)
    runner.store.set_setting(
        marker_key,
        json.dumps(
            {
                "schema_version": 1,
                "state": "recovery_required",
                "operation_id": "so-manual-restore",
                "branch_id": frame_id,
            }
        ),
    )

    class Runtime:
        def run(self, _plan):
            assert runner.store.get_setting(marker_key) is not None
            return {"ok": True, "status": "active", "recovery_id": "recovery-1"}

        def kernel_status_event(self, result, recovery_id):
            assert runner.store.get_setting(marker_key) is not None
            return {
                "type": "kernel_status",
                "status": result["status"],
                "recovery_id": recovery_id,
            }

    monkeypatch.setattr(runner, "_recovery_runtime", lambda _st, _emit: Runtime())
    monkeypatch.setattr(
        runner.session_domain.recovery,
        "prepare_action",
        lambda *_args, **_kwargs: SimpleNamespace(recovery_id="recovery-1"),
    )
    try:
        result = runner.execute_recovery_action(
            frame_id,
            "default",
            "restore",
        )

        assert result["status"] == "active"
        assert result["revert_recovery_cleared"] is True
        assert runner.store.get_setting(marker_key) is None
        assert any(
            event.get("type") == "kernel_status" and event.get("status") == "active"
            for event in hub.events
        )
    finally:
        runner.close()


def test_first_write_guard_reconciles_committed_revert_after_restart(tmp_path):
    hub = _Hub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), hub)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    runner._state(frame_id, "default")
    workspace = Path(runner.workspace_for_branch(frame_id, frame_id))
    workspace.mkdir(parents=True, exist_ok=True)
    analysis = workspace / "analysis.txt"
    try:
        analysis.write_text("v1", encoding="utf-8")
        target = runner.session_domain.create_checkpoint(frame_id)
        analysis.write_text("v2", encoding="utf-8")
        runner.session_domain.create_checkpoint(frame_id)
        runner.session_domain.branching.revert_and_continue(
            frame_id,
            branch_id=frame_id,
            target_checkpoint_id=target["checkpoint_id"],
        )
        marker_key = revert_recovery_setting_key(frame_id)
        assert runner.store.get_setting(marker_key)
        analysis.write_text("v2", encoding="utf-8")

        runner.require_session_writable(frame_id, "running the next turn")

        assert analysis.read_text(encoding="utf-8") == "v1"
        assert runner.store.get_setting(marker_key) is None
        assert any(event.get("type") == "branch_reverted" for event in hub.events)
    finally:
        runner.close()


def test_first_write_guard_fails_closed_for_corrupt_empty_revert_marker(
    tmp_path, monkeypatch
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    marker_key = revert_recovery_setting_key(frame_id)
    runner.store.set_setting(marker_key, "")
    monkeypatch.setattr(
        runner.session_domain,
        "reconcile_revert",
        lambda _root: {"resolved": False, "state": "recovery_required"},
    )
    try:
        with pytest.raises(gateway_mod.GatewayError) as failure:
            runner.require_session_writable(frame_id, "running the next turn")

        assert failure.value.code == 423
        assert runner.store.get_setting(marker_key) == ""
    finally:
        runner.close()


def test_first_write_guard_never_reconciles_an_active_revert_owner(
    tmp_path, monkeypatch
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    marker_key = revert_recovery_setting_key(frame_id)
    marker = json.dumps(
        {
            "schema_version": 1,
            "state": "preparing",
            "operation_id": "so-active-revert",
            "branch_id": frame_id,
        }
    )
    runner.store.set_setting(marker_key, marker)
    reconciled = False

    def must_not_reconcile(_root):
        nonlocal reconciled
        reconciled = True
        raise AssertionError("an active revert owner must keep its own barrier")

    monkeypatch.setattr(runner.session_domain, "reconcile_revert", must_not_reconcile)
    try:
        with runner._session_execution(
            state,
            owner="lifecycle",
            owner_id="active-revert",
            reason="reverting workspace",
        ):
            with pytest.raises(gateway_mod.GatewayError) as failure:
                runner.require_session_writable(frame_id, "running the next turn")

            assert failure.value.code == 423
            assert reconciled is False
            assert runner.store.get_setting(marker_key) == marker
    finally:
        runner.close()


def test_branch_activation_refuses_active_background_before_materialization(
    tmp_path,
    monkeypatch,
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    prepared = False

    def must_not_prepare(*args, **kwargs):
        del args, kwargs
        nonlocal prepared
        prepared = True
        raise AssertionError("activation must refuse before materialization")

    monkeypatch.setattr(runner.session_domain, "prepare_activation", must_not_prepare)
    try:
        with state.trusted_capture.background():
            with pytest.raises(gateway_mod.GatewayError) as failure:
                runner.activate_session_branch(
                    frame_id,
                    "default",
                    "br-must-not-activate",
                )
        assert failure.value.error_code == "trusted_capture_busy"
        assert prepared is False
        assert runner._existing_state(frame_id) is state
    finally:
        runner.close()


def test_external_mutation_refuses_activation_after_candidate_publication(
    tmp_path,
    monkeypatch,
):
    """The root execution identity survives SessionState replacement."""

    hub = _Hub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), hub)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    old = runner._state(frame_id, "default")
    branch_id = "br-publication-window"
    candidate_workspace = runner.workspace_for_branch(frame_id, branch_id)
    published = threading.Event()
    release = threading.Event()
    activation_errors: list[BaseException] = []

    monkeypatch.setattr(
        runner.session_domain,
        "prepare_activation",
        lambda *args, **kwargs: {
            "root_frame_id": frame_id,
            "branch_id": branch_id,
            "checkpoint_id": "cp-publication-window",
            "checkpoint": {
                "checkpoint_id": "cp-publication-window",
                "environment_pins": {},
                "generation_refs": {},
                "metadata": {},
            },
            "workspace": candidate_workspace,
            "workspace_preview": {},
        },
    )
    monkeypatch.setattr(
        runner.session_domain,
        "publish_activation",
        lambda *args, **kwargs: {
            "environment": {"applied": True},
            "artifacts": {"applied": True},
            "capabilities": {"applied": True},
            "permissions": {"applied": True},
        },
    )

    def hold_after_publication(state) -> None:
        assert state is runner._existing_state(frame_id)
        assert state is not old
        published.set()
        if not release.wait(2):
            raise AssertionError("test did not release branch activation")

    monkeypatch.setattr(runner, "_seed_messages", hold_after_publication)

    def activate() -> None:
        try:
            runner.activate_session_branch(frame_id, "default", branch_id)
        except BaseException as error:
            activation_errors.append(error)

    worker = threading.Thread(target=activate)
    worker.start()
    try:
        assert published.wait(1)
        candidate = runner._existing_state(frame_id)
        assert candidate is not None and candidate is not old
        # This is the ABA condition: the published state's compatible lock is
        # free while the root-stable lifecycle execution is still active.
        assert candidate.turn_lock.acquire(blocking=False) is True
        candidate.turn_lock.release()
        before = _external_mutation_snapshot(runner, candidate, hub)
        manager_called = False

        def must_not_upload(*args, **kwargs):
            del args, kwargs
            nonlocal manager_called
            manager_called = True
            raise AssertionError("activation ABA guard must refuse before upload")

        monkeypatch.setattr(runner.artifacts, "upload", must_not_upload)
        with pytest.raises(gateway_mod.GatewayError) as failure:
            runner.upload_artifact(
                {
                    "frame_id": frame_id,
                    "filename": "activation-race.txt",
                    "content_text": "must not land",
                }
            )
        assert failure.value.code == 409
        assert failure.value.error_code == "trusted_capture_busy"
        assert manager_called is False
        assert _external_mutation_snapshot(runner, candidate, hub) == before
    finally:
        release.set()
        worker.join(2)
        runner.close()
    assert not worker.is_alive()
    assert activation_errors == []


@pytest.mark.parametrize("delete_kind", ["session", "project"])
@pytest.mark.parametrize("stage1", [False, True], ids=["stage1-off", "stage1-on"])
def test_deletion_tombstone_refuses_mutation_after_runtime_is_popped(
    tmp_path,
    monkeypatch,
    delete_kind,
    stage1,
):
    hub = _Hub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=stage1), hub)
    project_id = "delete-gap-project"
    runner.store.create_project(name="Delete gap", project_id=project_id)
    frame_id = runner.store.new_frame(
        kind="turn", project_id=project_id, status="ready"
    )
    state = runner._state(frame_id, project_id)
    popped = threading.Event()
    release = threading.Event()
    deletion_errors: list[BaseException] = []
    manager_called = False

    def block_after_runtime_pop(root_frame_id):
        assert root_frame_id == frame_id
        assert runner._existing_state(frame_id) is None
        popped.set()
        if not release.wait(2):
            raise AssertionError("test did not release deletion")

    def must_not_upload(*args, **kwargs):
        del args, kwargs
        nonlocal manager_called
        manager_called = True
        raise AssertionError("deletion tombstone must refuse before upload")

    monkeypatch.setattr(
        runner.deletions,
        "_release_compute_safe",
        block_after_runtime_pop,
    )
    monkeypatch.setattr(runner.artifacts, "upload", must_not_upload)

    def delete() -> None:
        try:
            if delete_kind == "session":
                runner.delete_session(frame_id)
            else:
                runner.delete_project(project_id)
        except BaseException as error:
            deletion_errors.append(error)

    worker = threading.Thread(target=delete)
    worker.start()
    try:
        assert popped.wait(1)
        events_before = copy.deepcopy(hub.events)
        with pytest.raises(gateway_mod.GatewayError) as failure:
            runner.upload_artifact(
                {
                    "frame_id": frame_id,
                    "filename": "delete-gap.txt",
                    "content_text": "must not land",
                }
            )
        assert failure.value.code == 409
        assert manager_called is False
        assert runner._existing_state(frame_id) is None
        assert hub.events == events_before
        assert not (state.workspace / "delete-gap.txt").exists()
    finally:
        release.set()
        worker.join(2)
        runner.close()
    assert not worker.is_alive()
    assert deletion_errors == []


@pytest.mark.parametrize("delete_kind", ["session", "project"])
@pytest.mark.parametrize("stage1", [False, True], ids=["stage1-off", "stage1-on"])
def test_detached_state_aba_cannot_write_after_deletion_finishes(
    tmp_path,
    monkeypatch,
    delete_kind,
    stage1,
):
    hub = _Hub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=stage1), hub)
    project_id = "delete-aba-project"
    runner.store.create_project(name="Delete ABA", project_id=project_id)
    frame_id = runner.store.new_frame(
        kind="turn", project_id=project_id, status="ready"
    )
    stale = runner._state(frame_id, project_id)
    original_state = runner._state
    state_returned = threading.Event()
    resume = threading.Event()
    mutation_errors: list[BaseException] = []
    manager_called = False

    def delayed_state(*args, **kwargs):
        state = original_state(*args, **kwargs)
        state_returned.set()
        if not resume.wait(2):
            raise AssertionError("test did not resume stale mutation")
        return state

    def must_not_upload(*args, **kwargs):
        del args, kwargs
        nonlocal manager_called
        manager_called = True
        raise AssertionError("detached state must refuse before upload")

    monkeypatch.setattr(runner, "_state", delayed_state)
    monkeypatch.setattr(runner.artifacts, "upload", must_not_upload)

    def mutate() -> None:
        try:
            runner.upload_artifact(
                {
                    "frame_id": frame_id,
                    "filename": "after-delete.txt",
                    "content_text": "must not land",
                }
            )
        except BaseException as error:
            mutation_errors.append(error)

    worker = threading.Thread(target=mutate)
    worker.start()
    try:
        assert state_returned.wait(1)
        if delete_kind == "session":
            runner.delete_session(frame_id)
        else:
            runner.delete_project(project_id)
        events_after_delete = copy.deepcopy(hub.events)
        resume.set()
        worker.join(2)

        assert not worker.is_alive()
        assert len(mutation_errors) == 1
        assert isinstance(mutation_errors[0], gateway_mod.GatewayError)
        assert mutation_errors[0].code == 409
        assert manager_called is False
        assert runner._existing_state(frame_id) is None
        assert runner.store.get_frame(frame_id) is None
        assert hub.events == events_after_delete
        assert not (stale.workspace / "after-delete.txt").exists()
    finally:
        resume.set()
        worker.join(2)
        runner.close()


@pytest.mark.parametrize("stage1", [False, True], ids=["stage1-off", "stage1-on"])
def test_project_deletion_waits_for_frameless_mutation_and_refuses_late_entry(
    tmp_path,
    monkeypatch,
    stage1,
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=stage1), _Hub())
    project_id = "frameless-delete-project"
    runner.store.create_project(name="Frameless", project_id=project_id)
    original_upload = runner.artifacts.upload
    mutation_entered = threading.Event()
    release_mutation = threading.Event()
    mutation_errors: list[BaseException] = []
    deletion_errors: list[BaseException] = []
    late_manager_call = False

    def blocking_upload(payload, *, broadcast=None):
        nonlocal late_manager_call
        if payload.get("filename") == "early.txt":
            mutation_entered.set()
            if not release_mutation.wait(2):
                raise AssertionError("test did not release frameless upload")
        else:
            late_manager_call = True
        return original_upload(payload, broadcast=broadcast)

    monkeypatch.setattr(runner.artifacts, "upload", blocking_upload)

    def mutate() -> None:
        try:
            runner.upload_artifact(
                {
                    "project_id": project_id,
                    "filename": "early.txt",
                    "content_text": "finishes before deletion",
                }
            )
        except BaseException as error:
            mutation_errors.append(error)

    def delete() -> None:
        try:
            runner.delete_project(project_id)
        except BaseException as error:
            deletion_errors.append(error)

    mutation = threading.Thread(target=mutate)
    deletion = threading.Thread(target=delete)
    mutation.start()
    assert mutation_entered.wait(1)
    deletion.start()
    tick = threading.Event()
    try:
        deleting = False
        for _ in range(100):
            with runner._lock:
                deleting = project_id in runner._deleting_projects
            if deleting:
                break
            tick.wait(0.01)
        assert deleting
        assert deletion.is_alive(), "deletion must wait for the admitted mutation"
        with pytest.raises(gateway_mod.GatewayError) as failure:
            runner.upload_artifact(
                {
                    "project_id": project_id,
                    "filename": "late.txt",
                    "content_text": "must not enter",
                }
            )
        assert failure.value.code == 409
        assert late_manager_call is False
    finally:
        release_mutation.set()
        mutation.join(2)
        deletion.join(2)
    try:
        assert not mutation.is_alive() and not deletion.is_alive()
        assert mutation_errors == []
        assert deletion_errors == []
        assert runner.store.get_project(project_id) is None
        assert runner.store.list_artifacts({"project_id": project_id}) == []
        assert not (runner.cfg.data_dir / "uploads" / "early.txt").exists()
    finally:
        runner.close()


def test_project_deletion_keeps_a_frameless_path_still_referenced_elsewhere(
    tmp_path,
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    for project_id in ("shared-upload-a", "shared-upload-b"):
        runner.store.create_project(name=project_id, project_id=project_id)
    first = runner.upload_artifact(
        {
            "project_id": "shared-upload-a",
            "filename": "shared-name.txt",
            "content_text": "A",
        }
    )
    second = runner.upload_artifact(
        {
            "project_id": "shared-upload-b",
            "filename": "shared-name.txt",
            "content_text": "B",
        }
    )
    path = runner.cfg.data_dir / "uploads" / "shared-name.txt"
    try:
        runner.delete_project("shared-upload-a")
        assert runner.store.get_artifact(first["artifact_id"]) is None
        assert runner.store.get_artifact(second["artifact_id"]) is not None
        assert path.read_text() == "B"

        runner.delete_project("shared-upload-b")
        assert runner.store.get_artifact(second["artifact_id"]) is None
        assert not path.exists()
    finally:
        runner.close()


def test_project_delete_cleanup_window_refuses_cross_project_same_name_upload(
    tmp_path,
    monkeypatch,
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    for project_id in ("cleanup-window-a", "cleanup-window-b"):
        runner.store.create_project(name=project_id, project_id=project_id)
    runner.upload_artifact(
        {
            "project_id": "cleanup-window-a",
            "filename": "same-name.txt",
            "content_text": "A",
        }
    )
    path = runner.cfg.data_dir / "uploads" / "same-name.txt"
    original_cleanup = runner.deletions._cleanup
    cleanup_entered = threading.Event()
    release_cleanup = threading.Event()
    deletion_errors: list[BaseException] = []
    manager_called = False

    def blocked_cleanup(result, **kwargs):
        cleanup_entered.set()
        if not release_cleanup.wait(2):
            raise AssertionError("test did not release project cleanup")
        return original_cleanup(result, **kwargs)

    original_upload = runner.artifacts.upload

    def observe_upload(payload, *, broadcast=None):
        nonlocal manager_called
        manager_called = True
        return original_upload(payload, broadcast=broadcast)

    monkeypatch.setattr(runner.deletions, "_cleanup", blocked_cleanup)
    monkeypatch.setattr(runner.artifacts, "upload", observe_upload)

    def delete() -> None:
        try:
            runner.delete_project("cleanup-window-a")
        except BaseException as error:
            deletion_errors.append(error)

    worker = threading.Thread(target=delete)
    worker.start()
    try:
        assert cleanup_entered.wait(1)
        assert runner.store.get_project("cleanup-window-a") is None
        assert path.read_text() == "A"
        with pytest.raises(gateway_mod.GatewayError) as failure:
            runner.upload_artifact(
                {
                    "project_id": "cleanup-window-b",
                    "filename": "same-name.txt",
                    "content_text": "B must wait for cleanup",
                }
            )
        assert failure.value.code == 409
        assert manager_called is False
        assert path.read_text() == "A"
    finally:
        release_cleanup.set()
        worker.join(2)
    try:
        assert not worker.is_alive()
        assert deletion_errors == []
        assert not path.exists()
        saved = runner.upload_artifact(
            {
                "project_id": "cleanup-window-b",
                "filename": "same-name.txt",
                "content_text": "B after cleanup",
            }
        )
        assert saved["artifact_id"]
        assert path.read_text() == "B after cleanup"
    finally:
        runner.close()


def test_upload_cleanup_refuses_a_symlinked_root_swap(
    tmp_path,
    monkeypatch,
):
    from openai4s.server import session_deletion as deletion_mod

    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    project_id = "upload-root-swap"
    runner.store.create_project(name="Root swap", project_id=project_id)
    saved = runner.upload_artifact(
        {
            "project_id": project_id,
            "filename": "stale.txt",
            "content_text": "managed stale bytes",
        }
    )
    uploads = runner.cfg.data_dir / "uploads"
    parked = runner.cfg.data_dir / "uploads-parked"
    outside = runner.cfg.data_dir / "outside"
    outside.mkdir()
    (outside / "stale.txt").write_text("EXTERNAL SENTINEL")
    original_open = deletion_mod.os.open
    swapped = False

    def swap_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        try:
            opened_path = Path(path)
        except TypeError:
            opened_path = None
        if not swapped and opened_path == uploads:
            swapped = True
            uploads.rename(parked)
            uploads.symlink_to(outside, target_is_directory=True)
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(deletion_mod.os, "open", swap_before_open)
    try:
        runner.delete_project(project_id)
        assert swapped is True
        assert runner.store.get_artifact(saved["artifact_id"]) is None
        assert (outside / "stale.txt").read_text() == "EXTERNAL SENTINEL"
        assert (parked / "stale.txt").read_text() == "managed stale bytes"
    finally:
        runner.close()


def test_flag_off_project_delete_cleans_frameless_file(tmp_path):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=False), _Hub())
    project_id = "legacy-upload-cleanup"
    runner.store.create_project(name="Legacy cleanup", project_id=project_id)
    saved = runner.upload_artifact(
        {
            "project_id": project_id,
            "filename": "legacy-retained.txt",
            "content_text": "legacy retained bytes",
        }
    )
    path = runner.cfg.data_dir / "uploads" / "legacy-retained.txt"
    try:
        runner.delete_project(project_id)
        assert runner.store.get_artifact(saved["artifact_id"]) is None
        assert not path.exists()
    finally:
        runner.close()


def test_session_delete_never_unlinks_an_uploads_path_without_global_barrier(
    tmp_path,
):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path, stage1=True), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    uploads = runner.cfg.data_dir / "uploads"
    uploads.mkdir(exist_ok=True)
    path = uploads / "legacy-session-scoped.txt"
    path.write_text("must remain for a safe sweep")
    record = runner.store.save_artifact(
        path=str(path),
        filename=path.name,
        content_type="text/plain",
        size_bytes=path.stat().st_size,
        checksum="legacy-checksum",
        frame_id=frame_id,
    )
    try:
        runner.delete_session(frame_id)
        assert runner.store.get_artifact(record["artifact_id"]) is None
        assert path.read_text() == "must remain for a safe sweep"
    finally:
        runner.close()


def test_web_native_schema_history_and_cell_only_completion(monkeypatch, tmp_path):
    class Dispatcher:
        def __init__(self) -> None:
            self.last_output = {"stale": "must be cleared"}
            self.calls: list[tuple[str, list[dict]]] = []
            self.output_seen: list[object] = []

        def __call__(self, method, args):
            self.calls.append((method, args))
            self.output_seen.append(self.last_output)
            return {"entries": [{"name": "result.csv", "type": "file"}]}

    dispatcher = Dispatcher()
    runner, _hub, frame_id = _prepare_message_runner(monkeypatch, tmp_path, dispatcher)
    call = _native_call("call-list", "list_dir", {"path": "."})
    first_reply, assistant_message = _native_reply("Checking files.", [call])
    model_calls: list[list[dict]] = []
    tool_name_sets: list[set[str]] = []

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del cfg
        assert callable(on_delta)
        model_calls.append(copy.deepcopy(list(messages)))
        tool_name_sets.append({spec.name for spec in kwargs["tools"]})
        if len(model_calls) == 1:
            return first_reply

        history_tail = messages[-2:]
        assert history_tail[0] == assistant_message
        tool_result = history_tail[1]
        assert tool_result["role"] == "tool"
        assert tool_result["tool_call_id"] == "call-list"
        assert tool_result["wire_id"] == "call-list"
        assert tool_result["name"] == "list_dir"
        assert tool_result["is_error"] is False
        assert "result.csv" in tool_result["content"]
        return {
            "content": (
                "```python\n"
                "host.submit_output({'files': ['result.csv']}, ['done'])\n"
                "```"
            ),
            "usage": {},
        }

    def fake_execute(state, code, origin, emit, stream=True, language="python"):
        del code, origin, emit, stream, language
        state.dispatcher.last_output = {
            "output": {"files": ["result.csv"]},
            "completion_bullets": ["done"],
        }
        return {"result": {"stdout": "", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_execute_and_log", fake_execute)

    result = runner.run_message(frame_id, "default", "Inspect my files")

    assert result["status"] == "completed"
    assert len(model_calls) == 2
    assert dispatcher.calls == [("list_dir", [{"path": "."}])]
    assert dispatcher.output_seen == [None]
    assert all("list_dir" in names and "env_use" in names for names in tool_name_sets)
    assert all(
        "bash" not in names and "submit_output" not in names for names in tool_name_sets
    )
    assert [message["role"] for message in model_calls[1][-2:]] == [
        "assistant",
        "tool",
    ]


def test_cancel_after_llm_reply_prevents_returned_cell(monkeypatch, tmp_path):
    dispatcher = SimpleNamespace(last_output=None)
    runner, hub, frame_id = _prepare_message_runner(monkeypatch, tmp_path, dispatcher)
    model_calls = []

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del messages, cfg, on_delta, kwargs
        model_calls.append(1)
        runner._state(frame_id, "default").cancel.set()
        return {
            "content": "```python\nraise AssertionError('must not run')\n```",
            "usage": {},
        }

    def unexpected_execute(*args, **kwargs):
        raise AssertionError(f"cancelled cell was executed: {args!r} {kwargs!r}")

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_execute_and_log", unexpected_execute)
    monkeypatch.setattr(
        runner,
        "_run_reviewer",
        lambda *args, **kwargs: pytest.fail("cancelled turn must not be reviewed"),
    )
    runner.store.set_setting(f"review:auto:{frame_id}", "1")

    result = runner.run_message(frame_id, "default", "Run a cell")

    assert result["status"] == "cancelled"
    assert model_calls == [1]
    assert hub.events[-1]["type"] == "frame_update"
    assert hub.events[-1]["status"] == "cancelled"
    stored = runner.store.list_messages(frame_id)
    assert [message["role"] for message in stored] == ["user", "assistant"]
    assert stored[-1]["content"] == "_已取消。_"


def test_cancel_blocked_llm_releases_running_state_and_drops_late_output(
    monkeypatch, tmp_path
):
    dispatcher = SimpleNamespace(last_output=None)
    runner, hub, frame_id = _prepare_message_runner(monkeypatch, tmp_path, dispatcher)
    entered = threading.Event()
    release = threading.Event()
    provider_done = threading.Event()
    usage_accounted = threading.Event()
    late_cancel_checks = []
    original_add_frame_tokens = runner.store.add_frame_tokens

    def record_frame_tokens(*args, **kwargs):
        original_add_frame_tokens(*args, **kwargs)
        usage_accounted.set()

    monkeypatch.setattr(runner.store, "add_frame_tokens", record_frame_tokens)

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del messages, cfg
        entered.set()
        assert release.wait(5), "test did not release the blocked provider"
        late_cancel_checks.append(kwargs["should_cancel"]())
        if on_delta is not None:
            on_delta("LATE_AFTER_STOP")
        provider_done.set()
        return {
            "content": "```python\nraise AssertionError('must not run')\n```",
            "usage": {"prompt_tokens": 13, "completion_tokens": 5},
        }

    def unexpected_execute(*args, **kwargs):
        raise AssertionError(f"cancelled late cell was executed: {args!r} {kwargs!r}")

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_execute_and_log", unexpected_execute)

    try:
        job = runner.submit_message(frame_id, "default", "Run a slow request")
        assert entered.wait(2), "provider call did not start"
        cancelled = runner.cancel(
            frame_id,
            job.execution_id,
            owner=job.execution_owner,
            reason="Stop pressed during provider request",
        )

        assert cancelled["ok"] is True and cancelled["scope"] == "running"
        assert job.done.wait(2), "job stayed running behind the provider request"
        assert job.wait_result()["status"] == "cancelled"
        assert runner.is_running(frame_id) is False
        assert runner.store.get_frame(frame_id)["status"] == "cancelled"
        assert runner.executions.snapshot(frame_id)["owner"] is None

        # Simulate the next queued turn clearing the coordinator's shared
        # cancellation Event before the abandoned provider finally responds.
        # The provider call's private latch must stay cancelled.
        runner._state(frame_id, "default").cancel.clear()
        release.set()
        assert provider_done.wait(2), "detached provider did not finish"
        assert usage_accounted.wait(2), "cancelled provider usage was dropped"
        assert late_cancel_checks == [True]
        assert "LATE_AFTER_STOP" not in json.dumps(hub.events)
        frame = runner.store.get_frame(frame_id)
        assert frame["input_tokens"] == 13
        assert frame["output_tokens"] == 5
    finally:
        release.set()
        runner.close()


@pytest.mark.parametrize("with_native_call", [False, True])
def test_plan_mode_blocks_code_and_native_tools_and_closes_history(
    monkeypatch, tmp_path, with_native_call
):
    class RefusingDispatcher:
        last_output = None

        def __call__(self, method, args):
            raise AssertionError(f"plan mode dispatched {method!r} with {args!r}")

    dispatcher = RefusingDispatcher()
    runner, hub, frame_id = _prepare_message_runner(monkeypatch, tmp_path, dispatcher)
    content = (
        "I will inspect the data first.\n\n"
        "```json\n"
        '{"title":"Safe plan","rationale":"inspect before analysis",'
        '"confidence":"high","steps":['
        '{"id":"s1","title":"Inspect","detail":"read inputs",'
        '"deliverables":["inventory.csv"]}]}\n'
        "```\n"
        "```python\nraise AssertionError('plan cell must not run')\n```"
    )
    calls = (
        [_native_call("plan-call", "list_dir", {"path": "."})]
        if with_native_call
        else []
    )
    if calls:
        response, assistant_message = _native_reply(content, calls)
    else:
        response = {"content": content, "usage": {}}
        assistant_message = {"role": "assistant", "content": content}
    model_count = 0

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        nonlocal model_count
        del messages, cfg, on_delta
        model_count += 1
        assert kwargs["tools"] == ()
        return response

    def unexpected_execute(*args, **kwargs):
        raise AssertionError(f"plan cell was executed: {args!r} {kwargs!r}")

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_execute_and_log", unexpected_execute)

    result = runner.run_message(
        frame_id, "default", "Draft a reviewable plan", plan=True
    )

    assert result["status"] == "completed"
    assert model_count == 1
    assert runner.store.cell_count(frame_id) == 0
    plan = runner.store.get_plan_by_frame(frame_id)
    assert plan is not None and plan["status"] == "draft"
    assert plan["steps"][0]["title"] == "Inspect"
    assert any(
        artifact["filename"].startswith("plan_")
        for artifact in runner.store.list_artifacts()
    )

    state = runner._state(frame_id, "default")
    if with_native_call:
        assert state.messages[-2] == assistant_message
        result_message = state.messages[-1]
        assert result_message["role"] == "tool"
        assert result_message["tool_call_id"] == "plan-call"
        assert result_message["is_error"] is True
        assert "disabled in plan mode" in result_message["content"]
    else:
        assert state.messages[-1] == assistant_message
        assert all(message["role"] != "tool" for message in state.messages)

    ready_index = next(
        index for index, event in enumerate(hub.events) if event["type"] == "plan_ready"
    )
    terminal_index = max(
        index
        for index, event in enumerate(hub.events)
        if event["type"] == "frame_update" and event.get("status") == "completed"
    )
    assert ready_index < terminal_index


def test_native_env_switch_rebinds_dispatcher_before_next_call(monkeypatch, tmp_path):
    runner = gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())
    state = runner._state("frame-env-native", "default")
    state.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "switch then inspect"},
    ]
    dispatch_order: list[tuple[str, str]] = []

    class Dispatcher:
        def __init__(self, label: str) -> None:
            self.label = label
            self.last_output = None

        def __call__(self, method, args):
            dispatch_order.append((self.label, method))
            if method == "env_use":
                state.pending_env = args[0]["name"]
            return {"ok": True}

    state.dispatcher = Dispatcher("old")
    calls = [
        _native_call("env-call", "env_use", {"name": "struct"}),
        _native_call("list-call", "list_dir", {"path": "."}, ordinal=1),
    ]
    first_reply, _assistant = _native_reply("", calls)
    replies = iter(
        [
            first_reply,
            {
                "content": "```python\nhost.submit_output({'ok': True}, ['done'])\n```",
                "usage": {},
            },
        ]
    )
    model_histories: list[list[dict]] = []

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del cfg, on_delta
        assert kwargs["tools"]
        model_histories.append(copy.deepcopy(list(messages)))
        return next(replies)

    def apply_pending(current, emit):
        del emit
        dispatch_order.append(("apply", current.pending_env))
        current.env_name = current.pending_env
        current.pending_env = None
        current.dispatcher = Dispatcher("new")

    def fake_execute(current, code, origin, emit, stream=True, language="python"):
        del code, origin, emit, stream, language
        current.dispatcher.last_output = {"output": {"ok": True}}
        return {"result": {"stdout": "", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_apply_pending_env", apply_pending)
    monkeypatch.setattr(runner, "_execute_and_log", fake_execute)

    reason = runner._loop(state, lambda event: None, [])

    assert reason == "submitted"
    assert dispatch_order == [
        ("old", "env_use"),
        ("apply", "struct"),
        ("new", "list_dir"),
    ]
    assert [message["role"] for message in model_histories[1][-3:]] == [
        "assistant",
        "tool",
        "tool",
    ]
    assert [message["tool_call_id"] for message in model_histories[1][-2:]] == [
        "env-call",
        "list-call",
    ]


def test_streamed_deltas_hide_fences_and_precede_tool_and_terminal_events(
    monkeypatch, tmp_path
):
    dispatcher = SimpleNamespace(last_output=None)
    runner, hub, frame_id = _prepare_message_runner(monkeypatch, tmp_path, dispatcher)
    reply = (
        "Before.\n"
        "```python\n"
        "host.submit_output({'ok': True}, ['done'])\n"
        "```\n"
        "After."
    )

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del messages, cfg, kwargs
        assert callable(on_delta)
        for offset in range(0, len(reply), 5):
            on_delta(reply[offset : offset + 5])
        return {"content": reply, "usage": {}}

    def fake_execute(state, code, origin, emit, stream=True, language="python"):
        del code, origin, stream, language
        emit(
            {
                "type": "text_chunk",
                "frame_id": state.root_frame_id,
                "block_type": "tool",
                "chunk": "CELL-RAN",
            }
        )
        state.dispatcher.last_output = {"output": {"ok": True}}
        return {"result": {"stdout": "", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_execute_and_log", fake_execute)

    result = runner.run_message(frame_id, "default", "Stream one cell")

    assert result["status"] == "completed"
    text_events = [
        event
        for event in hub.events
        if event.get("type") == "text_chunk" and event.get("block_type") == "text"
    ]
    visible = "".join(event["chunk"] for event in text_events)
    # Anything after the action fence was generated before the cell ran and
    # therefore cannot be a trustworthy result narration.
    assert visible == "Before.\n"
    assert "host.submit_output" not in visible

    reset_index = next(
        index for index, event in enumerate(hub.events) if event["type"] == "text_reset"
    )
    text_indices = [
        index
        for index, event in enumerate(hub.events)
        if event.get("type") == "text_chunk" and event.get("block_type") == "text"
    ]
    tool_index = next(
        index
        for index, event in enumerate(hub.events)
        if event.get("type") == "text_chunk" and event.get("chunk") == "CELL-RAN"
    )
    terminal_index = max(
        index
        for index, event in enumerate(hub.events)
        if event.get("type") == "frame_update" and event.get("status") == "completed"
    )
    assert reset_index < min(text_indices) <= max(text_indices) < tool_index
    assert tool_index < terminal_index
    stored = runner.store.list_messages(frame_id)
    assert stored[-1]["role"] == "assistant"
    assert stored[-1]["content"] == "Before."


def test_code_only_failure_is_visible_after_real_cell_outcome(monkeypatch, tmp_path):
    dispatcher = SimpleNamespace(last_output=None)
    runner, hub, frame_id = _prepare_message_runner(monkeypatch, tmp_path, dispatcher)
    reply = "```python\nprint(missing_name)\n```\nThis worked perfectly."

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del messages, cfg, kwargs
        assert callable(on_delta)
        on_delta(reply)
        return {"content": reply, "usage": {}}

    def fake_execute(state, code, origin, emit, stream=True, language="python"):
        del state, code, origin, emit, stream, language
        return {
            "result": {
                "stdout": "",
                "stderr": "",
                "error": "NameError: name 'missing_name' is not defined",
            }
        }

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_execute_and_log", fake_execute)

    result = runner.run_message(frame_id, "default", "Run one broken cell")

    assert result["status"] == "failed"
    visible = "".join(
        event.get("chunk", "")
        for event in hub.events
        if event.get("type") == "text_chunk" and event.get("block_type") == "text"
    )
    assert "This cell failed" in visible
    assert "NameError" in visible
    assert "This worked perfectly" not in visible
    stored = runner.store.list_messages(frame_id)
    assert any("NameError" in message["content"] for message in stored)


def test_conversational_json_fence_does_not_cut_off_later_public_prose(
    monkeypatch, tmp_path
):
    dispatcher = SimpleNamespace(last_output=None)
    runner, hub, frame_id = _prepare_message_runner(monkeypatch, tmp_path, dispatcher)
    reply = (
        "The public response shape is:\n"
        '```json\n{"summary": "..."}\n```\n'
        "I will now verify the values.\n"
        "```python\nhost.submit_output({'summary': 'verified'}, ['Verified values'])\n```\n"
        "Unverified trailing claim."
    )

    def fake_chat(messages, cfg, on_delta=None, **kwargs):
        del messages, cfg, kwargs
        assert callable(on_delta)
        for offset in range(0, len(reply), 7):
            on_delta(reply[offset : offset + 7])
        return {"content": reply, "usage": {}}

    def fake_execute(state, code, origin, emit, stream=True, language="python"):
        del code, origin, emit, stream, language
        state.dispatcher.last_output = {
            "output": {"summary": "verified"},
            "completion_bullets": ["Verified values"],
        }
        return {"result": {"stdout": "", "stderr": "", "error": None}}

    monkeypatch.setattr(gateway_mod, "chat", fake_chat)
    monkeypatch.setattr(runner, "_execute_and_log", fake_execute)

    result = runner.run_message(frame_id, "default", "Verify the values")

    assert result["status"] == "completed"
    visible = "".join(
        event.get("chunk", "")
        for event in hub.events
        if event.get("type") == "text_chunk" and event.get("block_type") == "text"
    )
    assert "I will now verify the values." in visible
    assert '"summary": "..."' not in visible
    assert "Unverified trailing claim." not in visible


def test_wire_delegation_installs_live_event_and_child_step_sinks(tmp_path):
    """The Web composition wires both D8 sinks: the delegation_child_event
    normalizer (child ``output`` excluded server-side) and the root-keyed
    child step sink."""

    hub = _Hub()
    runner = gateway_mod.SessionRunner(_cfg(tmp_path), hub)
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    runner._ensure_runtime(state)
    delegated = state.delegation_runner
    assert delegated is not None
    tree = delegated._tree
    assert callable(tree.event_sink), "no delegation event sink wired"
    assert callable(tree.child_step_sink), "no child step sink wired"

    tree.event_sink(
        {
            "type": "delegation_child_event",
            "event": "done",
            "at": 123.0,
            "child": {
                "child_id": "c-1",
                "name": "helper",
                "status": "done",
                "task_status": "partial",
                "output": {"body": "child result body must not reach the socket"},
                "error": None,
                "depth": 1,
                "parent_child_id": None,
                "parent_frame_id": frame_id,
                "frame_id": "f-child",
                "created_at": 1.0,
                "started_at": 2.0,
                "finished_at": 3.0,
                "progress": {
                    "turn_boundary": 2,
                    "max_turns": 8,
                    "last_progress_at": 2.5,
                },
                "steering": {
                    "queued": 0,
                    "delivered": 1,
                    "discarded": 0,
                    "messages": [{"message_id": "m-1", "status": "delivered"}],
                },
                "overrides": {
                    "model": "deepseek-chat",
                    "steps": None,
                    "permissions": ["read"],
                    "capabilities": [],
                },
            },
        }
    )

    event = next(ev for ev in hub.events if ev.get("type") == "delegation_child_event")
    assert event["root_frame_id"] == frame_id
    child = event["child"]
    assert "output" not in child
    assert "messages" not in (child.get("steering") or {})
    assert child["task_status"] == "partial"
    assert child["frame_id"] == "f-child"
    assert child["steering"]["delivered"] == 1
    assert child["overrides"]["permission_count"] == 1

    tree.child_step_sink(
        {
            "phase": "begin",
            "step_id": "s-child-1",
            "kind": "skill",
            "title": "Loading a skill",
            "input": {
                "name": "x",
                "delegation": {
                    "delegation_child_id": "c-1",
                    "child_frame_id": "f-child",
                    "child_name": "helper",
                    "depth": 1,
                },
            },
        }
    )
    steps = runner.store.list_steps(frame_id)
    assert any(
        step["step_id"] == "s-child-1" for step in steps
    ), "child steps must persist root-keyed in frame_steps"
    step_event = next(ev for ev in hub.events if ev.get("type") == "step")
    assert step_event["step_id"] == "s-child-1"
    assert step_event["input"]["delegation"]["child_name"] == "helper"
