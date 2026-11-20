"""The setup-side CLI says what it did, and refuses what it cannot do, in words.

`init`, `serve`, `status`, `url`, `stop`, `env`, `setup`, `diagnostics` and the
two launch scripts are the first things a new user runs, usually before
anything works. Each case here is a place where one of them accepted bad input
silently, printed something untrue, or answered with a Python traceback.
"""

from __future__ import annotations

import importlib
import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig

_REPO = Path(__file__).resolve().parents[1]


def _cli():
    return importlib.import_module("openai4s.cli.main")


# --------------------------------------------------------------------------
# `openai4s init`, driven as a person at a terminal would drive it
# --------------------------------------------------------------------------


class _Terminal:
    """A stdin that says it is a terminal; the wizard reads through `input`."""

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:  # pragma: no cover - a wizard never reads a pipe
        raise AssertionError("the interactive wizard must not read stdin directly")


class _Wizard:
    """One interactive `openai4s init` run against a real Store in tmp_path."""

    def __init__(self, tmp_path, monkeypatch, capsys):
        from openai4s.llm import PROVIDERS
        from openai4s.onboarding import OnboardingService
        from openai4s.store import get_store

        self.cli = _cli()
        self.capsys = capsys
        self.monkeypatch = monkeypatch
        self.cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="ark"))
        self.cfg.ensure_dirs()
        self._get_store = get_store
        self.prompts: list[str] = []
        self.secret_prompts: list[str] = []

        def service():
            store = get_store(self.cfg.db_path)
            return OnboardingService(self.cfg, store, PROVIDERS), store

        monkeypatch.setattr(self.cli, "_onboarding_service", service)

    def run(self, answers, *, secrets=(), argv=(), terminal=True, piped=None):
        typed = iter(answers)
        hidden = iter(secrets)

        def fake_input(prompt=""):
            self.prompts.append(prompt)
            try:
                return next(typed)
            except StopIteration:
                raise EOFError from None

        def fake_getpass(prompt=""):
            self.secret_prompts.append(prompt)
            return next(hidden)

        self.monkeypatch.setattr("builtins.input", fake_input)
        self.monkeypatch.setattr(self.cli.getpass, "getpass", fake_getpass)
        if piped is not None:
            self.monkeypatch.setattr(self.cli.sys, "stdin", io.StringIO(piped))
        elif terminal:
            self.monkeypatch.setattr(self.cli.sys, "stdin", _Terminal())
        args = self.cli.build_parser().parse_args(["init", *argv])
        self.rc = self.cli.cmd_init(args)
        captured = self.capsys.readouterr()
        self.out, self.err = captured.out, captured.err
        return self

    def setting(self, key: str):
        return self._get_store(self.cfg.db_path).get_setting(key)


@pytest.fixture
def wizard(tmp_path, monkeypatch, capsys):
    return _Wizard(tmp_path, monkeypatch, capsys)


def test_init_asks_for_the_provider_again_instead_of_failing_at_the_end(wizard):
    """CLI-2. A mistyped provider was carried through every later question --
    each showing the *current* provider's model and URL -- and refused only
    after the last one, with every answer thrown away."""
    wizard.run(["opena1", "nonsense", "claude", "", "", "n", ""])

    assert wizard.rc == 0
    assert "unknown provider 'opena1'" in wizard.out
    assert "unknown provider 'nonsense'" in wizard.out
    provider_prompts = [p for p in wizard.prompts if p.startswith("Provider")]
    assert len(provider_prompts) == 3
    # The questions after it describe the provider that was chosen, not Ark.
    model_prompt = next(p for p in wizard.prompts if p.startswith("Model"))
    url_prompt = next(p for p in wizard.prompts if p.startswith("Base URL"))
    assert "claude" in model_prompt and "doubao" not in model_prompt
    assert "anthropic" in url_prompt and "volces" not in url_prompt
    assert wizard.setting("llm_provider") == "claude"


def test_init_ends_cleanly_when_input_runs_out_at_a_repeated_question(wizard):
    wizard.run(["nonsense"])

    assert wizard.rc == 1
    assert "input ended before setup was finished" in wizard.err
    assert "Traceback" not in wizard.err
    assert not wizard.setting("llm_provider")


def test_init_lists_what_each_provider_id_means(wizard):
    """CLI-3. The wizard printed six bare ids. The README and the workbench
    call the same choices OpenAI-compatible, Anthropic-compatible and so on,
    and nothing connected the two vocabularies."""
    wizard.run(["", "", "", "n", ""])

    assert wizard.rc == 0
    listed = {
        line.split()[0]: line
        for line in wizard.out.splitlines()
        if line.startswith("  ") and line.split()
    }
    assert "OpenAI-compatible" in listed["chatgpt"]
    assert "DeepSeek" in listed["chatgpt"] and "vLLM" in listed["chatgpt"]
    assert "Anthropic-compatible" in listed["claude"]
    assert "Ark-compatible" in listed["ark"]
    assert "Gemini-compatible" in listed["gemini"]
    assert "OpenAI Responses" in listed["openai_responses"]
    assert "ollama" in listed


@pytest.mark.parametrize(
    ("typed", "provider"),
    [
        ("openai", "chatgpt"),
        ("OpenAI-compatible", "chatgpt"),
        ("openai compatible", "chatgpt"),
        ("Anthropic-compatible", "claude"),
        ("anthropic", "claude"),
        ("Gemini-compatible", "gemini"),
        ("OpenAI Responses", "openai_responses"),
        ("openai-responses", "openai_responses"),
        ("Ark-compatible", "ark"),
    ],
)
def test_init_accepts_the_name_the_readme_uses_for_a_provider(wizard, typed, provider):
    wizard.run([typed, "", "", "n", ""])

    assert wizard.rc == 0
    assert f"using provider {provider}" in wizard.out
    assert wizard.setting("llm_provider") == provider


def test_init_asks_the_key_question_again_on_an_answer_that_is_not_y_or_n(wizard):
    """CLI-4. `maybe` at "Configure an API key now? [y/N]" was taken as no,
    silently, and the run ended with no key stored."""
    wizard.run(["", "", "", "maybe", "y", ""], secrets=["sk-test-cli4"])

    assert wizard.rc == 0
    key_prompts = [p for p in wizard.prompts if p.startswith("Configure an API key")]
    assert len(key_prompts) == 2
    assert "answer y or n" in wizard.out
    assert wizard.secret_prompts == ["API key (input hidden): "]


def _search_result(out: str) -> str:
    """The "Web search: …" line init prints after saving, not its menu."""
    return next(
        (
            line
            for line in out.splitlines()
            if line.startswith("Web search:") and "1)" not in line
        ),
        "",
    )


def test_init_asks_the_search_choice_again_on_a_number_off_the_menu(wizard):
    """CLI-4. `9` at "Choose 1-5" was read as "keep the current setup"; the
    run said "Configured ..." and the user believed an engine was chosen."""
    wizard.run(["", "", "", "n", "9", "5"])

    assert wizard.rc == 0
    search_prompts = [p for p in wizard.prompts if p.startswith("Choose 1-5")]
    assert len(search_prompts) == 2
    assert "enter a number from 1 to 5" in wizard.out
    assert _search_result(wizard.out).startswith("Web search: parallel")


def test_init_search_menu_says_doubao_is_tried_before_any_backup_choice(wizard):
    """CLI-5. Item 4 read "built-in keyless engines only", and with an Ark
    Agent Plan key stored the run then said "Web search: Doubao, then
    builtin". The behaviour stays (lead decision); the menu now says it."""
    wizard.run(["", "", "", "n", "4"])

    assert wizard.rc == 0
    menu = [line for line in wizard.out.splitlines() if "4)" in line]
    assert menu == [
        "  4) backups: built-in keyless engines only (Doubao, if configured, "
        "is still tried first)  5) Parallel first (keyless)"
    ]
    assert "Doubao is tried before the backups whenever an Agent Plan key" in (
        wizard.out
    )


def test_init_without_a_terminal_says_it_asked_nothing(wizard):
    """CLI-7. `echo | openai4s init` (or `docker exec` without -t) asked no
    question and saved the default provider and model as if chosen, with
    no word that the guided command had not been guided."""
    wizard.run([], piped="\n", terminal=False)

    assert wizard.rc == 0
    assert wizard.prompts == []
    assert wizard.err.startswith("note: stdin is not a terminal, so no questions")
    assert "Configured ark /" in wizard.out


def test_init_non_interactive_is_not_told_what_it_asked_for(wizard):
    wizard.run([], argv=["--non-interactive"], piped="", terminal=False)

    assert wizard.rc == 0
    assert "stdin is not a terminal" not in wizard.err


def test_init_json_says_a_key_is_still_needed_beside_complete(wizard, monkeypatch):
    """CLI-7. `init --non-interactive --json` with no key reported
    `"has_api_key": false, "complete": true`; a script reading `complete`
    took the install for usable. `complete` keeps its meaning (the first-run
    setup was saved) and `needs_api_key` says what is missing."""
    import json

    monkeypatch.delenv("OPENAI4S_ARK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI4S_LLM_API_KEY", raising=False)
    wizard.cfg.llm.api_key = ""  # the suite's fake key is in the env, not stored
    wizard.run([], argv=["--non-interactive", "--json"], piped="", terminal=False)

    payload = json.loads(wizard.out)
    assert wizard.rc == 0
    assert payload["has_api_key"] is False
    assert payload["complete"] is True
    assert payload["needs_api_key"] is True


def test_init_enter_at_the_search_choice_still_keeps_the_current_setup(wizard):
    wizard.run(["", "", "", "", ""])

    assert wizard.rc == 0
    assert len([p for p in wizard.prompts if p.startswith("Choose 1-5")]) == 1
    assert _search_result(wizard.out) == ""


def test_init_provider_flag_accepts_the_same_names(wizard):
    wizard.run(
        [],
        argv=["--non-interactive", "--provider", "OpenAI-compatible"],
        terminal=False,
    )

    assert wizard.rc == 0
    assert wizard.setting("llm_provider") == "chatgpt"
    assert "Configured chatgpt /" in wizard.out


def test_init_still_refuses_a_name_that_selects_nothing(wizard):
    wizard.run([], argv=["--non-interactive", "--provider", "deepmind"], terminal=False)

    assert wizard.rc == 1
    assert "unknown provider 'deepmind'" in wizard.err


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--search-key-stdin"], "--search-key-stdin needs --search doubao|tavily|you"),
        (
            ["--search-key-stdin", "--search", "builtin"],
            "--search builtin takes no key",
        ),
    ],
)
def test_init_refuses_a_search_key_with_no_engine_to_store_it_for(
    wizard, argv, expected
):
    """CLI-6. `echo KEY | openai4s init --search-key-stdin` read the key,
    dropped it, printed `Configured ...` and exited 0."""
    wizard.run([], argv=argv, piped="a-search-key-nobody-asked-for\n")

    assert wizard.rc == 1
    assert expected in wizard.err
    assert "Configured" not in wizard.out
    # Refused before anything was saved, not after the model settings were.
    assert not wizard.setting("llm_provider")


def test_init_still_stores_a_search_key_that_names_its_engine(wizard):
    wizard.run([], argv=["--search-key-stdin", "--search", "tavily"], piped="tvly-k\n")

    assert wizard.rc == 0
    assert "Web search:" in wizard.out
    assert "tavily" in wizard.out


# --------------------------------------------------------------------------
# `./start.sh` and `openai4s serve`: the address a person is told
# --------------------------------------------------------------------------


def _start_sh_with_a_recording_cli(tmp_path) -> Path:
    """A copy of start.sh beside a `.venv/bin/openai4s` that echoes its argv."""
    folder = tmp_path / "openai4s-folder"
    (folder / ".venv" / "bin").mkdir(parents=True)
    shutil.copy(_REPO / "start.sh", folder / "start.sh")
    stub = folder / ".venv" / "bin" / "openai4s"
    stub.write_text('#!/bin/sh\necho "CLI: $*"\n', encoding="utf-8")
    stub.chmod(0o755)
    return folder / "start.sh"


@pytest.mark.skipif(os.name != "posix", reason="start.sh is a POSIX launcher")
@pytest.mark.parametrize(
    ("env", "argv"),
    [({"OPENAI4S_PORT": "8812"}, ["--no-open"]), ({}, ["--port", "9000"])],
)
def test_start_sh_prints_no_address_of_its_own(tmp_path, env, argv):
    """CLI-9 / M-1. The banner hard-coded http://127.0.0.1:8760/ and printed
    it whatever port was asked for, directly above the daemon's own
    `listening at` line naming the real one. The script cannot know the
    address (--port, OPENAI4S_PORT and .env all move it), so it prints none."""
    script = _start_sh_with_a_recording_cli(tmp_path)

    result = subprocess.run(
        ["bash", str(script), *argv],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": os.environ.get("PATH", os.defpath), **env},
    )

    assert result.returncode == 0, result.stderr
    assert "8760" not in result.stdout + result.stderr
    assert "http://" not in result.stdout + result.stderr
    assert f"CLI: serve {' '.join(argv)}" in result.stdout


class _BlockBuffered(io.StringIO):
    """stdout as it is under nohup, a redirect or --detached: text reaches the
    log only when it is flushed."""

    def __init__(self) -> None:
        super().__init__()
        self.in_the_log = ""

    def flush(self) -> None:
        self.in_the_log = self.getvalue()


def test_serve_ready_lines_reach_a_redirected_log_while_the_daemon_runs(
    tmp_path, monkeypatch
):
    """CLI-9. `./start.sh > serve.log 2>&1` showed nothing but the wrong
    banner address for as long as the daemon ran: the `listening at` line was
    printed without a flush and only appeared after `openai4s stop`."""
    import openai4s.server as server_pkg

    cli = _cli()
    cfg = Config(
        data_dir=tmp_path,
        host="127.0.0.1",
        port=8812,
        llm=LLMConfig(provider="deepseek", api_key="k"),
    )
    cfg.ensure_dirs()
    log = _BlockBuffered()
    seen_while_serving: list[str] = []
    monkeypatch.setattr(cli, "get_config", lambda initialize_dirs=True: cfg)
    monkeypatch.setattr(server_pkg, "build_server", lambda _cfg: object())
    monkeypatch.setattr(
        server_pkg,
        "run_server",
        lambda _httpd: seen_while_serving.append(log.in_the_log),
    )
    monkeypatch.setattr(sys, "stdout", log)

    rc = cli.cmd_serve(type("A", (), {"detached": False, "no_open": True})())

    assert rc == 0
    (visible,) = seen_while_serving
    assert "openai4s listening at http://127.0.0.1:8812/" in visible
    assert "web UI ready" in visible


def test_serve_names_the_way_to_sign_in_once_and_with_the_path_fallback(tmp_path):
    """CLI-9. One start printed two sign-in lines in two wordings: the
    gateway's notice (`run `openai4s url` on this host …`) and serve's own,
    which alone carried the `uv run` fallback for a .venv not on PATH. Serve
    no longer prints its own; the gateway's notice is the sentence the CLI
    prints everywhere else (`--detached`, "already running")."""
    from openai4s.server import local_auth

    cli = _cli()
    cfg = Config(data_dir=tmp_path, host="127.0.0.1", port=8812)

    banner = local_auth.startup_auth_banner("127.0.0.1", 8812, team_mode=False)

    assert banner.splitlines()[-1].strip() == cli._sign_in_hint(cfg)
    assert "uv run openai4s url" in banner


# --------------------------------------------------------------------------
# `status`, `url`, `stop`: read the daemon's state, create nothing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["status", "url", "stop"])
def test_a_state_command_leaves_a_mistyped_data_dir_uncreated(
    tmp_path, monkeypatch, capsys, command
):
    """CLI-13. `OPENAI4S_DATA_DIR=<typo> openai4s status` answered "daemon:
    not running" and created <typo>/{artifacts,logs,...} as a side effect, so
    the typo turned into a real, empty data directory."""
    typo = tmp_path / "typo-dir"
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(typo))

    rc = _cli().main([command])

    out = capsys.readouterr()
    assert not typo.exists()
    assert str(typo) in out.out + out.err
    if command == "url":
        assert rc == 0
        assert "it does not exist" in out.err
    else:
        assert rc == 1
        assert f"there is no data directory at {typo}" in out.out


@pytest.mark.parametrize("command", ["status", "stop"])
def test_not_running_names_the_data_dir_it_checked(
    tmp_path, monkeypatch, capsys, command
):
    """CLI-13. `status` and `stop` said only "daemon: not running", while
    `url` named the data dir it looked in; a daemon started under another
    OPENAI4S_DATA_DIR looked simply absent."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(data))

    assert _cli().main([command]) == 1

    assert f"daemon: not running (data dir {data})" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["status", "url", "stop"])
def test_a_state_command_refuses_a_data_dir_that_is_a_file(
    tmp_path, monkeypatch, capsys, command
):
    """CLI-12. `OPENAI4S_DATA_DIR=<a file> openai4s status` was a
    FileExistsError traceback from mkdir."""
    afile = tmp_path / "afile"
    afile.write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(afile))

    rc = _cli().main([command])

    out = capsys.readouterr()
    assert rc == 1
    assert out.err.startswith(f"error: the data directory {afile} is not a directory")
    assert "Traceback" not in out.err
    assert out.out == ""


# --------------------------------------------------------------------------
# `serve`, `diagnostics`, `env`: one `error:` line instead of a traceback
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [["serve", "--no-open"], ["diagnostics"], ["env", "plan", "python"]],
)
def test_a_data_dir_that_cannot_be_prepared_is_one_error_line(
    tmp_path, monkeypatch, capsys, argv
):
    """CLI-12. `OPENAI4S_DATA_DIR=<a file> openai4s serve` ended in a 20-line
    traceback whose last line was mkdir's; `doctor` said the same thing in
    words and pointed at OPENAI4S_DATA_DIR."""
    afile = tmp_path / "afile"
    afile.write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(afile))
    monkeypatch.chdir(tmp_path)

    rc = _cli().main(argv)

    err = capsys.readouterr().err
    assert rc == 1
    assert err.startswith(
        f"error: cannot use the data directory {afile} (it is not a directory)"
    )
    assert "OPENAI4S_DATA_DIR" in err and "openai4s doctor" in err
    assert "Traceback" not in err


def test_diagnostics_into_a_directory_asks_for_a_file_name(
    tmp_path, monkeypatch, capsys
):
    """CLI-12. `openai4s diagnostics -o <existing dir>` was an
    IsADirectoryError traceback from zipfile."""
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "data"))
    folder = tmp_path / "out"
    folder.mkdir()

    rc = _cli().main(["diagnostics", "-o", str(folder)])

    err = capsys.readouterr().err
    assert rc == 1
    assert err.startswith(f"error: {folder} is a directory")
    assert str(folder / "openai4s-diagnostics.zip") in err
    assert list(folder.iterdir()) == []


def test_diagnostics_to_a_place_it_cannot_write_is_one_error_line(
    tmp_path, monkeypatch, capsys
):
    """CLI-12. `-o /nonexistent-dir/x.zip` was an OSError traceback."""
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "data"))
    blocker = tmp_path / "afile"
    blocker.write_text("", encoding="utf-8")
    target = blocker / "x.zip"

    rc = _cli().main(["diagnostics", "-o", str(target)])

    err = capsys.readouterr().err
    assert rc == 1
    assert err.startswith(f"error: cannot write {target} (")
    assert "Traceback" not in err


@pytest.mark.parametrize("action", ["plan", "apply"])
def test_env_names_an_unknown_environment_and_lists_the_real_ones(
    tmp_path, monkeypatch, capsys, action
):
    """CLI-12. `openai4s env plan nosuch` was two chained tracebacks ending
    in `EnvironmentError_: cannot read the spec for 'nosuch'`, and never said
    which names exist."""
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "data"))

    rc = _cli().main(["env", action, "nosuch"])

    err = capsys.readouterr().err
    assert rc == 1
    assert err.startswith("error: no environment named 'nosuch'; choose from: ")
    for name in ("python", "r", "phylo", "struct"):
        assert name in err
    assert not (tmp_path / "data").exists()


# --------------------------------------------------------------------------
# `setup` and `env`: two ways to build the kernel envs, each naming the other
# --------------------------------------------------------------------------


def _help_text(argv, capsys) -> str:
    with pytest.raises(SystemExit) as stopped:
        _cli().build_parser().parse_args([*argv, "--help"])
    assert stopped.value.code == 0
    return " ".join(capsys.readouterr().out.split())


def test_setup_and_env_help_say_how_they_differ(capsys):
    """CLI-22. `setup` ("create or update conda envs") and `env`
    ("environments as a transaction") never mentioned each other, and a
    scientist could not tell which one to use."""
    top = _help_text([], capsys)
    setup = _help_text(["setup"], capsys)
    env = _help_text(["env"], capsys)

    assert "`env` builds them as generations you can roll back" in top
    assert "`setup` builds them in place" in top
    assert "`openai4s env` builds the same specs" in setup
    assert "python, r, phylo or struct" in setup
    assert "`openai4s setup` instead creates or updates ordinary conda envs" in env


@pytest.mark.parametrize(
    ("argv", "phrases"),
    [
        (
            ["env", "plan"],
            [
                "by spec name in envs/: python, r, phylo, struct",
                "machine-readable plan",
            ],
        ),
        (["env", "apply"], ["by spec name in envs/", "without building anything"]),
        (["env", "list"], ["environments to list", "machine-readable list"]),
        (["env", "rollback"], ["environment name", "from `openai4s env list`"]),
        (["env", "recover"], ["environments to check", "machine-readable report"]),
    ],
)
def test_every_env_argument_says_what_it_takes(capsys, argv, phrases):
    """CLI-22. `names`, `--json`, `--dry-run`, `name` and `generation` had
    no help at all, and `recover` was "what a restart should know"."""
    text = _help_text(argv, capsys)
    for phrase in phrases:
        assert phrase in text
    assert "what a restart should know" not in text


def test_setup_dry_run_names_the_conda_env_and_where_it_goes(
    tmp_path, monkeypatch, capsys
):
    """CLI-22. `setup --dry-run` printed `[python] would create: conda env
    create -f .../python.yml` -- not that this makes a conda env literally
    named `python` (easily one the user already has), nor where."""
    cli = _cli()
    for name in ("python", "r"):
        (tmp_path / f"{name}.yml").write_text(f"name: {name}\n", encoding="utf-8")
    existing_r = tmp_path / "envs" / "r"
    monkeypatch.setattr(cli, "_find_conda_tool", lambda: "micromamba")
    monkeypatch.setattr(cli, "_envs_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_existing_envs", lambda: {"r": existing_r})
    args = type(
        "A", (), {"only": None, "profile": "standard", "dry_run": True, "update": False}
    )()

    assert cli.cmd_setup(args) == 0

    out = capsys.readouterr().out
    assert (
        "[python] would create conda env 'python' in micromamba's own envs "
        "directory: micromamba env create -f" in out
    )
    assert f"[r] conda env 'r' already exists at {existing_r}" in out


# --------------------------------------------------------------------------
# `status --json`: what the text form says, for scripts
# --------------------------------------------------------------------------


def test_status_json_carries_the_address_model_and_data_dir(
    tmp_path, monkeypatch, capsys
):
    """CLI-14. The text form showed the URL, model and data dir; `--json`
    had only running/pid/version/bundle_id, so a script that wanted the port
    had to parse the sentence."""
    import json

    cli = _cli()
    cfg = Config(data_dir=tmp_path, host="127.0.0.1", port=8760)

    class _Health:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        @staticmethod
        def read():
            return b'{"status": "ok", "model": "demo-model"}'

    monkeypatch.setattr(cli, "get_config", lambda **_: cfg)
    monkeypatch.setattr(cli, "_read_pid", lambda _cfg: 4321)
    monkeypatch.setattr(cli, "_daemon_alive", lambda _cfg, _pid: True)
    monkeypatch.setattr(cli, "_recorded_endpoint", lambda _cfg, _pid: ("::1", 9222))
    monkeypatch.setattr(
        cli,
        "_recorded_state",
        lambda _cfg: {"pid": 4321, "version": "9.9", "bundle_id": "b"},
    )
    monkeypatch.setattr(cli, "_open_daemon", lambda *_a, **_k: _Health())

    assert cli.cmd_status(type("A", (), {"json": True})()) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "running": True,
        "pid": 4321,
        "version": "9.9",
        "bundle_id": "b",
        "url": "http://[::1]:9222/",
        "host": "::1",
        "port": 9222,
        "model": "demo-model",
        "data_dir": str(tmp_path),
    }


def test_status_json_when_not_running_is_still_json(tmp_path, monkeypatch, capsys):
    """CLI-14. `status --json` with no daemon printed the human sentence."""
    import json

    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))

    assert _cli().main(["status", "--json"]) == 1

    assert json.loads(capsys.readouterr().out) == {
        "running": False,
        "data_dir": str(tmp_path),
    }


# --------------------------------------------------------------------------
# `jupyter`
# --------------------------------------------------------------------------


def test_jupyter_export_over_an_existing_spec_names_the_cli_flag(tmp_path, capsys):
    """CLI-21. A second `openai4s jupyter export <dir>` said `use
    replace=True`, the Python keyword, where the CLI flag is `--replace`."""
    cli = _cli()
    kernels = tmp_path / "kspecs"
    assert cli.main(["jupyter", "export", str(kernels)]) == 0
    capsys.readouterr()

    assert cli.main(["jupyter", "export", str(kernels)]) == 1

    err = capsys.readouterr().err
    assert "pass --replace to rewrite its kernel.json" in err
    assert cli.main(["jupyter", "export", str(kernels), "--replace"]) == 0


@pytest.mark.parametrize("bridge", [False, True])
def test_jupyter_install_warns_when_the_kernels_it_wrote_cannot_start(
    tmp_path, monkeypatch, capsys, bridge
):
    """CLI-34. Without ipykernel, `jupyter install` printed "installed
    openai4s-python …" and exit 0; the kernels showed up in JupyterLab and
    failed the moment one was started."""
    import openai4s.adapters.jupyter as jupyter

    status = dict(jupyter.adapter_status(), bridge_available=bridge)
    monkeypatch.setattr(jupyter, "adapter_status", lambda: status)

    rc = _cli().main(["jupyter", "install", "--prefix", str(tmp_path)])

    out = capsys.readouterr()
    assert rc == 0
    assert "installed openai4s-python:" in out.out
    if bridge:
        assert "warning" not in out.err
    else:
        assert out.err.startswith("warning: ipykernel is not installed in this ")
        assert "fail to start" in out.err
        assert "'ipykernel>=7,<8'" in out.err


# --------------------------------------------------------------------------
# `diagnostics`: a bundle that says what went wrong
# --------------------------------------------------------------------------


def _bundle_report(path) -> dict:
    import json
    import zipfile

    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("report.json"))


def test_diagnostics_reports_failed_and_cancelled_runs(tmp_path, monkeypatch, capsys):
    """CLI-33. Three `run`s that failed on a 401 and one Ctrl-C left no
    ledger terminal, so the bundle said `"reasons": {"submitted": 2}` and
    nothing about the crash it was exported for."""
    from openai4s.store import get_store

    data = tmp_path / "data"
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(data))
    cfg = Config(data_dir=data)
    cfg.ensure_dirs()
    store = get_store(cfg.db_path)
    for status in ("failed", "failed", "cancelled", "done"):
        store.update_frame(store.new_frame(), status=status)
    store.close()
    monkeypatch.setattr(
        "openai4s.doctor.report", lambda _cfg, **_k: {"status": "ok", "checks": []}
    )
    target = tmp_path / "diag.zip"

    assert _cli().main(["diagnostics", "-o", str(target)]) == 0

    frames = _bundle_report(target)["agent_turns"]["frames"]
    assert frames == {"cancelled": 1, "done": 1, "failed": 2}


def test_diagnostics_carries_doctor_verdicts_and_migrations_as_counts(
    tmp_path, monkeypatch, capsys
):
    """CLI-33. The bundle had no doctor result (`kernel_sandbox: "(default)"`
    did not say whether the sandbox held), and 43 migration rows of
    `<redacted:…>` made up most of report.json."""
    from openai4s.storage.migrations import SCHEMA_VERSION
    from openai4s.store import get_store

    data = tmp_path / "data"
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(data))
    cfg = Config(data_dir=data)
    cfg.ensure_dirs()
    get_store(cfg.db_path).close()
    verdicts = {
        "status": "warn",
        "checks": [
            {"name": "isolation", "status": "warn", "detail": "private detail text"},
            {"name": "model", "status": "ok", "detail": "model 'x'"},
            {"name": "not-a-doctor-check", "status": "ok", "detail": ""},
        ],
    }
    monkeypatch.setattr("openai4s.doctor.report", lambda _cfg, **_k: verdicts)
    target = tmp_path / "diag.zip"

    assert _cli().main(["diagnostics", "-o", str(target)]) == 0

    report = _bundle_report(target)
    assert report["doctor"]["status"] == "warn"
    assert report["doctor"]["checks"]["isolation"] == "warn"
    assert report["doctor"]["checks"]["model"] == "ok"
    assert "not-a-doctor-check" not in report["doctor"]["checks"]
    applied = report["security"]["schema"]["applied"]
    assert applied["count"] == SCHEMA_VERSION
    assert applied["first"] == 1 and applied["last"] == SCHEMA_VERSION
    assert applied["missing"] == 0 and applied["checksum_mismatches"] == 0
    assert "redacted" not in str(report["security"]["schema"])
    assert "private detail text" not in str(report)


@pytest.mark.parametrize("command", ["inspect-package", "verify-package"])
def test_a_package_command_recognises_a_diagnostics_bundle(
    tmp_path, monkeypatch, capsys, command
):
    """CLI-33. `inspect-package openai4s-diagnostics.zip` said "has no
    manifest.json; it is not an OpenAI4S package" -- two zips, two commands,
    and nothing said which goes with which."""
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "openai4s.doctor.report", lambda _cfg, **_k: {"status": "ok", "checks": []}
    )
    bundle = tmp_path / "openai4s-diagnostics.zip"
    assert _cli().main(["diagnostics", "-o", str(bundle)]) == 0
    capsys.readouterr()

    assert _cli().main([command, str(bundle)]) == 2

    out = capsys.readouterr().out
    assert "is a diagnostics bundle from `openai4s diagnostics`" in out
    assert "read its report.json" in out
