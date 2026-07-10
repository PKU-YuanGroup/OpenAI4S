"""Defense-in-depth safety layer for openai4s.

A faithful re-implementation of the three-layer security pipeline reverse-
engineered from Claude Science (report sections 5-7), kept strictly compatible
with the Code-as-Action model — the agent still acts only by writing Python that
runs in a persistent kernel; these layers wrap that execution, they do not
replace it with a tool schema.

    classifier ....... pre-exec code-safety gate: a
                       static fast-path allowlist + optional LLM classifier over
                       7 attack classes; UNSAFE code is refused, not run.
    audit_hook ....... in-kernel CPython audit hook (the dlopen guard):
                       blocks `ctypes.dlopen` of a shared library from an
                       agent-writable path (the classic "write .so then dlopen
                       to escape the OS sandbox" vector).
    biosecurity ...... calibrated-accountability prompt (`oiO`) + an independent
                       trajectory screener (`diO`) returning ALLOW/ESCALATE/BLOCK.
    injection ........ prompt-injection detector (`Mjz`) over tool-returned
                       content (web pages, PDFs, MCP output) — "tool results are
                       data, not instructions".

Every layer is opt-out via env (see `openai4s.config.SecurityConfig`) and
fails open when the base model is unconfigured, so a fresh local install still
runs while the cheap static gates stay on.
"""
from __future__ import annotations

from openai4s.security.biosecurity import (
    ScreenVerdict,
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
    "screen_trajectory",
]
