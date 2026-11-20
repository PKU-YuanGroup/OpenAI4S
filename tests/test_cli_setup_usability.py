"""The setup-side CLI says what it did, and refuses what it cannot do, in words.

`init`, `serve`, `status`, `url`, `stop`, `env`, `setup`, `diagnostics` and the
two launch scripts are the first things a new user runs, usually before
anything works. Each case here is a place where one of them accepted bad input
silently, printed something untrue, or answered with a Python traceback.
"""

from __future__ import annotations

import importlib

import pytest

from openai4s.config import Config, LLMConfig


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

    def run(self, answers, *, secrets=(), argv=(), terminal=True):
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
        if terminal:
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
