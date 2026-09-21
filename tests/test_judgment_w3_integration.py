"""Bundled templates must resolve in a process that only calls the registry.

Templates register as an import side effect of `openai4s.judgment.templates`.
Until MERGE-W3 the import lived at one call site (`JudgmentService.dispatch`),
so `host.judge` worked while `JudgmentService.run` -- the path W3-B's
evaluation shim takes -- raised `unknown template` for a template the build
ships. A fresh interpreter is the only honest way to test that: inside the
suite some other test has usually imported the package already.
"""

from __future__ import annotations

import subprocess
import sys

_PROBE = """
import sys
from openai4s.judgment.registry import get_template
for tid in ("system.probe", "skills.suggest", "literature.claim", "literature.screen"):
    try:
        get_template(tid)
    except KeyError as exc:
        print("MISSING", tid, exc)
        sys.exit(1)
print("ALL_RESOLVED")
"""

_RUN_PROBE = """
import pathlib, tempfile
from openai4s.config import Config, ExperimentalJudgmentFlags
from openai4s.host.judgment import JudgmentService

cfg = Config(
    data_dir=pathlib.Path(tempfile.mkdtemp()),
    experimental_judgment=ExperimentalJudgmentFlags(master=True, literature_check=True),
)
result = JudgmentService(cfg, None).run(
    purpose="literature_check",
    template_id="literature.claim",
    state={"claim": "x", "section": "y"},
)
# No key is configured, so unavailable/unconfigured is the honest answer.
# What must not happen is a ValueError naming a template the build ships.
print("STATUS", result.status, result.error_code)
"""


def _fresh(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
        env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "OPENAI4S_SKIP_DOTENV": "1"},
    )


def test_every_bundled_template_resolves_in_a_fresh_process() -> None:
    proc = _fresh(_PROBE)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "ALL_RESOLVED" in proc.stdout


def test_run_resolves_a_bundled_template_without_going_through_dispatch() -> None:
    proc = _fresh(_RUN_PROBE)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "unknown template" not in proc.stderr
    assert proc.stdout.startswith("STATUS unavailable"), proc.stdout
