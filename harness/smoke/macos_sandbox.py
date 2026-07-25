"""Fail-closed smoke for the real macOS Seatbelt kernel boundary.

This is intentionally not part of default pytest collection.  Scheduled CI
runs it on a GitHub-hosted macOS runner with ``OPENAI4S_KERNEL_SANDBOX=enforce``
so a missing/degraded sandbox is a hard failure rather than a warning.

The checks themselves live in ``sandbox_boundary`` and are shared with the
Linux/bubblewrap smoke: the promise does not depend on the backend, and two
copies of it would drift until one platform quietly stopped checking what the
other still did.
"""

from __future__ import annotations

import platform

from harness.smoke.sandbox_boundary import run_boundary_smoke


def main() -> int:
    if platform.system() != "Darwin":
        raise RuntimeError("macOS sandbox smoke must run on Darwin")
    return run_boundary_smoke(label="macos", expected_backend="seatbelt")


if __name__ == "__main__":
    raise SystemExit(main())
