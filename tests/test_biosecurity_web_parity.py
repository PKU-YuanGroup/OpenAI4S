"""Whether the biosecurity screener runs on the surface people actually use.

`OPENAI4S_BIOSECURITY` is on by default and `openai4s/config.py` documents it as
doing two things: "splice the calibrated-accountability (oiO) prompt AND run the
diO trajectory screener". The CLI's `_pre_exec_gate` did both. The Web daemon's
`_safety_refusal` called `classify_code` and returned — so the same cell that
`uv run openai4s run` refused was executed by `./start.sh`.

That is the worse half to lose. The prompt asks the model to behave; the
screener checks whether it did. An operator reading the config would believe a
control was in force on the primary product surface when only its advisory half
was.

The screener judges a *trajectory* — what the user asked for across the
conversation against what the agent has been doing — so the gate needs the
session, not just the cell. That is why the port widened.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig, SecurityConfig
from openai4s.server import gateway as gateway_mod


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.fixture
def runner(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    return gateway_mod.SessionRunner(cfg, _Hub())


def _session(messages):
    return SimpleNamespace(messages=messages)


BLOCKED = SimpleNamespace(blocked=True, reason="pretend diO said no")
ALLOWED = SimpleNamespace(blocked=False, reason="")


def test_the_trajectory_screener_actually_runs_here(runner, monkeypatch):
    """The defect. Before this, no call reached the screener from the Web
    path at all — so the assertion is that it is consulted, and that its
    verdict is honoured."""
    seen: list[tuple[str, str]] = []

    def _screen(user_text, actions, _cfg):
        seen.append((user_text, actions))
        return BLOCKED

    monkeypatch.setattr("openai4s.security.screen_trajectory", _screen)
    session = _session(
        [
            {"role": "user", "content": "synthesise the thing"},
            {"role": "assistant", "content": "ordering reagents"},
        ]
    )
    refusal = runner._safety_refusal(session, "print('cell')", "agent")

    assert seen, "the trajectory screener was never called"
    assert refusal is not None
    assert "BLOCKED by the biosecurity trajectory screener" in refusal
    assert "was NOT executed" in refusal


def test_the_screener_sees_the_conversation_not_just_the_cell(runner, monkeypatch):
    """A trajectory screener handed one cell is a code classifier with a
    misleading name. The user's request and the agent's prior turns have to
    reach it, which is the whole reason the port signature changed."""
    captured: dict = {}

    def _screen(user_text, actions, _cfg):
        captured["user"] = user_text
        captured["actions"] = actions
        return ALLOWED

    monkeypatch.setattr("openai4s.security.screen_trajectory", _screen)
    session = _session(
        [
            {"role": "user", "content": "USER-ASKED-THIS"},
            {"role": "assistant", "content": "AGENT-DID-THAT"},
        ]
    )
    runner._safety_refusal(session, "THIS-CELL", "agent")

    assert "USER-ASKED-THIS" in captured["user"]
    assert "AGENT-DID-THAT" in captured["actions"]
    assert "THIS-CELL" in captured["actions"]


def test_an_allowed_trajectory_does_not_refuse(runner, monkeypatch):
    """The gate must not become "refuse everything", which would also satisfy
    a test that only checked the block path."""
    monkeypatch.setattr(
        "openai4s.security.screen_trajectory", lambda *_a, **_k: ALLOWED
    )
    assert runner._safety_refusal(_session([]), "print(1)", "agent") is None


def test_escalate_stays_advisory(runner, monkeypatch):
    """Same choice the CLI loop makes, and for the same reason: there is no
    human in the execution path to escalate to, so turning it into a refusal
    would deadlock the turn rather than get anyone consulted."""
    escalate = SimpleNamespace(blocked=False, reason="needs context")
    monkeypatch.setattr(
        "openai4s.security.screen_trajectory", lambda *_a, **_k: escalate
    )
    assert runner._safety_refusal(_session([]), "print(1)", "agent") is None


def test_the_switch_still_switches(runner, monkeypatch, tmp_path):
    """`OPENAI4S_BIOSECURITY` off must mean the screener does not run — an
    always-on control is a different product than the one documented."""
    called: list[int] = []
    monkeypatch.setattr(
        "openai4s.security.screen_trajectory",
        lambda *_a, **_k: called.append(1) or BLOCKED,
    )
    runner.cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="k"),
        security=SecurityConfig(biosecurity=False),
    )
    assert runner._safety_refusal(_session([]), "print(1)", "agent") is None
    assert not called


def test_a_user_cell_is_not_screened(runner, monkeypatch):
    """Only agent-origin cells are gated — a person running their own code in
    the notebook is not the threat model, and screening them would make the
    REPL unusable."""
    called: list[int] = []
    monkeypatch.setattr(
        "openai4s.security.screen_trajectory",
        lambda *_a, **_k: called.append(1) or BLOCKED,
    )
    assert runner._safety_refusal(_session([]), "print(1)", "user") is None
    assert not called


def test_a_broken_screener_does_not_break_the_turn(runner, monkeypatch):
    """Fails open, deliberately and consistently with the CLI. A safety check
    that takes the product down when it errors gets turned off."""

    def _boom(*_a, **_k):
        raise RuntimeError("screener exploded")

    monkeypatch.setattr("openai4s.security.screen_trajectory", _boom)
    assert runner._safety_refusal(_session([]), "print(1)", "agent") is None


def test_the_static_classifier_still_runs_first(runner, monkeypatch):
    """Adding the screener must not have displaced the check that was there.
    A cell refused by `classify_code` is refused before the trajectory is even
    gathered."""
    monkeypatch.setattr(
        "openai4s.security.classify_code",
        lambda *_a, **_k: SimpleNamespace(
            safe=False, as_observation=lambda: "[static] nope"
        ),
    )
    called: list[int] = []
    monkeypatch.setattr(
        "openai4s.security.screen_trajectory",
        lambda *_a, **_k: called.append(1) or ALLOWED,
    )
    refusal = runner._safety_refusal(_session([]), "print(1)", "agent")
    assert refusal == "[static] nope"
    assert not called, "the trajectory was gathered for an already-refused cell"


def test_both_surfaces_use_one_definition_of_a_trajectory():
    """Two copies of "what counts as the trajectory" would be two safety
    policies wearing one name, drifting quietly."""
    import inspect

    source = inspect.getsource(gateway_mod.SessionRunner._safety_refusal)
    assert "gather_trajectory" in source
    # From the security facade, not the CLI loop's private helper. Reaching
    # into `openai4s.agent.loop._gather_trajectory` is what
    # `test_backend_import_contract` refuses, and it refused this — correctly:
    # a shared safety definition belongs beside the screener that consumes it.
    assert "from openai4s.security import gather_trajectory" in source
    assert "openai4s.agent.loop" not in source
