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
    # The progress line says where the run was; the error is one line after it.
    assert len(lines) == 2, lines
    assert lines[0] == "turn 1 · waiting for the model"
    assert lines[1].startswith("error: the model provider rejected the API key")
    assert "HTTP 401" in lines[1]
    assert "OPENAI4S_DEEPSEEK_API_KEY" in lines[1]
    assert "OPENAI4S_LLM_API_KEY" in lines[1]
    assert "The key is wrong." in lines[1]
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
    # stderr carries progress only; the error itself is the JSON on stdout.
    assert "error" not in captured.err
    assert "Traceback" not in captured.err
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


# --------------------------------------------------------------------------- #
#  CLI-25: `-v` is a readable trace on stderr; the result alone is on stdout
# --------------------------------------------------------------------------- #

_SUBMIT_REPLY = (
    "```python\nhost.submit_output({'summary': 'two'}, ['Computed 1+1'])\n```"
)


def _scripted_chat(replies):
    pending = list(replies)

    def chat(messages, cfg, **kwargs):
        del messages, cfg, kwargs
        return {
            "content": pending.pop(0) if pending else "Still thinking.",
            "reasoning": None,
            "usage": {},
            "finish_reason": "stop",
            "raw": {},
        }

    return chat


def test_the_verbose_trace_goes_to_stderr_and_json_stays_parseable(
    tmp_path, monkeypatch, capsys
):
    """`-v` printed the trace on stdout, so `run -v --json` was a trace with a
    JSON document glued to its end."""

    module, _cfg = _real_run(
        tmp_path,
        monkeypatch,
        _scripted_chat(["```python\nprint('cell ran')\n```", _SUBMIT_REPLY]),
    )

    status = module.main(["run", "-v", "--json", "Compute 1+1 and submit it."])

    captured = capsys.readouterr()
    assert status == 0
    assert json.loads(captured.out)["stop_reason"] == "submitted"
    assert "--- turn 1 (assistant) ---" in captured.err
    assert "--- turn 1 (observation) ---" in captured.err
    assert "cell ran" in captured.err
    assert "--- turn" not in captured.out


def test_the_final_answer_is_printed_once(tmp_path, monkeypatch, capsys):
    """A structured completion's summary was printed inside
    `submitted_output` and again as `final:`."""

    module, _cfg = _real_run(tmp_path, monkeypatch, _scripted_chat([_SUBMIT_REPLY]))

    status = module.main(["run", "Compute 1+1 and submit it."])

    out = capsys.readouterr().out
    assert status == 0
    assert out.count("two") == 1
    assert "final:" not in out


def _trace_of(events):
    from openai4s.agent.runtime import TranscriptEventSink

    lines: list[str] = []
    transcript: list = []
    sink = TranscriptEventSink(transcript, log=lines.append)
    for event in events:
        sink.emit(event)
    return "\n".join(lines), transcript


def test_the_trace_names_each_tool_call_and_its_arguments():
    """A tool-only reply has no text, so the trace showed an empty assistant
    turn followed by a result and never what was asked."""
    from openai4s.agent.actions import NativeToolBatch, NativeToolCall
    from openai4s.agent.events import ActionRouted, ReplyReceived
    from openai4s.agent.models import ModelReply

    call = NativeToolCall(
        id="c1",
        wire_id="c1",
        name="search_skills",
        ordinal=0,
        raw_arguments='{"query": "linear fit"}',
        arguments={"query": "linear fit", "limit": 3},
    )
    trace, _ = _trace_of(
        [
            ReplyReceived(ModelReply(content="", tool_calls=(call,)), 0),
            ActionRouted(NativeToolBatch((call,)), 0),
        ]
    )

    assert "--- turn 1 (assistant) ---" in trace
    assert '→ search_skills(query="linear fit", limit=3)' in trace


def test_the_trace_previews_a_long_result_and_the_transcript_keeps_all_of_it():
    """One `search_skills` result was three lines of 11-24 KB each."""
    from openai4s.agent.events import OutcomeProduced
    from openai4s.agent.models import ExecutionOutcome

    long_line = "x" * 24_000
    observation = f"[Tool Results]\n[Tool: search_skills] {long_line}\n" + "\n".join(
        f"line {n}" for n in range(200)
    )
    trace, transcript = _trace_of(
        [OutcomeProduced(ExecutionOutcome(observation=observation), 0)]
    )

    assert max(len(line) for line in trace.splitlines()) <= 2_000
    assert f"of {len(observation):,} characters" in trace
    assert "line 199" not in trace
    assert transcript[0].content == observation


def test_the_trace_shows_a_short_result_whole():
    from openai4s.agent.events import OutcomeProduced
    from openai4s.agent.models import ExecutionOutcome

    trace, _ = _trace_of(
        [OutcomeProduced(ExecutionOutcome(observation="[Observation]\nstdout:\n2"), 0)]
    )

    assert trace.endswith("[Observation]\nstdout:\n2")
    assert "trace shows" not in trace


# --------------------------------------------------------------------------- #
#  CLI-24: a run without `-v` says what it is doing
# --------------------------------------------------------------------------- #


def test_a_plain_run_reports_each_phase_on_stderr(tmp_path, monkeypatch, capsys):
    """It printed nothing on either stream until it ended, minutes later."""

    module, _cfg = _real_run(
        tmp_path,
        monkeypatch,
        _scripted_chat(["```python\nprint('cell ran')\n```", _SUBMIT_REPLY]),
    )

    status = module.main(["run", "Compute 1+1 and submit it."])

    captured = capsys.readouterr()
    assert status == 0
    assert captured.err.splitlines() == [
        "turn 1 · waiting for the model",
        "turn 1 · running python cell",
        "turn 2 · waiting for the model",
        "turn 2 · running python cell",
    ]
    assert "·" not in captured.out


def test_quiet_silences_the_progress_lines(tmp_path, monkeypatch, capsys):
    module, _cfg = _real_run(tmp_path, monkeypatch, _scripted_chat([_SUBMIT_REPLY]))

    status = module.main(["run", "--quiet", "Compute 1+1 and submit it."])

    captured = capsys.readouterr()
    assert status == 0
    assert captured.err == ""


def test_the_verbose_trace_does_not_repeat_each_phase(tmp_path, monkeypatch, capsys):
    module, _cfg = _real_run(tmp_path, monkeypatch, _scripted_chat([_SUBMIT_REPLY]))

    status = module.main(["run", "-v", "Compute 1+1 and submit it."])

    err = capsys.readouterr().err
    assert status == 0
    assert "--- turn 1 (assistant) ---" in err
    assert "waiting for the model" not in err


def test_a_phase_that_lasts_is_repeated_with_its_elapsed_time():
    """The line a person watching a long cell needs: still this, for this long."""
    import re
    import time

    from openai4s.agent.actions import CodeCell
    from openai4s.agent.events import ActionRouted, OutcomeProduced, TurnStarted
    from openai4s.agent.models import ExecutionOutcome
    from openai4s.agent.runtime import RunProgress

    lines: list[str] = []
    progress = RunProgress(lines.append, interval=0.05)
    try:
        progress.emit(TurnStarted(2))
        progress.emit(ActionRouted(CodeCell("r", "t.test(x)"), 2))
        deadline = time.monotonic() + 5
        while len(lines) < 4 and time.monotonic() < deadline:
            time.sleep(0.01)
        progress.emit(OutcomeProduced(ExecutionOutcome(observation="done"), 2))
        settled = len(lines)
        time.sleep(0.2)
    finally:
        progress.close()

    assert lines[:2] == ["turn 3 · waiting for the model", "turn 3 · running R cell"]
    assert len(lines) >= 4
    assert all(
        re.fullmatch(r"turn 3 · running R cell · \d+s", line) for line in lines[2:]
    )
    # A finished phase is not repeated.
    assert len(lines) == settled


def test_a_progress_line_that_cannot_be_written_does_not_end_the_run():
    from openai4s.agent.events import TurnStarted
    from openai4s.agent.runtime import TranscriptEventSink

    class Broken:
        def emit(self, event):
            raise OSError("stderr is closed")

    TranscriptEventSink([], progress=Broken()).emit(TurnStarted(0))


# --------------------------------------------------------------------------- #
#  CLI-29: Ctrl-C is one line and the documented exit 3, not a traceback
# --------------------------------------------------------------------------- #


def _interrupted_chat(replies):
    """Answer ``replies`` in turn, then behave as if Ctrl-C arrived mid-call."""
    scripted = _scripted_chat(replies)
    calls = {"n": 0}

    def chat(messages, cfg, **kwargs):
        calls["n"] += 1
        if calls["n"] > len(replies):
            raise KeyboardInterrupt
        return scripted(messages, cfg, **kwargs)

    return chat


def _main_survives_ctrl_c(module, argv):
    # An escaped KeyboardInterrupt would end the whole pytest session.
    try:
        return module.main(argv)
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C escaped `openai4s run` as a KeyboardInterrupt")


def test_ctrl_c_is_one_line_and_exit_3(tmp_path, monkeypatch, capsys):
    """It was a 39-line KeyboardInterrupt traceback and exit 130, while
    `run --help` promised 3 for a cancelled run."""

    module, cfg = _real_run(
        tmp_path,
        monkeypatch,
        _interrupted_chat(["```python\nprint('cell ran')\n```"]),
    )

    status = _main_survives_ctrl_c(
        module, ["run", "--quiet", "Compute 1+1 and submit it."]
    )

    captured = capsys.readouterr()
    assert status == 3 == module.RUN_NOT_COMPLETED_EXIT
    assert captured.out == ""
    assert "Traceback" not in captured.err
    frame_id, frame_status = _frame_rows(cfg)[0]
    assert frame_status == "cancelled"
    assert captured.err.strip().splitlines() == [
        f"interrupted: the run was cancelled after 1 turn; session {frame_id} "
        "is recorded as cancelled."
    ]


def test_ctrl_c_under_json_prints_the_cancelled_result(tmp_path, monkeypatch, capsys):
    """`--json` promised the full result for exit 3 and printed nothing."""

    module, cfg = _real_run(
        tmp_path,
        monkeypatch,
        _interrupted_chat(["```python\nprint('cell ran')\n```"]),
    )

    status = _main_survives_ctrl_c(
        module, ["run", "--json", "Compute 1+1 and submit it."]
    )

    captured = capsys.readouterr()
    assert status == 3
    assert "Traceback" not in captured.err
    result = json.loads(captured.out)
    assert result["stop_reason"] == "cancelled"
    assert result["turns"] == 1
    assert result["frame_id"] == _frame_rows(cfg)[0][0]
    # What the run did before it was stopped is in the result.
    assert any("cell ran" in turn["content"] for turn in result["transcript"])


def test_run_help_says_ctrl_c_exits_3(capsys):
    module = _cli_module()
    with pytest.raises(SystemExit):
        module.main(["run", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    row = text[text.index("3 the run ended without completing") :]
    assert "Ctrl-C" in row[: row.index(" 4 ")]


# --------------------------------------------------------------------------- #
#  CLI-27: a CLI run is stored as a session with its question and answer
# --------------------------------------------------------------------------- #


def _session(cfg, frame_id):
    with sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True) as db:
        frame = db.execute(
            "SELECT name, task_summary FROM frames WHERE frame_id=?", (frame_id,)
        ).fetchone()
        messages = db.execute(
            "SELECT role, content, metadata FROM messages WHERE root_frame_id=? "
            "ORDER BY seq",
            (frame_id,),
        ).fetchall()
        cells = db.execute(
            "SELECT code FROM execution_log WHERE frame_id=?", (frame_id,)
        ).fetchall()
    return frame, messages, [row[0] for row in cells]


def test_a_run_is_stored_with_its_task_answer_and_cells(tmp_path, monkeypatch, capsys):
    """The Web UI showed a CLI run as a blank "Untitled session": no question,
    no answer, an empty Notebook -- and a run with no file was not listed."""

    module, cfg = _real_run(
        tmp_path,
        monkeypatch,
        _scripted_chat(["```python\nprint('cell ran')\n```", _SUBMIT_REPLY]),
    )
    task = "Compute 1+1\n  and submit it."

    status = module.main(["run", "--json", "--quiet", task])

    assert status == 0
    frame_id = json.loads(capsys.readouterr().out)["frame_id"]
    assert frame_id == _frame_rows(cfg)[0][0]
    frame, messages, cells = _session(cfg, frame_id)
    assert frame == (None, "Compute 1+1 and submit it.")
    assert [(role, content) for role, content, _ in messages][0] == ("user", task)
    assert messages[1][0] == "assistant"
    assert "two" in messages[1][1] and "Computed 1+1" in messages[1][1]
    assert len(messages) == 2
    assert any("print('cell ran')" in code for code in cells)

    from openai4s.server.gateway import _frame_json, _session_is_unused
    from openai4s.store import get_store

    store = get_store(cfg.db_path)
    assert not _session_is_unused(store, _frame_json(store.get_frame(frame_id), store))


def test_a_failed_run_stores_the_error_it_printed(tmp_path, monkeypatch, capsys):
    module, cfg = _real_run(tmp_path, monkeypatch, _failing_chat(_transport_error(401)))

    status = module.main(["run", "--quiet", "Compute 1+1 and submit it."])

    assert status == 4
    error_line = capsys.readouterr().err.strip()
    _, messages, _ = _session(cfg, _frame_rows(cfg)[0][0])
    assert [role for role, _, _ in messages] == ["user", "assistant"]
    assert error_line.startswith(messages[1][1])
    assert json.loads(messages[1][2]) == {"failure": {"code": "llm_auth_failed"}}


def test_a_cancelled_run_stores_the_stop_marker(tmp_path, monkeypatch, capsys):
    module, cfg = _real_run(tmp_path, monkeypatch, _interrupted_chat([]))

    status = _main_survives_ctrl_c(module, ["run", "Compute 1+1 and submit it."])

    assert status == 3
    _, messages, _ = _session(cfg, _frame_rows(cfg)[0][0])
    assert [(role, content) for role, content, _ in messages] == [
        ("user", "Compute 1+1 and submit it."),
        ("assistant", "_Stopped by user._"),
    ]


# --------------------------------------------------------------------------- #
#  CLI-26: the run says where its files are and leaves no hidden cache behind
# --------------------------------------------------------------------------- #

_WRITE_FILE_CELL = (
    "```python\nopen('result.txt', 'w').write('2')\nprint('wrote it')\n```"
)


def test_the_run_lists_the_files_it_wrote_as_absolute_paths(
    tmp_path, monkeypatch, capsys
):
    """The files were in the working directory and the output named only
    internal artifact ids that no CLI command resolves."""

    (tmp_path / "input.csv").write_text("a,b\n")
    module, _cfg = _real_run(
        tmp_path, monkeypatch, _scripted_chat([_WRITE_FILE_CELL, _SUBMIT_REPLY])
    )

    status = module.main(["run", "--quiet", "Write the result and submit it."])

    out = capsys.readouterr().out
    assert status == 0
    written = str(tmp_path / "result.txt")
    # An untouched input and the data directory's own files are not listed.
    assert f"files written:\n  {written}\n" in out
    assert "input.csv" not in out and "openai4s.db" not in out


def test_the_json_result_lists_the_files_it_wrote(tmp_path, monkeypatch, capsys):
    module, _cfg = _real_run(
        tmp_path, monkeypatch, _scripted_chat([_WRITE_FILE_CELL, _SUBMIT_REPLY])
    )

    status = module.main(["run", "--json", "Write the result and submit it."])

    assert status == 0
    result = json.loads(capsys.readouterr().out)
    assert result["files_written"] == [str(tmp_path / "result.txt")]


_LARGE_OUTPUT_CELL = "```python\nprint('x' * 40000)\n```"


def _tool_reply(name, arguments):
    call = {
        "id": f"call-{name}",
        "wire_id": f"wire-{name}",
        "name": name,
        "ordinal": 0,
        "raw_arguments": json.dumps(arguments),
        "arguments": arguments,
        "parse_error": None,
        "provider_meta": {"provider": "test"},
    }
    return {
        "content": "",
        "tool_calls": [call],
        "assistant_message": {"role": "assistant", "content": "", "tool_calls": [call]},
        "usage": {},
    }


def _replies_chat(replies):
    """Like `_scripted_chat`, but a dict reply (a tool call) is sent as is."""
    pending = list(replies)
    text = _scripted_chat([])

    def chat(messages, cfg, **kwargs):
        reply = pending.pop(0) if pending else "Still thinking."
        if isinstance(reply, dict):
            return reply
        return {**text(messages, cfg, **kwargs), "content": reply}

    return chat


def test_a_spilled_tool_result_is_not_left_in_the_working_directory(
    tmp_path, monkeypatch, capsys
):
    """`.openai4s/context/<sha256>.{json,txt}` (100 KB) stayed in the
    directory the run was started from -- an untracked hidden directory in
    any git checkout."""

    (tmp_path / "big.txt").write_text(
        "\n".join(f"line {n:05d} " + "y" * 60 for n in range(800))
    )
    module, cfg = _real_run(
        tmp_path,
        monkeypatch,
        _replies_chat(
            [_tool_reply("read_text_file", {"path": "big.txt"}), _SUBMIT_REPLY]
        ),
    )

    status = module.main(["run", "--json", "--quiet", "Read big.txt and submit."])

    result = json.loads(capsys.readouterr().out)
    assert status == 0, result
    # The tool result was spilled (the data directory's archive keeps it) ...
    assert list(cfg.compaction_dir.rglob("*.json"))
    # ... and the working-directory copy went with the run.
    assert not (tmp_path / ".openai4s").exists()


def test_a_spilled_cell_output_is_not_left_in_the_working_directory(
    tmp_path, monkeypatch, capsys
):
    module, _cfg = _real_run(
        tmp_path, monkeypatch, _scripted_chat([_LARGE_OUTPUT_CELL, _SUBMIT_REPLY])
    )

    status = module.main(["run", "--json", "--quiet", "Print a lot and submit it."])

    result = json.loads(capsys.readouterr().out)
    assert status == 0
    # The observation pointed the model at a copy in the working directory ...
    assert "content ref=.openai4s/observations/obs-stdout-" in json.dumps(result)
    # ... which is gone once the run is over.
    assert not (tmp_path / ".openai4s").exists()


def test_the_spill_cleanup_keeps_what_was_there_before(tmp_path, monkeypatch, capsys):
    """A project folder has its own `.openai4s/`; only this run's copies go."""

    observations = tmp_path / ".openai4s" / "observations"
    observations.mkdir(parents=True)
    older = observations / ("obs-stdout-" + "a" * 16 + ".txt")
    older.write_text("from an earlier run")
    (tmp_path / ".openai4s" / "settings.json").write_text("{}")
    module, _cfg = _real_run(
        tmp_path, monkeypatch, _scripted_chat([_LARGE_OUTPUT_CELL, _SUBMIT_REPLY])
    )

    status = module.main(["run", "--quiet", "Print a lot and submit it."])

    assert status == 0
    assert sorted(path.name for path in observations.iterdir()) == [older.name]
    assert (tmp_path / ".openai4s" / "settings.json").exists()
