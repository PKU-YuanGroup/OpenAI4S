"""Real isolated toy subprocesses exercise the provider's transport and session."""

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from openai4s_lab_provider.client import (
    ProviderClient,
    ProviderError,
    ProviderGone,
    ProviderProtocolError,
    ProviderTimeout,
)

ENTRYPOINT = (
    Path(__file__).resolve().parents[1] / "openai4s_lab_provider" / "__main__.py"
)
PROFILE = "toy-extract-v0"
SOURCE = "beaker_1"
TARGET = "extraction_vessel"


@pytest.fixture
def provider(tmp_path):
    clients = []

    def start(*, env=None, **kwargs):
        # No inherited real credentials or application configuration.
        child_env = {"HOME": str(tmp_path), "PYTHONNOUSERSITE": "1"}
        child_env.update(env or {})
        client = ProviderClient(
            [sys.executable, "-I", str(ENTRYPOINT), "--backend", "toy"],
            env=child_env,
            cwd=tmp_path,
            **kwargs,
        ).start()
        clients.append(client)
        return client

    yield start
    for client in clients:
        client.close()


def _request(client, op, args):
    return client.request(op, args, timeout=5)


def _open(client, options=None, *, revision=None):
    descriptor = _request(client, "describe", {"profile": PROFILE})
    return _request(
        client,
        "open",
        {
            "profile": PROFILE,
            "seed": 42,
            "options": options or {},
            "expected_capability_revision": (
                descriptor["capability_revision"] if revision is None else revision
            ),
        },
    )


def _command(volume=200, *, end=False):
    return {
        "run_id": "labrun-toytest00001",
        "operation": "end_experiment" if end else "transfer_liquid",
        "capability_id": (
            "end_experiment" if end else f"transfer_liquid:{SOURCE}->{TARGET}"
        ),
        "source": None if end else SOURCE,
        "target": None if end else TARGET,
        "parameters": {} if end else {"volume": {"value": volume, "unit": "mL"}},
        "expected_revision": 0,
        "idempotency_key": "toy-test",
    }


def _execute(client, command_id, *, command=None, tokens=None):
    return _request(
        client,
        "execute",
        {
            "provider_command_id": command_id,
            "command": _command() if command is None else command,
            "fencing_tokens": {SOURCE: 1, TARGET: 1} if tokens is None else tokens,
        },
    )


def _query(client, command_id):
    return _request(client, "query", {"provider_command_id": command_id})


def _assert_no_truth(value):
    if isinstance(value, dict):
        assert not set(value) & {
            "ground_truth",
            "evaluation",
            "reward",
            "moles",
            "temperature_K",
            "volume_L",
            "gym_action",
        }
        for item in value.values():
            _assert_no_truth(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_truth(item)
    elif isinstance(value, str):
        assert "toy_component" not in value


def test_toy_process_full_lifecycle(provider):
    client = provider()
    hello = _request(client, "hello", {})
    assert hello["protocol"] == 1
    assert hello["backend"] == "toy"
    opened = _open(client)
    descriptor = opened["descriptor"]
    assert descriptor["device_id"] == "toy.extractor.01"
    assert descriptor["mode"] == "simulation"
    assert descriptor["profile"] == PROFILE
    assert descriptor["backend_version"] == hello["backend_version"]
    canonical = json.dumps(
        sorted(descriptor["capabilities"], key=lambda item: item["capability_id"]),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    assert (
        descriptor["capability_revision"]
        == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )
    _assert_no_truth(descriptor)
    _assert_no_truth(opened["observation"])
    assert "sequence" not in opened["observation"]
    assert opened["observation"]["sim_time"] == 0
    assert opened["observation"]["channels"][0]["shape"] == [2, 5]
    assert opened["observation"]["channels"][0]["source"] == "simulated_sensor"
    assert opened["evaluation"]["ground_truth"]
    assert _query(client, "not-executed") == {"known": False}
    receipt = _execute(client, "first")
    assert receipt["provider_command_id"] == "first"
    assert receipt["status"] == "succeeded"
    assert receipt["applied"] is True
    assert receipt["step_index"] == receipt["sim_time"] == 1
    assert receipt["raw"] == {"terminated": False, "truncated": False}
    assert receipt["end_reason"] is None
    assert receipt["observation"] != opened["observation"]
    _assert_no_truth(receipt["observation"])
    assert receipt["evaluation"]["ground_truth"] != opened["evaluation"]["ground_truth"]
    assert _query(client, "first") == receipt
    assert _request(client, "stop", {"reason": "test complete"}) == {
        "stopped": True,
        "semantics": "end_session",
    }
    assert _request(client, "close", {}) == {"closed": True}
    assert client.process.wait(timeout=5) == 0
    assert not client.alive()
    client.close()
    assert client.stderr_tail() == ""


def test_second_open_is_rejected_without_resetting_session(provider):
    client = provider()
    _open(client)
    first = _execute(client, "first")
    with pytest.raises(ProviderError) as caught:
        _open(client)
    assert caught.value.code == "invalid_parameters"
    assert _query(client, "first") == first
    assert _execute(client, "next")["step_index"] == 2


def test_open_revision_mismatch_does_not_create_session(provider):
    client = provider()
    with pytest.raises(ProviderError) as caught:
        _open(client, revision="0" * 64)
    assert caught.value.code == "adapter_mismatch"
    assert _open(client)["observation"]["sim_time"] == 0
    assert _execute(client, "first")["step_index"] == 1


def test_duplicate_command_returns_receipt_without_advancing(provider):
    client = provider()
    _open(client)
    first = _execute(client, "same", tokens={SOURCE: 5, TARGET: 5})
    # Cache lookup precedes fencing and mapping: replay never reaches the backend.
    repeated = _execute(client, "same", command=_command(400), tokens={SOURCE: 1})
    assert repeated == first
    next_receipt = _execute(client, "next", tokens={SOURCE: 6, TARGET: 6})
    assert next_receipt["step_index"] == 2
    volumes = next_receipt["evaluation"]["ground_truth"]["vessels"]
    assert [vessel["volume_L"] for vessel in volumes] == [0.6, 0.4]


def test_fencing_rejects_stale_batch_and_remembers_every_maximum(provider):
    client = provider()
    _open(client)
    _execute(client, "first", tokens={SOURCE: 4, TARGET: 10})
    stale = _execute(client, "stale", tokens={SOURCE: 3, TARGET: 20})
    assert stale["status"] == "failed"
    assert stale["applied"] is False
    assert stale["error"]["code"] == "resource_busy"
    assert _query(client, "stale") == stale
    second_stale = _execute(client, "stale-target", tokens={SOURCE: 5, TARGET: 19})
    assert second_stale["error"]["code"] == "resource_busy"
    fresh = _execute(client, "fresh", tokens={SOURCE: 6, TARGET: 20})
    assert fresh["status"] == "succeeded"
    assert fresh["step_index"] == 2


def test_stop_blocks_future_execution_but_keeps_receipts(provider):
    client = provider()
    _open(client)
    before = _execute(client, "before")
    _request(client, "stop", {"reason": "user request"})
    after = _execute(client, "after")
    assert after["status"] == "failed"
    assert after["applied"] is False
    assert after["error"]["code"] == "run_ended"
    assert _query(client, "before") == before


def test_terminal_action_sets_end_reason_and_blocks_next_step(provider):
    client = provider()
    _open(client)
    receipt = _execute(client, "end", command=_command(end=True))
    assert receipt["status"] == "succeeded"
    assert receipt["applied"] is True
    assert receipt["raw"] == {"terminated": True, "truncated": False}
    assert receipt["end_reason"] == "end_action"
    assert receipt["step_index"] == 1
    assert _execute(client, "after")["error"]["code"] == "run_ended"


def test_rejected_mapping_and_failed_precondition_do_not_advance(provider):
    client = provider()
    _open(client)
    rejected = _execute(client, "off-grid", command=_command(201))
    assert rejected["status"] == "rejected"
    assert rejected["applied"] is False
    assert rejected["error"]["code"] == "unsupported_action"
    first = _execute(client, "first", command=_command(600))
    assert first["step_index"] == 1
    failed = _execute(client, "insufficient", command=_command(600))
    assert failed["status"] == "failed"
    assert failed["applied"] is False
    assert failed["error"]["code"] == "precondition_failed"
    _assert_no_truth(failed["error"])
    second = _execute(client, "remainder", command=_command(400))
    assert second["step_index"] == 2
    assert second["evaluation"]["ground_truth"]["vessels"][1]["volume_L"] == 1


def test_fd_stdout_noise_is_redirected_to_stderr(provider):
    client = provider()
    opened = _open(client, {"print_to_stdout_on_open": True})
    assert opened["descriptor"]["profile"] == PROFILE
    assert _execute(client, "first")["status"] == "succeeded"
    client.close()
    assert "toy provider stdout probe\n" in client.stderr_tail()
    assert client.process.returncode == 0


def test_execute_timeout_kills_and_reaps_process_group(provider):
    client = provider()
    _open(client, {"sleep_on_execute_s": 60})
    pid = client.process.pid
    assert os.getpgid(pid) == pid
    with pytest.raises(ProviderTimeout):
        client.request(
            "execute",
            {
                "provider_command_id": "timeout",
                "command": _command(),
                "fencing_tokens": {SOURCE: 1, TARGET: 1},
            },
            timeout=0.2,
        )
    assert not client.alive()
    assert client.process.returncode is not None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)


def test_crash_reports_exit_code_and_bounded_stderr_tail(provider):
    client = provider(stderr_tail_bytes=32)
    _open(client, {"crash_on_execute": True})
    with pytest.raises(ProviderGone) as caught:
        _execute(client, "crash")
    assert caught.value.returncode == 17
    assert caught.value.stderr_tail == "toy provider: deliberate execute crash\n"[-32:]
    assert not client.alive()


def test_oversized_response_is_protocol_error_and_kills_process(provider):
    client = provider()
    _open(client, {"oversize_frame": True})
    with pytest.raises(ProviderProtocolError) as caught:
        _execute(client, "too-large")
    assert caught.value.code == "provider_protocol_error"
    assert not client.alive()


def test_secret_environment_keys_are_scrubbed_before_backend_import(provider):
    secret_names = ["LAB_FIXTURE_API_KEY", "LAB_FIXTURE_TOKEN", "AWS_SESSION_TOKEN"]
    env = dict.fromkeys(secret_names, "not-a-real-credential")
    env["LAB_FIXTURE_COLOR"] = "blue"
    client = provider(env=env)
    opened = _open(client, {"echo_env_keys": secret_names + ["LAB_FIXTURE_COLOR"]})
    assert opened["echo_env_keys"] == ["LAB_FIXTURE_COLOR"]
    assert "not-a-real-credential" not in json.dumps(opened)
    client.close()
    assert client.stderr_tail() == ""


def test_receipt_cache_retains_only_recent_256_commands(provider):
    client = provider()
    _open(client)
    _execute(client, "oldest")
    for index in range(256):
        last = _execute(client, f"rejected-{index}", command=_command(201))
        assert last["status"] == "rejected"
    assert _query(client, "oldest") == {"known": False}
    assert _query(client, "rejected-0")["provider_command_id"] == "rejected-0"
    assert _query(client, "rejected-255") == last
    # Once evicted, an id is no longer claimed to be a cached execution.
    assert _execute(client, "oldest")["step_index"] == 2


def test_fencing_requires_all_capability_resources(provider):
    client = provider()
    _open(client)
    with pytest.raises(ProviderError) as failure:
        _execute(client, "missing-fence", tokens={SOURCE: 1})
    assert failure.value.code == "invalid_parameters"
    assert _query(client, "missing-fence") == {"known": False}
    assert _execute(client, "complete-fence")["step_index"] == 1
