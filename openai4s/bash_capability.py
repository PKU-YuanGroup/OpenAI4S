"""Language-neutral wire constants for one-shot shell capabilities.

This tiny module is imported by both the Host issuer and the kernel-side SDK.
Keeping it outside ``openai4s.host`` prevents a worker import from executing the
host service package's composition imports.
"""

from __future__ import annotations

import hashlib
import shlex
from pathlib import Path

CAPABILITY_VERSION = "openai4s-bash-capability-v1"


def command_digest(command: str) -> str:
    """Return the canonical SHA-256 binding for a shell command string."""

    return hashlib.sha256(command.encode("utf-8", errors="surrogatepass")).hexdigest()


def command_preserves_failure_status(command: str) -> bool:
    """Whether obvious shell composition cannot mask an earlier failure.

    Test evidence is allowed to cite an exact successful ``host.bash`` command,
    but shell constructs such as ``pytest; true`` and ``pytest | cat`` replace
    the test process's exit status.  Parse operators outside quotes and reject
    the masking forms.  ``&&`` is retained: every command in that chain must
    succeed for the shell to return zero.  Explicit shell ``-c`` wrappers are
    checked recursively so quoting the compound command is not a bypass.
    """

    if not isinstance(command, str) or not command.strip():
        return False
    # Newlines are command separators to the shell even though ``shlex``
    # reports them as ordinary whitespace. Command/process substitution can
    # likewise hide a failing command inside an otherwise successful simple
    # command, so this evidence boundary refuses those forms conservatively.
    if "\n" in command or "\r" in command or "$(" in command or "`" in command:
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False
    punctuation = set("();<>|&")
    redirections = {"<", ">", "<<", ">>", "<<<", "<>", ">|", "<&", ">&", "&>", "&>>"}
    operators = [
        token for token in tokens if token and set(token).issubset(punctuation)
    ]
    if any(token != "&&" and token not in redirections for token in operators):
        return False
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not words:
        return False
    if any(Path(word).name.lower() == "eval" for word in words):
        return False
    shells = {"bash", "dash", "ksh", "sh", "zsh"}
    for index, word in enumerate(words[:-1]):
        if Path(word).name.lower() not in shells:
            continue
        for option_index in range(index + 1, len(words) - 1):
            option = words[option_index]
            if option == "--":
                continue
            if option.startswith("-") and "c" in option[1:]:
                return command_preserves_failure_status(words[option_index + 1])
            if not option.startswith("-"):
                break
    return True


__all__ = [
    "CAPABILITY_VERSION",
    "command_digest",
    "command_preserves_failure_status",
]
