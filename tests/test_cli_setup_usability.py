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
