"""Allocation budgets for the workspace tools and artifact registration.

Every one of these paths used to materialise its whole input and then slice.
Measured in this process before the fix: 768 MiB of peak traced memory to
return two lines of a 256 MiB file, 192 MiB to grep a 64 MiB one, 64 MiB to
change seven characters in a 32 MiB file, and 64 MiB to register an output
whose bytes the copy underneath never needed in Python at all. A daemon that
also serves every other session paid all of it.

These assertions are about that peak, not about the length of the reply. A
test that only checks the returned window passes just as happily against the
implementation that read the file whole, which is why every case here
allocates a file too large to hold and watches `tracemalloc` across the call.

The large files are sparse -- a short real prefix, then a hole -- so they cost
kilobytes of disk and no measurable test time, while reading one whole would
still be fatal.
"""

from __future__ import annotations

import hashlib
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

from openai4s.config import Config, LLMConfig
from openai4s.host.data import HostDataService
from openai4s.host.files import MAX_READ_BYTES, BoundedSelection, BoundedTextReader
from openai4s.host_dispatch import HostDispatcher
from openai4s.tools.glob_files import _MAX_MATCHES
from openai4s.tools.list_directory import _MAX_ENTRIES

MIB = 1024 * 1024


def _dispatcher(tmp_path: Path) -> HostDispatcher:
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    return HostDispatcher(cfg=cfg, frame_id="frame-1")


def _sparse(path: Path, prefix: bytes, size: int) -> Path:
    """A file whose first bytes are real and whose remainder is a hole."""
    with open(path, "wb") as handle:
        handle.write(prefix)
        handle.truncate(size)
    return path


def _peak(call):
    """Run `call` under tracemalloc, returning ``(result, peak bytes)``."""
    tracemalloc.start()
    try:
        result = call()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, peak


def test_read_file_answers_a_window_without_reading_the_whole_file(tmp_path):
    """`read_file` was `read_bytes()` -> `decode()` -> `splitlines()` -> slice.

    Three full-size allocations to answer a bounded question: this exact call
    peaked at 768.4 MiB before the budget and returned ten characters.
    """
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    _sparse(workspace / "huge.txt", b"alpha\nbeta\ngamma\n", 256 * MIB)

    result, peak = _peak(
        lambda: dispatcher("read_file", [{"path": "huge.txt", "limit": 2}])
    )

    assert result["content"] == "alpha\nbeta"
    assert peak < 16 * MIB, f"read_file peaked at {peak / MIB:.1f} MiB"
    # The budget, not the file, ended the scan -- and the reply says so rather
    # than looking like a file that simply had less in it.
    assert result["scanned_bytes"] <= MAX_READ_BYTES
    assert result["size_bytes"] == 256 * MIB
    assert result["truncated"] is True
    assert result["budget_truncated"] is True
    assert result["retained_chars"] == len("alpha") + len("beta")
    assert result["seen_chars"] > result["retained_chars"]
    assert result["dropped_chars"] == result["seen_chars"] - result["retained_chars"]
    # The hole is one run of NULs with no terminator anywhere in it, so the
    # reader cut it instead of carrying it -- which is the only reason the peak
    # above is a few megabytes and not the whole budget several times over.
    assert result["long_line_split"] is True


def test_a_small_read_keeps_the_untruncated_reply_shape(tmp_path):
    """A budget that changes the answer when it does not bite is a second bug.

    The truncation receipt appears only when something was truncated, so an
    ordinary read returns exactly the dict callers already parse.
    """
    dispatcher = _dispatcher(tmp_path)
    (dispatcher._workspace() / "lines.txt").write_text("one\ntwo\nthree\n")

    result = dispatcher("read_file", [{"path": "lines.txt", "offset": 1, "limit": 5}])

    assert result == {
        "path": "lines.txt",
        "total_lines": 3,
        "offset": 1,
        "content": "two\nthree",
        "truncated": False,
    }


def test_the_reader_does_not_split_a_line_on_a_read_boundary(tmp_path):
    """Both a CRLF and a multi-byte character can land on the boundary.

    Neither is a property of the file, so neither may show up in the answer: a
    split CRLF must not become two lines, and a UTF-8 sequence cut in half must
    not become a decode failure. `chunk_bytes=1` forces every boundary there
    is.
    """
    path = tmp_path / "boundary.txt"
    path.write_bytes("aa\r\nbb\r\ncafé\nnaïve\n".encode("utf-8"))

    reader = BoundedTextReader(path, chunk_bytes=1)

    assert list(reader.lines()) == ["aa", "bb", "café", "naïve"]
    assert reader.lines_read == 4
    assert reader.budget_exhausted is False
    assert reader.long_line_split is False


def test_edit_file_rewrites_a_large_file_without_holding_it(tmp_path):
    """`read_text` -> `str.replace` -> `write_text` peaked at 64.4 MiB here."""
    dispatcher = _dispatcher(tmp_path)
    target = _sparse(dispatcher._workspace() / "run.log", b"OLDTEXT\n", 32 * MIB)

    result, peak = _peak(
        lambda: dispatcher(
            "edit_file",
            [{"path": "run.log", "old_string": "OLDTEXT", "new_string": "NEWTEXT"}],
        )
    )

    assert result == {"path": "run.log", "replaced": 1}
    assert peak < 8 * MIB, f"edit_file peaked at {peak / MIB:.1f} MiB"
    # A real rewrite: the replacement landed, the rest of the file is intact,
    # and the staged copy was renamed rather than left behind.
    with open(target, "rb") as handle:
        assert handle.read(8) == b"NEWTEXT\n"
    assert target.stat().st_size == 32 * MIB
    assert [path.name for path in target.parent.iterdir()] == ["run.log"]


def test_a_refused_edit_streams_too_and_leaves_the_file_untouched(tmp_path):
    """The uniqueness rule is a rule only if the refusal changes nothing.

    The count is not known until the whole file has been streamed, so the
    rewrite is already staged by the time the edit is refused. Nothing may be
    swapped in and nothing may be left in the workspace.
    """
    dispatcher = _dispatcher(tmp_path)
    target = _sparse(dispatcher._workspace() / "twice.log", b"same\nsame\n", 32 * MIB)

    result, peak = _peak(
        lambda: dispatcher(
            "edit_file",
            [{"path": "twice.log", "old_string": "same", "new_string": "new"}],
        )
    )

    assert set(result) == {"error"}
    assert "not unique (2 matches)" in result["error"]
    assert peak < 8 * MIB, f"edit_file peaked at {peak / MIB:.1f} MiB"
    with open(target, "rb") as handle:
        assert handle.read(10) == b"same\nsame\n"
    assert target.stat().st_size == 32 * MIB
    assert [path.name for path in target.parent.iterdir()] == ["twice.log"]


def test_grep_streams_each_file_and_reports_what_it_could_not_reach(tmp_path):
    """`path.read_text()` per candidate peaked at 192.0 MiB for this search."""
    dispatcher = _dispatcher(tmp_path)
    workspace = dispatcher._workspace()
    _sparse(workspace / "huge.log", b"NEEDLE here\n", 64 * MIB)
    (workspace / "small.txt").write_text("NEEDLE there\n", encoding="utf-8")

    result, peak = _peak(lambda: dispatcher("grep", [{"pattern": "NEEDLE"}]))

    assert peak < 16 * MIB, f"grep peaked at {peak / MIB:.1f} MiB"
    assert sorted((hit["file"], hit["line"]) for hit in result["matches"]) == [
        ("huge.log", 1),
        ("small.txt", 1),
    ]
    assert result["count"] == 2
    # The big file was searched to the byte budget and no further. Saying so is
    # the point: a miss below the cut is a miss this search cannot see, and an
    # unqualified result reads as "not in this tree".
    assert result["files_searched"] == 2
    assert result["files_seen"] == 2
    assert result["files_truncated"] == 1
    assert result["truncated"] is True


def test_glob_and_list_dir_hold_the_cap_rather_than_the_directory(tmp_path):
    """Both sorted the whole directory before truncating; `list_dir` never did.

    `list_dir` returned every entry with a dict and a `stat()` each, so the
    reply grew with the directory and nothing said it should not.
    """
    dispatcher = _dispatcher(tmp_path)
    bulk = dispatcher._workspace() / "bulk"
    bulk.mkdir(parents=True, exist_ok=True)
    total = _MAX_MATCHES + 25
    for index in range(total):
        (bulk / f"f{index:05d}.dat").write_text("x", encoding="utf-8")

    globbed = dispatcher("glob", [{"pattern": "bulk/*.dat"}])
    listed = dispatcher("list_dir", [{"path": "bulk"}])

    assert globbed["count"] == _MAX_MATCHES
    assert globbed["total_count"] == total
    assert globbed["dropped"] == total - _MAX_MATCHES
    assert globbed["truncated"] is True
    # Bounded, and still the same answer as sorting the whole tree would give.
    assert globbed["matches"] == sorted(globbed["matches"])
    assert globbed["matches"][0] == "bulk/f00000.dat"

    assert listed["count"] == _MAX_ENTRIES
    assert listed["total_count"] == total
    assert listed["dropped"] == total - _MAX_ENTRIES
    assert listed["truncated"] is True
    assert len(listed["entries"]) == _MAX_ENTRIES
    assert listed["entries"][0] == {
        "name": "f00000.dat",
        "path": "bulk/f00000.dat",
        "is_dir": False,
        "size_bytes": 1,
    }


def test_the_selection_holds_its_cap_against_a_long_stream():
    """The bound is on what is held, not on what is reported.

    Descending keys are the adversarial case: every later item belongs in the
    answer, so a keeper that merely took the first N would be caught, and a
    keeper that grew would be caught by the peak.
    """
    selection = BoundedSelection(1000)

    def stream():
        for index in range(50_000, 0, -1):
            yield f"f{index:09d}"

    def consume():
        for key in stream():
            selection.offer(key)

    _result, peak = _peak(consume)

    assert peak < MIB, f"selection peaked at {peak / MIB:.2f} MiB"
    assert selection.seen == 50_000
    assert selection.retained == 1000
    assert selection.dropped == 49_000
    assert selection.values() == sorted(selection.values())
    assert selection.values()[:2] == ["f000000001", "f000000002"]
    assert selection.counters() == {
        "count": 1000,
        "total_count": 50_000,
        "dropped": 49_000,
        "truncated": True,
    }


class _RecordingStore:
    """The smallest store `save_artifact` can talk to."""

    def __init__(self) -> None:
        self.recorded: dict = {}

    def record_cell_artifact(self, **fields):
        self.recorded = fields
        return {"version_id": "v-1", "artifact_id": "a-1"}

    def version_meta(self, version_id):
        return {}

    def set_version_snapshot(self, version_id, snapshot_path):
        self.snapshot_path = snapshot_path


def test_save_artifact_checksums_a_large_file_without_holding_it(tmp_path):
    """`read_bytes()` purely to hash: 64 MiB peak to register a 64 MiB output.

    The copy underneath streams and always did, so the allocation bought
    nothing at all -- and the outputs worth registering are the ones a daemon
    cannot hold.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _RecordingStore()

    def resolve(path, *, must_exist=False):
        resolved = (workspace / path).resolve()
        if must_exist and not resolved.exists():
            raise FileNotFoundError(resolved)
        return resolved

    service = HostDataService(
        store=store,
        config=SimpleNamespace(artifacts_dir=tmp_path / "artifacts"),
        frame_id=lambda: "frame-1",
        resolve_path=resolve,
    )
    source = _sparse(workspace / "trajectory.dcd", b"science", 32 * MIB)
    expected = hashlib.sha256()
    with open(source, "rb") as handle:
        for block in iter(lambda: handle.read(MIB), b""):
            expected.update(block)

    result, peak = _peak(lambda: service.save_artifact({"path": source.name}))

    assert peak < 8 * MIB, f"save_artifact peaked at {peak / MIB:.1f} MiB"
    # Streaming has to produce the same row the whole-file read produced.
    assert store.recorded["size_bytes"] == 32 * MIB
    assert store.recorded["checksum"] == expected.hexdigest()
    assert Path(result["path"]).stat().st_size == 32 * MIB
