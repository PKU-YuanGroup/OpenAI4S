"""What the operational CLI commands -- `cluster`, `lab`, `share` -- tell the
person running them, and what they refuse to do without being asked.

The daemon routes behind `cluster` and `share` are tested against a real
daemon elsewhere; these drive the CLI handlers with the daemon request
stubbed, so they pin the CLI's own wording, refusals and exit codes.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace

import pytest


def _cli_module():
    return importlib.import_module("openai4s.cli.main")


def _no_daemon_call(*_args, **_kwargs):
    raise AssertionError("the daemon must not be asked")


def _cluster_args(action, **fields):
    defaults = dict(
        cluster_action=action,
        command=["true"],
        profile="cpu-interactive",
        backend=None,
        workdir=None,
        json=False,
        job_id="wl_1",
    )
    defaults.update(fields)
    return SimpleNamespace(**defaults)


def _stub_daemon(monkeypatch, module, response, calls=None):
    monkeypatch.setattr(module, "get_config", lambda: SimpleNamespace())
    monkeypatch.setattr(module, "_require_daemon", lambda cfg: True)

    def request(cfg, method, path, body=None):
        if calls is not None:
            calls.append((method, path, body))
        return response

    monkeypatch.setattr(module, "_daemon_request", request)


# --------------------------------------------------------------------------- #
#  CLI-17: a command given as one string, and a job that failed silently
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("as_json", [False, True])
def test_cluster_submit_refuses_a_command_given_as_one_string(
    monkeypatch, capsys, as_json
):
    """`cluster submit "echo hi"` was accepted and then FAILED with no reason
    and no logs: it asked for a program literally named `echo hi`."""
    module = _cli_module()
    monkeypatch.setattr(module, "get_config", lambda: SimpleNamespace())
    monkeypatch.setattr(module, "_require_daemon", _no_daemon_call)
    monkeypatch.setattr(module, "_daemon_request", _no_daemon_call)

    status = module.cmd_cluster(
        _cluster_args("submit", command=["echo hi"], json=as_json)
    )

    captured = capsys.readouterr()
    assert status == 2
    if as_json:
        payload = json.loads(captured.out)
        assert payload["code"] == "command_is_one_string"
        message = payload["error"]
    else:
        assert captured.out == ""
        message = captured.err
    assert "openai4s cluster submit echo hi" in message


def test_cluster_submit_takes_an_existing_path_that_has_a_space(
    tmp_path, monkeypatch, capsys
):
    module = _cli_module()
    program = tmp_path / "my tool.sh"
    program.write_text("#!/bin/sh\n")
    calls: list = []
    _stub_daemon(monkeypatch, module, (202, {"id": "wl_1", "phase": "PENDING"}), calls)

    status = module.cmd_cluster(_cluster_args("submit", command=[str(program)]))

    assert status == 0
    assert calls[0][2]["command"] == [str(program)]


def test_cluster_list_quotes_each_command_and_says_why_a_job_failed(
    monkeypatch, capsys
):
    """`['echo hi']` (FAILED) and `['echo', 'hi']` (COMPLETED) were both
    listed as `echo hi`."""
    module = _cli_module()
    jobs = [
        {
            "id": "wl_a",
            "phase": "FAILED",
            "profile": "cpu-interactive",
            "command": ["echo hi"],
            "reason": "BOOTSTRAP_FAILED",
            "exit_code": 127,
        },
        {
            "id": "wl_b",
            "phase": "COMPLETED",
            "profile": "cpu-interactive",
            "command": ["echo", "hi"],
            "reason": None,
            "exit_code": 0,
        },
    ]
    _stub_daemon(monkeypatch, module, (200, {"jobs": jobs}))

    status = module.cmd_cluster(_cluster_args("list"))

    lines = capsys.readouterr().out.splitlines()
    assert status == 0
    assert lines[0].endswith("'echo hi'  (BOOTSTRAP_FAILED, exit 127)")
    assert lines[1].endswith(" echo hi")


def test_cluster_logs_with_no_output_says_so(monkeypatch, capsys):
    module = _cli_module()
    _stub_daemon(monkeypatch, module, (200, {"stdout": "", "stderr": ""}))

    status = module.cmd_cluster(_cluster_args("logs"))

    captured = capsys.readouterr()
    assert status == 0
    assert captured.out == ""
    assert captured.err.strip() == "(no output)"
