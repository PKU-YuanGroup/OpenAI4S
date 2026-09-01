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

**This is the independent full-boundary CI gate**, not the interrupt smoke.
`.github/workflows/ci.yml` runs it on Ubuntu 24.04 under
``OPENAI4S_KERNEL_SANDBOX=enforce`` with Ubuntu's restricted bwrap AppArmor
profile loaded, and **without** ``OPENAI4S_KERNEL_ALLOW_RAW_NETWORK``. The
interrupt job still allows raw networking so its private-PID evidence does not
depend on network-namespace setup; that exception is why a green interrupt
check is not this check. A raw-network override here is a hard failure.

The four assertions are: outside write denied, raw network denied, workspace
write allowed, child secret absent. The receipt the smoke prints names
``backend=bubblewrap``, ``enforced=true``, ``self_test_passed=true``. The
release quality receipt attests the check run at the frozen SHA
(``ci-linux-sandbox-full``). The release workflow's own ``platform-checks``
matrix still does **not** re-execute this smoke: it stays in
``PLATFORM_CHECKS_UNAVAILABLE`` until multiple scheduled runs plus a candidate
SHA are green. There is no ``continue-on-error``.

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
    return run_boundary_smoke(
        label="linux",
        expected_backend="bubblewrap",
        forbid_raw_network=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
