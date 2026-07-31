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


def test_credential_scanning_is_a_working_tree_scan_not_a_history_scan():
    """The Gitleaks history scan is gone; this pins what carries the load now.

    Not removed for being red. #57 had made it pass, moving suppression off
    `<commit>:<file>:<rule>:<line>` fingerprints -- which squash-only merging
    duplicates out from under you -- and onto anchored `regexTarget = "secret"`
    values that survive a rewrite. That fix worked.

    It was removed because of the cost that fix could not touch. A generic
    entropy rule over *all history* fires on synthetic fixtures, and a fixture
    that has to look real in order to be found by the code under test is
    exactly the kind this repository keeps needing. Each one becomes another
    allowlist row a reviewer must argue for, and the list only grows: #57
    curated two values, then #63 added a third within the day -- for a string
    that the working tree already suppressed inline, because an inline comment
    cannot cover the commit that introduced the line before the comment
    existed. Every such suppression is correct and none of them is free.

    `scripts/source_secret_scan.py` keeps the property that mattered: named
    provider detectors (AWS, GitHub, OpenAI, Google, Slack, Stripe, private
    keys) instead of an entropy heuristic, so a placeholder in a fixture is not
    a finding while a real key pasted into that same file still is -- with no
    list to curate. It reads the working tree, which is where a leak has to be
    fixed regardless of which commit introduced it. CodeQL is untouched.

    What is given up, stated plainly rather than left implicit: a credential
    that was committed and later removed is no longer detected. If that matters
    again, run gitleaks over history once by hand -- do not reinstate a
    scheduled job with an allowlist to feed.
    """
    workflow = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    assert "python scripts/source_secret_scan.py" in workflow
    for gone in ("secret-scan.yml", ".gitleaksignore", ".gitleaks.toml"):
        assert not (ROOT / gone).exists() and not (WORKFLOWS / gone).exists()


def test_release_workflow_pins_every_action_to_a_commit():
    workflow = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    uses = [line for line in workflow.splitlines() if line.lstrip().startswith("uses:")]

    assert uses
    # Every action is SHA-pinned so a mutable upstream branch cannot inject code.
    # The one documented exception is pypa/gh-action-pypi-publish: it is a
    # Docker-container action whose image PyPA publishes tagged by RELEASE ref
    # only (never by commit SHA), so a SHA pin fails the image pull with
    # `manifest unknown` before the OIDC exchange starts. It must stay on PyPA's
    # documented `release/v1` image-backed ref — and nothing else may move.
    moving = [
        line
        for line in uses
        if not PINNED_ACTION.fullmatch(line)
        and line.strip() != "uses: pypa/gh-action-pypi-publish@release/v1"
    ]
    assert moving == []


def test_dependabot_tracks_uv_hooks_and_workflow_actions():
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")

    assert config.count("package-ecosystem:") == 3
    for ecosystem in ('"uv"', '"pre-commit"', '"github-actions"'):
        assert f"package-ecosystem: {ecosystem}" in config


def test_branch_naming_policy_exempts_dependabot_by_ref_not_by_actor():
    """The exemption has to key on the branch, because the actor changes.

    `github.actor` is whoever triggered the *latest* run, not who opened the
    PR. Clicking "Update branch" on a Dependabot PR — which a strict
    up-to-date ruleset forces for every Dependabot PR after the first merge —
    makes the maintainer the actor, so an actor-based exemption stops
    applying and this required check fails a `dependabot/uv/...` branch name
    it was never meant to judge. That renders Dependabot PRs unmergeable
    without an admin bypass, which is how it went unnoticed: the exemption
    looks correct until the day someone needs to update a branch.
    """
    workflow = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    condition = next(
        line
        for line in workflow.splitlines()
        if line.lstrip().startswith("if: github.event_name == 'pull_request'")
    )

    assert "startsWith(github.head_ref, 'dependabot/')" in condition
    assert "github.actor" not in condition
