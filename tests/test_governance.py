"""Offline contracts for governance and security automation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# `- uses:` is as common as `uses:` in these files, and a pattern anchored on
# the bare form collects nothing at all from a workflow written in list style:
# ci.yml's 42 pins were invisible to this module until the `-?` went in.
USES_LINE = re.compile(r"^\s*-?\s*uses:")

# The trailing version comment is REQUIRED, not optional. A 40-hex SHA is
# unreadable, so `# vX.Y.Z` is the only part of the pin a reviewer actually
# reads -- leaving it optional let an action merge with no human-readable
# identity at all, and let a bumped SHA keep a stale comment that tells every
# future reader the wrong version.
#
# What this deliberately does NOT claim: that the comment names the SHA it sits
# beside. Dereferencing a tag needs the network and this suite is offline by
# design, so the identity check stays a human step -- what is mechanised here
# is that there is always a claim to check.
PINNED_ACTION = re.compile(r"^\s*-?\s*uses:\s*[^@\s]+@[0-9a-f]{40}\s+#\s*\S.*$")

# The one documented exception, named once so every test below agrees on it.
# pypa/gh-action-pypi-publish is a Docker-container action whose image PyPA
# publishes tagged by RELEASE ref only (never by commit SHA), so a SHA pin
# fails the image pull with `manifest unknown` before the OIDC exchange starts.
UNPINNABLE_ACTIONS = frozenset({"uses: pypa/gh-action-pypi-publish@release/v1"})


def _uses_lines(name):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if USES_LINE.match(line)]


# CodeQL scanning is provided by the repository's CodeQL default setup, not an
# advanced workflow file (the two are mutually exclusive on GitHub). Only the
# scorecard workflow is a repo-managed security scanner here.
@pytest.mark.parametrize("name", ["scorecard.yml"])
def test_security_scanners_pin_every_action_to_a_commit(name):
    lines = (WORKFLOWS / name).read_text(encoding="utf-8").splitlines()
    uses = _uses_lines(name)

    assert uses
    assert all(PINNED_ACTION.fullmatch(line) for line in uses)
    assert all("pull_request_target" not in line for line in lines)


def test_every_workflow_pins_every_action_to_a_commit():
    """The sweep the two scoped tests above cannot perform.

    Those cover scorecard.yml and release.yml. ci.yml -- 42 `uses:` lines, the
    file every contributor's code and every fork PR passes through -- and
    publish-image.yml were pinned by convention only, named by no test at all.
    Dependabot's `workflow-actions` group rewrites `uses:` lines in all four,
    so a grouped bump landing a mutable tag in an uncovered file reads exactly
    like a covered one and passes every gate.

    Discovery is a glob rather than a list so a workflow added later is covered
    the day it lands, instead of the day someone remembers to extend a
    parametrize.
    """
    workflows = sorted(
        p.name for p in [*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]
    )
    assert workflows

    moving = {}
    for name in workflows:
        offenders = [
            line.strip()
            for line in _uses_lines(name)
            if not PINNED_ACTION.fullmatch(line)
            and line.strip() not in UNPINNABLE_ACTIONS
        ]
        if offenders:
            moving[name] = offenders

    assert moving == {}


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
    uses = _uses_lines("release.yml")

    assert uses
    # Every action is SHA-pinned so a mutable upstream branch cannot inject code.
    # The one documented exception is pypa/gh-action-pypi-publish, and the
    # reason it cannot be pinned is recorded beside UNPINNABLE_ACTIONS above.
    # It must stay on PyPA's documented `release/v1` image-backed ref — and
    # nothing else may move.
    moving = [
        line
        for line in uses
        if not PINNED_ACTION.fullmatch(line) and line.strip() not in UNPINNABLE_ACTIONS
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
