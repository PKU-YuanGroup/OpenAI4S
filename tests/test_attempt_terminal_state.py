"""Durable Cell attempt terminal states are classified from structure, never text.

Both durable attempt writers — the CLI ``LocalActionExecutor`` and the Web
``CellExecutionService`` — used to label an attempt ``timed_out`` whenever the
cell's error text contained ``timeout`` anywhere. A kernel result carries the
whole traceback, so a denied ``host.bash`` (whose frame reads
``self._bash.run(command, timeout=timeout, ...)``), a user ``NameError`` on a
variable called ``timeout``, or any urllib connection error was recorded as a
timeout, and the Action Timeline then showed a failed cell as interrupted.

A result the worker returned is a finished cell: interrupted, failed or
completed. Only the Host's own structured proof — a ``TimeoutError`` raised by
the watchdog instead of a result — makes an attempt ``timed_out``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from openai4s.agent.actions import CodeCell
from openai4s.agent.models import ModelReply, RunState
from openai4s.agent.runtime import LocalActionExecutor
from openai4s.execution import CellRequest
from openai4s.execution.watchdog import (
    KernelCancellation,
    KernelNotResetTimeout,
    KernelResetUnavailableTimeout,
)
from openai4s.server.cell_run import CellExecutionService
from tests.test_cell_execution_service import Harness, _session

DENIED_BASH_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "<kernel:3>", line 1, in <module>\n'
    '    print(host.bash("echo hi"))\n'
    '  File "/openai4s/sdk/host.py", line 612, in bash\n'
    "    return self._bash.run(command, timeout=timeout, workdir=workdir)\n"
    "RuntimeError: host.authorize_bash error: Permission denied: approval "
    "required but no interactive channel is attached"
)
NAME_ERROR_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "<kernel:1>", line 1, in <module>\n'
    "    print(timeout)\n"
    "NameError: name 'timeout' is not defined"
)
URLLIB_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "<kernel:2>", line 2, in <module>\n'
    '    urllib.request.urlopen("http://127.0.0.1:9/")\n'
    '  File "/lib/python3.12/urllib/request.py", line 215, in urlopen\n'
    "    return opener.open(url, data, timeout)\n"
    "urllib.error.URLError: <urlopen error [Errno 61] Connection refused>"
)
TIMED_OUT_MESSAGE = "ValueError: the upstream request timed out after 3 retries"

TEXT_ONLY_FAILURES = [
    pytest.param(DENIED_BASH_TRACEBACK, id="denied-host-bash"),
    pytest.param(NAME_ERROR_TRACEBACK, id="nameerror-timeout"),
    pytest.param(URLLIB_TRACEBACK, id="urllib-connection-refused"),
    pytest.param(TIMED_OUT_MESSAGE, id="user-error-says-timed-out"),
    pytest.param("ValueError: plain", id="plain-control"),
]


class _AttemptStore:
    def __init__(self) -> None:
        self.finished: list[str] = []

    def allocate_execution_attempt(self, *, group_id, producing_cell_id):
        return {"attempt_id": f"attempt-{producing_cell_id}"}

    def mark_execution_attempt_started(self, attempt_id):
        pass

    def mark_execution_attempt_response(self, attempt_id):
        pass

    def mark_execution_attempt_capture(self, attempt_id):
        pass

    def finish_execution_attempt(self, attempt_id, *, terminal_state):
        self.finished.append(terminal_state)


class _Ledger:
    current_group_id = "group-1"

    def __init__(self) -> None:
        self.store = _AttemptStore()


class _Kernel:
    generation = None

    def __init__(self, outcome) -> None:
        self.outcome = outcome

    def execute(self, code, **kwargs):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return dict(self.outcome)


class _Dispatcher:
    last_output = None


def _run_cli_cell(outcome) -> list[str]:
    ledger = _Ledger()
    executor = LocalActionExecutor(
        _Kernel(outcome),
        _Dispatcher(),
        lambda code, messages: None,
        lambda code, **kwargs: {"error": "R must not start"},
        action_ledger=ledger,
    )
    cell = CodeCell("python", "print(1)\n")
    try:
        executor.execute(
            cell, ModelReply(content="```python\nprint(1)\n```"), RunState([])
        )
    except BaseException:  # noqa: BLE001 - the raised path must still finish
        pass
    return ledger.store.finished


def _run_web_cell(tmp_path, *, result=None, raised=None) -> list[str]:
    harness = Harness()
    if result is not None:
        harness.run_result = result
    harness.fail_run = raised
    finished: list[str] = []
    ports = replace(
        harness.ports(),
        allocate_attempt=lambda *args: "attempt-1",
        mark_attempt_started=lambda attempt_id: None,
        mark_attempt_response=lambda attempt_id: None,
        mark_attempt_capture=lambda attempt_id: None,
        finish_attempt=lambda attempt_id, state, error: finished.append(state),
    )
    service = CellExecutionService(ports, id_factory=lambda: "cell-1")
    try:
        service.execute(
            _session(tmp_path),
            CellRequest("print(1)", "agent", stream=False),
            lambda event: None,
            action_group_id="group-1",
        )
    except BaseException:  # noqa: BLE001 - the raised path must still finish
        pass
    return finished


@pytest.mark.parametrize("error", TEXT_ONLY_FAILURES)
def test_cli_error_text_mentioning_timeout_is_a_failed_attempt(error):
    finished = _run_cli_cell(
        {"stdout": "", "stderr": "", "error": error, "interrupted": False}
    )
    assert finished == ["failed"]


@pytest.mark.parametrize("error", TEXT_ONLY_FAILURES)
def test_web_error_text_mentioning_timeout_is_a_failed_attempt(error, tmp_path):
    finished = _run_web_cell(
        tmp_path,
        result={"stdout": "", "stderr": "", "error": error, "interrupted": False},
    )
    assert finished == ["failed"]


@pytest.mark.parametrize("runner", ["cli", "web"])
def test_interrupted_and_clean_results_keep_their_states(runner, tmp_path):
    def run(result):
        if runner == "cli":
            return _run_cli_cell(result)
        return _run_web_cell(tmp_path, result=result)

    assert run(
        {"stdout": "", "error": "KeyboardInterrupt: timeout", "interrupted": True}
    ) == ["interrupted"]
    assert run({"stdout": "ok\n", "stderr": "", "error": None}) == ["completed"]


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(TimeoutError("cell exceeded 5s with no result"), id="reset"),
        pytest.param(KernelNotResetTimeout("cell exceeded 5s"), id="not-reset"),
        pytest.param(
            KernelResetUnavailableTimeout("cell exceeded 5s"),
            id="reset-unavailable",
        ),
    ],
)
def test_web_watchdog_timeout_is_a_timed_out_attempt(exc, tmp_path):
    assert _run_web_cell(tmp_path, raised=exc) == ["timed_out"]


def test_web_worker_death_and_cancellation_keep_their_states(tmp_path):
    assert _run_web_cell(tmp_path, raised=EOFError("worker exited")) == ["worker_died"]
    assert _run_web_cell(tmp_path, raised=KernelCancellation("stopped")) == [
        "cancelled"
    ]


def test_cli_host_side_timeout_is_a_timed_out_attempt():
    assert _run_cli_cell(TimeoutError("cell exceeded 5s with no result")) == [
        "timed_out"
    ]
    assert _run_cli_cell(RuntimeError("timeout while talking to the worker")) == [
        "failed"
    ]


def test_both_writers_share_one_classifier():
    """One vocabulary, one implementation: the two paths cannot drift again."""
    from openai4s.agent import runtime
    from openai4s.execution import attempts
    from openai4s.server import cell_run

    assert runtime.attempt_state_for_result is attempts.attempt_state_for_result
    assert cell_run.attempt_state_for_result is attempts.attempt_state_for_result
    assert runtime.attempt_state_for_exception is attempts.attempt_state_for_exception
    assert cell_run.attempt_state_for_exception is attempts.attempt_state_for_exception
    for error in (DENIED_BASH_TRACEBACK, NAME_ERROR_TRACEBACK, URLLIB_TRACEBACK):
        assert attempts.attempt_state_for_result({"error": error}) == "failed"
    assert attempts.attempt_state_for_result(None) == "failed"
    assert attempts.attempt_state_for_result({"error": ""}) == "completed"
    assert (
        attempts.attempt_state_for_exception(
            RuntimeError("timeout"), otherwise="worker_died"
        )
        == "worker_died"
    )
