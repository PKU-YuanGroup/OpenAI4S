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


# --------------------------------------------------------------------------- #
#  CLI-18: profiles and the cluster backend name the file that configures them
# --------------------------------------------------------------------------- #


def test_cluster_profiles_without_a_cluster_says_where_the_file_goes(
    tmp_path, monkeypatch, capsys
):
    module = _cli_module()
    _stub_daemon(
        monkeypatch,
        module,
        (200, {"cluster": "", "configured": False, "profiles": []}),
    )
    monkeypatch.setattr(
        module, "get_config", lambda: SimpleNamespace(data_dir=tmp_path)
    )

    status = module.cmd_cluster(_cluster_args("profiles"))

    out = " ".join(capsys.readouterr().out.split())
    assert status == 0
    assert str(tmp_path / "cluster.toml") in out
    assert "docs/team-server.md" in out
    assert "cpu-interactive" in out


def test_cluster_submit_help_says_what_the_backend_and_profile_need(capsys):
    module = _cli_module()
    with pytest.raises(SystemExit):
        module.main(["cluster", "submit", "--help"])
    # argparse may wrap at the hyphen of `cpu-interactive`.
    text = " ".join(capsys.readouterr().out.split()).replace("cpu- ", "cpu-")
    assert "cluster needs <data_dir>/cluster.toml" in text
    assert "only the default cpu-interactive exists" in text


# --------------------------------------------------------------------------- #
#  CLI-31: a lab query never downloads OpenMHS without consent
# --------------------------------------------------------------------------- #


class _Stdin:
    def __init__(self, tty: bool, answer: str = ""):
        self._tty = tty
        self._answer = answer
        self.asked = False

    def isatty(self):
        return self._tty

    def readline(self):
        self.asked = True
        return self._answer


@pytest.fixture()
def no_openmhs(tmp_path, monkeypatch):
    """OpenMHS absent, and a `fetch` that records instead of cloning."""
    from openai4s.mhs_lab import openmhs

    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(openmhs, "find_checkout", lambda: None)
    fetched: list = []

    def fetch(*_args, **_kwargs):
        fetched.append(True)
        raise RuntimeError("download stub reached")

    monkeypatch.setattr(openmhs, "fetch", fetch)
    return fetched


def _lab(argv):
    from openai4s.mhs_lab import cli

    return cli.main(argv)


@pytest.mark.parametrize("argv", [["search", "julabo"], ["show", "julabo"]])
def test_a_lab_query_in_a_script_refuses_instead_of_downloading(
    no_openmhs, monkeypatch, capsys, argv
):
    """`lab search julabo` cloned OpenMHS from GitHub (145 MB on disk) without
    asking, while `lab status` refused and pointed at `lab setup`."""
    monkeypatch.setattr("sys.stdin", _Stdin(tty=False))

    status = _lab(argv)

    err = capsys.readouterr().err
    assert status == 2
    assert no_openmhs == []
    assert "nothing was downloaded" in err
    assert "openai4s lab setup" in err and "--yes" in err


def test_a_lab_query_on_a_terminal_asks_first(no_openmhs, monkeypatch, capsys):
    stdin = _Stdin(tty=True, answer="n\n")
    monkeypatch.setattr("sys.stdin", stdin)

    status = _lab(["search", "julabo"])

    err = capsys.readouterr().err
    assert stdin.asked
    assert "Download OpenMHS" in err and "150 MB on disk" in err
    assert status == 2 and no_openmhs == []

    monkeypatch.setattr("sys.stdin", _Stdin(tty=True, answer="y\n"))
    _lab(["search", "julabo"])
    assert no_openmhs == [True]


@pytest.mark.parametrize(
    "argv", [["search", "--yes", "julabo"], ["setup"], ["add", "-y", "julabo"]]
)
def test_yes_and_setup_download_without_asking(no_openmhs, monkeypatch, argv):
    stdin = _Stdin(tty=True, answer="n\n")
    monkeypatch.setattr("sys.stdin", stdin)

    _lab(argv)

    assert no_openmhs == [True]
    assert not stdin.asked


def test_a_json_query_never_prompts(no_openmhs, monkeypatch, capsys):
    stdin = _Stdin(tty=True, answer="y\n")
    monkeypatch.setattr("sys.stdin", stdin)

    status = _lab(["search", "--json", "julabo"])

    assert status == 2
    assert not stdin.asked and no_openmhs == []


# --------------------------------------------------------------------------- #
#  CLI-32: `lab reset` asks before it erases the lab's evidence
# --------------------------------------------------------------------------- #


@pytest.fixture()
def reset_calls(tmp_path, monkeypatch):
    from openai4s.mhs_lab import supervisor

    monkeypatch.setenv("OPENAI4S_MHS_LAB_DIR", str(tmp_path / "lab"))
    calls: list = []
    monkeypatch.setattr(
        supervisor, "reset", lambda: calls.append(True) or {"reset": True}
    )
    return calls


def test_lab_reset_in_a_script_erases_nothing_without_yes(
    reset_calls, tmp_path, monkeypatch, capsys
):
    """`lab reset </dev/null` erased evidence, latches and LIMS at once."""
    monkeypatch.setattr("sys.stdin", _Stdin(tty=False))

    status = _lab(["reset"])

    err = " ".join(capsys.readouterr().err.split())
    assert status == 1 and reset_calls == []
    assert "nothing was reset" in err
    assert "evidence, E-stop latches, LIMS" in err
    assert str(tmp_path / "lab") in err and "--yes" in err


def test_lab_reset_on_a_terminal_asks_and_respects_no(reset_calls, monkeypatch, capsys):
    stdin = _Stdin(tty=True, answer="\n")
    monkeypatch.setattr("sys.stdin", stdin)

    assert _lab(["reset"]) == 1
    assert stdin.asked and reset_calls == []
    assert "Reset the lab?" in capsys.readouterr().err

    monkeypatch.setattr("sys.stdin", _Stdin(tty=True, answer="yes\n"))
    assert _lab(["reset"]) == 0
    assert reset_calls == [True]


def test_lab_reset_with_yes_does_not_ask(reset_calls, monkeypatch):
    stdin = _Stdin(tty=True, answer="n\n")
    monkeypatch.setattr("sys.stdin", stdin)

    assert _lab(["reset", "--yes"]) == 0
    assert reset_calls == [True] and not stdin.asked


def test_lab_reset_json_without_yes_is_a_refusal_object(
    reset_calls, monkeypatch, capsys
):
    monkeypatch.setattr("sys.stdin", _Stdin(tty=True, answer="y\n"))

    assert _lab(["reset", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["reset"] is False
    assert payload["code"] == "confirmation_required"
    assert reset_calls == []
