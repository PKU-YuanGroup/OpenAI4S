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

**This is a manual smoke, not a CI job.** It was scheduled nightly and failed
every night: a GitHub-hosted runner confines unprivileged user namespaces, so
bwrap creates its network namespace and then cannot bring up loopback inside it
(``bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted``). Nothing in
this repository can fix that from inside the runner, and a check that cannot
pass is not evidence -- it just trains people to ignore red. docs/platforms.md
now states the Linux tier as verified-by-hand rather than pointing at a job
that was always failing. Restoring it to CI needs a host that permits those
namespaces (self-hosted runner, or a suitably privileged container).

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
