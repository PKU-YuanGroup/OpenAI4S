"""Workspace path boundary and read budgets shared by class-based file tools.

This service owns session-root resolution and confinement, plus the two
primitives that keep a bounded question from costing unbounded work: a line
reader with a byte budget, and a top-N selector with a memory bound. Concrete
read/write/edit/search behaviour still lives beside its schema in
``openai4s.tools``; the tools import these two lazily, so ``openai4s.tools``
-- which is on the CLI's startup path -- stays off the ~40 ms
``openai4s.host`` package import.
"""

from __future__ import annotations

import bisect
import codecs
import fnmatch
from pathlib import Path
from typing import Any, Callable, Iterator

_SECRET_BASENAMES = (
    "*.env",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    ".netrc",
    ".pgpass",
)


def is_secret_path(path: str) -> bool:
    """Return whether a basename belongs to the host tool secret denylist."""
    import posixpath

    basename = posixpath.basename((path or "").replace("\\", "/").rstrip("/")).lower()
    if not basename:
        return False
    return any(fnmatch.fnmatchcase(basename, pattern) for pattern in _SECRET_BASENAMES)


#: How much of one file a workspace tool will pull into the daemon. Every
#: reader in this family used to be `read_bytes()`/`read_text()` followed by a
#: slice, so the cost of answering a bounded question was the size of the file
#: the agent named: measured before this, two lines of a 256 MB output peaked
#: at 768 MB of traced memory in a process that also serves every other
#: session. A budget makes a huge file answerable and partial; without one it
#: was unanswerable and fatal.
MAX_READ_BYTES = 8 * 1024 * 1024
#: Read granularity. Large enough that syscalls are not the cost, small enough
#: that the resident set stays a rounding error next to the budget.
READ_CHUNK_BYTES = 256 * 1024
#: Longest run of characters carried while waiting for a line terminator. A
#: file with no terminators at all -- a one-line JSON dump, a binary blob that
#: happens to decode -- would otherwise rebuild the whole byte budget inside a
#: single `pending` string and defeat the bound one chunk at a time, which is
#: the same single-blob case `jobs.py` guards. Such a file has no line numbers
#: to be wrong about; what matters is that the daemon does not hold it, and
#: that the split is reported rather than silent.
MAX_LINE_CHARS = 1024 * 1024
#: How many directory entries one scan visits before it stops and says so. The
#: walk is unbounded work, not just unbounded memory: bounding what is kept
#: does nothing about a tree that takes a minute to enumerate.
MAX_SCAN_ENTRIES = 100_000
#: How long one scan may spend walking before it stops and says so.
#:
#: The entry cap above bounds syscalls, not seconds, and its own comment makes
#: the point it does not finish: "the walk is unbounded work". How long 100,000
#: entries take is a property of the filesystem, not of this process -- on a
#: network mount or a directory whose entries are cold, a scan well under the
#: entry cap outlives any request timeout the caller set, and the caller has no
#: way to bound it because the walk is inside a single tool call.
#:
#: Reported through the same `scan_truncated` flag the entry cap uses, so a
#: partial answer never looks exhaustive whichever budget ran out.
MAX_SCAN_SECONDS = 10.0


def _is_terminated(piece: str) -> bool:
    """Whether a ``splitlines(keepends=True)`` piece ends with a terminator.

    Stripping it is the test, because `splitlines` recognises more than
    ``\\n`` -- ``\\x0b``, ``\\u2028`` and friends -- and this reader has to
    split on exactly what the previous whole-file `splitlines()` split on.
    """
    stripped = piece.splitlines()
    return bool(stripped) and stripped[0] != piece


class BoundedTextReader:
    """Decode a UTF-8 file into lines under a hard byte budget.

    The counters are half the point: `bytes_read` against the file size says
    the scan stopped early, and `chars_read` against what the caller kept says
    how much text it did not get. Both are only final once iteration ends.

    ``UnicodeDecodeError`` is raised rather than swallowed -- `read_file`
    answers with its binary shape and `grep` skips the file, and neither
    decision belongs in here.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        max_bytes: int = MAX_READ_BYTES,
        chunk_bytes: int = READ_CHUNK_BYTES,
        max_line_chars: int = MAX_LINE_CHARS,
    ) -> None:
        self._path = Path(path)
        self._max_bytes = max(1, int(max_bytes))
        self._chunk_bytes = max(1, int(chunk_bytes))
        self._max_line_chars = max(1, int(max_line_chars))
        #: Bytes actually read from disk: the daemon-side cost of the call.
        self.bytes_read = 0
        #: Characters decoded, whether or not they completed a line or were
        #: retained. Counted before anything is dropped, because accounting
        #: that reports only what survived cannot say how much did not.
        self.chars_read = 0
        self.lines_read = 0
        #: The budget ended the scan, not the file.
        self.budget_exhausted = False
        #: At least one line was cut at `max_line_chars` and continued.
        self.long_line_split = False

    def lines(self) -> Iterator[str]:
        """Yield complete lines, terminators stripped, until the budget runs out."""
        decoder = codecs.getincrementaldecoder("utf-8")()
        pending = ""
        with open(self._path, "rb") as handle:
            while True:
                remaining = self._max_bytes - self.bytes_read
                if remaining <= 0:
                    self.budget_exhausted = True
                    break
                chunk = handle.read(min(self._chunk_bytes, remaining))
                if not chunk:
                    break
                self.bytes_read += len(chunk)
                # Incremental, so a multi-byte character split across the read
                # boundary is held rather than reported as a broken file.
                decoded = decoder.decode(chunk)
                self.chars_read += len(decoded)
                pending += decoded
                pieces = pending.splitlines(keepends=True)
                pending = ""
                if pieces and not _is_terminated(pieces[-1]):
                    pending = pieces.pop()
                elif pieces and pieces[-1].endswith("\r"):
                    # A trailing lone "\r" may be the first half of a CRLF that
                    # landed on the boundary. Held back, or one line gets
                    # reported as two purely because of where the read ended.
                    pending = pieces.pop()
                for piece in pieces:
                    self.lines_read += 1
                    yield piece.splitlines()[0]
                if len(pending) > self._max_line_chars:
                    self.long_line_split = True
                    self.lines_read += 1
                    fragment, pending = pending, ""
                    yield fragment
            if not self.budget_exhausted:
                # `final=True` only at a real end of file: a character the
                # budget cut in half is a truncated read, not a decode failure,
                # and flushing here would report it as the latter.
                pending += decoder.decode(b"", True)
                if pending:
                    self.lines_read += 1
                    yield (
                        pending.splitlines()[0] if _is_terminated(pending) else pending
                    )


class BoundedSelection:
    """Keep the smallest ``limit`` keys of a stream without materialising it.

    `glob` and `list_dir` sorted the whole tree and then sliced, so a directory
    of a million files cost a million retained entries to answer a
    thousand-entry question. The bound is on what is *held*, not on what is
    *reported*: `seen` still counts everything offered, which is what lets the
    counters say how much was dropped rather than only that something was.
    """

    def __init__(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        #: (key, arrival, value), kept sorted ascending. `arrival` keeps the
        #: tuple comparison from ever reaching `value`, which need not be
        #: orderable at all -- `list_dir` stores directory entries here.
        self._items: list[tuple[str, int, Any]] = []
        self.seen = 0

    def offer(self, key: str, value: Any = None) -> None:
        """Consider one candidate. A stream of bare keys is its own value."""
        self.seen += 1
        items = self._items
        if len(items) >= self._limit:
            # One comparison rejects the common case, so a long stream costs a
            # comparison per item instead of an insertion per item.
            if key >= items[-1][0]:
                return
            items.pop()
        bisect.insort(items, (key, self.seen, key if value is None else value))

    @property
    def retained(self) -> int:
        return len(self._items)

    @property
    def dropped(self) -> int:
        return max(0, self.seen - self.retained)

    def values(self) -> list[Any]:
        """Retained values, in key order."""
        return [item[2] for item in self._items]

    def items(self) -> list[tuple[str, Any]]:
        """Retained (key, value) pairs, in key order."""
        return [(item[0], item[2]) for item in self._items]

    def counters(self, *, scan_truncated: bool = False) -> dict[str, Any]:
        """The truncation accounting every workspace collection tool returns.

        `jobs.py`'s shape: what came back, what was seen, what was dropped, and
        whether anything was. `dropped` is derivable from the other two and is
        reported anyway, for the same reason `jobs.py` reports it -- a reader
        deciding whether a partial answer is enough should not have to compute
        it. `scan_truncated` says the walk itself stopped early, which makes
        `total_count` a floor rather than a total.
        """
        counters: dict[str, Any] = {
            "count": self.retained,
            "total_count": self.seen,
        }
        if self.dropped:
            counters["dropped"] = self.dropped
        if self.dropped or scan_truncated:
            counters["truncated"] = True
        if scan_truncated:
            counters["scan_truncated"] = True
        return counters


class WorkspaceFileService:
    """Execute file tools inside the workspace for the current frame.

    ``frame_id`` is a provider rather than a captured value because the CLI may
    assign its root frame after constructing the dispatcher.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        frame_id: Callable[[], str | None],
        workspace: Callable[[], str | Path | None] | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._frame_id = frame_id
        self._workspace = workspace
        self._resolved_key: tuple[str | None, str | None] | None = None
        self._resolved_path: Path | None = None

    def workspace(self) -> Path:
        """Return the resolved workspace, creating it on first use.

        On first use, not every use. This did a `resolve()` and a
        `mkdir(parents=True, exist_ok=True)` per call, and `relative()` calls
        it once per candidate path -- so a glob over N files paid N of each on
        top of the globbing. Measured at ~16us per call, about half the cost of
        `relative()` itself.

        The memo is keyed on the two cheap inputs rather than on nothing,
        because both are late-bound: the CLI assigns its root frame after the
        dispatcher exists, and the key changing is exactly when the directory
        must be recomputed.
        """
        explicit = self._workspace() if self._workspace is not None else None
        key = (
            str(explicit) if explicit is not None else None,
            self._frame_id(),
        )
        cached = self._resolved_path
        if cached is not None and self._resolved_key == key:
            return cached
        workspace = (
            Path(explicit)
            if explicit is not None
            else self._data_dir / "agent-workspaces" / (key[1] or "default")
        ).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        self._resolved_key = key
        self._resolved_path = workspace
        return workspace

    def relative(self, path: Path) -> str | None:
        """Return a confined workspace-relative path, or ``None`` on escape."""
        try:
            return str(path.resolve().relative_to(self.workspace()))
        except (ValueError, OSError):
            return None

    def resolve(self, relative: str, *, must_exist: bool = False) -> Path:
        """Resolve a path and reject parent, absolute, and symlink escapes."""
        workspace = self.workspace()
        path = Path(relative)
        target = (path if path.is_absolute() else workspace / path).resolve()
        try:
            target.relative_to(workspace)
        except ValueError:
            raise ValueError(
                f"path escapes the workspace: {relative!r} "
                "(stay inside your working dir)"
            )
        if must_exist and not target.exists():
            raise FileNotFoundError(f"no such file: {relative}")
        return target

    @staticmethod
    def is_secret_path(path: str) -> bool:
        """Expose the shared denylist without coupling tools to this module."""
        return is_secret_path(path)

    def _execute_compat(self, host_method: str, spec: dict) -> dict:
        """Preserve the former service API while concrete tools own behaviour."""
        from openai4s.tools.registry import get_tool_by_host_method

        tool = get_tool_by_host_method(host_method)
        if tool is None:
            raise ValueError(f"no control tool registered for {host_method!r}")
        return tool.execute(self, spec)

    def read_file(self, spec: dict) -> dict:
        return self._execute_compat("read_file", spec)

    def write_file(self, spec: dict) -> dict:
        return self._execute_compat("write_file", spec)

    def edit_file(self, spec: dict) -> dict:
        return self._execute_compat("edit_file", spec)

    def glob(self, spec: dict) -> dict:
        return self._execute_compat("glob", spec)

    def grep(self, spec: dict) -> dict:
        return self._execute_compat("grep", spec)

    def list_dir(self, spec: dict) -> dict:
        return self._execute_compat("list_dir", spec)


__all__ = [
    "MAX_LINE_CHARS",
    "MAX_READ_BYTES",
    "MAX_SCAN_ENTRIES",
    "MAX_SCAN_SECONDS",
    "READ_CHUNK_BYTES",
    "BoundedSelection",
    "BoundedTextReader",
    "WorkspaceFileService",
    "is_secret_path",
]
