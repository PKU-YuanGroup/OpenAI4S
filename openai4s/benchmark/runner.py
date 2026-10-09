"""Run a case and decide whether what happened is what it declared.

The decision is the interesting part. A case that expects ``failure`` and gets
a clean run has failed just as surely as one that expects success and raises —
a benchmark that scores "no exception" measures nothing about the half of the
system that is supposed to refuse.
"""

from __future__ import annotations

import contextlib
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai4s.benchmark.model import Case, Workflow, load_workflows
from openai4s.benchmark.steps import STEPS, SkipCase, make_context


@dataclass
class CaseResult:
    """What one case did, and whether that is what it said it would do."""

    case_id: str
    workflow: str
    outcome: str
    passed: bool
    detail: str = ""
    skipped: bool = False
    duration_ms: int = 0
    observed: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "workflow": self.workflow,
            "expected_outcome": self.outcome,
            "passed": self.passed,
            "skipped": self.skipped,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
        }


def _check_expectations(observed: dict[str, Any], expect: dict[str, Any]) -> list[str]:
    """Every declared expectation, checked against what the steps returned."""
    problems = []
    for key, wanted in expect.items():
        if key.endswith("__contains"):
            field = key[: -len("__contains")]
            actual = observed.get(field)
            if not isinstance(actual, (list, tuple, str)) or wanted not in actual:
                problems.append(f"{field}: {actual!r} does not contain {wanted!r}")
            continue
        if key.endswith("__len"):
            field = key[: -len("__len")]
            actual = observed.get(field)
            length = len(actual) if hasattr(actual, "__len__") else None
            if length != wanted:
                problems.append(f"{field}: length {length} != {wanted}")
            continue
        if key.endswith("__truthy"):
            field = key[: -len("__truthy")]
            if bool(observed.get(field)) is not bool(wanted):
                problems.append(
                    f"{field}: truthiness {bool(observed.get(field))} != {wanted}"
                )
            continue
        if observed.get(key) != wanted:
            problems.append(f"{key}: {observed.get(key)!r} != {wanted!r}")
    return problems


@contextlib.contextmanager
def _process_default_data_dir(path: Path) -> Iterator[None]:
    """Answer ``get_config()`` with a Config on ``path`` for one case.

    Every step hands production code the case's own Config, so nothing in a
    case should resolve the process default. Something did: a delegation case
    reached the judgment shadows, which read ``get_config()``, and with
    OPENAI4S_DATA_DIR unset that opened and migrated a developer's real
    ~/.openai4s (2026-10-08). With the default answered from a scratch dir,
    such a lookup touches nobody's data whatever the environment says, and
    ``run_case`` fails the case for writing there.

    Only the cached singleton is swapped. OPENAI4S_DATA_DIR stays as it is:
    the kernel sandbox reads it to deny cells the real instance's token and
    database, and a scratch dir in its place would drop a custom real data dir
    from that list for the length of the case.
    """

    from openai4s import config as config_mod

    previous = config_mod._CONFIG
    config_mod._CONFIG = config_mod.Config(data_dir=path)
    try:
        yield
    finally:
        config_mod._CONFIG = previous


def _written_under(path: Path) -> list[str]:
    """What exists under ``path``, which nothing was supposed to create."""
    if not path.exists():
        return []
    return sorted(str(item.relative_to(path)) for item in path.rglob("*")) or ["."]


def run_case(workflow: Workflow, case: Case, root: Path | None = None) -> CaseResult:
    started = time.time()
    # Resolve every step up front, before allocating anything or entering the
    # outcome-evaluation try. A manifest that names an unimplemented step is a
    # manifest bug that must fail hard — raising it *inside* the try let an
    # error-expecting case (outcome `failure`/`permission_denied`) catch the
    # KeyError and score it as the refusal it declared, so a workflow describing
    # work nothing does passed green.
    resolved: list[tuple[str, Any]] = []
    for name in case.steps or workflow.steps:
        step = STEPS.get(name)
        if step is None:
            raise KeyError(
                f"workflow {workflow.id!r} names step {name!r}, which is not "
                f"implemented; a manifest may not describe work nothing does"
            )
        resolved.append((name, step))
    temporary = None
    if root is None:
        temporary = tempfile.TemporaryDirectory(prefix="openai4s-benchmark-")
        root = Path(temporary.name)
    context = make_context(Path(root))
    # Its own temp dir rather than one under `root`: a caller-supplied root can
    # outlive a case, and a leftover from an earlier run must not fail this one.
    scratch = tempfile.TemporaryDirectory(prefix="openai4s-benchmark-default-")
    process_default = Path(scratch.name) / "data"
    observed: dict[str, Any] = {}
    failure: Exception | None = None
    try:
        with _process_default_data_dir(process_default):
            for name, step in resolved:
                merged = {**case.inputs.get("*", {}), **case.inputs.get(name, {})}
                observed.update(step(context, merged) or {})
                # Steps that produce a package hand its path on by convention.
                if "path" in observed:
                    context.state.setdefault("package_path", observed["path"])
    except SkipCase as skip:
        if temporary is not None:
            temporary.cleanup()
        return CaseResult(
            case.id,
            workflow.id,
            case.outcome,
            passed=True,
            skipped=True,
            detail=str(skip),
            duration_ms=int((time.time() - started) * 1000),
        )
    except Exception as error:  # noqa: BLE001 - the case decides if this is right
        failure = error
    finally:
        stray = _written_under(process_default)
        scratch.cleanup()
        if temporary is not None:
            temporary.cleanup()

    duration = int((time.time() - started) * 1000)
    if stray:
        # Whatever the declared outcome: a refusal that also wrote into
        # another data dir is not the refusal the case meant to watch.
        return CaseResult(
            case.id,
            workflow.id,
            case.outcome,
            passed=False,
            detail=(
                "resolved the process-global data dir instead of the case's own "
                f"Config and wrote {', '.join(stray[:5])} there"
            ),
            duration_ms=duration,
            observed=observed,
        )
    expects_error = case.outcome in ("failure", "permission_denied")

    if failure is not None and not expects_error:
        return CaseResult(
            case.id,
            workflow.id,
            case.outcome,
            passed=False,
            detail=f"{type(failure).__name__}: {failure}",
            duration_ms=duration,
            observed=observed,
        )
    if failure is None and expects_error:
        return CaseResult(
            case.id,
            workflow.id,
            case.outcome,
            passed=False,
            detail=(
                f"the case declares {case.outcome!r} and the workflow completed "
                f"without refusing anything"
            ),
            duration_ms=duration,
            observed=observed,
        )
    if failure is not None:
        # The refusal is the result, so the expectations describe *it*.
        observed = {
            **observed,
            "error": f"{type(failure).__name__}: {failure}",
            "error_type": type(failure).__name__,
        }
        if expects_error and not case.expect:
            # An error-expecting case with no expectations would pass on *any*
            # exception — an incidental OSError from the sandbox or a bug inside
            # a step, not the refusal it means to assert. In a suite that decides
            # whether a release is good, that is fabricated coverage. Require the
            # case to name what it expects about the error.
            return CaseResult(
                case.id,
                workflow.id,
                case.outcome,
                passed=False,
                detail=(
                    f"case declares {case.outcome!r} but asserts nothing about "
                    f"the error; an empty expect would pass on any incidental "
                    f"exception. Add error_type/error__contains."
                ),
                duration_ms=duration,
                observed=observed,
            )
    problems = _check_expectations(observed, case.expect)
    return CaseResult(
        case.id,
        workflow.id,
        case.outcome,
        passed=not problems,
        detail="; ".join(problems),
        duration_ms=duration,
        observed=observed,
    )


def run_all(root: Path | None = None) -> dict[str, Any]:
    # The parameter was accepted and ignored, so a caller pointing at a
    # different suite silently got the repository default — a valid-looking
    # report for workflows nobody asked to run.
    workflows = load_workflows(root)
    results = [
        run_case(workflow, case) for workflow in workflows for case in workflow.cases
    ]
    return {
        "workflows": len(workflows),
        "cases": len(results),
        "passed": sum(1 for r in results if r.passed and not r.skipped),
        "skipped": sum(1 for r in results if r.skipped),
        "failed": sum(1 for r in results if not r.passed),
        "results": [r.public() for r in results],
    }


__all__ = ["CaseResult", "run_all", "run_case"]
