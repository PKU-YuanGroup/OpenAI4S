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
import os
import stat
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
    if os.name != "nt":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o755


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


def test_it_refuses_modified_and_untracked_checkout_bytes(upstream, tmp_path):
    module = _importer()
    root, commit = upstream
    document = root / "alignment" / "alignment-io" / "SKILL.md"
    original = document.read_text("utf-8")
    document.write_text(original + "dirty\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="checkout must be clean"):
        module.import_collection(
            root, tmp_path / "modified", expected_commit=commit, expected_skills=2
        )

    document.write_text(original, encoding="utf-8")
    (root / "alignment" / "alignment-io" / "examples" / "untracked.py").write_text(
        "DIRTY = True\n", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="checkout must be clean"):
        module.import_collection(
            root, tmp_path / "untracked", expected_commit=commit, expected_skills=2
        )


def test_conversion_reads_the_pinned_git_objects_not_checkout_files(
    upstream, tmp_path, monkeypatch
):
    module = _importer()
    root, commit = upstream
    document = root / "alignment" / "alignment-io" / "SKILL.md"
    document.write_text(
        document.read_text("utf-8").replace("description", "DIRTY description"),
        encoding="utf-8",
    )
    # Exercise object materialisation independently of the dirty-check contract.
    monkeypatch.setattr(module, "_validate_checkout", lambda *_args: None)

    destination = tmp_path / "out"
    module.import_collection(
        root, destination, expected_commit=commit, expected_skills=2
    )

    converted = (destination / "bio-alignment-alignment-io" / "SKILL.md").read_text(
        "utf-8"
    )
    assert "DIRTY description" not in converted


def test_partial_clone_cannot_lazy_fetch_missing_pinned_blobs(upstream, tmp_path):
    """A local-only import must not contact a promisor remote implicitly."""

    module = _importer()
    root, commit = upstream
    _git(root, "config", "uploadpack.allowFilter", "true")
    partial = tmp_path / "partial"
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--filter=blob:none",
            "--no-checkout",
            root.as_uri(),
            str(partial),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(partial, "sparse-checkout", "init", "--no-cone")
    _git(partial, "sparse-checkout", "set", "LICENSE")
    _git(partial, "checkout", "-q", commit)

    assert _git(partial, "status", "--porcelain=v1") == ""
    missing = _git(
        partial, "rev-list", "--objects", "--missing=print", commit
    ).splitlines()
    assert any(line.startswith("?") for line in missing)
    module._validate_checkout(partial, commit)

    # If `git cat-file` tries its normal partial-clone lazy fetch, this missing
    # remote turns the call into a network/transport error. With lazy fetching
    # disabled it instead reports the absent local blob to the importer, which
    # fails closed before publishing any destination tree.
    root.rename(tmp_path / "upstream-offline")
    destination = tmp_path / "materialized"
    with pytest.raises(RuntimeError, match="cannot read pinned Git blob"):
        module.import_collection(
            partial, destination, expected_commit=commit, expected_skills=2
        )
    assert not destination.exists()


@pytest.mark.skipif(os.name == "nt", reason="creating Git symlinks needs privileges")
def test_selected_git_symlink_is_rejected_without_following_local_target(
    upstream, tmp_path
):
    """A committed symlink must not smuggle host-local bytes into the bundle."""

    module = _importer()
    root, _commit = upstream
    outside = tmp_path / "host-local.py"
    outside.write_text("SECRET_LOCAL_BYTES = True\n", encoding="utf-8")
    selected = root / "alignment" / "alignment-io" / "examples" / "run.py"
    selected.unlink()
    selected.symlink_to(outside)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "selected symlink")
    commit = _git(root, "rev-parse", "HEAD")

    destination = tmp_path / "out"
    with pytest.raises(
        RuntimeError,
        match=r"regular file: alignment/alignment-io/examples/run\.py",
    ):
        module.import_collection(
            root, destination, expected_commit=commit, expected_skills=2
        )

    assert not destination.exists()


def test_pinned_tree_rejects_unicode_normalization_path_collisions(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    module = _importer()
    object_a = "1" * 40
    object_b = "2" * 40
    listing = (
        f"100644 blob {object_a}\talignment/alignment-io/examples/caf\N{LATIN SMALL LETTER E WITH ACUTE}.py\0"
        f"100644 blob {object_b}\talignment/alignment-io/examples/cafe\N{COMBINING ACUTE ACCENT}.py\0"
        f"100644 blob {'3' * 40}\tLICENSE\0"
    ).encode("utf-8")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=listing),
    )

    with pytest.raises(RuntimeError, match="collide on Windows/macOS"):
        module._selected_tree_entries(tmp_path, "unused")


def test_pinned_tree_rejects_colliding_ancestor_directory_spellings(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    module = _importer()
    listing = (
        f"100644 blob {'1' * 40}\tcategory/Foo/SKILL.md\0"
        f"100644 blob {'2' * 40}\tcategory/foo/examples/evil.py\0"
        f"100644 blob {'3' * 40}\tLICENSE\0"
    ).encode("utf-8")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=listing),
    )

    with pytest.raises(RuntimeError, match="path components that collide"):
        module._selected_tree_entries(tmp_path, "unused")


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
    _skill(root, "variants", "duplicate", '"BIO-VARIANT-CALLING"', "Body.\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "dup")
    commit = _git(root, "rev-parse", "HEAD")

    destination = tmp_path / "out"
    with pytest.raises(
        RuntimeError,
        match="duplicate declared skill name identity 'bio-variant-calling'",
    ):
        module.import_collection(
            root, destination, expected_commit=commit, expected_skills=3
        )

    assert not destination.exists()
    assert list(destination.parent.glob(f".{destination.name}.incoming-*")) == []


def test_a_declared_name_cannot_claim_another_generated_directory(upstream, tmp_path):
    module = _importer()
    root, _commit = upstream
    _skill(
        root,
        "variants",
        "cross-identity",
        "bio-alignment-alignment-io",
        "Body.\n",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "cross identity")
    commit = _git(root, "rev-parse", "HEAD")

    destination = tmp_path / "out"
    with pytest.raises(
        RuntimeError,
        match="declared skill name collides with generated directory identity",
    ):
        module.import_collection(
            root, destination, expected_commit=commit, expected_skills=3
        )

    assert not destination.exists()


@pytest.mark.parametrize(
    ("reserved_kind", "claim_kind", "catalog_name", "collection_name"),
    [
        ("collection root", "directory", "catalog", "bio-variants-calling"),
        ("collection root", "declared", "catalog", "bio-variant-calling"),
        ("catalog namespace", "directory", "bio-variants-calling", "collection"),
        ("catalog namespace", "declared", "bio-variant-calling", "collection"),
    ],
)
def test_importer_reserves_destination_catalog_and_collection_identities(
    upstream,
    tmp_path,
    reserved_kind,
    claim_kind,
    catalog_name,
    collection_name,
):
    module = _importer()
    root, commit = upstream
    destination = tmp_path / catalog_name / collection_name

    with pytest.raises(
        RuntimeError,
        match=rf"{claim_kind}.*reserved {reserved_kind} identity",
    ):
        module.import_collection(
            root, destination, expected_commit=commit, expected_skills=2
        )

    assert not destination.exists()
    assert list(destination.parent.glob(f".{destination.name}.incoming-*")) == []


def test_folded_declared_name_round_trips_through_runtime_parser():
    from openai4s.skills_loader.loader import _parse_frontmatter

    module = _importer()
    raw = (
        "---\n"
        "name: >-\n"
        "  Folded\n"
        "  Skill\n"
        "description: fixture\n"
        "tool_type: python\n"
        "primary_tool: pandas\n"
        "---\n"
        "Body.\n"
    )

    declared_name, converted = module._convert_document(raw, "fixture", "a" * 40)
    runtime_meta, _body = _parse_frontmatter(converted)

    assert declared_name == "Folded Skill"
    assert runtime_meta["name"] == declared_name


def test_block_tool_fields_are_removed_without_changing_other_frontmatter():
    from openai4s.skills_loader.loader import SkillLoader

    module = _importer()
    raw = (
        "---\n"
        "name: block-tools\n"
        "description: >-\n"
        "  Keep this folded\n"
        "  description intact.\n"
        "Tool_Type: >-\n"
        "  python\n"
        "  runtime\n"
        "PRIMARY_TOOL: |-\n"
        "  pandas\n"
        "  polars\n"
        "custom_note: |\n"
        "  Preserve this literal\n"
        "  value too.\n"
        "nested:\n"
        "  tool_type: keep nested tool\n"
        "  primary_tool: keep nested primary\n"
        "---\n"
        "Body remains unchanged.\n"
    )

    original_meta, original_body = SkillLoader.parse_document(raw)
    _name, converted = module._convert_document(raw, "fixture", "b" * 40)
    converted_meta, converted_body = SkillLoader.parse_document(converted)

    assert converted_body == original_body
    for key in {"name", "description", "custom_note", "nested"}:
        assert converted_meta[key] == original_meta[key]
    assert "tool_type" not in converted_meta
    assert "primary_tool" not in converted_meta

    # The original top-level headers and their continuation lines are gone
    # from the retained portion. Same-named nested fields remain byte-for-byte.
    retained = converted.split("\norigin: openai4s\n", 1)[0]
    assert "Tool_Type:" not in retained
    assert "PRIMARY_TOOL:" not in retained
    assert "\n  python\n  runtime\n" not in retained
    assert "\n  pandas\n  polars\n" not in retained
    assert "  tool_type: keep nested tool" in retained
    assert "  primary_tool: keep nested primary" in retained
    assert (
        "\nmetadata:\n"
        "  tool_type: python runtime\n"
        "  primary_tool: |-\n"
        "    pandas\n"
        "    polars\n"
        "  third_party:\n"
    ) in converted


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
    upstream_paths = [str(row["upstream_path"]) for row in manifest["skills"]]
    assert upstream_paths == ["alignment/alignment-io", "variants/calling"]
    assert all("\\" not in path for path in upstream_paths)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
