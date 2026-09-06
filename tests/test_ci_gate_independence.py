"""Each gate is its own job, so each can fail on its own.

`CLAUDE.md` lists the gates and says each "fails independently". Four of them
were steps inside another job: `check_directory_readmes` came after
`pre-commit` in `lint`, and the harness tier plus both response captures came
after `pytest` in `test`. A GitHub Actions step only runs when every step before
it passed, so all four were *unreachable* whenever the step above them was red.

That is not a smaller signal, it is a different one. A branch that failed
formatting and also added an undocumented directory reported one problem and
hid the other -- and the doc gap is the one a reviewer cannot see in the diff.
A branch with one unrelated red test looked exactly like a branch that had also
drifted the harness goldens and moved the response contract: three questions
collapsed into one answer, and the two that were never asked were reported as
though they had been.

The check is on the parsed workflow graph rather than on a run, because there is
no way to observe a step that did not execute from inside a working copy -- and
because the property is structural: it is about where the gate is declared, not
about what it returned this time.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


#: The command that *is* each gate, and nothing else. Matched against `run:`
#: lines rather than against a step's `name`, which is prose that can say
#: anything. `pytest` excludes `--collect-only`: that step is a precondition of
#: the same gate, not a second one, and matching it would make the suite look
#: like it ran twice.
GATES = {
    "pre-commit": ("pre-commit run --all-files", ()),
    "directory-readmes": ("scripts/check_directory_readmes.py", ()),
    "mypy": ("uv run mypy", ()),
    "secret-scan": ("scripts/source_secret_scan.py", ()),
    "pytest": ("uv run pytest", ("--collect-only",)),
    "harness": ("harness.cli run --tier pr", ()),
    "response-schemas": ("scripts/capture_response_schemas.py --check", ()),
    "response-contract": ("scripts/capture_response_contract.py --check", ()),
    "container-image": ("scripts/container_smoke.sh", ()),
    "linux-bwrap-interrupt": ("harness.smoke.linux_bwrap_interrupt", ()),
    "linux-sandbox-full": ("harness.smoke.linux_sandbox", ()),
    # The two npm-side gates. They are separate for the reason this whole file
    # is about: "the installer behaves" and "the published package has anything
    # in it" are different questions, and a red answer to one must not withhold
    # the other.
    "skills-installer": ("tools/skills-installer/selftest.mjs", ()),
    "skills-package": ("tools/skills-installer/check_package.mjs", ()),
    # Deliberately `python -m pytest`, not the `uv run pytest` needle above:
    # the locked single-cell stack is its own independent question.
    "singlecell-skill": (
        "python -m pytest tests/test_single_cell_rna_analysis_skill.py",
        (),
    ),
}


def _job_for(gate: str) -> tuple[str, dict, int]:
    """(job id, job, index of the step that runs the gate)."""
    workflow = _workflow()
    found = []
    for job_id, job in (workflow.get("jobs") or {}).items():
        for index, step in enumerate(job.get("steps") or []):
            needle, excluded = GATES[gate]
            for line in str(step.get("run") or "").splitlines():
                if needle in line and not any(bad in line for bad in excluded):
                    found.append((job_id, job, index))
                    break
    assert found, f"no job runs the {gate} gate; the check is asserting nothing"
    assert len(found) == 1, f"{gate} runs in more than one job: {[f[0] for f in found]}"
    return found[0]


@pytest.mark.parametrize("gate", sorted(GATES))
def test_every_gate_runs_in_a_job_of_its_own(gate):
    """Two gates in one job means the second is conditional on the first.

    `pytest` and `uv run pytest --collect-only` are the same job on purpose --
    collection is a precondition of running, not a separate question -- so the
    rule is stated over the gate list, which holds one entry per independent
    question.
    """
    job_id, _job, _index = _job_for(gate)
    others = sorted(
        other for other in GATES if other != gate and _job_for(other)[0] == job_id
    )
    assert not others, (
        f"the {gate} gate shares job {job_id!r} with {others}; "
        "whichever runs second cannot report while the first is failing"
    )


@pytest.mark.parametrize("gate", sorted(GATES))
def test_no_gate_sits_behind_another_command_that_can_fail_it(gate):
    """Setup may precede a gate; another gate's work may not.

    `uv sync` and a checkout are preconditions -- without them the gate cannot
    run at all, so a failure there is not a hidden answer. What must not precede
    it is a command that answers a *different* question, because then a red
    answer to that one silently withholds this one.
    """
    _job_id, job, index = _job_for(gate)
    steps = job.get("steps") or []
    before = []
    for step in steps[:index]:
        for line in str(step.get("run") or "").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Preconditions, not answers. Without these the gate cannot run at
            # all, so a failure here is a broken job rather than a withheld
            # verdict. `compileall` and `--collect-only` belong to the suite
            # gate itself: an import error found by either is the same defect
            # `pytest` would report one step later, not a different question.
            if stripped.startswith(
                (
                    "uv sync",
                    "uv venv",
                    "npm ",
                    "npx ",
                    "python -m pip",
                    "sudo apt-get",
                    "sudo apparmor_parser",
                    "/usr/bin/bwrap ",
                    "uv run python -m compileall",
                    "uv run pytest --collect-only",
                )
            ):
                continue
            before.append(stripped)
    assert not before, (
        f"the {gate} gate runs after {before} in the same job; "
        "any of those failing means this gate never reports"
    )


def test_the_gate_list_matches_what_the_repository_documents():
    """The list above is the contract, so it must not drift from CLAUDE.md.

    A gate quietly dropped from `ci.yml` *and* from this dict would leave every
    assertion here green while the gate stopped existing. CLAUDE.md is the third
    copy and the one a person reads.
    """
    claude_md = (Path(__file__).resolve().parents[1] / "CLAUDE.md").read_text("utf-8")
    for gate, (command, _excluded) in GATES.items():
        needle = command.split(" --")[0].split(" run ")[-1].strip()
        assert needle in claude_md, (
            f"the {gate} gate ({needle}) is not named in CLAUDE.md; either it is "
            "no longer a gate or the documentation stopped saying so"
        )


def test_every_job_has_a_timeout():
    """A job with no timeout is a gate that can hang instead of failing, and a
    hung required check blocks a branch with no verdict at all."""
    jobs = (_workflow().get("jobs") or {}).items()
    missing = sorted(job_id for job_id, job in jobs if not job.get("timeout-minutes"))
    assert not missing, missing
