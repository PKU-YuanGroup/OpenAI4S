"""INV-2 (Backend Opacity), asserted before the first backend exists.

The plan orders this test *ahead of* any Slurm code on purpose. A leak
guard written afterwards is written against whatever leaked — it codifies
the current state and calls it the rule. Written first, it is the rule, and
the backend has to be built to fit it.

What it holds:

* the orchestration **core** (everything in `openai4s/orchestration/` that
  is not inside a backend subpackage) never says `slurm`, `sbatch`,
  `squeue`, `partition`, `qos`, `srun`, `scancel`, `sacct`;
* its **import graph** never reaches a module that does — source-level
  absence is easy to satisfy while importing the leak one line away;
* the scheduler words that are legitimate somewhere live only in a backend
  subpackage and in cluster.toml, never in core.

The word list is deliberately about *schedulers*, not about the string
"slurm" alone: `partition` and `qos` are the two that would realistically
sneak into a "generic" resource profile and quietly make the abstraction a
Slurm-shaped hole.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_ORCHESTRATION = _REPO / "openai4s" / "orchestration"

#: Names a scheduler owns. Matched case-insensitively as whole words, so
#: `partitioning` in prose does not trip it while `partition="gpu"` does.
_SCHEDULER_WORDS = (
    "slurm",
    "sbatch",
    "squeue",
    "scancel",
    "sacct",
    "srun",
    "partition",
    "qos",
    "pbs",
    "torque",
    "lsf",
    "bsub",
)
_WORD_RE = re.compile(r"\b(?:" + "|".join(_SCHEDULER_WORDS) + r")\b", re.IGNORECASE)

#: Module paths are identifiers, not prose, so they are matched as
#: substrings. Writing this test found why that matters: `\bslurm\b` does
#: NOT match `slurm_backend` — `_` is a word character — so a word-boundary
#: scan over import names would have waved through the single most likely
#: spelling of the leak. The falsification test below is what surfaced it.
_PATH_RE = re.compile("|".join(_SCHEDULER_WORDS), re.IGNORECASE)

#: Subpackages allowed to name a scheduler: a backend implementation is
#: exactly where that vocabulary belongs.
_BACKEND_DIRS = {"slurm", "local"}


def _core_modules() -> list[Path]:
    """Every orchestration module that is NOT inside a backend subpackage."""
    modules = []
    for path in sorted(_ORCHESTRATION.rglob("*.py")):
        rel = path.relative_to(_ORCHESTRATION)
        if rel.parts and rel.parts[0] in _BACKEND_DIRS:
            continue
        modules.append(path)
    return modules


def test_the_core_exists_and_this_test_is_looking_at_it():
    """Without this, an empty or renamed package makes every assertion below
    vacuously true — the classic way a leak guard passes forever."""
    modules = _core_modules()
    names = {p.name for p in modules}
    assert "models.py" in names, names
    assert "ports.py" in names, names
    assert len(modules) >= 3


@pytest.mark.parametrize("path", _core_modules(), ids=lambda p: p.name)
def test_core_source_never_names_a_scheduler(path: Path):
    source = path.read_text(encoding="utf-8")
    hits = sorted({m.group(0).lower() for m in _WORD_RE.finditer(source)})
    assert not hits, (
        f"{path.relative_to(_REPO)} names scheduler concepts {hits}. INV-2: "
        f"those words belong in a backend subpackage ({sorted(_BACKEND_DIRS)}) "
        f"or cluster.toml, never in the orchestration core."
    )


@pytest.mark.parametrize("path", _core_modules(), ids=lambda p: p.name)
def test_core_imports_never_reach_a_backend(path: Path):
    """Source-level silence is cheap to fake; the import graph is the claim.

    Walks the module's own imports and refuses any that names a backend
    subpackage or a scheduler — including `from . import slurm`, which reads
    innocently and is the whole leak.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            imported.append(base)
            imported.extend(f"{base}.{alias.name}" for alias in node.names)
    offenders = sorted({name for name in imported if _PATH_RE.search(name or "")})
    assert not offenders, (
        f"{path.relative_to(_REPO)} imports {offenders}; the orchestration "
        f"core must be importable with no backend present at all."
    )


def test_core_is_importable_without_any_backend_module():
    """The runtime form of the same claim: importing the contract must not
    drag in an implementation.

    In a **subprocess**, and that is not fastidiousness. The first version
    did this in-process by deleting `openai4s.orchestration*` from
    `sys.modules` and re-importing — which left two live copies of every
    enum in the interpreter: modules imported before the purge (storage,
    which holds `DesiredState` from import time) kept the old classes while
    everything imported after got new ones. `is` comparisons across that
    seam are then false between values that print identically, so the
    reconciler stopped recognising `DesiredState.STOPPED` and silently
    never ran its cancel barrier. Two unrelated tests failed in the full
    suite and passed alone, and the cause was this test.

    A fresh interpreter is also the stronger claim: "importing the contract
    loads no backend" is about a clean process, not about one this test has
    already rearranged.
    """
    import subprocess

    probe = (
        "import sys, json;"
        "import openai4s.orchestration as m;"
        "loaded=[n for n in sys.modules if n.startswith('openai4s.orchestration')];"
        "print(json.dumps({'loaded': loaded,"
        " 'has_port': hasattr(m, 'AllocationBackend'),"
        " 'has_token': hasattr(m, 'SubmissionToken')}))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    leaked = sorted(n for n in result["loaded"] if _PATH_RE.search(n))
    assert not leaked, f"importing the contract loaded {leaked}"
    assert result["has_port"] and result["has_token"]


def test_the_guard_would_actually_catch_a_leak(tmp_path):
    """The falsification. A guard nobody has seen fail is a guard nobody
    knows works — so plant the leak this file exists to catch and confirm
    both halves fire on it."""
    planted = tmp_path / "leaky.py"
    planted.write_text(
        "from openai4s.orchestration import slurm_backend\n" "PARTITION = 'gpu'\n",
        encoding="utf-8",
    )
    source = planted.read_text(encoding="utf-8")
    assert _WORD_RE.search(source), "the source scan would have missed it"

    tree = ast.parse(source)
    names = [
        f"{node.module}.{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ]
    assert any(
        _PATH_RE.search(n) for n in names
    ), "the import scan would have missed it"


def test_external_ids_are_wrapped_not_bare_strings():
    """The other half of INV-2: the API exposes allocation_id, and a
    backend's own identifier travels wrapped so it cannot be mistaken for
    one."""
    from openai4s.orchestration import Allocation, ExternalHandle

    handle = ExternalHandle(backend="example", external_id="12345")
    assert "example" in str(handle) and "12345" in str(handle)
    allocation = Allocation(
        id=Allocation.new_id(),
        workload_id="wl_x",
        epoch=0,
        submission_token=__import__(
            "openai4s.orchestration", fromlist=["SubmissionToken"]
        ).SubmissionToken.mint(),
    )
    # the public identity is the allocation's own id, never the backend's
    assert allocation.id.startswith("alloc_")
    assert allocation.handle is None
