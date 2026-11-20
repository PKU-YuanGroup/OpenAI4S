"""What `openai4s run` tells the person (and the script) running it.

The run itself is covered elsewhere; these pin what reaches the terminal:
which stream carries what, what a failure looks like, and what a wrapper can
read from the exit status and from ``--json``.
"""

from __future__ import annotations

import importlib
import json
import sqlite3

import pytest


def _cli_module():
    return importlib.import_module("openai4s.cli.main")


def _real_run(tmp_path, monkeypatch, chat, *, max_turns=3):
    """Wire the real ``main(["run", ...])`` and real Agent onto ``chat``."""
    import openai4s.agent.loop as loop_mod
    from openai4s.config import Config, LLMConfig

    module = _cli_module()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key", model="some-model"),
        max_turns=max_turns,
    )
    monkeypatch.setattr(module, "get_config", lambda: cfg)
    monkeypatch.setattr(loop_mod, "chat", chat)
    monkeypatch.delenv("OPENAI4S_DEBUG", raising=False)
    monkeypatch.chdir(tmp_path)
    return module, cfg


def _frame_rows(cfg):
    with sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True) as db:
        return db.execute("SELECT frame_id, status FROM frames").fetchall()


def _failing_chat(error):
    def chat(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        raise error

    return chat


def _transport_error(status, body="", **fields):
    from openai4s.llm.models import TransportError

    return TransportError(
        f"LLM HTTP {status}: {body}", status=status, body=body, **fields
    )


# --------------------------------------------------------------------------- #
#  CLI-23: a failed model call is one paragraph, not a traceback
# --------------------------------------------------------------------------- #


def test_a_rejected_api_key_is_one_error_line_and_exit_4(tmp_path, monkeypatch, capsys):
    """It was 89 lines of urllib/transport/agent traceback, exit 1, with the
    cause on the last line -- the most common first failure a new user has."""

    body = json.dumps(
        {"error": {"code": "AuthenticationError", "message": "The key is wrong."}}
    )
    module, cfg = _real_run(
        tmp_path, monkeypatch, _failing_chat(_transport_error(401, body))
    )

    status = module.main(["run", "Compute 1+1 and submit it."])

    captured = capsys.readouterr()
    assert status == 4 == module.RUN_LLM_FAILED_EXIT
    assert captured.out == ""
    assert "Traceback" not in captured.err
    lines = captured.err.strip().splitlines()
    assert len(lines) == 1, lines
    assert lines[0].startswith("error: the model provider rejected the API key")
    assert "HTTP 401" in lines[0]
    assert "OPENAI4S_DEEPSEEK_API_KEY" in lines[0]
    assert "OPENAI4S_LLM_API_KEY" in lines[0]
    assert "The key is wrong." in lines[0]
    # The run did start, and its frame says how it ended.
    assert [row[1] for row in _frame_rows(cfg)] == ["failed"]


def test_a_failed_model_call_under_json_is_an_error_object_on_stdout(
    tmp_path, monkeypatch, capsys
):
    """`--json` printed nothing at all, so a script got no `code` to branch on."""

    body = json.dumps({"error": {"message": "The key is wrong."}})
    module, cfg = _real_run(
        tmp_path, monkeypatch, _failing_chat(_transport_error(401, body))
    )

    status = module.main(["run", "--json", "Compute 1+1 and submit it."])

    captured = capsys.readouterr()
    assert status == 4
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["code"] == "llm_auth_failed"
    assert payload["status"] == 401
    assert payload["provider"] == "deepseek"
    assert payload["detail"] == "The key is wrong."
    assert payload["error"].startswith("the model provider rejected the API key")
    assert payload["frame_id"] == _frame_rows(cfg)[0][0]


def _missing_credential():
    from openai4s.llm.models import MissingCredentialError

    return MissingCredentialError("no API key configured for provider 'deepseek'")


def _unreachable():
    from openai4s.llm.models import TransportError

    return TransportError("LLM connection error: [Errno 61] Connection refused")


@pytest.mark.parametrize(
    ("make_error", "code", "fragment"),
    [
        (_missing_credential, "llm_not_configured", "no API key is configured"),
        (lambda: _transport_error(403), "llm_auth_failed", "rejected the API key"),
        (lambda: _transport_error(404), "llm_model_not_found", "'some-model'"),
        (lambda: _transport_error(429), "llm_rate_limited", "rate-limiting"),
        (_unreachable, "llm_unreachable", "could not reach the model provider"),
        (
            lambda: _transport_error(500),
            "llm_error",
            "the model call failed (HTTP 500)",
        ),
    ],
)
def test_each_model_failure_gets_its_own_code_and_next_step(
    tmp_path, monkeypatch, capsys, make_error, code, fragment
):
    module, _cfg = _real_run(tmp_path, monkeypatch, _failing_chat(make_error()))

    status = module.main(["run", "--json", "Compute 1+1 and submit it."])

    payload = json.loads(capsys.readouterr().out)
    assert status == 4
    assert payload["code"] == code
    assert fragment in payload["error"]
    # Every one of them names something the reader can check.
    assert "OPENAI4S_" in payload["error"]


def test_the_missing_key_message_says_where_run_reads_its_key(
    tmp_path, monkeypatch, capsys
):
    """`run` reads the environment only, so the next step it names must be
    one that works for `run`."""

    module, _cfg = _real_run(
        tmp_path, monkeypatch, _failing_chat(_missing_credential())
    )

    status = module.main(["run", "Compute 1+1 and submit it."])

    err = capsys.readouterr().err
    assert status == 4
    assert "Traceback" not in err
    assert "OPENAI4S_DEEPSEEK_API_KEY (or OPENAI4S_LLM_API_KEY)" in err
    assert ".env" in err


@pytest.mark.parametrize("how", ["flag", "environment"])
def test_debug_keeps_the_traceback_and_the_same_status(
    tmp_path, monkeypatch, capsys, how
):
    module, _cfg = _real_run(
        tmp_path, monkeypatch, _failing_chat(_transport_error(404))
    )
    argv = ["run", "Compute 1+1 and submit it."]
    if how == "flag":
        argv.insert(1, "--debug")
    else:
        monkeypatch.setenv("OPENAI4S_DEBUG", "1")

    status = module.main(argv)

    err = capsys.readouterr().err
    assert status == 4
    assert "Traceback (most recent call last)" in err
    assert "TransportError" in err
    assert err.strip().splitlines()[-1].startswith("error: the model provider has no")


def test_a_credential_echoed_by_the_provider_is_not_printed(
    tmp_path, monkeypatch, capsys
):
    secret = "sk-9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c"
    body = json.dumps({"error": {"message": f"Incorrect API key provided: {secret}"}})
    module, _cfg = _real_run(
        tmp_path, monkeypatch, _failing_chat(_transport_error(401, body))
    )

    status = module.main(["run", "Compute 1+1 and submit it."])

    err = capsys.readouterr().err
    assert status == 4
    assert secret not in err
    assert "Incorrect API key provided:" in err


def test_run_help_documents_exit_4_and_the_codes_it_names(capsys):
    module = _cli_module()
    with pytest.raises(SystemExit):
        module.main(["run", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    row = text[text.index("4 the model call failed") :]
    for code in (
        "llm_not_configured",
        "llm_auth_failed",
        "llm_model_not_found",
        "llm_rate_limited",
        "llm_unreachable",
    ):
        assert code in row, code
    assert "--debug" in row and "OPENAI4S_DEBUG" in row
