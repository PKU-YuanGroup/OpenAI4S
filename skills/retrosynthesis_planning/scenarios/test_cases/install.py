"""One-command installer for a bundled Scenario evaluation case."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from retrosynthesis_planning.gt_codebase import install_cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(install_cli())
