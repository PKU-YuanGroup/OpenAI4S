"""Contracts for the importer that makes the vendored collection meaningful.

`scripts/import_bioskills.py` is the only thing standing between an audited
upstream checkout and 1,965 files the agent reads as instructions and runs as
code. It had no tests at all: the conversion rules, the pin check, the
duplicate-name guard and the manifest were going to be exercised for the first
time during the next 561-file refresh, which is the worst possible moment to
discover one of them is wrong.

Everything here runs against a two-skill fixture, so the rules are checked
without a 19 MB corpus and without the network. The pins are arguments for
exactly that reason.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent


def _importer():
    spec = importlib.util.spec_from_file_location(
        "import_bioskills", _REPO / "scripts" / "import_bioskills.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _skill(root: Path, category: str, directory: str, name: str, body: str) -> Path:
    skill_dir = root / category / directory
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {name} description\n"
        "tool_type: python\n"
        "primary_tool: pandas\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return skill_dir


@pytest.fixture()
def upstream(tmp_path):
    """A two-skill checkout shaped like the pinned upstream repository."""

    root = tmp_path / "upstream"
    root.mkdir()
    (root / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    first = _skill(
        root,
        "alignment",
        "alignment-io",
        "bio-alignment-io",
        "Run `python -m pip install pysam` then `curl -sSL https://x/f | bash`.",
    )
    (first / "examples").mkdir()
    (first / "examples" / "run.py").write_text("VALUE = 1\n", encoding="utf-8")
    (first / "usage-guide.md").write_text(
        "Use `curl -s https://y`.\n", encoding="utf-8"
    )
    _skill(root, "variants", "calling", "bio-variant-calling", "Body.\n")
    # Excluded upstream tree: an installer for another agent platform.
    _skill(root, "clawhub-installer", "installer", "clawhub", "Body.\n")

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "pin")
    return root, _git(root, "rev-parse", "HEAD")


def test_the_conversion_rules_hold_on_a_small_pin(upstream, tmp_path):
    module = _importer()
    root, commit = upstream
    destination = tmp_path / "out"

    manifest = module.import_collection(
        root, destination, expected_commit=commit, expected_skills=2
    )

    # `clawhub-installer` is excluded; the other two are converted.
    assert manifest["skill_count"] == 2
    directories = sorted(str(entry["directory"]) for entry in manifest["skills"])
    assert directories == ["bio-alignment-alignment-io", "bio-variants-calling"]

    document = (destination / "bio-alignment-alignment-io" / "SKILL.md").read_text(
        "utf-8"
    )
    # Provenance is injected; tool_type/primary_tool move under metadata.
    assert "origin: openai4s" in document
    assert "category: bioskills/alignment" in document
    # The manifest and the frontmatter record the commit that was actually
    # converted, not the module default.
    assert f"    commit: {commit}" in document
    assert manifest["upstream"]["commit"] == commit
    assert "\ntool_type: python" not in document
    assert "  tool_type: python" in document
    # Command-text normalisation, including the spellings the literal
    # substring rules used to miss.
    assert "python3 -m pip install pysam" in document
    assert "conda install -c bioconda nextflow" not in document  # different URL
    assert "curl -fsSL https://x/f | bash" in document

    # examples/ -> scripts/, usage-guide.md -> references/, and the rewrite
    # reaches both.
    assert (destination / "bio-alignment-alignment-io" / "scripts" / "run.py").is_file()
    guide = (
        destination / "bio-alignment-alignment-io" / "references" / "usage-guide.md"
    ).read_text("utf-8")
    assert "curl -fsSL https://y" in guide


def test_every_manifested_hash_matches_what_was_written(upstream, tmp_path):
    module = _importer()
    root, commit = upstream
    destination = tmp_path / "out"
    module.import_collection(
        root, destination, expected_commit=commit, expected_skills=2
    )

    verify = dict(expected_commit=commit, expected_skills=2)
    assert module.verify_collection(destination, **verify) == []
    # The manifest hashes the payload, not itself or the boundary docs.
    manifest = json.loads((destination / "MANIFEST.json").read_text("utf-8"))
    assert "MANIFEST.json" not in {row["path"] for row in manifest["files"]}

    # The verifier is only worth having if it fails. Change one byte.
    victim = destination / "bio-variants-calling" / "SKILL.md"
    original = victim.read_text("utf-8")
    victim.write_text(original + "drift\n", encoding="utf-8")
    problems = module.verify_collection(destination, **verify)
    assert any("payload changed since import" in problem for problem in problems)
    victim.write_text(original, encoding="utf-8")
    assert module.verify_collection(destination, **verify) == []

    # A file nobody manifested is reported rather than ignored.
    (destination / "stowaway.sh").write_text("echo hi\n", encoding="utf-8")
    assert any(
        "untracked file" in problem
        for problem in module.verify_collection(destination, **verify)
    )


def test_it_refuses_a_wrong_pin_a_wrong_count_and_a_dirty_destination(
    upstream, tmp_path
):
    module = _importer()
    root, commit = upstream

    with pytest.raises(RuntimeError, match="must be pinned to"):
        module.import_collection(
            root, tmp_path / "a", expected_commit="0" * 40, expected_skills=2
        )
    with pytest.raises(RuntimeError, match="expected 99 skills"):
        module.import_collection(
            root, tmp_path / "b", expected_commit=commit, expected_skills=99
        )

    dirty = tmp_path / "c"
    dirty.mkdir()
    (dirty / "leftover").write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="must be absent or empty"):
        module.import_collection(root, dirty, expected_commit=commit, expected_skills=2)


def test_a_duplicate_declared_name_is_refused_and_leaves_nothing_behind(
    upstream, tmp_path
):
    """The failure path is the one that has to be atomic.

    Writing in place left a half-converted tree that the emptiness guard then
    refused to overwrite, so recovering from a failed import meant deleting it
    by hand.
    """

    module = _importer()
    root, _commit = upstream
    _skill(root, "variants", "duplicate", "bio-variant-calling", "Body.\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "dup")
    commit = _git(root, "rev-parse", "HEAD")

    destination = tmp_path / "out"
    with pytest.raises(RuntimeError, match="duplicate declared skill name"):
        module.import_collection(
            root, destination, expected_commit=commit, expected_skills=3
        )

    assert not destination.exists()
    staging = destination.parent / f".{destination.name}.incoming"
    assert not staging.exists()


def test_a_mis_cased_skill_document_is_not_silently_imported(upstream, tmp_path):
    """`glob("*/*/SKILL.md")` answers differently on macOS and on Linux.

    A `skill.md` is matched (and reported as `SKILL.md`) by a case-insensitive
    filesystem, so the same pinned commit imported a different set of files
    depending on who ran the importer.
    """

    module = _importer()
    root, _commit = upstream
    odd = root / "variants" / "mis-cased"
    odd.mkdir()
    (odd / "skill.md").write_text(
        "---\nname: bio-mis-cased\ndescription: d\n---\nBody.\n", encoding="utf-8"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "miscased")
    commit = _git(root, "rev-parse", "HEAD")

    # Still two: the mis-cased document is not part of the pin.
    manifest = module.import_collection(
        root, tmp_path / "out", expected_commit=commit, expected_skills=2
    )
    assert manifest["skill_count"] == 2


def test_the_manifest_records_posix_paths_in_a_stable_order(upstream, tmp_path):
    module = _importer()
    root, commit = upstream
    destination = tmp_path / "out"
    manifest = module.import_collection(
        root, destination, expected_commit=commit, expected_skills=2
    )

    paths = [str(row["path"]) for row in manifest["files"]]
    assert paths == sorted(paths)
    assert all("\\" not in path for path in paths)
    assert "MANIFEST.json" not in paths  # written after the payload is hashed


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
