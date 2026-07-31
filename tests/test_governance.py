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
    # Passed explicitly, so a renamed or missing config is a loud error rather
    # than a silent fallback to gitleaks' built-in defaults.
    assert "--config .gitleaks.toml" in workflow


def _allowlisted_regexes(config: str) -> list[str]:
    """Every string literal in the allowlist's ``regexes`` list, any TOML style.

    Deliberately not a pattern over one quoting style. The first version of
    this helper matched only ``'''^…$'''`` on its own line, and an adversarial
    review showed that adding ``".*",`` in ordinary double quotes left all
    three assertions below passing while gitleaks permitted everything — a real
    ``sk-live-…`` went from ``leaks found: 1`` to ``no leaks found`` under that
    config. A pin that only sees the spelling it expects is not a pin.
    """
    block = re.search(r"regexes\s*=\s*\[(.*?)^\]", config, re.DOTALL | re.MULTILINE)
    assert block, "no `regexes = [...]` list found in .gitleaks.toml"
    body = "\n".join(
        line
        for line in block.group(1).splitlines()
        if not line.lstrip().startswith("#")
    )
    literal = re.compile(
        r"'''(.*?)'''|\"\"\"(.*?)\"\"\"|'([^']*)'|\"([^\"]*)\"", re.DOTALL
    )
    return [
        next(g for g in m.groups() if g is not None) for m in literal.finditer(body)
    ]


def test_gitleaks_config_extends_the_default_rules_rather_than_replacing_them():
    """The one mistake in this file that would look exactly like success.

    A config without ``[extend] useDefault = true`` REPLACES the default rule
    set instead of extending it. The scan then finds nothing and exits 0 —
    which is indistinguishable from a scan that works, so it would be believed.
    Hence this assertion, and hence the config landed in its own commit with no
    allowlist first (run 30568183935: ``535 commits scanned``, ``leaks found:
    4``) to prove the rules were still loaded before anything was permitted.
    """
    config = (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")

    assert "[extend]" in config
    assert re.search(r"^useDefault\s*=\s*true$", config, re.MULTILINE)

    # Every permitted value has to be argued for in review, so the set is
    # pinned exactly: adding one means editing this test. Both are synthetic
    # fixtures that exist in order to be *found* by the code under test — a
    # redaction that keys on entropy cannot be exercised by an obviously-fake
    # string, so these have to look real enough to trip the scanner.
    #
    # `regexTarget = "secret"` with anchored patterns is what keeps this from
    # becoming a path rule: only these literal values are permitted, so a real
    # credential in the same files still fails the scan. tests/
    # test_retrieval_source.py states the reason — a scanner that made an
    # exception for test files would be a scanner with a hole exactly where
    # people paste real keys "just to check".
    assert 'regexTarget = "secret"' in config
    assert config.count("[[allowlists]]") == 1
    permitted = _allowlisted_regexes(config)
    assert permitted == [
        "^abc123def456ghi789$",
        "^Zx9Qw3Er7Ty1Ui5Op2As6Df4Gh8Jk0Lm$",
        "^sk-ABCDEFGH1234567890$",
    ]
    # Anchored on both ends, so a permitted value cannot become a prefix rule
    # that admits `abc123def456ghi789<real-key>`.
    assert all(p.startswith("^") and p.endswith("$") for p in permitted)
    # No path/file/commit widening: those keys would suppress findings this
    # allowlist has not individually accounted for.
    for widening in ("paths", "files", "commits", "stopwords"):
        assert not re.search(rf"^\s*{widening}\s*=", config, re.MULTILINE)


def test_gitleaksignore_holds_only_fingerprints_that_squashing_cannot_duplicate():
    """Fingerprint suppression and squash-only merging do not compose.

    A `.gitleaksignore` entry names `<commit>:<file>:<rule>:<line>`, and
    `protect-main` permits only squash and rebase merges. Squashing does not
    destroy the branch commits — they stay reachable on `next` — it *copies*
    their findings under a new SHA on `main`. The old fingerprint keeps
    suppressing the old commit and nothing suppresses the new one, so `main`
    goes red on a tree nobody changed. That is what happened when #52 landed:
    four v0.3 findings reappeared under `f2d8adb…` while their originals stayed
    suppressed under `8d715ebe…` and `3dcda11f…`.

    Suppressing by value in `.gitleaks.toml` covers original and copy at once,
    which is why those four rows could be dropped from here — not because they
    were dead. They were live; they were merely insufficient.

    The count stays pinned so a new fingerprint — which would be a fresh
    instance of the same duplicating bug — cannot be added without a reviewer
    seeing it. Of the twelve: six are star-history.com `sealed_token` values in
    the two READMEs (three English, three Chinese), an encrypted wrapper around
    a metadata-read-only GitHub token that is designed to be published and that
    gitleaks flags on entropy alone. Four are synthetic canaries in the
    redaction tests (`test_diagnostics.py`, `test_observability.py`), reported
    once per commit that touched those lines. The last two are a planning
    document (`docs/refactor-plan.md`) and an `NGC_API_KEY` fixture in
    `test_compute_nvidia.py`.
    """
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
