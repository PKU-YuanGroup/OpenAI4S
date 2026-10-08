"""Real provider subprocesses, sandbox spawn, and complete port failure mapping."""

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from openai4s.lab.models import (
    Dispatch,
    ErrorCode,
    LabError,
    NormalizedCommand,
    SessionOpenRequest,
)
from openai4s.lab.provider_process import ProviderProcessDevice
from openai4s_lab_provider.toy import Backend

PACKAGE = Path(__file__).resolve().parents[1] / "openai4s_lab_provider"
PROFILE = "toy-extract-v0"


def device(tmp_path, **kwargs):
    return ProviderProcessDevice(
        "toy.extractor.01",
        "toy",
        python=sys.executable,
        package_dir=kwargs.pop("package_dir", PACKAGE),
        runs_root=tmp_path / "lab" / "runs",
        sandbox_mode=kwargs.pop("sandbox_mode", "auto"),
        timeouts={"execute": 0.4, "close": 0.4, **kwargs},
    )


def open_request(**kwargs):
    return SessionOpenRequest(
        PROFILE, 42, kwargs, Backend().describe(PROFILE)["capability_revision"]
    )


def dispatch(ident="labcmd-000000000001", volume=200):
    return Dispatch(
        ident,
        NormalizedCommand.from_dict(
            {
                "run_id": "labrun-000000000001",
                "operation": "transfer_liquid",
                "source": "beaker_1",
                "target": "extraction_vessel",
                "parameters": {"volume": {"value": volume, "unit": "mL"}},
                "expected_revision": 0,
                "idempotency_key": ident,
                "capability_id": "transfer_liquid:beaker_1->extraction_vessel",
            }
        ),
        {"beaker_1": 1, "extraction_vessel": 1},
    )


def test_real_toy_port_lifecycle_and_private_run_cleanup(tmp_path):
    port = device(tmp_path)
    described = port.describe(PROFILE)
    assert described.device_id == port.device_id
    assert any("provider sandbox:" in text for text in described.assumptions)
    assert not list(port.runs_root.iterdir())
    opened = port.open(open_request())
    ident = opened.session_id
    try:
        assert ident.startswith("labsession-") and port.alive(ident)
        assert port.query(ident, "never") is None
        first = port.execute(ident, dispatch())
        assert first.applied and first.step_index == 1
        assert port.query(ident, first.provider_command_id) == first
        assert port.execute(ident, dispatch()) == first
        refused = port.execute(ident, dispatch("overdraw", 1000))
        assert (
            not refused.applied
            and refused.error["code"] == ErrorCode.PRECONDITION_FAILED
        )
        assert port.alive(ident)
        assert port.stop(ident, "test complete").stopped
    finally:
        port.close(ident)
    port.close(ident)
    assert not port.alive(ident)
    assert not list(port.runs_root.iterdir())
    assert not list((tmp_path / "lab/cache").iterdir())
    with pytest.raises(LabError) as caught:
        port.query(ident, "never")
    assert caught.value.code == ErrorCode.PROVIDER_UNAVAILABLE


@pytest.mark.parametrize(
    ("options", "code"),
    [
        ({"sleep_on_execute_s": 5}, ErrorCode.PROVIDER_TIMEOUT),
        ({"crash_on_execute": True}, ErrorCode.PROVIDER_UNAVAILABLE),
        ({"oversize_frame": True}, ErrorCode.PROVIDER_PROTOCOL_ERROR),
        ({"raise_on_execute": True}, ErrorCode.PROVIDER_PROTOCOL_ERROR),
    ],
)
def test_real_toy_transport_failures_kill_session(tmp_path, options, code):
    port = device(tmp_path)
    ident = port.open(open_request(**options)).session_id
    try:
        with pytest.raises(LabError) as caught:
            port.execute(ident, dispatch())
        assert caught.value.code == code
        assert not port.alive(ident)
        assert "toy_component" not in str(caught.value)
    finally:
        port.close(ident)


def peer(tmp_path, replies):
    package = tmp_path / "peer"
    package.mkdir()
    opened = Backend().open(
        PROFILE, 42, {}, open_request().expected_capability_revision
    )
    frames = {
        "hello": {"protocol": 1, "backend": "toy", "backend_version": {}},
        "open": opened,
        "close": {"closed": True},
        **replies,
    }
    (package / "__main__.py").write_text(
        "import json,sys\nreplies=" + repr(frames) + "\n"
        "for line in sys.stdin:\n"
        " f=json.loads(line); result=replies[f['op']]\n"
        " reply={'v':1,'id':f['id'],'ok':True,'result':result}\n"
        " if isinstance(result,dict) and 'peer_error' in result:\n"
        "  reply={'v':1,'id':f['id'],'ok':False,'error':result['peer_error']}\n"
        " print(json.dumps(reply),flush=True)\n"
        " if f['op']=='close': break\n"
    )
    return device(tmp_path, package_dir=package)


@pytest.mark.parametrize("code", list(ErrorCode))
def test_declared_error_codes_preserve_session(tmp_path, code):
    port = peer(
        tmp_path,
        {"query": {"peer_error": {"code": code.value, "message": "private values"}}},
    )
    ident = port.open(open_request()).session_id
    try:
        with pytest.raises(LabError) as caught:
            port.query(ident, "id")
        assert caught.value.code == code
        # The standalone client treats provider_protocol_error as fatal itself.
        assert port.alive(ident) == (code != ErrorCode.PROVIDER_PROTOCOL_ERROR)
        assert "private values" not in str(caught.value)
    finally:
        port.close(ident)


@pytest.mark.parametrize(
    ("reply", "code", "live"),
    [
        (
            {"peer_error": {"code": "alien", "message": "private values"}},
            ErrorCode.PROVIDER_PROTOCOL_ERROR,
            False,
        ),
        ({"known": True, "receipt": None}, ErrorCode.OUTCOME_UNKNOWN, True),
        ({"known": 0}, ErrorCode.PROVIDER_PROTOCOL_ERROR, False),
        ({"known": False, "extra": 1}, ErrorCode.PROVIDER_PROTOCOL_ERROR, False),
        ({"applied": True}, ErrorCode.PROVIDER_PROTOCOL_ERROR, False),
    ],
)
def test_query_unknown_and_malformed_results(tmp_path, reply, code, live):
    port = peer(tmp_path, {"query": reply})
    ident = port.open(open_request()).session_id
    try:
        with pytest.raises(LabError) as caught:
            port.query(ident, "id")
        assert caught.value.code == code
        assert port.alive(ident) == live
    finally:
        port.close(ident)


@pytest.mark.parametrize("mutation", ["shape", "revision", "expected", "identity"])
def test_invalid_open_closes_process(tmp_path, mutation):
    opened = Backend().open(
        PROFILE, 42, {}, open_request().expected_capability_revision
    )
    if mutation == "shape":
        opened["observation"] = None
    elif mutation == "revision":
        opened["descriptor"]["capability_revision"] = "0" * 64
    elif mutation == "expected":
        opened["descriptor"]["capabilities"][1]["parameters"]["volume"]["allowed"] = [
            200
        ]
        from openai4s.lab.models import Capability, capability_revision

        opened["descriptor"]["capability_revision"] = capability_revision(
            tuple(Capability.from_dict(c) for c in opened["descriptor"]["capabilities"])
        )
    else:
        opened["descriptor"]["device_id"] = "other"
    port = peer(tmp_path, {"open": opened})
    with pytest.raises(LabError) as caught:
        port.open(open_request())
    assert caught.value.code == (
        ErrorCode.ADAPTER_MISMATCH
        if mutation in {"revision", "expected"}
        else ErrorCode.PROVIDER_PROTOCOL_ERROR
    )
    assert not port._sessions
    assert not list(port.runs_root.iterdir())


def wait_until_in_flight(port):
    # Every request holds the device lock for its whole exchange.
    deadline = time.monotonic() + 10
    while port._lock.acquire(blocking=False):
        port._lock.release()
        assert time.monotonic() < deadline, "request never started"
        time.sleep(0.005)
    time.sleep(0.2)  # let the frame reach the provider


def test_close_preempts_an_in_flight_execute(tmp_path):
    # The manager's stop and daemon shutdown close sessions whose execute may
    # be blocked for its whole timeout. Close kills the provider at once and
    # the blocked call fails as a lost provider (CONTRACT §9).
    port = device(tmp_path, execute=8, close=5)
    ident = port.open(open_request(sleep_on_execute_s=60)).session_id
    process = port._sessions[ident].client.process
    outcome = {}

    def blocked():
        try:
            outcome["receipt"] = port.execute(ident, dispatch())
        except LabError as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=blocked)
    worker.start()
    wait_until_in_flight(port)
    started = time.monotonic()
    port.close(ident)
    elapsed = time.monotonic() - started
    worker.join(10)
    assert not worker.is_alive()
    assert elapsed < 3, elapsed
    assert "receipt" not in outcome
    assert outcome["error"].code is ErrorCode.PROVIDER_UNAVAILABLE
    assert not port.alive(ident)
    # Reaped by the request that owned it, after the group was signalled.
    assert process.returncode is not None
    assert not list(port.runs_root.iterdir())


def test_unencodable_host_request_keeps_session(tmp_path):
    port = device(tmp_path)
    ident = port.open(open_request()).session_id
    try:
        with pytest.raises(LabError) as caught:
            port.stop(ident, float("nan"))
        assert caught.value.code == ErrorCode.INVALID_PARAMETERS
        assert port.alive(ident)
        assert port.query(ident, "never") is None
    finally:
        port.close(ident)


def test_sandbox_enforce_failure_and_auto_degradation(tmp_path, monkeypatch):
    import openai4s.lab.provider_process as module
    from openai4s.security.sandbox import (
        KernelSandbox,
        SandboxStatus,
        SandboxUnavailableError,
    )

    def sandbox(workspace, *, mode, allow_raw_network):
        assert allow_raw_network is False
        if mode == "enforce":
            raise SandboxUnavailableError("test unavailable")
        return KernelSandbox(
            status=SandboxStatus(
                mode,
                "degraded",
                None,
                False,
                False,
                "not_enforced",
                str(workspace),
                None,
                "test unavailable",
            )
        )

    monkeypatch.setattr(module, "create_kernel_sandbox", sandbox)
    with pytest.raises(LabError) as caught:
        device(tmp_path, sandbox_mode="enforce").open(open_request())
    assert caught.value.code == ErrorCode.PROVIDER_UNAVAILABLE
    port = device(tmp_path)
    opened = port.open(open_request())
    try:
        assert "state=degraded" in opened.descriptor.assumptions[-1]
        assert port.sandbox_status["state"] == "degraded"
    finally:
        port.close(opened.session_id)


def test_spawn_passes_sandbox_fds_and_filters_environment(tmp_path, monkeypatch):
    import openai4s.lab.provider_process as module
    from openai4s.security.sandbox import KernelSandbox, SandboxStatus

    read_fd, write_fd = os.pipe()
    original = module._SandboxClient.start
    seen = []

    class Sandbox(KernelSandbox):
        def popen_pass_fds(self):
            return (write_fd,)

    def sandbox(workspace, **kwargs):
        return Sandbox(
            status=SandboxStatus(
                "auto",
                "degraded",
                None,
                False,
                False,
                "not_enforced",
                str(workspace),
                None,
                "test",
            )
        )

    def start(client):
        assert client._pass_fds == (write_fd,)
        assert "OPENAI_API_KEY" not in client.env
        assert "VIRTUAL_ENV" not in client.env and "PYTHONPATH" not in client.env
        assert client.env["PYTHONNOUSERSITE"] == "1"
        assert client.env["MPLBACKEND"] == "Agg"
        for name in ("NUMBA_CACHE_DIR", "MPLCONFIGDIR"):
            cache = Path(client.env[name])
            assert cache.is_relative_to(tmp_path / "lab/cache")
            assert cache.resolve().is_relative_to(client.cwd)
        # Replace only the test peer to prove actual inheritance, not just kwargs.
        script = (
            "import os; os.write("
            + str(write_fd)
            + ", b'fd'); os.close("
            + str(write_fd)
            + "); import runpy; runpy.run_path("
            + repr(str(PACKAGE / "__main__.py"))
            + ",run_name='__main__')"
        )
        client.argv = [sys.executable, "-I", "-c", script, "--backend", "toy"]
        seen.append(True)
        return original(client)

    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-credential")
    monkeypatch.setenv("VIRTUAL_ENV", "daemon-env")
    monkeypatch.setattr(module, "create_kernel_sandbox", sandbox)
    monkeypatch.setattr(module._SandboxClient, "start", start)
    port = device(tmp_path)
    try:
        opened = port.open(open_request())
        assert seen and os.read(read_fd, 2) == b"fd"
        port.close(opened.session_id)
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.external
def test_real_chemgymrl_process_port(tmp_path):
    python = os.environ.get("OPENAI4S_LAB_CHEMGYMRL_PYTHON")
    if not python:
        pytest.skip("install an explicit provider environment first")
    from importlib.resources import files

    from openai4s.lab.manifest import load_descriptor

    descriptor = load_descriptor(
        json.loads(
            files("openai4s_lab_provider.chemgymrl")
            .joinpath("manifests/WaterOilExtract-v0.json")
            .read_text()
        )
    )
    port = ProviderProcessDevice(
        "chemgym.extractor.01",
        "chemgymrl",
        python=python,
        package_dir=PACKAGE,
        runs_root=tmp_path / "lab/runs",
        sandbox_mode="auto",
    )
    opened = port.open(
        SessionOpenRequest(descriptor.profile, 42, {}, descriptor.capability_revision)
    )
    try:
        assert port.alive(opened.session_id)
        command = NormalizedCommand(
            "labrun-external",
            "end_experiment",
            None,
            None,
            {},
            0,
            "smoke",
            "end_experiment",
        )
        cap = next(c for c in descriptor.capabilities if c.terminal)
        result = port.execute(
            opened.session_id,
            Dispatch("labcmd-external", command, {r: 1 for r in cap.resources}),
        )
        assert result.applied
    finally:
        port.close(opened.session_id)
