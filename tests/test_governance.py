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

    # Every allowlisted fingerprint has to be argued for in review, so the count
    # is pinned here: adding one means editing this test. The six README rows are
    # star-history.com `sealed_token` values (three English, three Chinese) — an
    # encrypted wrapper around a metadata-read-only GitHub token, which
    # star-history decrypts per request and which is designed to be published in
    # a README. gitleaks flags them on entropy alone.
    # The last four are synthetic canaries in the redaction tests — two per
    # commit that touched those lines, since the history scan reports each
    # commit that introduces them.
    # They are deliberately high-entropy because the redaction they exercise
    # keys on entropy and shape — an obviously-fake string would make those
    # tests pass for the wrong reason, which is also why the vendor-prefix
    # rename alone could not satisfy the entropy-based rule. Suppressed by fingerprint rather
    # than by widening a path rule, so a real leak in the same file is still
    # caught, and pinned by count here so a new suppression cannot be added
    # without a reviewer seeing it.
    ignored = (ROOT / ".gitleaksignore").read_text(encoding="utf-8").splitlines()
    assert len(ignored) == 12
    assert all(
        re.fullmatch(r"[0-9a-f]{40}:.+:[a-z0-9-]+:\d+", item) for item in ignored
    )


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


def test_contributors_workflow_updates_protected_main_through_a_pr():
    workflow = (WORKFLOWS / "contributors.yml").read_text(encoding="utf-8")
    ci_workflow = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    uses = [line for line in workflow.splitlines() if line.lstrip().startswith("uses:")]

    # This workflow receives write access, so mutable action tags are not safe.
    assert uses
    assert all(PINNED_ACTION.fullmatch(line) for line in uses)

    for contract in (
        "contents: write",
        "pull-requests: write",
        "actions: write",
        "UPDATE_BRANCH: docs/community-contributors",
        "persist-credentials: false",
        "pillow-12.3.0-",
        "#sha256=78cb2c6865a35ab8ff8b75fd122f6033b92a62c82801110e48ddd6c936a45d91",
        "peter-evans/create-pull-request@",
        "base: main",
        "add-paths:",
        ".github/contributors/",
        "needs.update.outputs.pull-request-number != ''",
        "needs.update.outputs.pull-request-operation != 'closed'",
        "gh workflow run ci.yml",
        '--repo "$GITHUB_REPOSITORY"',
        '--ref "$UPDATE_BRANCH"',
    ):
        assert contract in workflow

    assert "[skip ci]" not in workflow
    assert not re.search(r"(?m)^\s*git push(?:\s|$)", workflow)
    assert "pull_request_target" not in workflow
    assert "workflow_dispatch:" in ci_workflow
