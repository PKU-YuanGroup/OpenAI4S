"""Live TypeSafe System One checks. Opt in with network+external and a key.

``tests/conftest.py`` strips any ``OPENAI4S_*API_KEY`` at import, including
``OPENAI4S_TYPESAFE_API_KEY``. Maintainers can export
``OPENAI4S_JUDGMENT_LIVE_KEY`` instead; this module copies it into the
client's documented env var for the duration of each test.
"""

from __future__ import annotations

import os

import pytest

from openai4s.judgment.port import BackendError
from openai4s.judgment.types import Answer, Choice, Noul, Score
from openai4s.judgment.typesafe import TypeSafeBackend
from openai4s.judgment.validate import parse_response

pytestmark = [pytest.mark.network, pytest.mark.external]


def _live_key() -> str:
    for name in ("OPENAI4S_TYPESAFE_API_KEY", "OPENAI4S_JUDGMENT_LIVE_KEY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


@pytest.fixture
def live_key() -> str:
    key = _live_key()
    if not key:
        pytest.skip("OPENAI4S_TYPESAFE_API_KEY is not set")
    return key


class _EmptyStore:
    def get_secret_setting(self, key: str, *, scope: str | None = None) -> str:
        del key, scope
        return ""


def test_live_noul_choice_and_score(
    live_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI4S_JUDGMENT_FAKE_ENDPOINT", raising=False)
    monkeypatch.setenv("OPENAI4S_TYPESAFE_API_KEY", live_key)
    backend = TypeSafeBackend(lambda: _EmptyStore())
    questions = {
        "is_urgent": Noul(instructions="Does state.text convey urgency?"),
        "department": Choice(
            instructions="Which team should handle state.text?",
            options={
                "billing": "Payments, invoicing, refunds",
                "technical": "Bugs, outages, integrations",
                "sales": "Pricing, upgrades, new accounts",
            },
        ),
        "frustration": Score(
            instructions="How frustrated is the customer in state.text?",
            levels=("Calm", "Frustrated", "Very angry"),
        ),
    }
    try:
        reply = backend.evaluate(
            state="Help! My payouts have been failing for 3 days.",
            questions=questions,
            model="jev-1.13.0",
            timeout=30.0,
        )
    except BackendError as exc:
        assert live_key not in str(exc)
        raise
    assert live_key not in str(reply)
    assert reply.fake is False
    assert isinstance(reply.answers["is_urgent"], Answer)
    assert reply.answers["is_urgent"].kind == "noul"
    assert 0.0 <= float(reply.answers["is_urgent"].value) <= 1.0
    assert reply.answers["department"].kind == "choice"
    assert reply.answers["department"].value in {
        "billing",
        "technical",
        "sales",
    }
    assert reply.answers["frustration"].kind == "score"
    assert 0.0 <= float(reply.answers["frustration"].value) <= 2.0
    # Re-validate a reconstructed wire payload so validate.py is exercised live.
    reconstructed = {
        "model": reply.model,
        "usage": dict(reply.usage),
        "answers": {
            "is_urgent": {
                "type": "noul",
                "noul": reply.answers["is_urgent"].value,
            },
            "department": {
                "type": "choice",
                "choice": reply.answers["department"].value,
                "probabilities": dict(reply.answers["department"].probabilities or {}),
                "confidence": reply.answers["department"].confidence,
            },
            "frustration": {
                "type": "score",
                "score": reply.answers["frustration"].value,
                "probabilities": dict(reply.answers["frustration"].probabilities or {}),
                "confidence": reply.answers["frustration"].confidence,
                "legend": {
                    "0": "Calm",
                    "1": "Frustrated",
                    "2": "Very angry",
                },
            },
        },
    }
    checked = parse_response(reconstructed, questions)
    assert set(checked.answers) == set(questions)
