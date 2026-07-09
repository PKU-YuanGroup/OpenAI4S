"""Bash tool: run a shell command through the HostDispatcher.

Execution routes to the host `bash` method, which runs in the session
workspace and is itself wrapped by the permission gate + egress fence. The
`precheck_command` here is a cheap, static, best-effort defense-in-depth gate
that SUPPLEMENTS (never replaces) those runtime layers and the code
classifier: it refuses a handful of obviously-catastrophic literal commands
before they are ever dispatched. It is not a sandbox and does not attempt to
defeat obfuscation.
"""
from __future__ import annotations

import re

from openai4s.tools.base import Tool

bash = Tool(
    name="bash",
    host_method="bash",
    description="Run a shell command in the session workspace (networking available).",
    parameters={
        "properties": {
            "command": {"type": "string", "description": "Shell command to run."},
            "timeout": {
                "type": "number",
                "description": "Seconds before the command is killed (default 120).",
            },
            "workdir": {
                "type": "string",
                "description": "Working directory, relative to the workspace.",
            },
        },
        "required": ["command"],
    },
    read_only=False,
    dangerous=True,
    mutates_cwd=True,
    needs_network=True,
)


# Cheap static blocklist. Each entry is (compiled regex, human reason). These
# match a few unambiguous catastrophes only; anything subtler is left to the
# runtime permission gate, the egress fence, and the code classifier.
_DANGEROUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    # recursive delete aimed at the filesystem root, home, or $HOME
    (
        re.compile(
            r"\brm\b"
            # Require an actual recursive short/long option somewhere before
            # the target, while allowing other GNU-style options around it.
            r"(?=[^\n;|&]*\s(?:-[A-Za-z]*[rR][A-Za-z]*|--recursive)(?:\s|$))"
            r"(?:\s+(?:--|--[\w-]+(?:=[^\s;|&]+)?|-[\w-]+))*"
            r"\s+['\"]?(?:/|~|\$HOME|\$\{HOME\})['\"]?"
            r"(?:\s|/|\*|;|&|\||$)"  # boundary (allow /*, trailing sep, or EOL)
        ),
        "recursive delete targeting '/', '~', or $HOME",
    ),
    # format a filesystem
    (re.compile(r"\bmkfs\b", re.IGNORECASE), "mkfs would format a filesystem"),
    # dd writing straight to a device
    (
        re.compile(r"\bdd\b[^\n]*\bof=/dev/", re.IGNORECASE),
        "dd writing directly to a device",
    ),
    # overwrite a raw block device via redirection
    (
        re.compile(
            r">\s*/dev/(?:sd[a-z]|nvme\d+n\d+|hd[a-z]|vd[a-z]|disk\d+)", re.IGNORECASE
        ),
        "overwrite of a raw block device",
    ),
    # world-writable permissions on the filesystem root
    (
        re.compile(
            r"\bchmod\b\s+(?:-\w+\s+)*(?:0?777|a\+rwx|\+rwx)\s+"
            r"(?:--\s+)?['\"]?/(?:\*|\s|['\"]|$)",
            re.IGNORECASE,
        ),
        "chmod 777 on '/'",
    ),
    # classic shell fork bomb  :(){ :|:& };:
    (
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:?\s*&\s*\}\s*;\s*:"),
        "shell fork bomb",
    ),
    # piping a remote download straight into a shell interpreter
    (
        re.compile(
            r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?"
            r"(?:(?:/usr)?/bin/)?(?:ba|z|d|k|c|tc|fi|a)?sh\b",
            re.IGNORECASE,
        ),
        "piping a downloaded script directly into a shell",
    ),
]


def precheck_command(command: str) -> str | None:
    """Return the first matched danger reason for `command`, else None.

    Pure re/stdlib and total: any non-string or unexpected input yields None
    (fail-open) so this can never break a call — the runtime layers remain the
    real enforcement.
    """
    try:
        if not isinstance(command, str) or not command:
            return None
        for rx, reason in _DANGEROUS_PATTERNS:
            if rx.search(command):
                return reason
    except Exception:
        return None
    return None
