"""Behaviour of the loopback TypeSafe System One fake endpoint."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from harness.providers.typesafe_fake import default_answer, parse_fail_spec, start_fake

SECRET = "ts-w1a-fake-secret-NEVER-LEAK-c4e1"


def _post(url: str, payload: dict[str, Any], *, token: str | None = SECRET) -> Any:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        error.read()
        raise


QUESTIONS = {
    "model": "jev-1.13.0",
    "state": "Help! My payouts have been failing for 3 days.",
    "questions": {
        "is_urgent": {"type": "noul", "instructions": "Does this convey urgency?"},
        "department": {
            "type": "choice",
            "instructions": "Which team should handle this?",
            "criteria": {
                "billing": "Payments",
                "technical": "Bugs",
                "sales": "Pricing",
            },
        },
        "frustration": {
            "type": "score",
            "instructions": "How frustrated is the customer?",
            "criteria": ["Calm", "Frustrated", "Very angry"],
        },
    },
}


def test_missing_bearer_is_401() -> None:
    url, stop = start_fake(port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(url, QUESTIONS, token=None)
        assert caught.value.code == 401
    finally:
        stop()


def test_empty_bearer_is_401() -> None:
    url, stop = start_fake(port=0)
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post(url, QUESTIONS, token="   ")
        assert caught.value.code == 401
    finally:
        stop()


def test_default_answers_are_deterministic() -> None:
    url, stop = start_fake(port=0)
    try:
        first = _post(url, QUESTIONS)
        second = _post(url, QUESTIONS)
    finally:
        stop()
    assert first == second
    assert first["answers"]["is_urgent"] == {"type": "noul", "noul": 0.5}
    department = first["answers"]["department"]
    assert department["choice"] == "billing"
    assert department["probabilities"]["billing"] == pytest.approx(0.6)
    rest = [
        department["probabilities"]["technical"],
        department["probabilities"]["sales"],
    ]
    assert rest[0] == pytest.approx(0.2)
    assert rest[1] == pytest.approx(0.2)
    assert sum(department["probabilities"].values()) == pytest.approx(1.0)
    frustration = first["answers"]["frustration"]
    assert frustration["score"] == 1.0
    assert frustration["probabilities"]["1"] == 1.0
    assert frustration["legend"] == {
        "0": "Calm",
        "1": "Frustrated",
        "2": "Very angry",
    }


def test_script_by_question_id() -> None:
    script = {"is_urgent": {"type": "noul", "noul": 0.91}}
    url, stop = start_fake(port=0, script=script)
    try:
        payload = _post(url, QUESTIONS)
    finally:
        stop()
    assert payload["answers"]["is_urgent"]["noul"] == 0.91
    assert payload["answers"]["department"]["choice"] == "billing"


def test_script_by_instructions_substring() -> None:
    script = {
        "instructions": {
            "convey urgency": {"type": "noul", "noul": 0.11},
        }
    }
    url, stop = start_fake(port=0, script=script)
    try:
        payload = _post(url, QUESTIONS)
    finally:
        stop()
    assert payload["answers"]["is_urgent"]["noul"] == 0.11


def test_fail_429_twice_then_succeeds() -> None:
    url, stop = start_fake(port=0, fail="429:2", retry_after="0")
    try:
        for _ in range(2):
            with pytest.raises(urllib.error.HTTPError) as caught:
                _post(url, QUESTIONS)
            assert caught.value.code == 429
            assert caught.value.headers.get("Retry-After") == "0"
        payload = _post(url, QUESTIONS)
    finally:
        stop()
    assert payload["answers"]["is_urgent"]["noul"] == 0.5


def test_latency_ms_delays_response() -> None:
    import time

    url, stop = start_fake(port=0, latency_ms=120)
    try:
        started = time.monotonic()
        _post(url, QUESTIONS)
        elapsed = time.monotonic() - started
    finally:
        stop()
    assert elapsed >= 0.1


def test_log_records_body_not_authorization(tmp_path: Path) -> None:
    log_path = tmp_path / "fake.jsonl"
    url, stop = start_fake(port=0, log_path=log_path)
    try:
        _post(url, QUESTIONS, token=SECRET)
    finally:
        stop()
    logged = log_path.read_text(encoding="utf-8")
    record = json.loads(logged.splitlines()[0])
    assert record["body"]["questions"]["is_urgent"]["type"] == "noul"
    assert SECRET not in logged
    assert "Authorization" not in logged
    assert "authorization" not in logged
    assert "Bearer" not in logged


def test_refuses_non_loopback_bind() -> None:
    with pytest.raises(ValueError, match="loopback"):
        start_fake(host="0.0.0.0")
    with pytest.raises(ValueError, match="loopback"):
        start_fake(host="10.0.0.1")
    with pytest.raises(ValueError, match="loopback"):
        start_fake(host="localhost")


def test_other_path_is_404() -> None:
    url, stop = start_fake(port=0)
    try:
        parsed = urllib.request.Request(
            url.replace("/v1/systemone", "/v1/other"),
            data=b"{}",
            method="POST",
            headers={"Authorization": f"Bearer {SECRET}"},
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(parsed, timeout=3)  # noqa: S310
        assert caught.value.code == 404
    finally:
        stop()


def test_parse_fail_spec() -> None:
    assert parse_fail_spec("429:2") == (429, 2)
    assert parse_fail_spec("529:1") == (529, 1)


def test_default_answer_choice_splits_remainder() -> None:
    answer = default_answer(
        {
            "type": "choice",
            "instructions": "pick",
            "criteria": {"a": "A", "b": "B"},
        }
    )
    assert answer["choice"] == "a"
    assert answer["probabilities"]["a"] == pytest.approx(0.6)
    assert answer["probabilities"]["b"] == pytest.approx(0.4)


def test_package_reexports_start_fake() -> None:
    from harness.providers import start_fake as exported

    assert exported is start_fake


def test_module_cli_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "harness.providers.typesafe_fake", "--help"],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0
    assert "--port" in result.stdout
    assert SECRET not in result.stdout
    assert SECRET not in result.stderr
