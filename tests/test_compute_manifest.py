"""A job must be checkable against what it promised to produce.

The compute package contained no hashing at all: `grep -riE
'sha256|hashlib|checksum' openai4s/compute openai4s_compute_provider` returned
nothing. So a job could declare `outputs` globs, produce none of them, and be
reported `succeeded` — the declared patterns were persisted and never read
back — and a transfer truncated midway was indistinguishable from a complete
one.
"""
from pathlib import Path

import pytest

from openai4s.compute.manifest import (
    build_manifest,
    hash_file,
    manifest_digest,
    reconcile,
)


@pytest.fixture
def harvest(tmp_path):
    root = tmp_path / "hpc" / "job-1"
    (root / "results").mkdir(parents=True)
    (root / "model.pt").write_bytes(b"weights")
    (root / "results" / "scores.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (root / "stdout.log").write_text("done\n", encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# the record itself
# --------------------------------------------------------------------------


def test_the_manifest_records_every_file_with_size_and_hash(harvest):
    entries = build_manifest(harvest)
    by_path = {item["path"]: item for item in entries}

    assert set(by_path) == {"model.pt", "results/scores.csv", "stdout.log"}
    assert by_path["model.pt"]["size"] == len(b"weights")
    assert by_path["model.pt"]["sha256"] == hash_file(harvest / "model.pt")
    assert all(len(item["sha256"]) == 64 for item in entries)


def test_paths_are_relative_so_the_record_travels(harvest):
    """An absolute path would pin the manifest to one machine's data
    directory, and would carry that directory into anything the manifest is
    later shown in."""
    for item in build_manifest(harvest):
        assert not Path(item["path"]).is_absolute()
        assert "hpc" not in item["path"]


def test_the_digest_changes_when_any_byte_does(harvest):
    before = manifest_digest(build_manifest(harvest))
    (harvest / "results" / "scores.csv").write_text("a,b\n1,3\n", encoding="utf-8")
    assert manifest_digest(build_manifest(harvest)) != before


def test_the_digest_is_stable_for_the_same_bytes(harvest):
    """Reproducible, or it cannot be compared against a recorded value."""
    assert manifest_digest(build_manifest(harvest)) == manifest_digest(
        build_manifest(harvest)
    )


def test_a_truncated_file_is_visible(harvest):
    """The scp-exited-0-but-copied-half case. Size and hash are the only
    things that can see it."""
    before = {i["path"]: i for i in build_manifest(harvest)}["model.pt"]
    (harvest / "model.pt").write_bytes(b"wei")
    after = {i["path"]: i for i in build_manifest(harvest)}["model.pt"]

    assert after["size"] < before["size"]
    assert after["sha256"] != before["sha256"]


# --------------------------------------------------------------------------
# reconciliation against what the job promised
# --------------------------------------------------------------------------


def test_a_declared_output_that_never_arrived_is_reported(harvest):
    """The load-bearing half: the job said it would write this, it did not,
    and until now that was still a success."""
    _featured, unmatched = reconcile(
        build_manifest(harvest), ["*.pt", "predictions.parquet"]
    )
    assert unmatched == ["predictions.parquet"]


def test_featured_is_the_declared_subset_not_everything(harvest):
    """`featured_files` was documented as the subset matching the declared
    globs and was in fact every harvested file."""
    featured, unmatched = reconcile(build_manifest(harvest), ["*.csv"])
    assert featured == ["results/scores.csv"]
    assert unmatched == []


def test_a_glob_matches_a_nested_basename(harvest):
    """A job declaring `*.csv` means any csv it wrote, not only ones sitting
    at the harvest root."""
    featured, unmatched = reconcile(build_manifest(harvest), ["scores.csv"])
    assert featured == ["results/scores.csv"]
    assert unmatched == []


def test_declaring_nothing_features_everything(harvest):
    """The documented behaviour of omitting `outputs`, and there is nothing to
    fail against."""
    entries = build_manifest(harvest)
    featured, unmatched = reconcile(entries, None)
    assert len(featured) == len(entries)
    assert unmatched == []


def test_overlapping_patterns_do_not_duplicate_a_file(harvest):
    featured, _ = reconcile(build_manifest(harvest), ["*.csv", "scores.csv"])
    assert featured == ["results/scores.csv"]


def test_an_empty_harvest_fails_every_declared_pattern(tmp_path):
    """A job that produced nothing at all must not slip through as success
    just because there is nothing to compare."""
    empty = tmp_path / "empty"
    empty.mkdir()
    featured, unmatched = reconcile(build_manifest(empty), ["*.pt", "*.csv"])
    assert featured == []
    assert unmatched == ["*.pt", "*.csv"]


@pytest.mark.parametrize(
    "declared",
    ['["*.csv"]', ["*.csv"], ("*.csv",), "*.csv", {"featured": ["*.csv"]}],
)
def test_declared_outputs_are_accepted_in_the_shapes_they_arrive_in(harvest, declared):
    """The column round-trips as JSON text, callers pass lists, and the SDK
    has used a dict — a shape this does not understand would silently mean
    'nothing declared', which is exactly the failure it exists to prevent."""
    featured, unmatched = reconcile(build_manifest(harvest), declared)
    assert featured == ["results/scores.csv"]
    assert unmatched == []


def test_the_documented_mixed_outputs_shape_is_understood(harvest):
    """`skills/remote-compute-ssh` documents a list mixing bare globs with
    `{'glob': ..., 'visibility': ...}` entries. Treating a dict as its repr
    would have matched nothing and marked every job using that form failed."""
    declared = [
        "*.pt",
        {"glob": "*.csv", "visibility": "featured"},
        {"glob": "*.log", "visibility": "hidden"},
    ]
    featured, unmatched = reconcile(build_manifest(harvest), declared)

    assert unmatched == [], "every featured pattern is present in the harvest"
    assert "model.pt" in featured
    assert "results/scores.csv" in featured


def test_a_hidden_pattern_that_matches_nothing_does_not_fail_the_job(harvest):
    """`hidden` says the caller does not want it surfaced. Failing the job
    over one would punish them for saying so."""
    _featured, unmatched = reconcile(
        build_manifest(harvest),
        [{"glob": "debug/*.trace", "visibility": "hidden"}],
    )
    assert unmatched == []


def test_an_unrecognised_shape_means_nothing_declared(harvest):
    """The lenient direction on purpose: this decides whether a job is marked
    failed, and inventing a pattern from a shape we cannot read would fail
    correct jobs."""
    _featured, unmatched = reconcile(build_manifest(harvest), 12345)
    assert unmatched == []
