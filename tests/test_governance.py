"""Offline contracts for governance and security automation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PINNED_ACTION = re.compile(r"^\s*uses:\s*[^@\s]+@[0-9a-f]{40}(?:\s+#.*)?$")


# CodeQL scanning is provided by the repository's CodeQL default setup, not an
# advanced workflow file (the two are mutually exclusive on GitHub). Only the
# scorecard workflow is a repo-managed security scanner here.
@pytest.mark.parametrize("name", ["scorecard.yml"])
def test_security_scanners_pin_every_action_to_a_commit(name):
    lines = (WORKFLOWS / name).read_text(encoding="utf-8").splitlines()
    uses = [line for line in lines if line.lstrip().startswith("uses:")]

    assert uses
    assert all(PINNED_ACTION.fullmatch(line) for line in uses)
    assert all("pull_request_target" not in line for line in lines)


def test_gitleaks_scans_history_with_a_checksum_pinned_binary():
    workflow = (WORKFLOWS / "secret-scan.yml").read_text(encoding="utf-8")

    assert "fetch-depth: 0" in workflow
    assert 'GITLEAKS_VERSION: "8.30.1"' in workflow
    assert re.search(r'GITLEAKS_SHA256: "[0-9a-f]{64}"', workflow)
    assert "sha256sum --check --strict" in workflow
    assert "gitleaks git --redact --verbose" in workflow
    assert "pull_request_target" not in workflow

    ignored = (ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
    assert len(ignored) == 5
    assert all(
        re.fullmatch(r"[0-9a-f]{40}:.+:[a-z0-9-]+:\d+", item) for item in ignored
    )


def test_release_workflow_pins_every_action_to_a_commit():
    workflow = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    uses = [line for line in workflow.splitlines() if line.lstrip().startswith("uses:")]

    assert uses
    # No moving refs: the OIDC-privileged PyPI publish step must be SHA-pinned
    # like every other action so a mutable upstream branch cannot inject code.
    moving = [line for line in uses if not PINNED_ACTION.fullmatch(line)]
    assert moving == []


def test_dependabot_tracks_uv_hooks_and_workflow_actions():
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

    assert config.count("package-ecosystem:") == 3
    for ecosystem in ('"uv"', '"pre-commit"', '"github-actions"'):
        assert f"package-ecosystem: {ecosystem}" in config
