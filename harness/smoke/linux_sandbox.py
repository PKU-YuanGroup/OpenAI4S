"""Fail-closed smoke for the real Linux bubblewrap kernel boundary.

The frozen platform matrix (docs/v02-decisions.md, 8.5) puts Linux at beta
"after enforced bubblewrap E2E", and the consequence column is explicit that
the tier is gated on a real enforced-sandbox smoke test, **not on a probe that
degrades**. macOS had one; Linux did not, so the tier it was being given rested
on nothing.

Run with ``OPENAI4S_KERNEL_SANDBOX=enforce``, so a missing or degraded
bubblewrap is a hard failure rather than the warning a developer install
prints. It asserts the backend really is bubblewrap: a run that fell back to
something else and still passed would be reporting on a boundary it never
tested.

**This is a manual smoke, not a CI job.** Its former hosted run failed during
network-namespace setup and was removed because a permanently red check was not
evidence. The newer targeted interrupt job loads Ubuntu's restricted bwrap
AppArmor profile, which may change that old result, but this complete boundary
smoke has not yet been re-evaluated under that setup. docs/platforms.md therefore
keeps the broad Linux tier verified-by-hand instead of inferring it from the
raw-network interrupt gate.

Deliberately not in default pytest collection -- it requires `bwrap`, which a
laptop may not have, and a check that quietly skips is the thing the frozen
decision refuses.
"""

from __future__ import annotations

import platform

from harness.smoke.sandbox_boundary import run_boundary_smoke


def main() -> int:
    if platform.system() != "Linux":
        raise RuntimeError("Linux sandbox smoke must run on Linux")
    return run_boundary_smoke(label="linux", expected_backend="bubblewrap")


if __name__ == "__main__":
    raise SystemExit(main())
