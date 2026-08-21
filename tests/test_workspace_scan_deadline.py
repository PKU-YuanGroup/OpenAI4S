"""The walk was bounded in entries, which is not the same as being bounded.

`MAX_SCAN_ENTRIES` caps how many directory entries a scan visits, and its own
comment names the reason: "the walk is unbounded work, not just unbounded
memory". It then bounds the work in syscalls. How long those syscalls take is a
property of the filesystem, not of this process — on a network mount, or over
entries whose inodes are cold, a scan well under a hundred thousand entries
outlives any timeout the caller set, and the caller cannot intervene because
the whole walk happens inside one tool call.

So the three walkers now carry a wall-clock budget as well, reported through
the same `scan_truncated` flag the entry cap uses: a partial answer must never
look exhaustive, whichever budget ran out.

Fault-injected by making the *filesystem* slow rather than by lowering the
budget — a test that shrank `MAX_SCAN_SECONDS` to zero would pass against a
walker that checked the clock once and never again.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from openai4s.host import files as files_mod
from openai4s.tools.content_search import ContentSearchTool
from openai4s.tools.glob_files import GlobFilesTool
from openai4s.tools.list_directory import ListDirectoryTool


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    for index in range(12):
        (root / f"file{index}.txt").write_text("needle\n", encoding="utf-8")
    return SimpleNamespace(
        workspace=lambda: root,
        resolve=lambda name: root / name,
        relative=lambda path: str(path.relative_to(root)),
        is_secret_path=lambda relative: False,
        resolved_secret_checker=lambda: lambda _path: False,
        root=root,
    )


@pytest.fixture
def slow_clock(monkeypatch):
    """A clock that jumps a second per reading.

    The walkers call `time.monotonic()` once per entry, so this makes every
    entry cost a second without making the test take one — the shape of a cold
    network mount, at a speed a suite can afford.
    """
    ticks = {"now": 0.0}

    def monotonic():
        ticks["now"] += 1.0
        return ticks["now"]

    for module in ("content_search", "glob_files", "list_directory"):
        monkeypatch.setattr(
            f"openai4s.tools.{module}.time.monotonic", monotonic, raising=True
        )
    return ticks


def test_grep_stops_on_the_clock_and_says_it_was_cut(workspace, slow_clock):
    result = ContentSearchTool().execute(workspace, {"pattern": "needle"})

    assert result.get("scan_truncated") is True, result
    # Under the entry cap the whole way -- twelve files against a hundred
    # thousand -- so only the clock can have stopped it.
    assert len(result.get("matches") or result.get("hits") or []) < 12


def test_glob_stops_on_the_clock_and_says_it_was_cut(workspace, slow_clock):
    result = GlobFilesTool().execute(workspace, {"pattern": "*.txt"})

    assert result.get("scan_truncated") is True, result


def test_list_dir_stops_on_the_clock_and_says_it_was_cut(workspace, slow_clock):
    result = ListDirectoryTool().execute(workspace, {"path": "."})

    assert result.get("scan_truncated") is True, result


def test_an_ordinary_scan_is_not_reported_as_truncated(workspace):
    """The budget must not make every answer look partial."""
    for tool, arguments in (
        (ContentSearchTool(), {"pattern": "needle"}),
        (GlobFilesTool(), {"pattern": "*.txt"}),
        (ListDirectoryTool(), {"path": "."}),
    ):
        result = tool.execute(workspace, arguments)
        assert not result.get("scan_truncated"), (tool.name, result)


def test_the_budget_is_a_real_number_of_seconds():
    """A guard nobody can reach is not a guard."""
    assert isinstance(files_mod.MAX_SCAN_SECONDS, (int, float))
    assert 0 < files_mod.MAX_SCAN_SECONDS <= 60
    assert time.monotonic() > 0
