"""Defense-in-depth safety layer for openai4s.

Kept strictly compatible with the Code-as-Action model — the agent still acts
only by writing Python or R that runs in a persistent kernel; these layers wrap
that execution, they do not replace it with a tool schema. The classifier,
injection and biosecurity trio began as a re-implementation of the three-layer
pipeline reverse-engineered from Claude Science (report sections 5-7); the
package has since grown past it in both directions.

Six of the layers are about a Cell:

    classifier ....... pre-exec code-safety gate: a static fast-path allowlist,
                       high-confidence attack signatures, and an optional LLM
                       classifier over 7 attack classes; UNSAFE code is
                       refused, not run.
    sandbox .......... the worker's OS confinement (Seatbelt on macOS,
                       bubblewrap on Linux), proven by a real deny/allow
                       self-test and reported as a measured status rather than
                       inferred from configuration.
    audit_hook ....... in-kernel CPython audit hook (the dlopen guard):
                       blocks `ctypes.dlopen` of a shared library from an
                       agent-writable path (the classic "write .so then dlopen
                       to escape the OS sandbox" vector).
    shellcheck ....... a small static blocklist in front of kernel-local
                       `host.bash`; unambiguously catastrophic literals only,
                       and explicitly not a shell parser.
    injection ........ prompt-injection detector (`Mjz`) over tool-returned
                       content (web pages, PDFs, MCP output) — "tool results are
                       data, not instructions". It annotates; it never deletes.
    biosecurity ...... calibrated-accountability prompt (`oiO`) + an independent
                       trajectory screener (`diO`) returning ALLOW/ESCALATE/BLOCK.

Two further boundaries live here that are not about a Cell at all:

    byoc_confinement . the OS boundary around the BYOC provider helper, shaped
                       the other way round from the kernel's: that helper must
                       reach the network and must not read the user's home.
    permissions +      credential storage — owner-only modes on the data
    secret_broker +    directory, then secrets behind an opaque reference in
    secret_migration   the system keychain or the process environment.

Failure behaviour is per layer, and reading one off the list above is the
mistake to avoid. The LLM-backed gates (classifier, injection, biosecurity) are
opt-out via env (see `openai4s.config.SecurityConfig`) and fail open when the
base model is unconfigured, so a fresh local install still runs while the cheap
static checks stay on — whereas `OPENAI4S_KERNEL_SANDBOX=enforce` and every
SecretBroker mode fail *closed*. README.md carries the full enforcement and
failure matrix.

Only the three verdict APIs used from outside the package are re-exported here;
the rest are imported from their own modules.
"""

from __future__ import annotations

from openai4s.security.biosecurity import (
    ScreenVerdict,
    gather_trajectory,
    looks_biosecurity_relevant,
    screen_trajectory,
)
from openai4s.security.classifier import Verdict, classify_code, is_always_safe
from openai4s.security.injection import InjectionVerdict, scan_tool_result

__all__ = [
    "Verdict",
    "classify_code",
    "is_always_safe",
    "InjectionVerdict",
    "scan_tool_result",
    "ScreenVerdict",
    "looks_biosecurity_relevant",
    "gather_trajectory",
    "screen_trajectory",
]
