"""Offline contracts for governance and security automation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PINNED_ACTION = re.compile(r"^\s*uses:\s*[^@\s]+@[0-9a-f]{40}(?:\s+#.*)?$")


def test_private_security_process_and_conduct_policy_are_discoverable():
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    conduct = (ROOT / "CODE_OF_CONDUCT.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "security/advisories/new" in security
    assert "Do not open a public issue" in security
    assert "Reporting and enforcement" in conduct
    assert "SECURITY.md" in readme
    assert "CODE_OF_CONDUCT.md" in readme


@pytest.mark.parametrize("name", ["codeql.yml", "scorecard.yml"])
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


def test_release_publish_action_is_the_only_documented_moving_action_ref():
    workflow = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    uses = [line for line in workflow.splitlines() if line.lstrip().startswith("uses:")]

    moving = [line for line in uses if not PINNED_ACTION.fullmatch(line)]
    assert moving == ["        uses: pypa/gh-action-pypi-publish@release/v1"]


def test_dependabot_tracks_uv_hooks_and_workflow_actions():
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

    assert config.count("package-ecosystem:") == 3
    for ecosystem in ('"uv"', '"pre-commit"', '"github-actions"'):
        assert f"package-ecosystem: {ecosystem}" in config
