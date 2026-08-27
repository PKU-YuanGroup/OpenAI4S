"""Capability authorization for worker-local ``host.bash``."""

from __future__ import annotations

import hashlib
import os
import shlex
import signal
import sys
import time
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host.bash import BashAuthorizationService, command_digest
from openai4s.host_dispatch import build_dispatcher
from openai4s.sdk.bash import BashExecutor
from openai4s.sdk.host import build_host, decode_args


class _Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _proposal(workspace: Path, *, command: str = "echo allowed") -> dict:
    return {
        "command": command,
        "command_sha256": command_digest(command),
        "cwd": str(workspace.resolve()),
        "workspace": str(workspace.resolve()),
        "generation": "python:g-7",
        "challenge": "challenge-0123456789abcdef",
        "timeout": 30,
    }


def _consume(capability: dict) -> dict:
    return {
        "token": capability["token"],
        "command_sha256": capability["command_sha256"],
        "cwd": capability["cwd"],
        "generation": capability["generation"],
        "challenge": capability["challenge"],
    }


def _service(workspace: Path, **kwargs) -> BashAuthorizationService:
    return BashAuthorizationService(
        workspace=lambda: workspace,
        frame_id=lambda: "frame-1",
        token_factory=lambda: "test-token-0123456789abcdef0123456789",
        **kwargs,
    )


def test_capability_is_bound_and_single_use(tmp_path):
    service = _service(tmp_path)
    capability = service.authorize(_proposal(tmp_path))
    assert capability["command_sha256"] == command_digest("echo allowed")
    assert capability["frame_id"] == "frame-1"

    binding = _consume(capability)
    assert service.consume(binding)["ok"] is True
    replay = service.consume(binding)
    assert set(replay) == {"error"}
    assert "already consumed" in replay["error"]


@pytest.mark.parametrize("field", ["command_sha256", "cwd", "generation", "challenge"])
def test_tampered_binding_is_rejected(tmp_path, field):
    service = _service(tmp_path)
    capability = service.authorize(_proposal(tmp_path))
    binding = _consume(capability)
    binding[field] = str(binding[field]) + "-tampered"

    result = service.consume(binding)
    assert set(result) == {"error"}
    assert field in result["error"]


def test_expired_capability_is_rejected(tmp_path):
    clock = _Clock()
    service = _service(tmp_path, clock=clock, ttl_seconds=2)
    capability = service.authorize(_proposal(tmp_path))
    clock.value += 3

    result = service.consume(_consume(capability))
    assert set(result) == {"error"}
    assert "expired" in result["error"]


def test_host_independently_checks_generation_when_lifecycle_stamps_it(tmp_path):
    service = _service(tmp_path, generation=lambda: "python:persistent-9")
    proposal = _proposal(tmp_path)
    proposal["generation"] = "worker:claimed-8"

    result = service.authorize(proposal)
    assert set(result) == {"error"}
    assert "active Host generation" in result["error"]

    proposal["generation"] = "python:persistent-9"
    assert service.authorize(proposal)["generation"] == "python:persistent-9"


def test_cwd_escape_and_symlink_escape_are_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    service = _service(workspace)

    escaped = _proposal(workspace)
    escaped["cwd"] = str(outside)
    assert "escapes" in service.authorize(escaped)["error"]

    link = workspace / "outside-link"
    link.symlink_to(outside, target_is_directory=True)
    via_link = _proposal(workspace)
    via_link["cwd"] = str(link)
    assert "escapes" in service.authorize(via_link)["error"]


def test_explicit_allowed_root_can_authorize_cwd(tmp_path):
    workspace = tmp_path / "workspace"
    shared = tmp_path / "shared"
    workspace.mkdir()
    shared.mkdir()
    service = _service(workspace, allowed_roots=lambda: [shared])
    proposal = _proposal(workspace)
    proposal["cwd"] = str(shared)

    capability = service.authorize(proposal)
    assert capability["cwd"] == str(shared.resolve())
    assert capability["allowed_root"] == str(shared.resolve())


def test_sdk_without_host_authorization_fails_before_subprocess(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    host = build_host(lambda method, args: (_ for _ in ()).throw(ValueError(method)))

    with pytest.raises(RuntimeError, match="authorization unavailable"):
        host.bash("touch must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


def test_worker_rejects_tampered_capability_before_subprocess(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI4S_WORKSPACE", str(tmp_path))
    spawned = []
    # `Popen`, not `run`. The executor was rewritten to drain concurrently and
    # kill by process group; a stub still guarding `run` would record nothing
    # and this assertion would pass without testing anything.
    monkeypatch.setattr(
        "openai4s.sdk.bash.subprocess.Popen",
        lambda *args, **kwargs: spawned.append((args, kwargs)),
    )

    def tampered_authorizer(method, args):
        assert method == "authorize_bash"
        spec = decode_args(args)[0]
        now = int(time.time() * 1000)
        return {
            "version": "openai4s-bash-capability-v1",
            "token": "tampered-token-0123456789abcdef",
            "command_sha256": "0" * 64,
            "cwd": spec["cwd"],
            "workspace": str(tmp_path),
            "allowed_root": str(tmp_path),
            "frame_id": "frame-tampered",
            "generation": spec["generation"],
            "challenge": spec["challenge"],
            "issued_at_ms": now,
            "expires_at_ms": now + 10_000,
        }

    host = build_host(lambda method, args: None, bash_authorizer=tampered_authorizer)
    with pytest.raises(RuntimeError, match="command_sha256"):
        host.bash("touch tampered-must-not-run")
    assert spawned == []
    assert not (tmp_path / "tampered-must-not-run").exists()


def test_worker_local_replay_window_is_bounded():
    executor = BashExecutor(lambda method, args: None)
    for index in range(1100):
        executor._mark_token_used(f"token-{index}")

    assert len(executor._used_tokens) == 1024
    assert len(executor._used_token_order) == 1024
    assert "token-0" not in executor._used_tokens
    with pytest.raises(RuntimeError, match="replayed"):
        executor._mark_token_used("token-1099")


def test_sdk_executes_only_after_issue_and_consume_and_reports_diff(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI4S_WORKSPACE", str(tmp_path))
    audits = []
    service = BashAuthorizationService(
        workspace=lambda: tmp_path,
        frame_id=lambda: "frame-sdk",
        audit=lambda **fields: audits.append(fields),
    )
    calls = []

    def authorization(method, args):
        calls.append(method)
        spec = decode_args(args)[0]
        if method == "authorize_bash":
            return service.authorize(spec)
        if method == "consume_bash_authorization":
            return service.consume(spec)
        if method == "record_bash_result":
            return service.record_result(spec)
        raise AssertionError(method)

    host = build_host(lambda method, args: None, bash_authorizer=authorization)
    result = host.bash("printf 'science' > result.txt; printf 'done'")

    assert result["exit_code"] == 0
    assert result["stdout"] == "done"
    assert result["audit_recorded"] is True
    assert "result.txt" in result["workspace_diff"]["created"]
    assert calls == [
        "authorize_bash",
        "consume_bash_authorization",
        "record_bash_result",
    ]
    assert audits[0]["method"] == "bash"
    assert (
        audits[0]["args"][0]["stdout"]["sha256"] == hashlib.sha256(b"done").hexdigest()
    )
    executed_command = "printf 'science' > result.txt; printf 'done'"
    assert audits[0]["resource_keys"] == (
        f"command-sha256:{command_digest(executed_command)}",
    )


def test_host_dispatcher_permission_gate_and_secret_safe_audit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI4S_WORKSPACE", str(tmp_path))
    cfg = Config(
        data_dir=tmp_path / ".data",
        llm=LLMConfig(provider="deepseek", api_key="test-only"),
    )
    dispatcher = build_dispatcher(cfg, workspace=tmp_path)
    frame_id = dispatcher.store.new_frame(kind="turn")
    dispatcher.frame_id = frame_id
    steps = []
    dispatcher.on_step = steps.append

    # bash resolves to ask and unattended approval is deny by default.
    denied = build_host(dispatcher)
    with pytest.raises(RuntimeError, match="Permission denied"):
        denied.bash("printf 'API_TOKEN=super-secret-value' > denied.txt")
    assert not (tmp_path / "denied.txt").exists()

    dispatcher.store.set_permission_rule(
        scope="conversation",
        scope_id=frame_id,
        tool="bash",
        pattern="*",
        decision="allow",
    )
    allowed = build_host(dispatcher)
    with dispatcher.bind_action_context(
        {"action_group_id": "group-test-cell", "action_id": "group-test-cell:action"}
    ):
        result = allowed.bash("printf 'API_TOKEN=super-secret-value'")
    assert result["exit_code"] == 0

    rows = dispatcher.store._conn.execute(
        "SELECT method,args_preview,action_group_id,resource_keys "
        "FROM host_call_log ORDER BY created_at"
    ).fetchall()
    requests = dispatcher.store._conn.execute(
        "SELECT target,payload FROM permission_requests ORDER BY created_at"
    ).fetchall()
    persisted = "\n".join(str(tuple(row)) for row in [*rows, *requests])
    assert "super-secret-value" not in persisted
    assert "<redacted secret args>" in persisted
    assert "<redacted>" in persisted
    step_dump = repr(steps)
    assert "super-secret-value" not in step_dump
    completed = [
        event
        for event in steps
        if event.get("phase") == "end"
        and isinstance(event.get("output"), dict)
        and event["output"].get("command_category")
    ]
    assert completed[-1]["output"]["exit_code"] == 0
    assert completed[-1]["output"]["stdout"]["chars"] > 0
    assert "workspace_diff" in completed[-1]["output"]
    bash_row = next(row for row in rows if row["method"] == "bash")
    assert bash_row["action_group_id"] == "group-test-cell"
    authorized_command = "printf 'API_TOKEN=super-secret-value'"
    assert f"command-sha256:{command_digest(authorized_command)}" in (
        bash_row["resource_keys"] or ""
    )


def test_secret_file_reference_is_rejected_by_host(tmp_path):
    service = _service(tmp_path)
    for command in ("cat .env", "head secrets.pem", "cp id_rsa backup"):
        result = service.authorize(_proposal(tmp_path, command=command))
        assert set(result) == {"error"}
        assert "secret files" in result["error"]


def test_result_must_match_consumed_token_and_records_once(tmp_path):
    clock = _Clock(time.time())
    service = _service(tmp_path, clock=clock)
    capability = service.authorize(_proposal(tmp_path))
    binding = _consume(capability)
    assert service.consume(binding)["ok"] is True
    result = {
        **binding,
        "status": "completed",
        "exit_code": 0,
        "stdout": "ok",
        "stderr": "",
        "workspace_diff": {},
    }
    assert service.record_result(result)["ok"] is True
    assert "unknown token" in service.record_result(result)["error"]


def test_consumed_unreported_capability_is_eventually_purged(tmp_path):
    clock = _Clock()
    tokens = iter(
        (
            "test-token-first-0123456789abcdef",
            "test-token-second-0123456789abcdef",
        )
    )
    service = BashAuthorizationService(
        workspace=lambda: tmp_path,
        frame_id=lambda: "frame-cleanup",
        clock=clock,
        ttl_seconds=2,
        token_factory=lambda: next(tokens),
    )
    first = service.authorize(_proposal(tmp_path))
    assert service.consume(_consume(first))["ok"] is True

    # No record_bash_result arrives (for example a killed worker).  A later
    # issuance must clear the abandoned consumed token after the retention
    # window instead of permanently consuming one of the 1024 slots.
    clock.value += 3603
    second = service.authorize(_proposal(tmp_path, command="echo second"))
    assert second["token"].startswith("test-token-second")
    assert first["token"] not in service._issued


def _real_authorizer(tmp_path):
    service = BashAuthorizationService(
        workspace=lambda: tmp_path,
        frame_id=lambda: "frame-sdk",
        audit=lambda **fields: None,
    )

    def authorization(method, args):
        spec = decode_args(args)[0]
        if method == "authorize_bash":
            return service.authorize(spec)
        if method == "consume_bash_authorization":
            return service.consume(spec)
        if method == "record_bash_result":
            return service.record_result(spec)
        raise AssertionError(method)

    return authorization


def test_a_timed_out_command_takes_its_children_with_it(tmp_path, monkeypatch):
    """`subprocess.run(timeout=)` with `shell=True` kills the shell alone.

    `bash -c "python train.py"` lost the shell and kept the python -- the
    process actually holding the GPU, the file handles and the memory. The
    timeout looked honoured and the work carried on, exactly the shape the
    local Jobs manager already had a process-group ladder for; the kernel-side
    executor had none, so the two disagreed about the case that matters.

    Real processes throughout: a mocked Popen cannot show a child outliving
    its parent's signal.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI4S_WORKSPACE", str(tmp_path))
    marker = tmp_path / "child.pid"
    host = build_host(
        lambda method, args: None, bash_authorizer=_real_authorizer(tmp_path)
    )

    with pytest.raises(RuntimeError, match="timed out"):
        host.bash(
            f"sleep 120 & echo $! > {marker}; wait",
            timeout=1.5,
        )

    assert marker.exists(), "the child never started; the test proves nothing"
    child_pid = int(marker.read_text().strip())

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        try:
            os.kill(child_pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        raise AssertionError(
            "the timeout reached the shell but not the child it started"
        )


def test_a_shell_that_exits_first_does_not_escape_the_deadline(tmp_path, monkeypatch):
    """The half the test above does not reach: the shell exits, the work does not.

    `sleep 120 & ...; wait` makes the shell outlive its child, so
    `proc.wait(timeout=)` raises and the group ladder runs. `... & exit 0` does
    the opposite, and the deadline evaporated: measured before this change, the
    wait returned in 0.00s with no `TimeoutExpired`, so the group was never
    signalled, the drain threads stayed blocked on pipes the survivor held --
    one leaked thread per call -- the work finished six seconds past a
    two-second deadline, and `host.bash` reported `completed` with rc=0. A
    terminal state for a job that was still running.

    `start_new_session=True` was already there for exactly this, and
    `stop_process_group`'s docstring already said "both `proc.poll()` and
    `proc.wait()` answer about the leader alone". Only the wait was never
    switched to match.

    Real processes, for the same reason as the test above.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI4S_WORKSPACE", str(tmp_path))
    marker = tmp_path / "survived.txt"
    host = build_host(
        lambda method, args: None, bash_authorizer=_real_authorizer(tmp_path)
    )

    started = time.time()
    with pytest.raises(RuntimeError, match="timed out"):
        host.bash(f"( sleep 6; echo survived > {marker} ) & exit 0", timeout=1.5)
    elapsed = time.time() - started

    # It waited for the deadline rather than returning the instant the shell
    # exited, which is what made the old behaviour look like a fast success.
    assert elapsed >= 1.4, f"returned in {elapsed:.2f}s; the deadline was not held"

    # And the work the shell left behind is gone. Polled rather than slept on
    # once: the assertion is that it never completes, so give it longer than it
    # would have needed.
    deadline = time.time() + 8
    while time.time() < deadline:
        if marker.exists():
            raise AssertionError(
                "the abandoned child outlived the deadline and finished its work"
            )
        time.sleep(0.1)


def test_command_output_is_bounded_as_it_is_produced(tmp_path, monkeypatch):
    """`capture_output=True` held both whole streams before any slice ran.

    The `[-30000:]` on the way out described what the caller saw, not what the
    worker allocated: a command printing a gigabyte put a gigabyte in the
    kernel's memory first and then showed 30k of it.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI4S_WORKSPACE", str(tmp_path))
    host = build_host(
        lambda method, args: None, bash_authorizer=_real_authorizer(tmp_path)
    )

    result = host.bash(
        f"{shlex.quote(sys.executable)} -c "
        + shlex.quote("import sys\nsys.stdout.write('z' * 5_000_000)\n"),
        timeout=60,
    )

    assert result["exit_code"] == 0
    assert len(result["stdout"]) <= 30_000
    # The tail is what is kept, so the end of the stream survives.
    assert result["stdout"].endswith("z")
