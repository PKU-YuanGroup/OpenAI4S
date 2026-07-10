"""Focused tests for remote capability verification probes."""

from __future__ import annotations

import shlex
import subprocess
from types import SimpleNamespace

import pytest

from openai4s.compute import registry
from openai4s.host_dispatch import HostDispatcher
from openai4s.sdk.host import build_host


@pytest.fixture
def registered_host():
    registry.add_host("gpu-test")
    return "gpu-test"


def _spec(alias: str, **overrides) -> dict:
    spec = {
        "alias": alias,
        "capability": "fold",
        "script": "/opt/models/fold.sh",
        "engine": "test-engine",
    }
    spec.update(overrides)
    return spec


@pytest.mark.parametrize(
    "verify_command",
    [
        "test -e /opt/model; touch /tmp/pwned",
        "which python | sh",
        "which python & whoami",
        "which `id`",
        "which $(id)",
        "test -e /opt/model\nwhoami",
        "test -e /opt/model\rwhoami",
        "test -e /opt/model\x00whoami",
        "test -e /opt/model extra",
        "test -e ''",
        "which python extra",
        "command -v python",
        # Pre-change these were expanded by the remote shell; the rebuilt
        # command is quoted and must reject rather than probe a literal path.
        "test -e ~/models/fold.sh",
        'test -e "$HOME/run.sh"',
    ],
)
def test_legacy_probe_rejects_shell_syntax_before_ssh(
    monkeypatch, registered_host, verify_command
):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append((a, kw)))

    result = HostDispatcher()._m_register_remote_capability(
        _spec(registered_host, verify_command=verify_command)
    )

    assert "error" in result
    assert calls == []
    assert "fold" not in registry.get_host(registered_host)["capabilities"]


@pytest.mark.parametrize(
    "probe",
    [
        {"kind": "unknown", "path": "/opt/model"},
        {"kind": "path_exists", "path": "/opt/model", "extra": "token"},
        {"kind": "path_exists", "path": "/opt/model;whoami"},
        {"kind": "executable_exists", "binary": "../python"},
        {"kind": "executable_exists", "binary": "python three"},
        {"kind": "executable_exists", "binary": "python3", "extra": "token"},
    ],
)
def test_structured_probe_rejects_unknown_or_ambiguous_input_before_ssh(
    monkeypatch, registered_host, probe
):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append((a, kw)))

    result = HostDispatcher()._m_register_remote_capability(
        _spec(registered_host, probe=probe)
    )

    assert "error" in result
    assert calls == []


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "probe": {"kind": "path_exists", "path": "/opt/model"},
            "verify_command": "test -e /opt/model",
        },
        {"probe": "test -e /opt/model"},
        {"verify_command": ["test", "-e", "/opt/model"]},
        {"script": "/opt/model; whoami", "probe": None},
        {"script": 123, "probe": None},
    ],
)
def test_ambiguous_or_ill_typed_probe_inputs_never_reach_ssh(
    monkeypatch, registered_host, overrides
):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append((a, kw)))

    result = HostDispatcher()._m_register_remote_capability(
        _spec(registered_host, **overrides)
    )

    assert "error" in result
    assert calls == []


def test_rejected_probe_spec_still_projects_an_activity_step(
    monkeypatch, registered_host
):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append((a, kw)))
    steps = []
    dispatcher = HostDispatcher()
    dispatcher.on_step = steps.append

    result = dispatcher(
        "register_remote_capability",
        [_spec(registered_host, verify_command="test -e /x; touch /tmp/pwned")],
    )

    assert "error" in result
    assert calls == []
    # The blocked attempt must stay visible in the activity timeline.
    assert [step["phase"] for step in steps] == ["begin", "end"]
    assert steps[0]["input"]["probe"] is None
    assert steps[0]["input"]["verification_command"] is None
    assert steps[1]["status"] == "error"


def test_structured_path_probe_is_quoted_and_visible_in_activity(
    monkeypatch, registered_host
):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    steps = []
    dispatcher = HostDispatcher()
    dispatcher.on_step = steps.append
    path = '/opt/Model Runner/runner\'s "service".sh'
    result = dispatcher(
        "register_remote_capability",
        [
            _spec(
                registered_host,
                script=path,
                probe={"kind": "path_exists", "path": path},
            )
        ],
    )

    expected_command = f"test -e {shlex.quote(path)}"
    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0][0][-1] == expected_command
    assert steps[0]["input"]["probe"] == {"kind": "path_exists", "path": path}
    assert steps[0]["input"]["verification_command"] == expected_command
    saved = registry.get_host(registered_host)["capabilities"]["fold"]
    assert saved["probe"] == {"kind": "path_exists", "path": path}
    assert saved["verification"] == expected_command


def test_sdk_forwards_structured_probe_on_the_wire():
    calls = []
    host = build_host(lambda method, args: calls.append((method, args)) or {"ok": True})
    probe = {"kind": "path_exists", "path": "/opt/Model Runner/fold.sh"}

    result = host.register_remote_capability(
        "gpu-test", "fold", script=probe["path"], probe=probe
    )

    assert result == {"ok": True}
    assert calls[0][0] == "register_remote_capability"
    assert calls[0][1][0]["probe"] == probe
    assert calls[0][1][0]["verifyCommand"] == ""


def test_structured_executable_probe_builds_plain_which(monkeypatch, registered_host):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = HostDispatcher()._m_register_remote_capability(
        _spec(
            registered_host,
            script="",
            probe={"kind": "executable_exists", "binary": "python3.11"},
        )
    )

    assert result["ok"] is True
    assert calls[0][-1] == "which python3.11"


@pytest.mark.parametrize(
    ("verify_command", "expected_command", "expected_probe"),
    [
        (
            "test -e '/opt/Model Runner/fold.sh'",
            "test -e '/opt/Model Runner/fold.sh'",
            {"kind": "path_exists", "path": "/opt/Model Runner/fold.sh"},
        ),
        (
            "which python3",
            "which python3",
            {"kind": "executable_exists", "binary": "python3"},
        ),
    ],
)
def test_legacy_allowed_grammar_is_parsed_and_canonicalized(
    monkeypatch,
    registered_host,
    verify_command,
    expected_command,
    expected_probe,
):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = HostDispatcher()._m_register_remote_capability(
        _spec(registered_host, verify_command=verify_command)
    )

    assert result["ok"] is True
    assert calls[0][-1] == expected_command
    saved = registry.get_host(registered_host)["capabilities"]["fold"]
    assert saved["probe"] == expected_probe
    assert saved["verification"] == expected_command


def test_script_fallback_is_a_safe_path_exists_probe(monkeypatch, registered_host):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    script = "/opt/Model Runner/fold's runner.sh"
    result = HostDispatcher()._m_register_remote_capability(
        _spec(registered_host, script=script)
    )

    assert result["ok"] is True
    assert calls[0][-1] == f"test -e {shlex.quote(script)}"
    saved = registry.get_host(registered_host)["capabilities"]["fold"]
    assert saved["probe"] == {"kind": "path_exists", "path": script}


def test_registry_updates_only_after_successful_probe(monkeypatch, registered_host):
    outcomes = iter(
        [
            SimpleNamespace(returncode=1, stdout="", stderr="missing"),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: next(outcomes))
    dispatcher = HostDispatcher()
    spec = _spec(
        registered_host,
        probe={"kind": "path_exists", "path": "/opt/models/fold.sh"},
    )

    failed = dispatcher._m_register_remote_capability(spec)
    assert "error" in failed
    assert "fold" not in registry.get_host(registered_host)["capabilities"]

    succeeded = dispatcher._m_register_remote_capability(spec)
    assert succeeded["ok"] is True
    assert "fold" in registry.get_host(registered_host)["capabilities"]
