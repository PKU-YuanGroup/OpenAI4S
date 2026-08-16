"""A real worker, over a real socket, driven by the real Kernel (M3b-1/2).

The worker is started as an actual subprocess with no inherited protocol
pipes; it dials the gateway, presents a credential, and then runs cells
through the same `Kernel.execute` the local path uses. That is the only
arrangement that proves the claim: a `StringIO` or a fake socket would
exercise the parts that were never in doubt.

Everything the plan pins is asserted here rather than assumed: the
credential is single-use, an old epoch is refused (INV-7), a forged or
expired one is refused, nothing secret rides the environment (INV-9), and
the frame protocol behaves identically to the pipe path — including the
mid-cell `host_call` round trip, which is the whole reason this transport
has to preserve the host-call transaction discipline.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from openai4s.kernel.manager import Kernel
from openai4s.kernel.transport import OutboundTcpTransport
from openai4s.orchestration.bootstrap import (
    BootstrapAuthority,
    BootstrapCredential,
    BootstrapError,
    load_or_mint_secret,
    write_credential_file,
)
from openai4s.orchestration.worker_gateway import (
    LISTEN_ENV,
    WorkerGateway,
    gateway_from_environment,
    parse_listen,
)

_WORKER = Path(__file__).resolve().parent.parent / "openai4s" / "kernel" / "worker.py"


# -- the credential -----------------------------------------------------------


@pytest.fixture()
def authority(tmp_path):
    return BootstrapAuthority(load_or_mint_secret(tmp_path))


def test_the_signing_secret_is_owner_only_and_stable(tmp_path):
    first = load_or_mint_secret(tmp_path)
    second = load_or_mint_secret(tmp_path)
    assert first == second, "a second call must not mint a new secret"
    path = tmp_path / "worker-bootstrap-secret"
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_a_credential_verifies_once_and_only_once(authority):
    credential = authority.issue(allocation_id="alloc_1", epoch=0)
    authority.verify(credential)
    authority.consume(credential)
    with pytest.raises(BootstrapError, match="already been used"):
        authority.consume(credential)


def test_a_forged_credential_is_refused(authority):
    credential = authority.issue(allocation_id="alloc_1", epoch=0)
    forged = BootstrapCredential(
        allocation_id="alloc_2",  # a different allocation, same signature
        epoch=credential.epoch,
        rank=credential.rank,
        expires_at=credential.expires_at,
        nonce=credential.nonce,
        signature=credential.signature,
    )
    with pytest.raises(BootstrapError, match="signature"):
        authority.consume(forged)


def test_an_expired_credential_is_refused(tmp_path):
    now = [1000.0]
    authority = BootstrapAuthority(load_or_mint_secret(tmp_path), clock=lambda: now[0])
    credential = authority.issue(allocation_id="alloc_1", epoch=0, ttl_s=10)
    now[0] += 11
    with pytest.raises(BootstrapError, match="expired"):
        authority.consume(credential)


def test_an_old_epoch_is_refused_after_recovery(authority):
    """INV-7: recovery mints a new epoch, and the previous incarnation's
    worker must not be able to register afterwards."""
    old = authority.issue(allocation_id="alloc_1", epoch=0)
    authority.issue(allocation_id="alloc_1", epoch=1)  # recovery
    with pytest.raises(BootstrapError, match="STALE_EPOCH"):
        authority.consume(old)


def test_the_credential_is_a_file_not_an_environment_variable(authority, tmp_path):
    """INV-9: the scheduler is told a path; the secret stays 0600 on disk."""
    credential = authority.issue(allocation_id="alloc_1", epoch=0)
    path = write_credential_file(credential, tmp_path / "runtime")
    assert oct(path.stat().st_mode)[-3:] == "600"
    assert credential.signature in path.read_text()

    # and the broker would refuse to carry it in the environment at all
    from openai4s.orchestration.slurm.broker import SubmitSpec

    with pytest.raises(ValueError, match="INV-9|invalid environment"):
        SubmitSpec(
            job_name="j",
            comment="tok_a",
            script="x",
            environment={"OPENAI4S_WORKER_BOOTSTRAP_TOKEN": credential.signature},
        )
    # the path variable passes, because a path is not a secret
    spec = SubmitSpec(
        job_name="j",
        comment="tok_a",
        script="x",
        environment={"OPENAI4S_WORKER_BOOTSTRAP_PATH": str(path)},
    )
    assert spec.environment["OPENAI4S_WORKER_BOOTSTRAP_PATH"] == str(path)


# -- the listener -------------------------------------------------------------


def test_listen_spec_parsing():
    assert parse_listen(None) is None
    assert parse_listen("") is None
    assert parse_listen("8765") == ("0.0.0.0", 8765)
    assert parse_listen("127.0.0.1:8765") == ("127.0.0.1", 8765)
    for bad in ("nope", "1.2.3.4:x", "0"):
        with pytest.raises(ValueError):
            parse_listen(bad)


def test_the_gateway_is_off_unless_asked_for(monkeypatch, authority):
    monkeypatch.delenv(LISTEN_ENV, raising=False)
    assert gateway_from_environment(authority) is None
    monkeypatch.setenv(LISTEN_ENV, "127.0.0.1:8799")
    gateway = gateway_from_environment(authority)
    assert gateway is not None


@pytest.fixture()
def gateway(authority):
    node = WorkerGateway(authority, bind=("127.0.0.1", 0))
    node.start()
    try:
        yield node
    finally:
        node.stop()


def _dial(gateway, payload: str) -> dict:
    host, port = gateway.address
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall((payload + "\n").encode())
        sock.settimeout(10)
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                return {}
            data += chunk
    return json.loads(data.split(b"\n", 1)[0])


def test_an_unauthenticated_peer_is_refused_before_any_protocol(gateway):
    assert _dial(gateway, json.dumps({"allocation_id": "x"})) == {
        "ok": False,
        "error": "refused",
    }
    assert gateway.accepted == 0
    assert gateway.rejected == 1


def test_the_refusal_says_nothing_about_why(gateway, authority):
    """Distinguishing expired from replayed from forged is an oracle for
    somebody guessing."""
    expired = BootstrapCredential(
        allocation_id="a", epoch=0, rank=0, expires_at=1.0, nonce="n", signature="bad"
    )
    good_but_used = authority.issue(allocation_id="a", epoch=0)
    authority.consume(good_but_used)
    first = _dial(gateway, expired.to_json())
    second = _dial(gateway, good_but_used.to_json())
    assert first == second == {"ok": False, "error": "refused"}


def test_the_handshake_hands_back_an_authorization_generation(gateway, authority):
    """`host.bash` binds its one-shot token to the worker's generation. A
    local worker gets one through `_child_env`; the transport branch of
    `Kernel._spawn` returns before any child environment exists, so a remote
    worker had none and every `host.bash` on a cluster session was refused.
    The Host mints it after the credential verifies and echoes it here —
    which also keeps it out of the submission, where an environment variable
    would land in the scheduler's job record (INV-9)."""
    credential = authority.issue(allocation_id="alloc_1", epoch=0)
    answer = _dial(gateway, credential.to_json())
    assert answer["ok"] is True
    assert answer["generation"].startswith("kernel:")


def test_each_authenticated_connection_gets_its_own_generation(gateway, authority):
    """Re-auth, a new epoch and a recovery must not share one: a worker from
    the epoch before a recovery would otherwise keep authorizing against the
    generation the new one is using."""
    first = _dial(gateway, authority.issue(allocation_id="a", epoch=0).to_json())
    second = _dial(gateway, authority.issue(allocation_id="a", epoch=1).to_json())
    assert first["generation"] != second["generation"]


def test_a_refused_peer_is_told_no_generation(gateway):
    """It is minted after the credential verifies, so there is nothing to
    leak to a peer that never authenticated."""
    answer = _dial(gateway, json.dumps({"allocation_id": "x"}))
    assert "generation" not in answer


def test_a_local_kernel_refuses_to_adopt_a_generation(tmp_path):
    """The child was started with one in its environment; replacing it
    afterwards would leave the worker authorizing against one string while
    the Host checked another."""
    kernel = Kernel(cwd=str(tmp_path))
    with pytest.raises(RuntimeError, match="child environment"):
        kernel.adopt_authorization_generation("kernel:something-else")


def test_a_replayed_credential_cannot_open_a_second_connection(gateway, authority):
    credential = authority.issue(allocation_id="alloc_1", epoch=0)
    assert _dial(gateway, credential.to_json())["ok"] is True
    assert _dial(gateway, credential.to_json()) == {"ok": False, "error": "refused"}
    assert gateway.accepted == 1


def test_await_worker_is_keyed_by_epoch(gateway, authority):
    """A straggler from a previous epoch must not satisfy a wait for the
    current one — the rendezvous is fenced exactly like the credential."""
    credential = authority.issue(allocation_id="alloc_1", epoch=0)
    assert _dial(gateway, credential.to_json())["ok"] is True

    assert gateway.await_worker("alloc_1", 1, timeout_s=0.3) is None
    registration = gateway.await_worker("alloc_1", 0, timeout_s=2)
    assert registration is not None
    assert registration.allocation_id == "alloc_1"
    registration.transport.close(graceful=False)


# -- a real worker over a real socket -----------------------------------------


class _RemoteWorker:
    """Starts worker.py as a subprocess that dials the gateway."""

    def __init__(
        self, gateway, authority, tmp_path, *, allocation_id="alloc_1", epoch=0
    ):
        import subprocess

        credential = authority.issue(allocation_id=allocation_id, epoch=epoch)
        path = write_credential_file(credential, tmp_path / "runtime")
        host, port = gateway.address
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "OPENAI4S_WORKER_CONNECT": f"{host}:{port}",
            "OPENAI4S_WORKER_BOOTSTRAP_PATH": str(path),
        }
        self.process = subprocess.Popen(
            [sys.executable, "-u", str(_WORKER)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def close(self):
        try:
            self.process.kill()
            self.process.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture()
def remote_kernel(gateway, authority, tmp_path):
    """A Kernel whose worker is a separate process on the other end of a
    socket, with no inherited pipes at all."""
    worker = _RemoteWorker(gateway, authority, tmp_path)
    registration = gateway.await_worker("alloc_1", 0, timeout_s=30)
    if registration is None:
        out, err = worker.process.communicate(timeout=5)
        worker.close()
        pytest.fail(f"worker never dialled in. stderr={err.decode()[:2000]}")
    kernel = Kernel(transport_factory=lambda: registration.transport)
    try:
        yield kernel
    finally:
        try:
            kernel.shutdown()
        except Exception:  # noqa: BLE001
            pass
        worker.close()


def test_a_remote_worker_executes_cells(remote_kernel):
    result = remote_kernel.execute("print('hello from the other side')")
    assert result["stdout"].strip() == "hello from the other side"
    assert not result["error"]


def test_a_remote_worker_keeps_its_namespace_across_cells(remote_kernel):
    """The point of a persistent session: the variable survives the cell."""
    remote_kernel.execute("x = 41")
    result = remote_kernel.execute("print(x + 1)")
    assert result["stdout"].strip() == "42"


def test_host_call_round_trips_over_the_socket(remote_kernel):
    """The inner RPC loop is what a tool_use architecture lacks, and it must
    work identically over a socket — same transaction lock, same id
    routing."""
    seen = []

    def dispatcher(method, args):
        seen.append(method)
        if method == "llm":
            # args[0] is the assembled request, not the prompt string — the
            # point here is that the round trip happened at all.
            assert args[0]["messages"][0]["content"] == "ping"
            return {"echo": "pong"}
        # The provenance layer makes its own host calls around a cell; this
        # dispatcher has to answer them the way the real one does rather
        # than handing every method the same shape.
        return None

    remote_kernel.dispatcher = dispatcher
    result = remote_kernel.execute(
        "r = host.llm('ping')\nprint(r['echo'])",
    )
    assert "llm" in seen, f"the llm call never round-tripped; saw {seen}"
    assert result["stdout"].strip() == "pong"


def test_an_error_in_a_remote_cell_reports_its_line(remote_kernel):
    result = remote_kernel.execute("x = 1\nraise ValueError('boom')")
    assert "boom" in result["error"]
    # Line attribution rides inside `trace`, the same shape the pipe path
    # produces — the response key set is frozen and has no top-level
    # error_lineno.
    assert (result.get("trace") or {}).get("error_lineno") == 2, result.get("trace")


def test_the_remote_transport_reports_no_local_process(remote_kernel):
    """A pid from another machine is worse than no pid: whoever records it
    believes it names a process here."""
    assert remote_kernel.pid is None
    assert remote_kernel._transport.process is None
    assert (
        remote_kernel._transport.stderr_tail is None
    ), "'we were not looking' must not be reported as 'nothing was written'"


def test_interrupting_a_remote_worker_without_a_hook_is_an_error(remote_kernel):
    """Silence here would leave a cell apparently cancelled and actually
    running."""
    with pytest.raises(RuntimeError, match="no way to interrupt"):
        remote_kernel.interrupt()


def test_a_remote_interrupt_uses_the_hook_it_was_given(gateway, authority, tmp_path):
    called = []
    worker = _RemoteWorker(gateway, authority, tmp_path, allocation_id="alloc_2")
    try:
        registration = gateway.await_worker("alloc_2", 0, timeout_s=30)
        assert registration is not None
        transport = OutboundTcpTransport(
            registration.transport._sock,
            peer=registration.peer,
            interrupt_hook=lambda: called.append(1) or True,
        )
        kernel = Kernel(transport_factory=lambda: transport)
        kernel.interrupt()
        assert called == [1]
        transport.close(graceful=False)
    finally:
        worker.close()


def test_a_worker_told_to_connect_without_a_credential_refuses_to_start(
    gateway, tmp_path
):
    """A worker that cannot prove who it is must not go on to run cells."""
    import subprocess

    host, port = gateway.address
    process = subprocess.run(
        [sys.executable, "-u", str(_WORKER)],
        env={
            "PATH": os.environ.get("PATH", ""),
            "OPENAI4S_WORKER_CONNECT": f"{host}:{port}",
        },
        capture_output=True,
        timeout=60,
    )
    assert process.returncode == 70
    assert b"refusing to connect without a credential" in process.stderr


def test_the_local_path_is_untouched_by_all_of_this():
    """The whole feature is additive: with no transport factory, a Kernel is
    the child-process-over-pipes it always was."""
    kernel = Kernel()
    try:
        assert kernel._proc is not None, "the local child is still the local child"
        assert kernel.pid == kernel._proc.pid
        result = kernel.execute("print(6 * 7)")
        assert result["stdout"].strip() == "42"
        assert kernel._transport.stderr_tail is not None
    finally:
        kernel.shutdown()


def test_restarting_a_remote_worker_says_it_cannot_rather_than_pretending(
    remote_kernel,
):
    """`restart` used to reach straight into a local child's `.stdin`, which
    on this path is a `None` that never existed — an AttributeError instead
    of an answer. A remote worker is not this process's to respawn: the
    caller's move is recovery, and it can only make it if it is told."""
    with pytest.raises(RuntimeError, match="placed and reconnected"):
        remote_kernel.restart()


def test_a_burned_credential_stays_burned_across_a_restart(tmp_path):
    """The reasoning that made this in-memory was wrong: a restart takes the
    worker's *connection*, not its credential file. That file is on the
    shared filesystem the job was given and stays valid for its whole TTL,
    so a fresh daemon with an empty set re-admits it."""
    state = tmp_path / "fence.json"
    secret = load_or_mint_secret(tmp_path)

    first = BootstrapAuthority(secret, state_path=state)
    credential = first.issue(allocation_id="alloc_1", epoch=0)
    first.consume(credential)

    # the daemon restarts: same data dir, same secret, new object
    second = BootstrapAuthority(secret, state_path=state)
    with pytest.raises(BootstrapError, match="already been used"):
        second.consume(credential)


def test_an_epoch_fence_survives_a_restart(tmp_path):
    """INV-7 across a process boundary: a recovery fenced epoch 0, and the
    worker from it must still be refused by the daemon that comes back."""
    state = tmp_path / "fence.json"
    secret = load_or_mint_secret(tmp_path)

    first = BootstrapAuthority(secret, state_path=state)
    stale = first.issue(allocation_id="alloc_1", epoch=0)
    first.issue(allocation_id="alloc_1", epoch=1)  # recovery

    second = BootstrapAuthority(secret, state_path=state)
    with pytest.raises(BootstrapError, match="STALE_EPOCH"):
        second.consume(stale)


def test_pre_auth_handshakes_are_bounded(authority):
    """The thread is allocated before the credential is checked, so an
    unbounded pool is an unauthenticated thread-exhaustion path: one TCP
    connect buys a thread for the whole handshake deadline. Excess sockets
    are closed rather than queued — a queue behind a full pool is the same
    resource under another name."""
    node = WorkerGateway(authority, bind=("127.0.0.1", 0), max_pending_handshakes=1)
    node.start()
    try:
        host, port = node.address
        # Occupy the only slot with a peer that connects and says nothing.
        squatter = socket.create_connection((host, port), timeout=10)
        try:
            deadline = time.monotonic() + 5
            while node.refused_busy == 0 and time.monotonic() < deadline:
                extra = socket.create_connection((host, port), timeout=10)
                extra.settimeout(2)
                try:
                    extra.recv(64)  # closed immediately, so this returns b""
                except OSError:
                    pass
                extra.close()
            assert node.refused_busy > 0, "excess sockets were not refused"
        finally:
            squatter.close()
    finally:
        node.stop()


def test_a_dribbling_peer_cannot_hold_a_slot_past_the_deadline(authority, monkeypatch):
    """`settimeout` bounds each recv, not the handshake: a peer sending one
    byte every 29s never tripped a 30s socket timeout, and the 64 KiB cap is
    ~65,000 recvs away — about three weeks on one thread. The deadline is now
    total, so a peer that never completes a line is dropped by the clock
    rather than by the byte cap."""
    import openai4s.orchestration.worker_gateway as wg

    monkeypatch.setattr(wg, "HANDSHAKE_TIMEOUT_S", 0.5)
    node = WorkerGateway(authority, bind=("127.0.0.1", 0))
    node.start()
    try:
        host, port = node.address
        with socket.create_connection((host, port), timeout=10) as sock:
            sock.sendall(b'{"allocation_id"')  # no newline, ever
            sock.settimeout(10)
            # The daemon closes it once the deadline passes; a read here
            # returns the refusal and then EOF.
            try:
                while sock.recv(4096):
                    pass
            except OSError:
                pass
        deadline = time.monotonic() + 5
        while node.rejected == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert node.rejected >= 1
    finally:
        node.stop()


def test_the_fence_file_is_owner_only(tmp_path):
    state = tmp_path / "fence.json"
    secret = load_or_mint_secret(tmp_path)
    authority = BootstrapAuthority(secret, state_path=state)
    authority.consume(authority.issue(allocation_id="alloc_1", epoch=0))
    assert oct(state.stat().st_mode)[-3:] == "600"


def test_a_corrupt_fence_refuses_admission_rather_than_forgetting(tmp_path):
    """This test used to assert the opposite — "a corrupt fence is an empty
    fence, not an outage" — and that reading is what a security review
    reproduced as a replay: a consumed, unexpired credential was accepted
    again after the fence was corrupted and the authority rebuilt.

    The fence is not a cache. It is the record of which credentials have
    been spent, and reading it as empty re-admits every unexpired
    credential still on the shared filesystem and un-fences every epoch a
    recovery had closed. `_save_state` publishes through `os.replace`, so an
    interrupted write leaves the previous complete file — a file that will
    not parse is disk damage or somebody editing it, and the second is
    precisely the party this fence refuses.

    A *missing* file is still an empty fence, because a fresh install has
    genuinely burned nothing. The refusal names both remedies.
    """
    state = tmp_path / "fence.json"
    secret = load_or_mint_secret(tmp_path)
    authority = BootstrapAuthority(secret, state_path=state)
    burned = authority.issue(allocation_id="alloc_1", epoch=0)
    authority.consume(burned)

    state.write_text("{ not json")
    revived = BootstrapAuthority(secret, state_path=state)

    # The replay the review reproduced.
    with pytest.raises(BootstrapError, match="could not be read"):
        revived.consume(burned)
    # And nothing else is admitted either, since the fence cannot answer for
    # any nonce.
    with pytest.raises(BootstrapError, match="could not be read"):
        revived.consume(revived.issue(allocation_id="alloc_2", epoch=0))


def test_a_missing_fence_is_still_an_empty_fence(tmp_path):
    """The distinction that makes the refusal above tolerable: a daemon that
    has never written one starts clean rather than refusing to serve."""
    secret = load_or_mint_secret(tmp_path)
    authority = BootstrapAuthority(secret, state_path=tmp_path / "absent.json")
    authority.consume(authority.issue(allocation_id="alloc_1", epoch=0))
