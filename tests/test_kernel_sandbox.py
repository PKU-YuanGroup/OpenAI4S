"""Offline contract tests for the Python/R OS sandbox spawn boundary."""

from __future__ import annotations

import json
import os
import signal
import stat
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

import pytest

import openai4s.kernel.manager as manager_module
import openai4s.kernel.transport as transport_module
import openai4s.security.sandbox as sandbox_module
from openai4s.kernel import Kernel
from openai4s.kernel.transport import PipeTransport
from openai4s.security.sandbox import (
    KernelReadIsolation,
    KernelSandbox,
    SandboxConfigurationError,
    SandboxStatus,
    SandboxUnavailableError,
    build_seatbelt_profile,
    create_kernel_sandbox,
    wrap_bwrap_command,
    wrap_seatbelt_command,
)


@pytest.fixture(autouse=True)
def _reset_warn_once_dedup():
    """``_warn_once`` dedups by message for the whole process, so a warning
    already emitted by an earlier test (e.g. a real kernel on a bwrap-less CI
    runner) would make a later ``pytest.warns`` assertion see nothing.  Reset
    the cache before each test so the security-warning assertions are
    order-independent."""
    sandbox_module._warned_details.clear()
    yield


def _passing_runner(calls: list | None = None, *, sibling_isolated: bool = False):
    def run(command, **kwargs):
        if calls is not None:
            calls.append((list(command), kwargs))
        checks = {
            "network_blocked": True,
            "outside_write_blocked": True,
            "temp_write": True,
            "workspace_write": True,
        }
        if sibling_isolated:
            checks.update(
                {
                    "allowed_roots_readable": True,
                    "sibling_read_blocked": True,
                    "sibling_symlink_read_blocked": True,
                    "sibling_hardlink_read_blocked": True,
                }
            )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"ok": True, "checks": checks}) + "\n",
            stderr="",
        )

    return run


def _failing_runner(command, **kwargs):
    del command, kwargs
    return SimpleNamespace(
        returncode=71,
        stdout="",
        stderr="sandbox_apply: Operation not permitted\n",
    )


def test_seatbelt_profile_escapes_paths_and_blocks_network_by_default():
    workspace = '/tmp/project with space/quote"\\tail) (allow network*)'
    temp_dir = "/tmp/private temp"

    profile = build_seatbelt_profile(workspace, temp_dir)

    assert "(deny network*)" in profile
    assert '(subpath "/tmp/project with space/quote\\"\\\\tail) ' in profile
    assert '(subpath "/tmp/private temp")' in profile
    assert '(literal "/dev/fd/3")' in profile
    assert profile.count("(allow default)") == 1
    # The path stays one quoted Scheme string; its quote cannot terminate the
    # path and inject the attacker-shaped policy text that follows it.
    assert 'quote"\\tail' not in profile


def test_seatbelt_raw_network_switch_is_explicit_and_argv_has_no_shell():
    command = ["/usr/bin/python3", "-c", "print('ok')"]
    wrapped = wrap_seatbelt_command(
        command,
        executable="/usr/bin/sandbox-exec",
        workspace="/tmp/work",
        temp_dir="/tmp/private",
        allow_raw_network=True,
    )

    assert wrapped[:2] == ["/usr/bin/sandbox-exec", "-p"]
    assert "(deny network*)" not in wrapped[2]
    assert wrapped[3:] == command


def test_seatbelt_denies_sibling_reads_and_reallows_only_exact_workspace(tmp_path):
    root = tmp_path / "agent-workspaces"
    workspace = root / "attacker"
    sibling = root / "victim"
    workspace.mkdir(parents=True)
    sibling.mkdir()

    profile = build_seatbelt_profile(
        workspace,
        tmp_path / "private",
        read_isolation=KernelReadIsolation((root,)),
    )

    root_q = sandbox_module._seatbelt_string(root.resolve())
    workspace_q = sandbox_module._seatbelt_string(workspace.resolve())
    deny = f"(deny file-read* (literal {root_q}) (subpath {root_q}))"
    allow = f"(allow file-read* (literal {workspace_q}) " f"(subpath {workspace_q}))"
    assert deny in profile
    assert allow in profile
    assert profile.index(deny) < profile.index(allow)
    assert str(sibling.resolve()) not in profile


def test_seatbelt_rejects_a_workspace_outside_or_equal_to_isolation_root(tmp_path):
    root = tmp_path / "agent-workspaces"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(SandboxConfigurationError, match="strict child"):
        build_seatbelt_profile(
            outside,
            tmp_path / "private",
            read_isolation=KernelReadIsolation((root,)),
        )
    with pytest.raises(SandboxConfigurationError, match="strict child"):
        build_seatbelt_profile(
            root,
            tmp_path / "private",
            read_isolation=KernelReadIsolation((root,)),
        )


def test_seatbelt_multi_tenant_policy_denies_data_and_personal_roots_exactly(
    tmp_path,
):
    data_dir = tmp_path / "daemon-data"
    workspace = data_dir / "agent-workspaces" / "current"
    current_skill = data_dir / "project-skills" / "current" / "pdf"
    foreign_skill = data_dir / "project-skills" / "foreign" / "private"
    users = tmp_path / "scratch" / "users"
    mine = users / "alice"
    other = users / "bob"
    private = tmp_path / "private"
    for directory in (workspace, current_skill, foreign_skill, mine, other, private):
        directory.mkdir(parents=True)

    policy = KernelReadIsolation(
        (data_dir, users),
        (current_skill, mine),
    )
    profile = build_seatbelt_profile(
        workspace,
        private,
        read_isolation=policy,
    )

    for root in (data_dir.resolve(), users.resolve()):
        quoted = sandbox_module._seatbelt_string(root)
        assert f"(literal {quoted})" in profile
        assert f"(subpath {quoted})" in profile
    for allowed in (workspace.resolve(), current_skill.resolve(), mine.resolve()):
        quoted = sandbox_module._seatbelt_string(allowed)
        assert f"(allow file-read* (literal {quoted}) (subpath {quoted}))" in profile
    assert str(foreign_skill.resolve()) not in profile
    assert str(other.resolve()) not in profile


def test_bwrap_mounts_only_workspace_and_private_temp_writable():
    workspace = "/tmp/project; still-one-argument"
    temp_dir = "/tmp/kernel-private"
    command = ["/usr/bin/python3", "-u", "/code/worker.py"]

    wrapped = wrap_bwrap_command(
        command,
        executable="/usr/bin/bwrap",
        workspace=workspace,
        temp_dir=temp_dir,
    )

    assert wrapped[0] == "/usr/bin/bwrap"
    assert "--unshare-net" in wrapped
    assert wrapped[wrapped.index("--ro-bind") + 1 : wrapped.index("--ro-bind") + 3] == [
        "/",
        "/",
    ]
    bind_positions = [i for i, value in enumerate(wrapped) if value == "--bind"]
    assert [[wrapped[i + 1], wrapped[i + 2]] for i in bind_positions] == [
        [workspace, workspace],
        [temp_dir, temp_dir],
    ]
    assert wrapped[wrapped.index("--") + 1 :] == command


def test_runtime_bwrap_inherits_the_pipe_transport_session(tmp_path):
    """One process group must contain bwrap, the worker, and Cell children.

    ``PipeTransport`` already uses ``start_new_session=True``. A second
    ``bwrap --new-session`` moved the worker into another group, so the
    transport's watchdog group kill stopped only bwrap and leaked Cell work.
    The standalone wrapper keeps its default flag for self-tests and other
    callers; the runtime adapter deliberately omits the duplicate boundary.
    """

    sandbox = KernelSandbox(
        status=SandboxStatus(
            state="enabled",
            mode="enforce",
            backend="bubblewrap",
            enforced=True,
            self_test_passed=True,
            network_policy="blocked",
            workspace=str(tmp_path),
            temp_dir=str(tmp_path / "private"),
            detail="test",
        ),
        executable="/usr/bin/bwrap",
        temp_dir=str(tmp_path / "private"),
    )

    wrapped = sandbox.wrap_command(["/bin/true"])

    assert "--new-session" not in wrapped
    assert "--new-session" in wrap_bwrap_command(
        ["/bin/true"],
        executable="/usr/bin/bwrap",
        workspace=tmp_path,
        temp_dir=tmp_path / "private",
    )


def test_bwrap_raw_network_compatibility_switch_only_removes_network_namespace():
    wrapped = wrap_bwrap_command(
        ["/bin/true"],
        executable="bwrap",
        workspace="/workspace",
        temp_dir="/kernel-tmp",
        allow_raw_network=True,
    )

    assert "--unshare-net" not in wrapped
    assert ["--ro-bind", "/", "/"] == wrapped[
        wrapped.index("--ro-bind") : wrapped.index("--ro-bind") + 3
    ]


def test_bwrap_hides_shared_root_and_rebinds_only_workspace(tmp_path):
    root = (tmp_path / "agent-workspaces").resolve()
    workspace = root / "attacker"
    sibling = root / "victim"
    private = tmp_path / "private"
    workspace.mkdir(parents=True)
    sibling.mkdir()
    private.mkdir()

    wrapped = wrap_bwrap_command(
        ["/bin/true"],
        executable="bwrap",
        workspace=workspace,
        temp_dir=private,
        read_isolation=KernelReadIsolation((root,)),
    )

    assert "--unshare-pid" in wrapped
    tmpfs = wrapped.index("--tmpfs")
    workspace_bind = next(
        index
        for index, value in enumerate(wrapped)
        if value == "--bind"
        and wrapped[index + 1 : index + 3] == [str(workspace), str(workspace)]
    )
    remount = wrapped.index("--remount-ro")
    assert wrapped[tmpfs : tmpfs + 2] == ["--tmpfs", str(root)]
    assert tmpfs < workspace_bind < remount
    assert wrapped[remount : remount + 2] == ["--remount-ro", str(root)]
    assert str(sibling) not in wrapped


def test_bwrap_masks_every_private_root_and_ro_binds_only_exact_exceptions(tmp_path):
    data_dir = (tmp_path / "daemon-data").resolve()
    workspace = data_dir / "agent-workspaces" / "current"
    skill = data_dir / "project-skills" / "current" / "skill"
    foreign_skill = data_dir / "project-skills" / "foreign" / "skill"
    users = (tmp_path / "scratch" / "users").resolve()
    mine = users / "alice"
    other = users / "bob"
    private = data_dir / "kernel-temp" / "current"
    for directory in (workspace, skill, foreign_skill, mine, other, private):
        directory.mkdir(parents=True)

    wrapped = wrap_bwrap_command(
        ["/bin/true"],
        executable="bwrap",
        workspace=workspace,
        temp_dir=private,
        read_isolation=KernelReadIsolation(
            (data_dir, users),
            (skill, mine, private),
        ),
    )

    tmpfs_roots = {
        wrapped[index + 1] for index, value in enumerate(wrapped) if value == "--tmpfs"
    }
    assert tmpfs_roots == {str(data_dir), str(users)}
    ro_binds = {
        wrapped[index + 1]
        for index, value in enumerate(wrapped)
        if value == "--ro-bind" and wrapped[index + 1] != "/"
    }
    assert ro_binds == {str(skill), str(mine)}
    assert str(foreign_skill) not in wrapped
    assert str(other) not in wrapped
    temp_bind = ["--bind", str(private), str(private)]
    assert any(wrapped[index : index + 3] == temp_bind for index in range(len(wrapped)))


def _team_interrupt_sandbox(tmp_path):
    root = tmp_path / "daemon-data"
    workspace = root / "agent-workspaces" / "current"
    private = root / "kernel-temp" / "current"
    workspace.mkdir(parents=True)
    private.mkdir(parents=True)
    return KernelSandbox(
        status=SandboxStatus(
            mode="auto",
            state="enabled",
            backend="bubblewrap",
            enforced=True,
            self_test_passed=True,
            network_policy="blocked",
            workspace=str(workspace),
            temp_dir=str(private),
            detail="forced Linux private PID test",
        ),
        executable="/usr/bin/bwrap",
        temp_dir=str(private),
        read_isolation=KernelReadIsolation((root,)),
    )


def _write_proc_status(proc_root, pid, parent):
    process_dir = proc_root / str(pid)
    process_dir.mkdir(parents=True, exist_ok=True)
    (process_dir / "status").write_text(
        f"Name:\tprocess-{pid}\nPPid:\t{parent}\n",
        encoding="ascii",
    )


def _write_proc_children(proc_root, pid, children):
    task_dir = proc_root / str(pid) / "task" / str(pid)
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "children").write_text(
        " ".join(str(child) for child in children),
        encoding="ascii",
    )


def _seed_team_bwrap_proc(proc_root, *, launcher=4100, init=4101, command=4102):
    _write_proc_status(proc_root, init, launcher)
    _write_proc_status(proc_root, command, init)
    _write_proc_children(proc_root, launcher, (init,))
    _write_proc_children(proc_root, init, (command,))


def test_team_bwrap_uses_info_fd_and_persistent_pidfd_for_interrupts(
    monkeypatch, tmp_path
):
    sandbox = _team_interrupt_sandbox(tmp_path)
    proc_root = tmp_path / "proc"
    _seed_team_bwrap_proc(proc_root)
    init_pidfd, init_pidfd_peer = os.pipe()
    worker_pidfd, worker_pidfd_peer = os.pipe()
    opened = []
    sent = []

    def open_pidfd(pid, flags):
        opened.append((pid, flags))
        return {4101: init_pidfd, 4102: worker_pidfd}[pid]

    monkeypatch.setattr(
        sandbox_module.os,
        "pidfd_open",
        open_pidfd,
        raising=False,
    )
    monkeypatch.setattr(
        sandbox_module.signal,
        "pidfd_send_signal",
        lambda *args: sent.append(args),
        raising=False,
    )
    wrapped = sandbox.wrap_command(["/bin/true"])
    (info_fd,) = sandbox.popen_pass_fds()
    assert wrapped[wrapped.index("--info-fd") : wrapped.index("--info-fd") + 2] == [
        "--info-fd",
        str(info_fd),
    ]
    # bubblewrap reports its raw-clone namespace init, not the command which
    # that init forks after the info report releases it.
    os.write(info_fd, b'{"child-pid": 4101}')
    sandbox.adopt_process(4100, proc_root=proc_root)

    assert opened == [(4101, 0), (4102, 0)]
    with pytest.raises(OSError):
        os.fstat(init_pidfd)
    with pytest.raises(OSError):
        os.fstat(info_fd)
    assert sandbox.send_interrupt(9999, signal.SIGINT) is True
    assert sent
    assert all(call[1] == 0 for call in sent)
    assert sandbox.send_interrupt(4100, signal.SIGINT) is True
    assert sent[-1] == (worker_pidfd, signal.SIGINT, None, 0)

    sandbox.close()
    with pytest.raises(OSError):
        os.fstat(worker_pidfd)
    os.close(init_pidfd_peer)
    os.close(worker_pidfd_peer)


@pytest.mark.parametrize(
    ("report", "failure"),
    [
        (b"{}", "omitted child-pid"),
        (b"x" * (sandbox_module._BWRAP_INFO_MAX_BYTES + 1), "byte limit"),
    ],
)
def test_team_bwrap_rejects_invalid_bounded_info_report(tmp_path, report, failure):
    sandbox = _team_interrupt_sandbox(tmp_path)
    sandbox.wrap_command(["/bin/true"])
    (info_fd,) = sandbox.popen_pass_fds()
    os.write(info_fd, report)

    with pytest.raises(SandboxUnavailableError, match=failure):
        sandbox.adopt_process(4100)
    sandbox.close()


def test_team_bwrap_info_timeout_and_missing_pidfd_fail_closed(monkeypatch, tmp_path):
    timed_out = _team_interrupt_sandbox(tmp_path / "timeout")
    timed_out.wrap_command(["/bin/true"])
    monkeypatch.setattr(sandbox_module, "_BWRAP_INFO_TIMEOUT_S", 0.0)
    with pytest.raises(SandboxUnavailableError, match="before timeout"):
        timed_out.adopt_process(4100)
    timed_out.close()

    monkeypatch.setattr(sandbox_module, "_BWRAP_INFO_TIMEOUT_S", 5.0)
    unsupported = _team_interrupt_sandbox(tmp_path / "unsupported")
    unsupported.wrap_command(["/bin/true"])
    (info_fd,) = unsupported.popen_pass_fds()
    os.write(info_fd, b'{"child-pid": 4101}')
    monkeypatch.setattr(sandbox_module.os, "pidfd_open", None, raising=False)
    monkeypatch.setattr(sandbox_module.signal, "pidfd_send_signal", None, raising=False)
    with pytest.raises(SandboxUnavailableError, match="requires pidfd support"):
        unsupported.adopt_process(4100)
    unsupported.close()


@pytest.mark.parametrize(
    ("children", "failure"),
    [
        ((), "one command before timeout"),
        ((4102, 4103), "multiple direct children"),
    ],
)
def test_team_bwrap_rejects_zero_or_multiple_init_children(
    monkeypatch, tmp_path, children, failure
):
    sandbox = _team_interrupt_sandbox(tmp_path)
    proc_root = tmp_path / "proc"
    _seed_team_bwrap_proc(proc_root)
    _write_proc_children(proc_root, 4101, children)
    init_pidfd, init_pidfd_peer = os.pipe()
    monkeypatch.setattr(
        sandbox_module.os,
        "pidfd_open",
        lambda pid, _flags: init_pidfd if pid == 4101 else pytest.fail(str(pid)),
        raising=False,
    )
    monkeypatch.setattr(
        sandbox_module.signal,
        "pidfd_send_signal",
        lambda *_args: None,
        raising=False,
    )
    monkeypatch.setattr(sandbox_module, "_BWRAP_CHILD_TIMEOUT_S", 0.0)
    sandbox.wrap_command(["/bin/true"])
    (info_fd,) = sandbox.popen_pass_fds()
    os.write(info_fd, b'{"child-pid": 4101}')

    with pytest.raises(SandboxUnavailableError, match=failure):
        sandbox.adopt_process(4100, proc_root=proc_root)
    with pytest.raises(OSError):
        os.fstat(init_pidfd)
    os.close(init_pidfd_peer)
    sandbox.close()


@pytest.mark.parametrize(
    ("changed_process", "failure"),
    [
        ("init", "namespace init changed launcher"),
        ("command", "command changed parent"),
    ],
)
def test_team_bwrap_revalidates_parent_chain_after_pinning_command(
    monkeypatch, tmp_path, changed_process, failure
):
    sandbox = _team_interrupt_sandbox(tmp_path)
    proc_root = tmp_path / "proc"
    _seed_team_bwrap_proc(proc_root)
    init_pidfd, init_pidfd_peer = os.pipe()
    worker_pidfd, worker_pidfd_peer = os.pipe()

    def open_pidfd(pid, _flags):
        if pid == 4101:
            return init_pidfd
        assert pid == 4102
        if changed_process == "init":
            _write_proc_status(proc_root, 4101, 9999)
        else:
            _write_proc_status(proc_root, 4102, 9999)
        return worker_pidfd

    monkeypatch.setattr(
        sandbox_module.os,
        "pidfd_open",
        open_pidfd,
        raising=False,
    )
    monkeypatch.setattr(
        sandbox_module.signal,
        "pidfd_send_signal",
        lambda *_args: None,
        raising=False,
    )
    sandbox.wrap_command(["/bin/true"])
    (info_fd,) = sandbox.popen_pass_fds()
    os.write(info_fd, b'{"child-pid": 4101}')

    with pytest.raises(SandboxUnavailableError, match=failure):
        sandbox.adopt_process(4100, proc_root=proc_root)
    with pytest.raises(OSError):
        os.fstat(init_pidfd)
    with pytest.raises(OSError):
        os.fstat(worker_pidfd)
    os.close(init_pidfd_peer)
    os.close(worker_pidfd_peer)
    sandbox.close()


def test_pipe_transport_passes_only_requested_info_fd(tmp_path):
    read_fd, write_fd = os.pipe()
    started = []
    transport = PipeTransport(
        [
            sys.executable,
            "-c",
            f"import os; os.write({write_fd}, b'child-info')",
        ],
        cwd=str(tmp_path),
        env=dict(os.environ),
        pass_fds=(write_fd,),
        process_started=started.append,
    )
    os.close(write_fd)
    try:
        assert os.read(read_fd, 64) == b"child-info"
        assert started == [transport.process.pid]
    finally:
        os.close(read_fd)
        transport.close()


def test_pipe_transport_reaps_and_closes_when_process_adoption_fails(
    monkeypatch, tmp_path
):
    class Stream:
        closed = False

        def close(self):
            self.closed = True

    class Process:
        pid = 4100

        def __init__(self):
            self.stdin = Stream()
            self.stdout = Stream()
            self.stderr = Stream()
            self.killed = False
            self.waits = []

        def kill(self):
            self.killed = True

        def wait(self, *, timeout):
            self.waits.append(timeout)
            return 0

    process = Process()
    monkeypatch.setattr(
        transport_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )

    def reject_adoption(_pid):
        raise SandboxUnavailableError("ambiguous private PID tree")

    with pytest.raises(SandboxUnavailableError, match="ambiguous private PID tree"):
        PipeTransport(
            ["bwrap", "--unshare-pid", "/bin/true"],
            cwd=str(tmp_path),
            env={},
            pass_fds=(9,),
            process_started=reject_adoption,
        )

    assert process.killed is True
    assert process.waits == [2]
    assert all(
        stream.closed for stream in (process.stdin, process.stdout, process.stderr)
    )


def _interrupt_sandbox(tmp_path, *, backend="bubblewrap", enforced=True):
    return KernelSandbox(
        status=SandboxStatus(
            mode="enforce" if enforced else "off",
            state="enabled" if enforced else "disabled",
            backend=backend,
            enforced=enforced,
            self_test_passed=True if enforced else None,
            network_policy="blocked" if enforced else "not_enforced",
            workspace=str(tmp_path),
            temp_dir=None,
            detail="interrupt resolver test",
        )
    )


def test_bwrap_interrupt_resolves_and_validates_direct_worker(tmp_path):
    launcher = 4100
    worker = 4101
    task_dir = tmp_path / str(launcher) / "task" / str(launcher)
    task_dir.mkdir(parents=True)
    (task_dir / "children").write_text(f"{worker}\n", encoding="ascii")
    worker_dir = tmp_path / str(worker)
    worker_dir.mkdir()
    (worker_dir / "status").write_text(
        f"Name:\tpython\nPPid:\t{launcher}\n", encoding="utf-8"
    )

    sandbox = _interrupt_sandbox(tmp_path)

    assert sandbox.interrupt_target_pid(launcher, proc_root=tmp_path) == worker


@pytest.mark.parametrize(
    ("children", "worker_status"),
    [
        ("", None),
        ("4101 4102", None),
        ("not-a-pid", None),
        ("4101", "Name:\tpython\nPPid:\t9999\n"),
        ("4101", "Name:\tpython\n"),
    ],
)
def test_bwrap_interrupt_falls_back_for_ambiguous_procfs(
    tmp_path, children, worker_status
):
    launcher = 4100
    task_dir = tmp_path / str(launcher) / "task" / str(launcher)
    task_dir.mkdir(parents=True)
    (task_dir / "children").write_text(children, encoding="ascii")
    if worker_status is not None:
        worker_dir = tmp_path / "4101"
        worker_dir.mkdir()
        (worker_dir / "status").write_text(worker_status, encoding="utf-8")

    sandbox = _interrupt_sandbox(tmp_path)

    assert sandbox.interrupt_target_pid(launcher, proc_root=tmp_path) == launcher


@pytest.mark.parametrize(
    ("backend", "enforced"), [("seatbelt", True), ("bubblewrap", False)]
)
def test_interrupt_target_uses_launcher_outside_enforced_bwrap(
    tmp_path, backend, enforced
):
    sandbox = _interrupt_sandbox(tmp_path, backend=backend, enforced=enforced)

    assert sandbox.interrupt_target_pid(4100, proc_root=tmp_path) == 4100
    assert sandbox.send_interrupt(4100, signal.SIGINT, proc_root=tmp_path) is False


def test_bwrap_interrupt_pins_worker_with_pidfd_before_signalling(
    tmp_path, monkeypatch
):
    launcher = 4100
    worker = 4101
    task_dir = tmp_path / str(launcher) / "task" / str(launcher)
    task_dir.mkdir(parents=True)
    (task_dir / "children").write_text(f"{worker}\n", encoding="ascii")
    worker_dir = tmp_path / str(worker)
    worker_dir.mkdir()
    (worker_dir / "status").write_text(
        f"Name:\tpython\nPPid:\t{launcher}\n", encoding="utf-8"
    )
    sandbox = _interrupt_sandbox(tmp_path)
    read_fd, write_fd = os.pipe()
    opened = []
    sent = []

    def pidfd_open(pid, flags):
        opened.append((pid, flags))
        return read_fd

    def pidfd_send_signal(fd, signum, siginfo, flags):
        sent.append((fd, signum, siginfo, flags))

    monkeypatch.setattr(sandbox_module.os, "pidfd_open", pidfd_open, raising=False)
    monkeypatch.setattr(
        sandbox_module.signal,
        "pidfd_send_signal",
        pidfd_send_signal,
        raising=False,
    )
    try:
        assert (
            sandbox.send_interrupt(launcher, signal.SIGINT, proc_root=tmp_path) is True
        )
        with pytest.raises(OSError):
            os.fstat(read_fd)
    finally:
        os.close(write_fd)

    assert opened == [(worker, 0)]
    assert sent == [(read_fd, signal.SIGINT, None, 0)]


def test_bwrap_interrupt_refuses_a_target_that_changes_after_pidfd_open(
    tmp_path, monkeypatch
):
    launcher = 4100
    worker = 4101
    sandbox = _interrupt_sandbox(tmp_path)
    targets = iter((worker, launcher))
    read_fd, write_fd = os.pipe()
    sent = []

    monkeypatch.setattr(
        sandbox,
        "interrupt_target_pid",
        lambda *_a, **_k: next(targets),
    )
    monkeypatch.setattr(
        sandbox_module.os,
        "pidfd_open",
        lambda _pid, _flags: read_fd,
        raising=False,
    )
    monkeypatch.setattr(
        sandbox_module.signal,
        "pidfd_send_signal",
        lambda *_args: sent.append(_args),
        raising=False,
    )
    try:
        assert (
            sandbox.send_interrupt(launcher, signal.SIGINT, proc_root=tmp_path) is True
        )
        with pytest.raises(OSError):
            os.fstat(read_fd)
    finally:
        os.close(write_fd)

    assert sent == []


@pytest.mark.parametrize("failure", ["unsupported", "exited"])
def test_bwrap_interrupt_never_falls_back_to_a_numeric_child_pid(
    tmp_path, monkeypatch, failure
):
    launcher = 4100
    worker = 4101
    sandbox = _interrupt_sandbox(tmp_path)
    monkeypatch.setattr(
        sandbox,
        "interrupt_target_pid",
        lambda *_a, **_k: worker,
    )
    killed = []
    monkeypatch.setattr(sandbox_module.os, "kill", lambda *args: killed.append(args))

    if failure == "unsupported":
        monkeypatch.setattr(sandbox_module.os, "pidfd_open", None, raising=False)
        monkeypatch.setattr(
            sandbox_module.signal, "pidfd_send_signal", None, raising=False
        )
    else:

        def exited(_pid, _flags):
            raise ProcessLookupError("worker exited")

        monkeypatch.setattr(sandbox_module.os, "pidfd_open", exited, raising=False)
        monkeypatch.setattr(
            sandbox_module.signal,
            "pidfd_send_signal",
            lambda *_args: None,
            raising=False,
        )

    assert sandbox.send_interrupt(launcher, signal.SIGINT, proc_root=tmp_path) is True
    assert killed == []


def test_bwrap_interrupt_reports_an_unreachable_worker_once(
    tmp_path, monkeypatch, capsys
):
    """Dropping a stop request is an accepted trade only while it is visible."""

    sandbox = _interrupt_sandbox(tmp_path)
    monkeypatch.setattr(
        sandbox_module.os, "pidfd_open", lambda _pid, _flags: 99, raising=False
    )
    monkeypatch.setattr(
        sandbox_module.signal,
        "pidfd_send_signal",
        lambda *_args: None,
        raising=False,
    )

    # tmp_path carries no procfs structure, so the resolver falls back to the
    # launcher: the adapter owns the request and deliberately drops it.
    assert sandbox.send_interrupt(4100, signal.SIGINT, proc_root=tmp_path) is True
    first = capsys.readouterr().err
    assert "cell interrupt cannot reach the sandboxed worker" in first
    assert "did not name one direct worker child" in first

    assert sandbox.send_interrupt(4100, signal.SIGINT, proc_root=tmp_path) is True
    assert capsys.readouterr().err == ""


def test_bwrap_interrupt_reports_missing_pidfd_support(tmp_path, monkeypatch, capsys):
    sandbox = _interrupt_sandbox(tmp_path)
    monkeypatch.setattr(sandbox_module.os, "pidfd_open", None, raising=False)
    monkeypatch.setattr(sandbox_module.signal, "pidfd_send_signal", None, raising=False)

    assert sandbox.send_interrupt(4100, signal.SIGINT, proc_root=tmp_path) is True
    assert "no pidfd support" in capsys.readouterr().err


def test_kernel_interrupt_lets_the_sandbox_own_bwrap_signal_delivery():
    calls = []

    class Process:
        pid = 4100

        @staticmethod
        def poll():
            return None  # alive; `interrupt()` refuses to signal a dead child

        @staticmethod
        def send_signal(signum):
            calls.append(("direct", signum))

    kernel = Kernel.__new__(Kernel)
    kernel._proc = Process()
    kernel._sandbox = SimpleNamespace(
        send_interrupt=lambda pid, signum: calls.append(("sandbox", pid, signum))
        or True
    )

    assert kernel.interrupt().delivered is True
    assert calls == [("sandbox", 4100, signal.SIGINT)]


def test_kernel_interrupt_signals_the_direct_process_outside_bwrap():
    calls = []

    class Process:
        pid = 4100

        @staticmethod
        def poll():
            return None

        @staticmethod
        def send_signal(signum):
            calls.append(("direct", signum))

    kernel = Kernel.__new__(Kernel)
    kernel._proc = Process()
    kernel._sandbox = SimpleNamespace(send_interrupt=lambda _pid, _signum: False)

    delivery = kernel.interrupt()

    assert calls == [("direct", signal.SIGINT)]
    assert (delivery.delivered, delivery.target) == (True, "local-process")


def test_an_owned_but_undelivered_interrupt_is_reported_as_undelivered():
    """`send_interrupt` returning True means "this adapter owns delivery", not
    "a signal arrived" -- six of its branches return True having sent nothing.
    The sandbox already diagnosed each one; it printed the diagnosis to stderr,
    where the caller that has to tell a user whether their stop worked cannot
    read it. Now `interrupt()` reads it back, so a cancel that did nothing
    stops being indistinguishable from one that worked."""

    class Process:
        pid = 4100

        @staticmethod
        def poll():
            return None

        @staticmethod
        def send_signal(signum):  # pragma: no cover - must not be reached
            raise AssertionError("the sandbox owns delivery; do not double-send")

    gap = "bubblewrap did not provide a pinned command identity"
    kernel = Kernel.__new__(Kernel)
    kernel._proc = Process()
    kernel._sandbox = SimpleNamespace(
        send_interrupt=lambda _pid, _signum: True,
        take_interrupt_gap=lambda: gap,
    )

    delivery = kernel.interrupt()

    assert delivery.delivered is False
    assert not delivery, "the result must be falsy so `if not interrupt()` works"
    assert delivery.reason == gap
    assert delivery.target == "sandbox"


def test_interrupting_an_exited_worker_does_not_report_a_delivered_stop():
    """`Popen.send_signal` returns silently for a child that has already
    exited, so the old code reported the same nothing for a dead worker as for
    a live one that took the signal."""

    class Process:
        pid = 4100

        @staticmethod
        def poll():
            return 1  # exited

        @staticmethod
        def send_signal(signum):  # pragma: no cover - must not be reached
            raise AssertionError("nothing to signal")

    kernel = Kernel.__new__(Kernel)
    kernel._proc = Process()
    kernel._sandbox = SimpleNamespace(send_interrupt=lambda _pid, _signum: False)

    delivery = kernel.interrupt()

    assert delivery.delivered is False
    assert "already exited" in (delivery.reason or "")


def test_the_sandbox_records_every_interrupt_gap_but_prints_once(capsys):
    """The print is rate-limited for the human reading a terminal. The record
    is not, because the second stop request needs this call's answer rather
    than whether an earlier one used up the single print."""

    sandbox = KernelSandbox.__new__(KernelSandbox)
    sandbox._interrupt_gap_reported = False
    sandbox._interrupt_gap = None

    sandbox._report_interrupt_gap("first reason")
    assert sandbox.take_interrupt_gap() == "first reason"
    assert sandbox.take_interrupt_gap() is None, "the reason must be consumed once"

    sandbox._report_interrupt_gap("second reason")
    assert sandbox.take_interrupt_gap() == "second reason"

    printed = capsys.readouterr().err
    assert printed.count("[openai4s] cell interrupt cannot reach") == 1


def test_seatbelt_profile_appends_targeted_read_denies():
    workspace = "/tmp/work"
    temp_dir = "/tmp/private"
    deny = (("prefix", "/data/openai4s.db"), ("subpath", "/home/u/.ssh"))

    plain = build_seatbelt_profile(workspace, temp_dir)
    guarded = build_seatbelt_profile(workspace, temp_dir, deny_read=deny)

    # The profile now carries a baseline of its own: the macOS keychain is
    # denied unconditionally, because that is where `OPENAI4S_SECRET_STORE`
    # puts the LLM API key and a cell could otherwise run `/usr/bin/security`
    # and read it. So the claim is no longer "no denies at all" but "no denies
    # for paths nobody asked about".
    assert "/data/openai4s.db" not in plain
    assert "/home/u/.ssh" not in plain
    assert "Keychains" in plain  # the baseline is there even with no deny_read
    assert '(deny file-read* (prefix "/data/openai4s.db"))' in guarded
    assert '(deny file-read* (subpath "/home/u/.ssh"))' in guarded
    # last-match-wins: the read denies must follow the leading (allow default)
    assert guarded.index("(allow default)") < guarded.index("file-read*")


def test_seatbelt_profile_rejects_unknown_deny_read_kind():
    with pytest.raises(sandbox_module.SandboxConfigurationError):
        build_seatbelt_profile("/w", "/t", deny_read=(("glob", "/x"),))


def test_bwrap_masks_secret_reads_after_binds_and_skips_missing(tmp_path):
    secret_dir = tmp_path / "creds"
    secret_dir.mkdir()
    db = tmp_path / "openai4s.db"
    db.write_text("KEY=1")
    missing = tmp_path / "nope.db"  # never created -> skipped
    deny = (
        ("prefix", str(db)),
        ("subpath", str(secret_dir)),
        ("prefix", str(missing)),
    )

    wrapped = wrap_bwrap_command(
        ["/bin/true"],
        executable="bwrap",
        workspace=str(tmp_path / "ws"),
        temp_dir=str(tmp_path / "tmp"),
        deny_read=deny,
    )

    # A file is masked with /dev/null, a directory with an empty tmpfs.
    assert ["--ro-bind", "/dev/null", str(db)] == wrapped[
        wrapped.index(str(db)) - 2 : wrapped.index(str(db)) + 1
    ]
    assert ["--tmpfs", str(secret_dir)] == wrapped[
        wrapped.index(str(secret_dir)) - 1 : wrapped.index(str(secret_dir)) + 1
    ]
    assert str(missing) not in wrapped  # non-existent target skipped
    # Masks land after the workspace/temp binds and before --chdir/--.
    last_bind = max(i for i, v in enumerate(wrapped) if v == "--bind")
    assert last_bind < wrapped.index(str(db)) < wrapped.index("--chdir")


def test_default_secret_read_denials_uses_data_dir_and_drops_workspace(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    denials = sandbox_module._default_secret_read_denials(tmp_path / "ws")
    db = str((tmp_path / "openai4s.db").resolve())
    assert ("prefix", db) in denials

    # An entry that IS the workspace is dropped so the kernel boundary stays
    # readable: with workspace == ~/.ssh, the ~/.ssh subpath deny is elided.
    ssh = Path.home() / ".ssh"
    dropped = sandbox_module._default_secret_read_denials(ssh)
    assert not any(path == str(ssh.resolve()) for _kind, path in dropped)


def test_off_is_explicit_and_skips_detection_and_self_test(tmp_path):
    def unexpected(*args, **kwargs):
        raise AssertionError((args, kwargs))

    sandbox = create_kernel_sandbox(
        tmp_path,
        mode="off",
        platform_name="darwin",
        which=unexpected,
        runner=unexpected,
    )

    assert sandbox.status.state == "disabled"
    assert sandbox.status.mode == "off"
    assert sandbox.status.enforced is False
    assert sandbox.status.network_policy == "not_enforced"
    assert sandbox.wrap_command(["python", "worker.py"]) == ["python", "worker.py"]


def test_auto_missing_backend_falls_back_with_visible_status_and_warning(tmp_path):
    with pytest.warns(RuntimeWarning, match="SECURITY WARNING"):
        sandbox = create_kernel_sandbox(
            tmp_path,
            mode="auto",
            platform_name="linux",
            which=lambda name: None,
        )

    status = sandbox.status.to_dict()
    assert status["state"] == "unavailable"
    assert status["backend"] is None
    assert status["enforced"] is False
    assert status["network_policy"] == "not_enforced"
    assert "bwrap" in status["detail"]
    assert status["warning"].startswith("OPENAI4S SECURITY WARNING")


def test_enforce_fails_closed_when_backend_is_missing(tmp_path):
    with pytest.raises(SandboxUnavailableError, match="bwrap"):
        create_kernel_sandbox(
            tmp_path,
            mode="enforce",
            platform_name="linux",
            which=lambda name: None,
        )


@pytest.mark.parametrize("mode", ["auto", "off"])
def test_workspace_read_isolation_never_degrades_without_a_boundary(tmp_path, mode):
    root = tmp_path / "agent-workspaces"
    workspace = root / "attacker"
    workspace.mkdir(parents=True)

    with pytest.raises(SandboxUnavailableError):
        create_kernel_sandbox(
            workspace,
            mode=mode,
            read_isolation=KernelReadIsolation((root,)),
            platform_name="linux",
            which=lambda name: None,
        )


def test_workspace_read_isolation_fails_closed_on_incomplete_self_test(tmp_path):
    root = tmp_path / "agent-workspaces"
    workspace = root / "attacker"
    workspace.mkdir(parents=True)

    # The historical probe says `ok`, but did not prove any sibling-read
    # property. A team boundary must reject that result instead of degrading.
    with pytest.raises(SandboxUnavailableError, match="self-test failed"):
        create_kernel_sandbox(
            workspace,
            mode="auto",
            read_isolation=KernelReadIsolation((root,)),
            platform_name="darwin",
            which=lambda name: "/usr/bin/sandbox-exec",
            runner=_passing_runner(),
        )

    with pytest.raises(SandboxUnavailableError, match="self-test failed"):
        create_kernel_sandbox(
            workspace,
            mode="auto",
            read_isolation=KernelReadIsolation((root,)),
            platform_name="darwin",
            which=lambda name: "/usr/bin/sandbox-exec",
            runner=_failing_runner,
        )


def test_workspace_read_isolation_accepts_only_a_complete_probe(tmp_path):
    root = tmp_path / "agent-workspaces"
    workspace = root / "attacker"
    workspace.mkdir(parents=True)
    calls = []

    sandbox = create_kernel_sandbox(
        workspace,
        mode="auto",
        read_isolation=KernelReadIsolation((root,)),
        platform_name="darwin",
        which=lambda name: "/usr/bin/sandbox-exec",
        runner=_passing_runner(calls, sibling_isolated=True),
    )
    try:
        assert sandbox.status.enforced is True
        assert "team reads isolated under" in sandbox.status.detail
        profile = calls[0][0][2]
        assert any("sibling_read_blocked" in part for part in calls[0][0])
        assert str(root.resolve()) in profile
    finally:
        sandbox.close()


def test_team_boundary_refuses_a_preexisting_external_hardlink(tmp_path):
    data_dir = tmp_path / "daemon-data"
    workspace = data_dir / "agent-workspaces" / "attacker"
    victim = data_dir / "artifact-versions" / "victim.bin"
    workspace.mkdir(parents=True)
    victim.parent.mkdir(parents=True)
    victim.write_bytes(b"VICTIM")
    os.link(victim, workspace / "preexisting-link.bin")

    with pytest.raises(SandboxUnavailableError, match="hardlinked outside"):
        create_kernel_sandbox(
            workspace,
            mode="auto",
            read_isolation=KernelReadIsolation((data_dir,)),
            platform_name="darwin",
            which=lambda name: "/usr/bin/sandbox-exec",
            runner=_passing_runner(sibling_isolated=True),
        )


def test_team_boundary_allows_hardlinks_fully_accounted_for_in_workspace(tmp_path):
    data_dir = tmp_path / "daemon-data"
    workspace = data_dir / "agent-workspaces" / "current"
    workspace.mkdir(parents=True)
    first = workspace / "first.bin"
    first.write_bytes(b"OWN")
    os.link(first, workspace / "second.bin")

    sandbox = create_kernel_sandbox(
        workspace,
        mode="auto",
        read_isolation=KernelReadIsolation((data_dir,)),
        platform_name="darwin",
        which=lambda name: "/usr/bin/sandbox-exec",
        runner=_passing_runner(sibling_isolated=True),
    )
    sandbox.close()


def test_workspace_hardlink_scan_streams_a_wide_directory(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    yielded = 0

    class Entry:
        path = str(workspace / "file")

        def stat(self, *, follow_symlinks):
            assert follow_symlinks is False
            return SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=1)

    class Scan:
        def __iter__(self):
            return self

        def __next__(self):
            nonlocal yielded
            yielded += 1
            if yielded > 5:
                raise AssertionError("scandir was materialized past the hard cap")
            return Entry()

        def close(self):
            pass

    monkeypatch.setattr(sandbox_module, "_WORKSPACE_LINK_SCAN_MAX_ENTRIES", 3)
    monkeypatch.setattr(sandbox_module.os, "scandir", lambda _path: Scan())

    with pytest.raises(SandboxUnavailableError, match="entry limit"):
        sandbox_module._assert_no_external_workspace_hardlinks(workspace)
    assert yielded == 4


def test_allowed_root_probe_streams_its_entry_budget(monkeypatch, tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    yielded = 0

    class Entry:
        path = str(root / "link")

        def stat(self, *, follow_symlinks):
            assert follow_symlinks is False
            return SimpleNamespace(st_mode=stat.S_IFLNK)

    class Scan:
        def __iter__(self):
            return self

        def __next__(self):
            nonlocal yielded
            yielded += 1
            if yielded > 4096:
                raise AssertionError("allowed-root scandir exceeded its budget")
            return Entry()

        def close(self):
            pass

    monkeypatch.setattr(sandbox_module.os, "scandir", lambda _path: Scan())

    probe, created = sandbox_module._allow_read_probe(root, "streaming")
    try:
        assert created is True
        assert yielded == 4096
    finally:
        probe.unlink()


def test_team_private_temp_lives_under_protected_data_and_masks_system_temp(tmp_path):
    data_dir = tmp_path / "daemon-data"
    workspace = data_dir / "agent-workspaces" / "current"
    workspace.mkdir(parents=True)
    calls = []

    sandbox = create_kernel_sandbox(
        workspace,
        mode="auto",
        read_isolation=KernelReadIsolation((data_dir,)),
        platform_name="darwin",
        which=lambda name: "/usr/bin/sandbox-exec",
        runner=_passing_runner(calls, sibling_isolated=True),
    )
    private_temp = Path(sandbox.status.temp_dir or "")
    try:
        assert private_temp.parent == (data_dir / "kernel-temp").resolve()
        profile = calls[0][0][2]
        system_temp = sandbox_module._seatbelt_string(
            Path(sandbox_module.tempfile.gettempdir()).resolve()
        )
        assert f"(literal {system_temp})" in profile
        private_q = sandbox_module._seatbelt_string(private_temp)
        assert (
            f"(allow file-read* (literal {private_q}) (subpath {private_q}))" in profile
        )
    finally:
        sandbox.close()


def test_system_temp_mask_preserves_an_exact_runtime_exception(monkeypatch, tmp_path):
    data_dir = tmp_path / "daemon-data"
    workspace = data_dir / "agent-workspaces" / "current"
    system_temp = tmp_path / "canonical-system-temp"
    runtime = system_temp / "ci-runtime"
    workspace.mkdir(parents=True)
    runtime.mkdir(parents=True)
    (runtime / "marker.txt").write_text("RUNTIME", encoding="utf-8")
    monkeypatch.setattr(sandbox_module.tempfile, "gettempdir", lambda: str(system_temp))
    calls = []

    sandbox = create_kernel_sandbox(
        workspace,
        mode="auto",
        read_isolation=KernelReadIsolation(
            (data_dir,),
            allowed_roots=(runtime,),
        ),
        platform_name="darwin",
        which=lambda name: "/usr/bin/sandbox-exec",
        runner=_passing_runner(calls, sibling_isolated=True),
    )
    try:
        profile = calls[0][0][2]
        runtime_q = sandbox_module._seatbelt_string(runtime.resolve())
        assert (
            f"(allow file-read* (literal {runtime_q}) (subpath {runtime_q}))" in profile
        )
    finally:
        sandbox.close()


def test_kernel_composes_source_and_interpreter_runtime_exceptions(
    monkeypatch, tmp_path
):
    data_dir = tmp_path / "daemon-data"
    workspace = data_dir / "agent-workspaces" / "current"
    python = tmp_path / "system-temp" / "python-env" / "bin" / "python"
    rscript = tmp_path / "system-temp" / "r-env" / "bin" / "Rscript"
    worker = tmp_path / "system-temp" / "source" / "kernel" / "worker.R"
    for path in (python, rscript, worker):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("runtime", encoding="utf-8")
    workspace.mkdir(parents=True)
    captured = {}

    class FakeSandbox:
        status = SimpleNamespace(temp_dir=None)

        def close(self):
            pass

    def create(_workspace, *, read_isolation):
        captured["policy"] = read_isolation
        return FakeSandbox()

    monkeypatch.setattr(manager_module, "create_kernel_sandbox", create)
    monkeypatch.setattr(manager_module.Kernel, "_spawn", lambda self: None)

    manager_module.Kernel(
        cwd=str(workspace),
        python=str(python),
        argv=["/bin/sh", "-c", "exec", str(rscript), str(worker)],
        read_isolation=KernelReadIsolation((data_dir,)),
    )

    allowed = {Path(path) for path in captured["policy"].allowed_roots}
    assert manager_module._KERNEL_RUNTIME_SOURCE_ROOT in allowed
    assert python.parent.parent in allowed
    assert rscript.parent.parent in allowed
    assert worker.parent.parent in allowed


def test_composed_runtime_below_system_temp_remains_exactly_readable(
    monkeypatch, tmp_path
):
    data_dir = tmp_path / "daemon-data"
    workspace = data_dir / "agent-workspaces" / "current"
    system_temp = tmp_path / "system-temp"
    python = system_temp / "python-env" / "bin" / "python"
    workspace.mkdir(parents=True)
    python.parent.mkdir(parents=True)
    python.write_text("runtime", encoding="utf-8")
    monkeypatch.setattr(sandbox_module.tempfile, "gettempdir", lambda: str(system_temp))
    runtime_roots = manager_module._kernel_runtime_read_roots(str(python), None, None)
    calls = []

    sandbox = create_kernel_sandbox(
        workspace,
        mode="auto",
        read_isolation=KernelReadIsolation(
            (data_dir,),
            allowed_roots=runtime_roots,
        ),
        platform_name="darwin",
        which=lambda name: "/usr/bin/sandbox-exec",
        runner=_passing_runner(calls, sibling_isolated=True),
    )
    try:
        profile = calls[0][0][2]
        runtime_q = sandbox_module._seatbelt_string(python.parent.parent.resolve())
        assert (
            f"(allow file-read* (literal {runtime_q}) (subpath {runtime_q}))" in profile
        )
    finally:
        sandbox.close()


def test_team_private_temp_rejects_a_symlinked_root(tmp_path):
    data_dir = tmp_path / "daemon-data"
    workspace = data_dir / "agent-workspaces" / "current"
    outside = tmp_path / "outside-temp"
    workspace.mkdir(parents=True)
    outside.mkdir()
    (data_dir / "kernel-temp").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SandboxConfigurationError, match="cannot be a symlink"):
        create_kernel_sandbox(
            workspace,
            mode="auto",
            read_isolation=KernelReadIsolation((data_dir,)),
            platform_name="darwin",
            which=lambda name: "/usr/bin/sandbox-exec",
            runner=_passing_runner(sibling_isolated=True),
        )


def test_successful_self_test_enables_seatbelt_and_private_temp(tmp_path):
    calls: list = []
    sandbox = create_kernel_sandbox(
        tmp_path,
        mode="auto",
        platform_name="darwin",
        which=lambda name: "/usr/bin/sandbox-exec",
        runner=_passing_runner(calls),
    )
    private_temp = Path(sandbox.status.temp_dir or "")
    try:
        assert sandbox.status.state == "enabled"
        assert sandbox.status.backend == "seatbelt"
        assert sandbox.status.self_test_passed is True
        assert sandbox.status.network_policy == "blocked"
        assert private_temp.is_dir()
        assert calls and calls[0][0][0] == "/usr/bin/sandbox-exec"

        env = sandbox.apply_environment({"PATH": "/usr/bin"})
        assert env["TMPDIR"] == str(private_temp)
        assert env["TMP"] == str(private_temp)
        assert env["TEMP"] == str(private_temp)
        assert env["MPLCONFIGDIR"] == str(private_temp / "matplotlib")
        assert sandbox.wrap_command(["/bin/true"])[0] == "/usr/bin/sandbox-exec"
    finally:
        sandbox.close()
    assert not private_temp.exists()


def test_auto_self_test_failure_falls_back_and_enforce_fails_closed(tmp_path):
    with pytest.warns(RuntimeWarning, match="self-test failed"):
        auto = create_kernel_sandbox(
            tmp_path,
            mode="auto",
            platform_name="darwin",
            which=lambda name: "/usr/bin/sandbox-exec",
            runner=_failing_runner,
        )
    assert auto.status.state == "unavailable"
    assert auto.status.backend == "seatbelt"
    assert auto.status.self_test_passed is False

    with pytest.raises(SandboxUnavailableError, match="self-test failed"):
        create_kernel_sandbox(
            tmp_path,
            mode="enforce",
            platform_name="darwin",
            which=lambda name: "/usr/bin/sandbox-exec",
            runner=_failing_runner,
        )


def test_facility_failure_and_warning_are_cached_process_wide(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def unavailable(command, **kwargs):
        del kwargs
        calls.append(list(command))
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="user namespaces are not enabled",
        )

    monkeypatch.setattr(sandbox_module, "_failed_self_tests", {})
    monkeypatch.setattr(sandbox_module, "_warned_details", set())
    monkeypatch.setattr(sandbox_module.subprocess, "run", unavailable)
    options = {
        "mode": "auto",
        "platform_name": "linux",
        "which": lambda name: "/usr/bin/bwrap",
        "runner": sandbox_module._default_runner,
    }

    with pytest.warns(RuntimeWarning, match="user namespaces are not enabled"):
        first = create_kernel_sandbox(tmp_path, **options)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        second = create_kernel_sandbox(tmp_path, **options)

    assert len(calls) == 1
    assert caught == []
    assert first.status.state == second.status.state == "unavailable"
    assert first.status.detail == second.status.detail


@pytest.mark.parametrize("value", ["maybe", "required", "TRUE-ish"])
def test_invalid_mode_is_never_silently_downgraded(tmp_path, value):
    with pytest.raises(SandboxConfigurationError, match="must be one of"):
        create_kernel_sandbox(tmp_path, mode=value)


class _RecordingSandbox:
    def __init__(self, temp_dir: Path):
        self.commands: list[list[str]] = []
        self.closed = False
        self.temp_dir = temp_dir
        self.status = SandboxStatus(
            mode="auto",
            state="enabled",
            backend="test",
            enforced=True,
            self_test_passed=True,
            network_policy="blocked",
            workspace=str(temp_dir.parent),
            temp_dir=str(temp_dir),
            detail="injected test boundary",
        )

    def wrap_command(self, command):
        self.commands.append(list(command))
        return list(command)

    def apply_environment(self, environment):
        result = dict(environment)
        result["OPENAI4S_SANDBOX_MANAGER_TEST"] = "present"
        result["TMPDIR"] = str(self.temp_dir)
        return result

    def close(self):
        self.closed = True


def test_manager_wraps_spawn_without_changing_frame_or_rpc_loop(tmp_path):
    private_temp = tmp_path / "private-temp"
    private_temp.mkdir()
    sandbox = _RecordingSandbox(private_temp)

    with Kernel(
        dispatcher=lambda method, args: f"{method}:{args[0]}",
        cwd=str(tmp_path),
        sandbox=sandbox,
    ) as kernel:
        result = kernel.execute(
            "import os\n"
            "print(os.environ['OPENAI4S_SANDBOX_MANAGER_TEST'])\n"
            "print(host._call('echo', ['round-trip']))\n"
            "print(os.environ['TMPDIR'])"
        )
        status = kernel.sandbox_status

    assert result["error"] is None
    assert result["stdout"].splitlines() == [
        "present",
        "echo:round-trip",
        str(private_temp),
    ]
    assert len(sandbox.commands) == 1
    assert sandbox.commands[0][-1].endswith("openai4s/kernel/worker.py")
    assert status["enforced"] is True
    assert status["network_policy"] == "blocked"
    assert sandbox.closed is True


def test_raw_network_environment_flag_is_strict(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_KERNEL_ALLOW_RAW_NETWORK", "sometimes")
    with pytest.raises(
        SandboxConfigurationError, match="OPENAI4S_KERNEL_ALLOW_RAW_NETWORK"
    ):
        create_kernel_sandbox(tmp_path, mode="auto")


def test_raw_network_environment_flag_is_reflected_in_status(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_KERNEL_ALLOW_RAW_NETWORK", "1")
    sandbox = create_kernel_sandbox(
        tmp_path,
        mode="auto",
        platform_name="linux",
        which=lambda name: "/usr/bin/bwrap",
        runner=_passing_runner(),
    )
    try:
        assert sandbox.status.network_policy == "raw_allowed"
        assert "--unshare-net" not in sandbox.wrap_command(["/bin/true"])
    finally:
        sandbox.close()
