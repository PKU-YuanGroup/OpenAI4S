"""Private-side evaluator entrypoint for an installed Scenario case."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from retrosynthesis_planning.gt_codebase import evaluator_cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(evaluator_cli())
