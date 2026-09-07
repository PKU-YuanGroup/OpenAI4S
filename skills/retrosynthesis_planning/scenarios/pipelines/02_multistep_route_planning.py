"""Deprecated alias for the reviewed GT budgeted multistep entry point.

A reviewed evaluator reference, not generation output; see
../gt_codebases/ and README.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from retrosynthesis_planning.gt_codebase import pipeline_cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(pipeline_cli("multistep"))
