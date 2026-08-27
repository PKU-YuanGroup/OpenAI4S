"""Completion evidence for turns whose deliverable is source code.

The existing reconciliation asks whether a claimed file exists — the right
question for a figure, the wrong one for a pipeline. A `.py` path that exists
says nothing about whether the module imports, whether it was ever captured, or
whether any test ran against it, and the model's own "all tests passed" was the
only thing standing in for the last one.

These tests pin the four fields, every way each one can fail, and — the part
that matters most — that verifying an entry point COMPILES it and never runs
it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from openai4s.agent.actions import (
    FINALIZE_RESPONSE_NAME,
    FinalizeAction,
    NativeToolCall,
)
from openai4s.agent.finalize import execute_finalize_action, finalize_response_schema
from openai4s.bash_capability import (
    command_digest,
    command_preserves_failure_status,
)
from openai4s.host.code_evidence import (
    CODE_EVIDENCE_KEYS,
    EVIDENCE_REQUIRED_MODES,
    CodeEvidenceContext,
    gather_code_evidence_context,
    requires_code_evidence,
    stdout_reports_failure,
    verify_code_evidence,
)
from openai4s.host.completion import CompletionService

GOOD_STDOUT = "collected 3 items\n\ntests/test_pipeline.py ...\n\n3 passed in 0.12s\n"


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _Cells:
    """The one execution_log read the verifier makes."""

    def __init__(self, rows):
        self.rows = rows
        self.asked = []

    def __call__(self, cell_id):
        self.asked.append(cell_id)
        return self.rows.get(cell_id)


@pytest.fixture()
def tree(tmp_path):
    """A workspace holding a small structured deliverable."""
    root = tmp_path / "workspace"
    root.mkdir()
    _write(root, "seqpipe/__init__.py", "from .run import main\n")
    _write(root, "seqpipe/domain.py", "def score(x):\n    return x * 2\n")
    _write(
        root,
        "seqpipe/run.py",
        "from .domain import score\n\n\ndef main():\n    return score(21)\n",
    )
    _write(
        root,
        "tests/test_domain.py",
        "from seqpipe.domain import score\n\n\ndef test_score():\n    assert score(2) == 4\n",
    )
    return root


def _context(tree, cells=None, frame_id="frame-1"):
    paths = {
        "seqpipe/__init__.py",
        "seqpipe/domain.py",
        "seqpipe/run.py",
        "tests/test_domain.py",
    }
    rows = (
        cells
        if cells is not None
        else {
            "cell-7": {
                "root_frame_id": frame_id,
                "status": "ok",
                "stdout": GOOD_STDOUT,
            }
        }
    )
    return CodeEvidenceContext(
        search_roots=(tree,),
        artifact_checksums={
            path: frozenset({_sha(tree / path)})
            for path in paths
            if (tree / path).is_file()
        },
        test_receipt=lambda cell_id, command: (
            cell_id == "cell-7" and command == "python -m pytest tests/"
        ),
        turn_id="turn-current",
        branch_id=frame_id,
        artifact_names=frozenset(paths),
        cell_lookup=_Cells(rows),
        frame_id=frame_id,
    )


def _payload(tree, **overrides):
    payload = {
        "summary": "built the pipeline",
        "completion_bullets": ["Wrote the pipeline package"],
        "source_files": [
            {"path": "seqpipe/__init__.py"},
            {"path": "seqpipe/domain.py", "sha256": _sha(tree / "seqpipe/domain.py")},
            {"path": "seqpipe/run.py"},
            {"path": "tests/test_domain.py"},
        ],
        "entry_points": ["seqpipe/run.py"],
        "architecture_summary": (
            "seqpipe.domain owns scoring, seqpipe.run is the thin entry point, "
            "tests/ covers the domain."
        ),
        "test_evidence": [
            {"command": "python -m pytest tests/", "producing_cell_id": "cell-7"}
        ],
    }
    payload.update(overrides)
    return payload


def _record_test_cell(
    store,
    frame: str,
    *,
    turn_id: str,
    command: str,
    cell_id: str = "cell-test",
    branch_id: str | None = None,
    bash_ok: bool = True,
    with_receipt: bool = True,
):
    branch = branch_id or frame
    group = store.append_action_group(
        root_frame_id=frame,
        branch_id=branch,
        turn_id=turn_id,
        kind="code",
    )
    attempt = store.allocate_execution_attempt(
        group_id=group["group_id"],
        producing_cell_id=cell_id,
        state_revision=1,
    )
    store.mark_execution_attempt_started(attempt["attempt_id"])
    store.log_cell(
        frame_id=frame,
        root_frame_id=frame,
        code=f"host.bash({command!r})",
        result={"id": cell_id, "stdout": "model-controlled text"},
    )
    if with_receipt:
        store.log_host_call(
            method="bash",
            args=[{"command_sha256": command_digest(command)}],
            ok=bash_ok,
            frame_id=frame,
            action_group_id=group["group_id"],
            resource_keys=[f"command-sha256:{command_digest(command)}"],
        )
    store.mark_execution_attempt_response(attempt["attempt_id"])
    store.mark_execution_attempt_capture(attempt["attempt_id"])
    store.finish_execution_attempt(attempt["attempt_id"], terminal_state="completed")
    return group


# --------------------------------------------------------------------------
# backward compatibility: analysis_run is untouched
# --------------------------------------------------------------------------


def test_only_the_two_code_modes_require_evidence():
    assert EVIDENCE_REQUIRED_MODES == {"reusable_pipeline", "codebase_change"}
    assert requires_code_evidence("codebase_change") is True
    assert requires_code_evidence("analysis_run") is False
    assert requires_code_evidence(None) is False


@pytest.mark.parametrize("mode", [None, "analysis_run", "", "something_else"])
def test_an_analysis_turn_is_never_asked_for_code_evidence(mode, tree):
    """Backward compatible in both directions: a payload with no code fields
    passes, and a payload with *bogus* ones is not validated either."""
    assert verify_code_evidence({}, task_mode=mode, context=_context(tree)) is None
    bogus = {
        "source_files": [{"path": "/etc/hosts"}],
        "entry_points": ["nope.py"],
        "test_evidence": [{"command": "x", "producing_cell_id": "ghost"}],
    }
    assert verify_code_evidence(bogus, task_mode=mode, context=_context(tree)) is None


# --------------------------------------------------------------------------
# the happy path, and the four fields being required
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", sorted(EVIDENCE_REQUIRED_MODES))
def test_a_real_structured_deliverable_is_accepted(mode, tree):
    assert (
        verify_code_evidence(_payload(tree), task_mode=mode, context=_context(tree))
        is None
    )


@pytest.mark.parametrize("missing", CODE_EVIDENCE_KEYS)
def test_each_field_is_required_and_named_in_the_refusal(missing, tree):
    payload = _payload(tree)
    payload.pop(missing)
    error = verify_code_evidence(
        payload, task_mode="codebase_change", context=_context(tree)
    )
    assert error and missing in error
    assert "codebase_change" in error


def test_an_unverifiable_runtime_refuses_rather_than_waving_it_through(tree):
    """ "cannot verify" is not "verified". The produce-file provider degrades to
    the legacy accept on a broken store; this one must not, because the whole
    claim of these fields is that they are checkable."""
    error = verify_code_evidence(
        _payload(tree), task_mode="reusable_pipeline", context=None
    )
    assert error and "no evidence context" in error


# --------------------------------------------------------------------------
# source_files
# --------------------------------------------------------------------------


def test_a_deleted_source_file_is_refused(tree):
    payload = _payload(tree, source_files=[{"path": "seqpipe/domain.py"}])
    (tree / "seqpipe/domain.py").unlink()
    error = verify_code_evidence(
        payload, task_mode="codebase_change", context=_context(tree)
    )
    assert error and "seqpipe/domain.py" in error and "unavailable" in error


@pytest.mark.parametrize("claim", ["/etc/hosts", "../outside.py", "../../etc/passwd"])
def test_a_source_file_outside_the_evidence_roots_is_refused(claim, tree):
    """Existence somewhere on the machine is not this run's evidence — the same
    confinement rule the produce-file reconciler enforces."""
    (tree.parent / "outside.py").write_text("x = 1\n", encoding="utf-8")
    error = verify_code_evidence(
        _payload(tree, source_files=[{"path": claim}]),
        task_mode="codebase_change",
        context=_context(tree),
    )
    assert error and "session's workspace" in error


def test_a_mismatched_sha256_is_refused(tree):
    actual = _sha(tree / "seqpipe/run.py")
    error = verify_code_evidence(
        _payload(tree, source_files=[{"path": "seqpipe/run.py", "sha256": "0" * 64}]),
        task_mode="codebase_change",
        context=_context(tree),
    )
    assert error and "declared sha256" in error
    assert actual[:16] not in error


def test_a_file_that_was_never_captured_as_an_artifact_is_refused(tree):
    _write(tree, "seqpipe/uncaptured.py", "x = 1\n")
    error = verify_code_evidence(
        _payload(tree, source_files=[{"path": "seqpipe/uncaptured.py"}]),
        task_mode="reusable_pipeline",
        context=_context(tree),
    )
    assert error and "artifact version at that exact path" in error


def test_another_sessions_same_path_and_bytes_are_not_source_evidence(tmp_path):
    from openai4s.store import get_store

    workspace = tmp_path / "workspace"
    source = _write(workspace, "pkg/module.py", "VALUE = 1\n")
    store = get_store(tmp_path / "scope.db")
    current = store.new_frame(kind="turn", project_id="default")
    other = store.new_frame(kind="turn", project_id="default")
    data = source.read_bytes()
    store.save_artifact(
        path=str(source),
        filename="pkg/module.py",
        content_type="text/x-python",
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        frame_id=other,
        root_frame_id=other,
        project_id="default",
    )

    context = gather_code_evidence_context(store, current, (workspace,))
    context = CodeEvidenceContext(
        **{**context.__dict__, "test_receipt": lambda _cell, _command: True}
    )
    error = verify_code_evidence(
        {
            "source_files": [{"path": "pkg/module.py"}],
            "entry_points": ["pkg/module.py"],
            "architecture_summary": "module.py owns the implementation.",
            "test_evidence": [{"command": "pytest", "producing_cell_id": "x"}],
        },
        task_mode="codebase_change",
        context=context,
    )
    assert error and "artifact version at that exact path" in error
    store.close()


def test_same_basename_elsewhere_and_bytes_changed_after_capture_are_refused(tmp_path):
    from openai4s.store import get_store

    workspace = tmp_path / "workspace"
    captured = _write(workspace, "left/module.py", "VALUE = 1\n")
    _write(workspace, "right/module.py", "VALUE = 1\n")
    store = get_store(tmp_path / "paths.db")
    frame = store.new_frame(kind="turn", project_id="default")
    data = captured.read_bytes()
    store.save_artifact(
        path=str(captured),
        filename="module.py",
        content_type="text/x-python",
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        frame_id=frame,
        root_frame_id=frame,
        project_id="default",
    )
    context = gather_code_evidence_context(store, frame, (workspace,))
    context = CodeEvidenceContext(
        **{**context.__dict__, "test_receipt": lambda _cell, _command: True}
    )
    base_payload = {
        "entry_points": ["right/module.py"],
        "architecture_summary": "module.py owns the implementation.",
        "test_evidence": [{"command": "pytest", "producing_cell_id": "x"}],
    }
    wrong_path = verify_code_evidence(
        {**base_payload, "source_files": [{"path": "right/module.py"}]},
        task_mode="codebase_change",
        context=context,
    )
    assert wrong_path and "exact path" in wrong_path

    captured.write_text("VALUE = 2\n", encoding="utf-8")
    changed = verify_code_evidence(
        {
            **base_payload,
            "source_files": [{"path": "left/module.py"}],
            "entry_points": ["left/module.py"],
        },
        task_mode="codebase_change",
        context=context,
    )
    assert changed and "exact path" in changed
    store.close()


@pytest.mark.parametrize("relative", [".env", "id_rsa", ".aws/credentials"])
def test_source_hashing_refuses_secret_files_before_disclosing_a_digest(
    tmp_path, relative
):
    from openai4s.host.files import WorkspaceFileService
    from openai4s.store import get_store

    workspace = tmp_path / "workspace"
    source = _write(workspace, relative, "credential-material-never-hash-me\n")
    actual = _sha(source)
    store = get_store(tmp_path / f"secret-{source.name}.db")
    frame = store.new_frame(kind="turn", project_id="default")
    data = source.read_bytes()
    store.save_artifact(
        path=str(source),
        filename=relative,
        content_type="text/plain",
        size_bytes=len(data),
        checksum=actual,
        frame_id=frame,
        root_frame_id=frame,
        project_id="default",
    )
    files = WorkspaceFileService(
        data_dir=tmp_path / ".data",
        frame_id=lambda: frame,
        workspace=lambda: workspace,
    )
    context = gather_code_evidence_context(
        store, frame, (workspace,), file_service=files, turn_id="turn-secret"
    )
    error = verify_code_evidence(
        {
            "source_files": [{"path": relative, "sha256": "0" * 64}],
            "entry_points": [relative],
            "architecture_summary": "The declared file owns the implementation.",
            "test_evidence": [{"command": "pytest", "producing_cell_id": "x"}],
        },
        task_mode="codebase_change",
        context=context,
    )
    assert error and "unavailable" in error
    assert actual[:16] not in error
    store.close()


def test_source_hashing_refuses_a_benign_named_symlink_alias_to_a_secret(tmp_path):
    from openai4s.host.files import WorkspaceFileService
    from openai4s.store import get_store

    workspace = tmp_path / "workspace"
    secret = _write(workspace, ".env", "TOKEN=credential-material\n")
    alias = workspace / "apparently-benign.py"
    alias.symlink_to(secret)
    actual = _sha(secret)
    store = get_store(tmp_path / "alias.db")
    frame = store.new_frame(kind="turn", project_id="default")
    store.save_artifact(
        path=str(alias),
        filename=alias.name,
        content_type="text/x-python",
        size_bytes=secret.stat().st_size,
        checksum=actual,
        frame_id=frame,
        root_frame_id=frame,
        project_id="default",
    )
    files = WorkspaceFileService(
        data_dir=tmp_path / ".data",
        frame_id=lambda: frame,
        workspace=lambda: workspace,
    )
    context = gather_code_evidence_context(
        store, frame, (workspace,), file_service=files, turn_id="turn-alias"
    )
    error = verify_code_evidence(
        {
            "source_files": [{"path": alias.name, "sha256": "0" * 64}],
            "entry_points": [alias.name],
            "architecture_summary": "The declared file owns the implementation.",
            "test_evidence": [{"command": "pytest", "producing_cell_id": "x"}],
        },
        task_mode="codebase_change",
        context=context,
    )
    assert error and "unavailable" in error
    assert actual[:16] not in error
    store.close()


def test_an_empty_source_list_is_not_a_deliverable(tree):
    error = verify_code_evidence(
        _payload(tree, source_files=[]),
        task_mode="reusable_pipeline",
        context=_context(tree),
    )
    assert error and "source_files" in error


# --------------------------------------------------------------------------
# entry_points — compiled, never executed
# --------------------------------------------------------------------------


def test_a_python_entry_point_that_does_not_compile_is_refused(tree):
    _write(tree, "seqpipe/broken.py", "def main(:\n    pass\n")
    error = verify_code_evidence(
        _payload(tree, entry_points=["seqpipe/broken.py"]),
        task_mode="codebase_change",
        context=_context(tree),
    )
    assert error and "does not compile" in error


def test_verifying_an_entry_point_compiles_it_and_never_runs_it(tree, tmp_path):
    """A verifier that imported or exec'd the entry point would run
    agent-authored code inside the daemon process. The module below writes a
    file at import time; that file must not appear."""
    beacon = tmp_path / "executed.marker"
    _write(
        tree,
        "seqpipe/sideeffect.py",
        f"from pathlib import Path\n\nPath({str(beacon)!r}).write_text('ran')\n\n\n"
        "def main():\n    return 1\n",
    )
    context = _context(tree)
    assert (
        verify_code_evidence(
            _payload(tree, entry_points=["seqpipe/sideeffect.py"]),
            task_mode="codebase_change",
            context=context,
        )
        is None
    )
    assert not beacon.exists()


def test_an_r_entry_point_is_existence_checked_not_parsed_as_python(tree):
    """R is not Python. Compiling an R file would reject valid code, so the
    daemon checks that it is there and reads its bytes, and says so."""
    _write(tree, "pipeline.R", "main <- function() {\n  1 + 1\n}\n")
    assert (
        verify_code_evidence(
            _payload(tree, entry_points=["pipeline.R"]),
            task_mode="reusable_pipeline",
            context=_context(tree),
        )
        is None
    )
    error = verify_code_evidence(
        _payload(tree, entry_points=["missing.R"]),
        task_mode="reusable_pipeline",
        context=_context(tree),
    )
    assert error and "unavailable" in error


def test_an_empty_entry_point_list_is_refused(tree):
    error = verify_code_evidence(
        _payload(tree, entry_points=[]),
        task_mode="codebase_change",
        context=_context(tree),
    )
    assert error and "entry_points" in error


# --------------------------------------------------------------------------
# test_evidence — bound to Host-authorized shell receipts
# --------------------------------------------------------------------------


def test_a_forged_cell_id_is_refused(tree):
    error = verify_code_evidence(
        _payload(
            tree,
            test_evidence=[{"command": "pytest", "producing_cell_id": "cell-ghost"}],
        ),
        task_mode="codebase_change",
        context=_context(tree),
    )
    assert error and "never executed" in error


def test_an_empty_execution_log_is_named_instead_of_a_phantom_cell(tree):
    """The refusal used to say the named cell 'this run never executed' even
    when the runtime simply recorded no cells at all — actively false when the
    cell DID run, and it sent the model into a repair loop chasing a phantom.
    An empty log is its own, honest, refusal."""
    context = CodeEvidenceContext(
        search_roots=(tree,),
        artifact_names=_context(tree).artifact_names,
        cell_lookup=_Cells({}),
        frame_id="frame-1",
        has_cells=lambda: False,
    )
    error = verify_code_evidence(
        _payload(tree), task_mode="codebase_change", context=context
    )
    assert error and "recorded no cells" in error
    assert "never executed" not in error


def test_a_missing_cell_in_a_recorded_runtime_keeps_the_phantom_wording(tree):
    context = CodeEvidenceContext(
        search_roots=(tree,),
        artifact_names=_context(tree).artifact_names,
        cell_lookup=_Cells({}),
        frame_id="frame-1",
        has_cells=lambda: True,
    )
    error = verify_code_evidence(
        _payload(tree), task_mode="codebase_change", context=context
    )
    assert error and "never executed" in error


def test_a_failed_cell_cannot_back_a_passing_claim(tree):
    cells = {
        "cell-7": {
            "root_frame_id": "frame-1",
            "status": "error",
            "stdout": GOOD_STDOUT,
        }
    }
    error = verify_code_evidence(
        _payload(tree), task_mode="codebase_change", context=_context(tree, cells)
    )
    assert error and "'error'" in error


def test_printing_passing_looking_output_is_not_a_test_receipt(tree):
    """An ok Cell and model-controlled stdout are not execution evidence."""
    context = _context(tree)
    context = CodeEvidenceContext(
        **{
            **context.__dict__,
            "test_receipt": lambda _cell_id, _command: False,
        }
    )
    error = verify_code_evidence(
        _payload(tree), task_mode="reusable_pipeline", context=context
    )
    assert error and "no successful Host-authorized execution receipt" in error


def test_a_receipt_for_command_a_cannot_back_command_b(tree):
    error = verify_code_evidence(
        _payload(
            tree,
            test_evidence=[
                {"command": "python -m pytest other/", "producing_cell_id": "cell-7"}
            ],
        ),
        task_mode="reusable_pipeline",
        context=_context(tree),
    )
    assert error and "exact command" in error


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest tests/; true",
        "python -m pytest tests/ || true",
        "python -m pytest tests/ | cat",
        "python -m pytest tests/ & wait",
        "python -m pytest tests/\ntrue",
        'sh -c "python -m pytest tests/; true"',
        'env sh -c "python -m pytest tests/; true"',
        'echo "$(python -m pytest tests/; true)"',
    ],
)
def test_shell_composition_cannot_mask_a_failed_test(command, tree):
    context = _context(tree)
    context = CodeEvidenceContext(
        **{
            **context.__dict__,
            # Prove the syntax guard fires even if an exact successful receipt
            # exists for the masking command.
            "test_receipt": lambda _cell_id, _command: True,
        }
    )
    error = verify_code_evidence(
        _payload(
            tree,
            test_evidence=[{"command": command, "producing_cell_id": "cell-7"}],
        ),
        task_mode="codebase_change",
        context=context,
    )
    assert error and "mask an earlier failure status" in error


@pytest.mark.parametrize(
    "command",
    [
        "python -m pytest tests/",
        "python -m pytest tests/ && echo verified",
        "python -c \"print(';')\"",
        "python -m pytest tests/ >results.txt 2>&1",
    ],
)
def test_non_masking_shell_forms_remain_valid_evidence_commands(command):
    assert command_preserves_failure_status(command) is True


def test_a_successful_receipt_does_not_depend_on_model_controlled_stdout(tree):
    cells = {"cell-7": {"root_frame_id": "frame-1", "status": "ok", "stdout": "   "}}
    assert (
        verify_code_evidence(
            _payload(tree), task_mode="codebase_change", context=_context(tree, cells)
        )
        is None
    )


def test_the_store_receipt_binds_exact_command_cell_turn_and_branch(tmp_path):
    from openai4s.store import get_store

    store = get_store(tmp_path / "receipts.db")
    frame = store.new_frame(kind="turn", project_id="default")
    command = "python -m pytest tests/test_pipeline.py -q"
    _record_test_cell(store, frame, turn_id="turn-current", command=command)

    common = {
        "producing_cell_id": "cell-test",
        "root_frame_id": frame,
        "branch_id": frame,
        "turn_id": "turn-current",
    }
    assert store.has_successful_bash_receipt(
        **common, command_sha256=command_digest(command)
    )
    assert not store.has_successful_bash_receipt(
        **common, command_sha256=command_digest("python -m pytest other.py -q")
    )
    assert not store.has_successful_bash_receipt(
        **{**common, "turn_id": "turn-prior"},
        command_sha256=command_digest(command),
    )
    assert not store.has_successful_bash_receipt(
        **{**common, "branch_id": "branch-other"},
        command_sha256=command_digest(command),
    )
    store.close()


@pytest.mark.parametrize("with_receipt,bash_ok", [(False, True), (True, False)])
def test_print_only_and_nonzero_shell_runs_do_not_create_passing_receipts(
    tmp_path, with_receipt, bash_ok
):
    from openai4s.store import get_store

    store = get_store(tmp_path / f"failed-{with_receipt}-{bash_ok}.db")
    frame = store.new_frame(kind="turn", project_id="default")
    command = "python -m pytest -q"
    _record_test_cell(
        store,
        frame,
        turn_id="turn-current",
        command=command,
        with_receipt=with_receipt,
        bash_ok=bash_ok,
    )
    assert not store.has_successful_bash_receipt(
        producing_cell_id="cell-test",
        command_sha256=command_digest(command),
        root_frame_id=frame,
        branch_id=frame,
        turn_id="turn-current",
    )
    store.close()


def test_a_real_cell_from_another_session_is_not_this_runs_evidence(tree):
    cells = {
        "cell-7": {
            "root_frame_id": "someone-elses-frame",
            "status": "ok",
            "stdout": GOOD_STDOUT,
        }
    }
    error = verify_code_evidence(
        _payload(tree), task_mode="codebase_change", context=_context(tree, cells)
    )
    assert error and "another session" in error


def test_test_evidence_without_a_cell_id_is_refused(tree):
    error = verify_code_evidence(
        _payload(tree, test_evidence=[{"command": "pytest"}]),
        task_mode="codebase_change",
        context=_context(tree),
    )
    assert error and "producing_cell_id" in error


@pytest.mark.parametrize(
    "text,flagged",
    [
        ("3 passed in 0.12s", False),
        ("0 failed, 3 passed", False),
        ("no errors", False),
        ("1 failed, 2 passed", True),
        ("FAILED tests/x.py::y", True),
        ("Traceback (most recent call last):", True),
        ("Ran 3 tests\nFAILED (failures=1)", True),
        ("Ran 3 tests\nOK", False),
        ("2 errors", True),
    ],
)
def test_the_failure_reader_is_anchored_rather_than_keyword_soup(text, flagged):
    assert (stdout_reports_failure(text) is not None) is flagged


# --------------------------------------------------------------------------
# both completion doors enforce it
# --------------------------------------------------------------------------


def test_submit_output_refuses_a_code_mode_completion_without_evidence(tree):
    service = CompletionService(
        task_mode=lambda: "reusable_pipeline",
        code_evidence=lambda: _context(tree),
    )
    result = service.submit(
        {"output": {"summary": "done"}, "completion_bullets": ["Built the pipeline"]}
    )
    assert "error" in result and "source_files" in result["error"]
    assert service.last_output is None


def test_submit_output_accepts_and_carries_the_verified_declarations(tree):
    service = CompletionService(
        task_mode=lambda: "codebase_change",
        code_evidence=lambda: _context(tree),
    )
    payload = _payload(tree)
    result = service.submit(
        {
            "output": {"summary": "done"},
            "completion_bullets": ["Built the pipeline"],
            **{key: payload[key] for key in CODE_EVIDENCE_KEYS},
        }
    )
    assert result == {"status": "ok"}
    assert service.last_output is not None
    for key in CODE_EVIDENCE_KEYS:
        assert service.last_output[key] == payload[key]


def test_submit_output_is_revoked_when_verified_source_changes_after_submit(tree):
    service = CompletionService(
        task_mode=lambda: "codebase_change",
        # Rebuild the context on each call, as the gateway does after it has
        # captured the completed Cell's newest Artifact versions.
        code_evidence=lambda: _context(tree),
    )
    payload = _payload(tree)
    result = service.submit(
        {
            "output": {"summary": "done"},
            "completion_bullets": ["Built the pipeline"],
            **{key: payload[key] for key in CODE_EVIDENCE_KEYS},
        }
    )
    assert result == {"status": "ok"}

    _write(tree, "seqpipe/run.py", "def main():\n    return 99\n")

    error = service.revalidate_pending_completion()
    assert error and "changed after host.submit_output" in error
    assert service.last_output is None


def test_non_python_entry_point_bytes_are_sealed_after_submit(tree):
    _write(tree, "pipeline.R", "main <- function() 1\n")
    service = CompletionService(
        task_mode=lambda: "reusable_pipeline",
        code_evidence=lambda: _context(tree),
    )
    payload = _payload(tree, entry_points=["pipeline.R"])
    assert service.submit(
        {
            "output": {"summary": "done"},
            "completion_bullets": ["Built the pipeline"],
            **{key: payload[key] for key in CODE_EVIDENCE_KEYS},
        }
    ) == {"status": "ok"}

    _write(tree, "pipeline.R", "main <- function() 2\n")

    error = service.revalidate_pending_completion()
    assert error and "changed after host.submit_output" in error
    assert service.last_output is None


def test_an_analysis_submission_envelope_is_byte_for_byte_what_it_was(tree):
    service = CompletionService(
        task_mode=lambda: "analysis_run", code_evidence=lambda: _context(tree)
    )
    assert service.submit(
        {"output": {"summary": "done"}, "completion_bullets": ["Analyzed the data"]}
    ) == {"status": "ok"}
    assert service.last_output == {
        "output": {"summary": "done"},
        "completion_bullets": ["Analyzed the data"],
    }


def test_a_broken_evidence_store_refuses_instead_of_degrading(tree):
    def explode():
        raise RuntimeError("store closed")

    service = CompletionService(
        task_mode=lambda: "codebase_change", code_evidence=explode
    )
    result = service.submit(
        {
            "output": {"summary": "done"},
            "completion_bullets": ["Built it"],
            **{key: _payload(tree)[key] for key in CODE_EVIDENCE_KEYS},
        }
    )
    assert "error" in result and "no evidence context" in result["error"]


def _finalize(arguments, code_evidence=None):
    call = NativeToolCall(
        id="call-1",
        wire_id="w-1",
        name=FINALIZE_RESPONSE_NAME,
        ordinal=0,
        raw_arguments="{}",
        arguments=arguments,
    )
    return execute_finalize_action(
        FinalizeAction(call=call), code_evidence=code_evidence
    )


def test_finalize_declares_the_four_optional_fields_on_a_still_closed_schema():
    schema = finalize_response_schema()
    for key in CODE_EVIDENCE_KEYS:
        assert key in schema["properties"]
        assert key not in schema["required"]
    assert schema["additionalProperties"] is False
    # No field for a test's output text: the claim cannot exist, so it cannot
    # be believed.
    item = schema["properties"]["test_evidence"]["items"]
    assert set(item["properties"]) == {"command", "producing_cell_id"}


def test_finalize_refuses_a_code_mode_payload_whose_evidence_fails(tree):
    from openai4s.host.completion import CompletionService as Service

    service = Service(
        task_mode=lambda: "codebase_change", code_evidence=lambda: _context(tree)
    )
    outcome = _finalize(
        {"summary": "done", "completion_bullets": ["Built the pipeline"]},
        code_evidence=service.verify_code_claims,
    )
    assert outcome.completion is None
    assert outcome.history_messages[0]["is_error"] is True
    assert "source_files" in outcome.history_messages[0]["content"]


def test_finalize_accepts_a_verified_code_mode_payload(tree):
    from openai4s.host.completion import CompletionService as Service

    service = Service(
        task_mode=lambda: "codebase_change", code_evidence=lambda: _context(tree)
    )
    payload = _payload(tree)
    outcome = _finalize(payload, code_evidence=service.verify_code_claims)
    assert outcome.completion is not None
    assert outcome.completion["output"]["entry_points"] == ["seqpipe/run.py"]


def test_finalize_without_the_hook_is_exactly_what_it_was(tree):
    outcome = _finalize({"summary": "done", "completion_bullets": ["Answered it"]})
    assert outcome.completion is not None
    assert outcome.history_messages[0]["is_error"] is False


# --------------------------------------------------------------------------
# the context the dispatcher actually builds
# --------------------------------------------------------------------------


def test_the_context_reads_artifact_names_and_the_execution_log_from_the_store(
    tmp_path,
):
    from openai4s.host.files import WorkspaceFileService
    from openai4s.store import get_store

    store = get_store(tmp_path / "ctx.db")
    frame = store.new_frame(kind="turn", project_id="default", status="ready")
    path = tmp_path / "seqpipe" / "run.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def main():\n    return 1\n", encoding="utf-8")
    data = path.read_bytes()
    store.save_artifact(
        path=str(path),
        filename="seqpipe/run.py",
        content_type="text/x-python",
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        frame_id=frame,
        root_frame_id=frame,
        project_id="default",
    )
    turn_id = "turn-current"
    group = store.append_action_group(
        root_frame_id=frame,
        branch_id=frame,
        turn_id=turn_id,
        kind="code",
    )
    cell_id = "cell-real"
    attempt = store.allocate_execution_attempt(
        group_id=group["group_id"],
        producing_cell_id=cell_id,
        state_revision=1,
    )
    store.mark_execution_attempt_started(attempt["attempt_id"])
    store.log_cell(
        frame_id=frame,
        root_frame_id=frame,
        code="import subprocess",
        result={"id": cell_id, "status": "ok", "stdout": GOOD_STDOUT},
    )
    store.log_host_call(
        method="bash",
        args=[{"command_sha256": command_digest("pytest")}],
        ok=True,
        frame_id=frame,
        action_group_id=group["group_id"],
        resource_keys=[f"command-sha256:{command_digest('pytest')}"],
    )
    store.mark_execution_attempt_response(attempt["attempt_id"])
    store.mark_execution_attempt_capture(attempt["attempt_id"])
    store.finish_execution_attempt(attempt["attempt_id"], terminal_state="completed")

    files = WorkspaceFileService(
        data_dir=tmp_path / ".data",
        frame_id=lambda: frame,
        workspace=lambda: tmp_path,
    )
    context = gather_code_evidence_context(
        store,
        frame,
        (tmp_path,),
        file_service=files,
        turn_id=turn_id,
        branch_id=frame,
    )
    assert "seqpipe/run.py" in context.artifact_names
    assert "run.py" not in context.artifact_names
    assert context.frame_id == frame
    row = context.cell_lookup(cell_id)
    assert row and row["status"] == "ok" and row["stdout"] == GOOD_STDOUT

    payload = {
        "source_files": [{"path": "seqpipe/run.py"}],
        "entry_points": ["seqpipe/run.py"],
        "architecture_summary": "run.py is the entry point.",
        "test_evidence": [{"command": "pytest", "producing_cell_id": cell_id}],
    }
    assert (
        verify_code_evidence(payload, task_mode="codebase_change", context=context)
        is None
    )
    store.close()


def test_the_gathered_context_knows_whether_the_runtime_recorded_any_cells(
    tmp_path,
):
    from openai4s.store import get_store

    store = get_store(tmp_path / "cells.db")
    frame = store.new_frame(kind="turn", project_id="default", status="ready")
    empty = gather_code_evidence_context(store, frame, (tmp_path,))
    assert empty.has_cells is not None and empty.has_cells() is False

    store.log_cell(
        frame_id=frame,
        root_frame_id=frame,
        code="print('x')",
        result={"id": "cell-any", "status": "ok", "stdout": "x\n"},
    )
    recorded = gather_code_evidence_context(store, frame, (tmp_path,))
    assert recorded.has_cells is not None and recorded.has_cells() is True
    store.close()
