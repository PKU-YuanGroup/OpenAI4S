"""`openai4s user` (M1-3): offline account management, no daemon.

All passwords are obviously fake test values. PBKDF2 iterations are shrunk
per test so the loop stays fast (the production constant is pinned in
test_team_repository.py).
"""

from __future__ import annotations

import io

import pytest

from openai4s.cli.main import build_parser, main
from openai4s.config import Config
from openai4s.storage import team as team_mod
from openai4s.store import get_store


@pytest.fixture(autouse=True)
def _fast_pbkdf2(monkeypatch):
    monkeypatch.setattr(team_mod, "PBKDF2_ITERATIONS", 1200)


def _store():
    return get_store(Config().db_path)


@pytest.mark.parametrize(
    "argv",
    [
        ["user", "add", "alice", "--role", "admin"],
        ["user", "add", "bob", "--password-stdin"],
        ["user", "add", "carol", "--display-name", "Carol C"],
        ["user", "list"],
        ["user", "list", "--json"],
        ["user", "disable", "alice"],
        ["user", "reset-password", "alice"],
        ["user", "reset-password", "alice", "--password-stdin"],
    ],
)
def test_user_argv_parses(argv):
    args = build_parser().parse_args(argv)
    assert args.user_action == argv[1]


def test_user_rejects_bad_role():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["user", "add", "x", "--role", "root"])


def test_add_generates_and_prints_password_once(capsys):
    assert main(["user", "add", "alice", "--role", "admin"]) == 0
    out = capsys.readouterr().out
    assert "created admin alice" in out
    assert "initial password: " in out
    password = out.split("initial password: ")[1].strip()
    # the printed password really is the account's password
    assert _store().team.verify_password("alice", password) is not None


def test_add_password_stdin_not_argv(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("fake-stdin-password\n"))
    assert main(["user", "add", "bob", "--password-stdin"]) == 0
    out = capsys.readouterr().out
    # nothing echoes the password back
    assert "fake-stdin-password" not in out
    assert _store().team.verify_password("bob", "fake-stdin-password") is not None


def test_add_empty_stdin_password_fails(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert main(["user", "add", "bob", "--password-stdin"]) == 2


def test_duplicate_username_exit_code(capsys):
    assert main(["user", "add", "alice"]) == 0
    assert main(["user", "add", "alice"]) == 2
    assert "already exists" in capsys.readouterr().err


def test_list_shows_users_and_disabled_flag(capsys):
    main(["user", "add", "alice", "--role", "admin"])
    main(["user", "disable", "alice"])
    capsys.readouterr()
    assert main(["user", "list"]) == 0
    out = capsys.readouterr().out
    assert "alice" in out and "[disabled]" in out


def test_disable_unknown_user(capsys):
    assert main(["user", "disable", "ghost"]) == 2
    assert "no such user" in capsys.readouterr().err


def test_reset_password_rotates(capsys, monkeypatch):
    main(["user", "add", "alice"])
    capsys.readouterr()
    assert main(["user", "reset-password", "alice"]) == 0
    out = capsys.readouterr().out
    new_pw = out.split("new password: ")[1].strip()
    assert _store().team.verify_password("alice", new_pw) is not None


def test_cli_actions_are_audited():
    main(["user", "add", "alice"])
    main(["user", "disable", "alice"])
    actions = [r["action"] for r in _store().team.list_audit()]
    assert "user_add" in actions and "user_disable" in actions
