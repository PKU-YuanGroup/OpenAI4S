#!/usr/bin/env python3
"""Fail closed on credential-shaped material in the release source tree.

The scanner is intentionally dependency-free and reports only detector names,
paths, and line numbers; it never echoes a matching secret.  Git is used only
to select tracked and non-ignored candidate files.  A deterministic filesystem
fallback keeps source archives and minimal release environments scannable.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    detector: str


_TEXT_DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    (
        "openai-api-key",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b"),
    ),
    (
        "ark-api-key",
        re.compile(r"\bark-[0-9a-z](?:[0-9a-z-]{30,})\b", re.IGNORECASE),
    ),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    (
        "slack-token",
        re.compile(r"\bxox(?:b|p|a|r|s)-[0-9A-Za-z-]{20,}\b"),
    ),
    (
        "stripe-live-key",
        re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}\b"),
    ),
)

_FORBIDDEN_FILENAMES = frozenset(
    {".env", ".npmrc", ".pypirc", "credentials.json", "service-account.json"}
)
_ALLOWED_ENV_TEMPLATES = frozenset({".env.example", ".env.sample", ".env.template"})
_FORBIDDEN_SUFFIXES = frozenset({".key", ".p12", ".pfx"})
_EXCLUDED_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
    }
)
_MAX_TEXT_BYTES = 8 * 1024 * 1024
_SYNTHETIC_TOKEN = re.compile(
    r"(?:^|[-_])(?:dummy|example|fake|synthetic|test)(?:[-_]|$)",
    re.IGNORECASE,
)


def _git_candidates(root: Path) -> list[Path] | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [
        root / Path(raw.decode("utf-8", "surrogateescape"))
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def candidate_files(root: Path) -> list[Path]:
    """Return regular, non-symlink release candidates in stable order."""

    root = root.resolve()
    candidates = _git_candidates(root)
    if candidates is None:
        candidates = list(root.rglob("*"))
    selected: list[Path] = []
    for path in candidates:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in _EXCLUDED_PARTS for part in relative.parts):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        selected.append(path)
    return sorted(set(selected), key=lambda value: value.as_posix())


def scan(root: Path) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    for path in candidate_files(root):
        relative = path.relative_to(root).as_posix()
        lower_name = path.name.casefold()
        if (
            lower_name in _FORBIDDEN_FILENAMES
            or (
                lower_name.startswith(".env.")
                and lower_name not in _ALLOWED_ENV_TEMPLATES
            )
            or path.suffix.casefold() in _FORBIDDEN_SUFFIXES
        ):
            findings.append(Finding(relative, 0, "credential-file"))
        try:
            payload = path.read_bytes()
        except OSError:
            findings.append(Finding(relative, 0, "unreadable-file"))
            continue
        if len(payload) > _MAX_TEXT_BYTES or b"\0" in payload[:8192]:
            continue
        text = payload.decode("utf-8", "replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for detector, pattern in _TEXT_DETECTORS:
                matches = pattern.finditer(line)
                if any(
                    _SYNTHETIC_TOKEN.search(match.group(0)) is None for match in matches
                ):
                    findings.append(Finding(relative, line_number, detector))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository or unpacked source root (default: project root)",
    )
    args = parser.parse_args(argv)
    findings = scan(args.root)
    if findings:
        for finding in findings:
            location = (
                f"{finding.path}:{finding.line}" if finding.line else finding.path
            )
            print(f"{location}: {finding.detector}", file=sys.stderr)
        print(
            f"source secret scan failed with {len(findings)} finding(s); matched values were suppressed",
            file=sys.stderr,
        )
        return 1
    print(f"source secret scan passed ({len(candidate_files(args.root))} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
