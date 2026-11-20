"""`openai4s user` (M1-3): offline account management, no daemon.

All passwords are obviously fake test values. PBKDF2 iterations are shrunk
per test so the loop stays fast (the production constant is pinned in
test_team_repository.py).
"""

from __future__ import annotations

import io
import json

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
        ["user", "disable", "alice", "--yes"],
        ["user", "enable", "alice"],
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


@pytest.mark.parametrize("action", ["add", "reset-password"])
def test_a_chosen_password_needs_eight_characters(capsys, monkeypatch, action):
    """CLI-16: `echo 1 | openai4s user add carol --password-stdin` made an
    account with a one-character password."""
    if action == "reset-password":
        main(["user", "add", "carol"])
        before = capsys.readouterr().out.split("initial password: ")[1].split()[0]
    monkeypatch.setattr("sys.stdin", io.StringIO("1\n"))
    assert main(["user", action, "carol", "--password-stdin"]) == 2
    assert "at least 8 characters" in capsys.readouterr().err
    if action == "add":
        assert _store().team.get_user_by_username("carol") is None
    else:
        assert _store().team.verify_password("carol", before) is not None
    monkeypatch.setattr("sys.stdin", io.StringIO("12345678\n"))
    assert main(["user", action, "carol", "--password-stdin"]) == 0
    assert _store().team.verify_password("carol", "12345678") is not None


def test_add_says_what_happens_next_in_either_mode(capsys, monkeypatch):
    """CLI-16: with team mode off a new account cannot sign in, and nothing
    said so; with it on, nothing said where to sign in."""
    import importlib

    cli = importlib.import_module("openai4s.cli.main")

    monkeypatch.setattr(cli, "_live_team_mode", lambda cfg: False)
    assert main(["user", "add", "alice", "--role", "admin"]) == 0
    captured = capsys.readouterr()
    assert "OPENAI4S_TEAM_MODE=1" in captured.err and "/login" in captured.err
    assert "team mode is off" not in captured.out

    monkeypatch.setattr(cli, "_live_team_mode", lambda cfg: True)
    assert main(["user", "add", "bob"]) == 0
    captured = capsys.readouterr()
    assert "sign in at http" in captured.out and "/login" in captured.out
    assert "team mode is off" not in captured.err


def test_user_help_names_the_team_mode_switch(capsys):
    with pytest.raises(SystemExit):
        main(["user", "--help"])
    assert "OPENAI4S_TEAM_MODE=1" in capsys.readouterr().out


def test_duplicate_username_exit_code(capsys):
    assert main(["user", "add", "alice"]) == 0
    assert main(["user", "add", "alice"]) == 2
    assert "already exists" in capsys.readouterr().err


def test_list_shows_users_and_disabled_flag(capsys):
    main(["user", "add", "alice"])
    main(["user", "disable", "alice"])
    capsys.readouterr()
    assert main(["user", "list"]) == 0
    out = capsys.readouterr().out
    assert "alice" in out and "disabled" in out


def test_list_has_a_header_and_the_display_name(capsys, monkeypatch):
    """TEAM-7: three unlabelled columns, and a --display-name visible only
    under --json; on a daemon without team mode, no word that the accounts
    do nothing yet."""
    import importlib

    cli = importlib.import_module("openai4s.cli.main")
    monkeypatch.setattr(cli, "_live_team_mode", lambda cfg: False)
    main(["user", "add", "alice", "--role", "admin", "--display-name", "Alice A"])
    main(["user", "add", "bob"])
    main(["user", "disable", "bob"])
    capsys.readouterr()
    assert main(["user", "list"]) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0].split() == ["USERNAME", "ROLE", "STATE", "ID", "DISPLAY", "NAME"]
    alice = next(line for line in lines if line.startswith("alice"))
    assert alice.split()[:3] == ["alice", "admin", "active"]
    assert alice.endswith("Alice A")
    bob = next(line for line in lines if line.startswith("bob"))
    assert bob.split()[:3] == ["bob", "member", "disabled"]
    assert "OPENAI4S_TEAM_MODE=1" in captured.err

    # --json stays pure JSON on stdout, with nothing added
    assert main(["user", "list", "--json"]) == 0
    captured = capsys.readouterr()
    assert [u["display_name"] for u in json.loads(captured.out)] == ["Alice A", None]

    monkeypatch.setattr(cli, "_live_team_mode", lambda cfg: True)
    assert main(["user", "list"]) == 0
    assert "OPENAI4S_TEAM_MODE" not in capsys.readouterr().err


def test_disable_unknown_user(capsys):
    assert main(["user", "disable", "ghost"]) == 2
    assert "no such user" in capsys.readouterr().err


# -- CLI-15: disable is confirmed, reversible, and never the last admin ------


def test_enable_undoes_a_disable(capsys):
    """There was no way back: `set_disabled(..., True)` had two callers and
    nothing ever passed False, so a mistyped name meant a second account."""
    main(["user", "add", "bob"])
    password = capsys.readouterr().out.split("initial password: ")[1].strip()
    assert main(["user", "disable", "bob"]) == 0
    assert "undo with: openai4s user enable bob" in capsys.readouterr().out
    assert _store().team.verify_password("bob", password) is None

    assert main(["user", "enable", "bob"]) == 0
    assert "enabled bob" in capsys.readouterr().out
    assert _store().team.get_user_by_username("bob")["disabled"] is False
    assert _store().team.verify_password("bob", password) is not None
    assert "user_enable" in [r["action"] for r in _store().team.list_audit()]

    # idempotent, and an unknown name is still an error
    assert main(["user", "enable", "bob"]) == 0
    assert "already enabled" in capsys.readouterr().out
    assert main(["user", "enable", "ghost"]) == 2
    assert "no such user" in capsys.readouterr().err


def test_disable_refuses_the_last_enabled_admin(capsys):
    main(["user", "add", "alice", "--role", "admin"])
    capsys.readouterr()
    assert main(["user", "disable", "alice"]) == 2
    assert "last enabled admin" in capsys.readouterr().err
    assert _store().team.get_user_by_username("alice")["disabled"] is False
    assert "user_disable" not in [r["action"] for r in _store().team.list_audit()]

    # with a second admin the first may go -- and then the second may not
    main(["user", "add", "root", "--role", "admin"])
    assert main(["user", "disable", "alice"]) == 0
    capsys.readouterr()
    assert main(["user", "disable", "root"]) == 2
    assert "last enabled admin" in capsys.readouterr().err
    # a disabled admin does not count as one who can still manage the instance
    assert _store().team.get_user_by_username("root")["disabled"] is False


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_disable_asks_first_at_a_terminal(capsys, monkeypatch):
    main(["user", "add", "bob"])
    capsys.readouterr()
    asked: list[str] = []

    def answer_with(reply: str):
        def _input(prompt: str = "") -> str:
            asked.append(prompt)
            return reply

        return _input

    monkeypatch.setattr("sys.stdin", _Terminal())
    monkeypatch.setattr("builtins.input", answer_with("n"))
    assert main(["user", "disable", "bob"]) == 1
    assert len(asked) == 1 and "bob" in asked[0] and "LLM key" in asked[0]
    assert "nothing changed" in capsys.readouterr().out
    assert _store().team.get_user_by_username("bob")["disabled"] is False

    # --yes does not ask
    assert main(["user", "disable", "bob", "--yes"]) == 0
    assert len(asked) == 1
    assert _store().team.get_user_by_username("bob")["disabled"] is True

    main(["user", "enable", "bob"])
    monkeypatch.setattr("builtins.input", answer_with("y"))
    assert main(["user", "disable", "bob"]) == 0
    assert len(asked) == 2
    assert _store().team.get_user_by_username("bob")["disabled"] is True


def test_disable_says_the_llm_keys_are_gone_for_good(capsys):
    """The key deletion is deliberate (a credential must not outlive the
    account that watches it); what was missing was saying so."""
    main(["user", "add", "bob"])
    store = _store()
    user = store.team.get_user_by_username("bob")
    store.user_keys.set_ref(user["id"], "deepseek", "secret:v2:fake/llm-user/bob")
    capsys.readouterr()
    assert main(["user", "disable", "bob"]) == 0
    out = capsys.readouterr().out
    assert "1 personal LLM key(s) deleted" in out
    assert "not restored" in out
    assert store.user_keys.list_for_user(user["id"]) == []


def test_reset_password_on_a_disabled_account_says_it_stays_disabled(capsys):
    main(["user", "add", "bob"])
    main(["user", "disable", "bob"])
    capsys.readouterr()
    assert main(["user", "reset-password", "bob"]) == 0
    out = capsys.readouterr().out
    assert "bob is disabled" in out and "openai4s user enable bob" in out


def test_user_commands_find_the_account_in_any_case(capsys):
    """TEAM-8: one rule for the username everywhere -- `user add` refused
    `BOB` beside `bob`, so `user disable BOB` means bob too."""
    main(["user", "add", "bob"])
    capsys.readouterr()
    assert main(["user", "disable", "BOB", "--yes"]) == 0
    assert "disabled bob" in capsys.readouterr().out
    assert _store().team.get_user_by_username("bob")["disabled"] is True
    assert main(["user", "enable", "Bob"]) == 0
    assert "enabled bob" in capsys.readouterr().out
    assert main(["user", "reset-password", "BOB"]) == 0
    assert "password reset for bob" in capsys.readouterr().out


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
