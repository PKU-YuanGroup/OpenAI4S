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
import os
import stat
import time
import unicodedata
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterator

#: Basenames that carry a credential wherever they appear, matched
#: case-insensitively against the last path segment.
_SECRET_BASENAMES = (
    "*.env",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".netrc",
    ".pgpass",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    ".htpasswd",
)

#: Directories whose *contents* are credentials whatever the file is called.
#:
#: A basename-only denylist is not a fence around credentials, it is a fence
#: around credential-shaped *names*: `~/.aws/credentials`, `~/.ssh/known_hosts`
#: and `~/.ssh/authorized_keys` all passed it, because the secret lives in the
#: directory rather than in the name. The rest of this codebase already knows
#: that -- the kernel OS sandbox denies the whole `~/.ssh` subtree to a cell
#: (`security/sandbox._default_secret_read_denials`), the export boundary
#: matches every path segment (`server/session_package._is_secret_path`), and
#: the code classifier flags `~/.aws/credentials` in a cell. The host tool
#: plane was the one surface still matching only the last segment, so this
#: aligns it with three existing policies rather than inventing a fourth.
#:
#: Matched on any segment, the last one included: naming the directory itself
#: is the same request as naming a file in it.
#:
#: Measured before widening, over 182,494 files across real project trees and
#: 52,876 under `$HOME`: 2 and 10 additional denials respectively, every one of
#: them a genuine credential file (`.npmrc` auth tokens, `~/.ssh/*`,
#: `~/.config/gh/hosts.yml`). Zero additional denials in this repository. The
#: interactive cost of directory awareness is not the reason to keep a
#: basename-only denylist; there is no such cost to speak of.
_SECRET_DIR_SEGMENTS = frozenset(
    {".ssh", ".aws", ".gnupg", ".docker", ".kube", ".azure"}
)

#: Credential directories that only bear credentials under a specific parent,
#: matched as an adjacent run of segments: `gcloud` and `gh` on their own are
#: ordinary words, and a substring test over the joined path would also match
#: `notes/.config/gcloud-migration-plan.md`.
_SECRET_DIR_SEQUENCES = ((".config", "gcloud"), (".config", "gh"))

#: Names credential-bearing often enough to refuse when *no human will ever
#: see the path*, and not often enough to refuse when one will.
#:
#: This is the second tier, and it exists because the same table cannot serve
#: both call sites. `config.json` is the whole argument: it is the Docker
#: registry auth file and it is also an utterly ordinary filename -- 7 of the 8
#: paths this tier adds over `is_secret_path` under `$HOME`, and 2 of 2 under
#: `~/Documents`, were ordinary `config.json` files. Refusing those in
#: `is_secret_path` would deny an interactive read with no approval path (the
#: denylist is a hard pre-gate, not a prompt), to buy nothing: under `.docker/`
#: the directory rule already catches it.
#:
#: For automatic approval the trade runs the other way. A match is promoted to
#: an audited `ask`: an attached human can review the false positive, while a
#: headless run refuses it instead of guessing. Use this one from that review
#: policy; use `is_secret_path` for the unconditional tool pre-gate.
_UNATTENDED_SECRET_BASENAMES = frozenset(
    {
        "credentials",
        "credentials.json",
        "service-account.json",
        "service_account.json",
        "known_hosts",
        "authorized_keys",
        "hosts.yml",
        "hosts.json",
        "token.json",
        "access-token",
        "config.json",
    }
)


def _normalize_policy_segment(value: str) -> str:
    """Filesystem-compatible case normalization shared by every policy path.

    Default macOS filesystems treat spellings such as ``.Kube`` and ``.kube``,
    or NFC/NFD forms such as ``Café`` and ``Café``, as the same object.
    Case-folding plus NFC keeps the raw gate, complete directory inventory, and
    prospective-path matching aligned with that identity; an incomplete
    inventory fails closed instead of weakening this normalization.
    """

    return unicodedata.normalize("NFC", value.casefold())


def _path_segments(path: str) -> tuple[str, ...]:
    """Case-folded path segments with both separators normalized."""
    normalized = (path or "").replace("\\", "/").strip()
    return tuple(
        _normalize_policy_segment(part)
        for part in normalized.split("/")
        if part and part != "."
    )


def _has_secret_directory(segments: tuple[str, ...]) -> bool:
    """Whether any segment (or adjacent run) names a credential directory."""
    if any(segment in _SECRET_DIR_SEGMENTS for segment in segments):
        return True
    for sequence in _SECRET_DIR_SEQUENCES:
        span = len(sequence)
        for start in range(len(segments) - span + 1):
            if segments[start : start + span] == sequence:
                return True
    return False


def is_secret_path(path: str) -> bool:
    """Return whether a path is on the host tool secret denylist.

    Directory-aware: a credential-bearing parent segment refuses the read even
    when the basename is generic. Callers pass a workspace-*relative* path, so
    a workspace that itself sits inside a credential directory stays readable
    -- the same carve-out `_default_secret_read_denials` makes for the kernel,
    and for the same reason: a boundary that denies its own root is unusable
    rather than strict.
    """
    segments = _path_segments(path)
    if not segments:
        return False
    if any(fnmatch.fnmatchcase(segments[-1], pattern) for pattern in _SECRET_BASENAMES):
        return True
    return _has_secret_directory(segments)


def is_credential_path(path: str) -> bool:
    """``is_secret_path`` widened for automatic approval review.

    The one predicate for the allow-to-ask fence, so its automatic reviewer
    does not carry a private copy that drifts from the copy the tools enforce.
    See `_UNATTENDED_SECRET_BASENAMES` for why the two tiers are not one.
    """
    segments = _path_segments(path)
    if not segments:
        return False
    return is_secret_path(path) or segments[-1] in _UNATTENDED_SECRET_BASENAMES


class _UnsafeAliasInspection(ValueError):
    """A secret-alias check could not prove that a path is safe."""


def _traverses_secret_symlink_path(workspace: Path, path: Path) -> bool:
    """Whether a stable symlink chain names a secret path before resolving.

    ``Path.resolve()`` deliberately forgets the names used to reach a file. If
    ``.ssh`` is itself a symlink to ``vault``, resolving an innocuous alias to
    ``.ssh/known_hosts`` leaves only ``vault/known_hosts`` for the final check.
    Walk the chain lexically as well, so every intermediate destination is
    classified before its credential-bearing segment disappears.

    This complements the final canonical-path check; it does not make the
    later pathname open atomic against concurrent filesystem mutation.
    """
    if path.is_absolute():
        try:
            inside = path.relative_to(workspace)
        except ValueError:
            raise _UnsafeAliasInspection(
                "path escapes the workspace during secret alias inspection"
            ) from None
        current = workspace
        pending = deque(inside.parts)
    else:
        current = workspace
        pending = deque(path.parts)

    symlink_hops = 0
    while pending:
        segment = pending.popleft()
        if segment in ("", "."):
            continue
        if segment == "..":
            # Apply `..` to the already-resolved prefix. Calling abspath on the
            # whole candidate first is wrong for `link/..`: POSIX applies the
            # parent step to the link destination, not to the link's spelling.
            current = current.parent
            try:
                current.relative_to(workspace)
            except ValueError:
                raise _UnsafeAliasInspection(
                    "path escapes the workspace during secret alias inspection"
                ) from None
            continue

        current /= segment
        try:
            inside = current.relative_to(workspace)
        except ValueError:
            inside = None
        if inside is not None and is_secret_path(str(inside)):
            return True

        try:
            is_link = current.is_symlink()
        except OSError as error:
            raise _UnsafeAliasInspection(
                "secret alias inspection could not read the path"
            ) from error
        if not is_link:
            continue

        symlink_hops += 1
        if symlink_hops > 40:
            raise _UnsafeAliasInspection("secret alias inspection exceeded link limit")
        try:
            destination = Path(os.readlink(current))
        except OSError as error:
            raise _UnsafeAliasInspection(
                "secret alias inspection could not read a link"
            ) from error
        if destination.is_absolute():
            try:
                inside_destination = destination.relative_to(workspace)
            except ValueError:
                raise _UnsafeAliasInspection(
                    "path escapes the workspace during secret alias inspection"
                ) from None
            current = workspace
            destination_parts = inside_destination.parts
        else:
            current = current.parent
            destination_parts = destination.parts
        pending.extendleft(reversed(destination_parts))
    return False


class _SecretAliasSnapshot:
    """One bounded tool-call inventory of credential-bearing aliases.

    Reverse symlink lookup does not exist: proving that ``vault/file`` is not
    also named ``nested/.ssh/file`` requires inspecting the workspace, not only
    the target's ancestors. The inventory therefore completes a no-follow DFS
    before answering its first candidate. It retains only canonical secret
    roots/exact files and prospective sequence parents; work is capped by the
    shared entry/time budgets, and any unreadable or incomplete walk refuses
    the operation instead of treating missing evidence as safety.

    The snapshot is deliberately operation-scoped. Concurrent mutations after
    the completed inventory remain part of the documented check/open race; a
    cross-operation negative cache would turn ordinary later file creation
    into a persistent bypass and is not used.
    """

    def __init__(
        self, workspace: Path, *, include_unattended_basenames: bool = False
    ) -> None:
        self.workspace = workspace
        self._include_unattended_basenames = include_unattended_basenames
        self._deadline = time.monotonic() + MAX_SCAN_SECONDS
        self._remaining_entries = MAX_SCAN_ENTRIES
        self._prepared = False
        self._secret_roots: tuple[Path, ...] = ()
        self._secret_root_identities: frozenset[tuple[int, int]] = frozenset()
        self._secret_exact: frozenset[Path] = frozenset()
        self._secret_exact_identities: frozenset[tuple[int, int]] = frozenset()
        self._sequence_parents: tuple[
            tuple[Path, tuple[str, ...], tuple[int, int] | None], ...
        ] = ()

    def _check_time(self) -> None:
        if time.monotonic() > self._deadline:
            raise _UnsafeAliasInspection("secret alias inspection timed out")

    def _consume_entry(self) -> None:
        self._check_time()
        if self._remaining_entries <= 0:
            raise _UnsafeAliasInspection("secret alias inspection exceeded its budget")
        self._remaining_entries -= 1

    def _signature(
        self, directory: Path, *, missing_ok: bool = False
    ) -> tuple[int, int, int, int, int] | None:
        self._check_time()
        try:
            metadata = directory.stat()
        except FileNotFoundError:
            if missing_ok:
                # A `.config` symlink may deliberately point at a directory a
                # later write would create. Its prospective canonical suffix
                # is still recorded even though there are no children to scan.
                return None
            raise _UnsafeAliasInspection(
                "secret alias inventory changed while it was scanned"
            ) from None
        except OSError as error:
            raise _UnsafeAliasInspection(
                "secret alias inspection could not stat a directory"
            ) from error
        return (
            int(metadata.st_dev),
            int(metadata.st_ino),
            int(metadata.st_mtime_ns),
            int(metadata.st_ctime_ns),
            int(metadata.st_mode),
        )

    def _resolve_candidate(self, candidate: Path) -> Path:
        self._check_time()
        try:
            return candidate.resolve()
        except (OSError, RuntimeError) as error:
            raise _UnsafeAliasInspection(
                "secret alias inspection could not resolve a candidate"
            ) from error

    def _resolve_subtree_candidate(self, candidate: Path) -> Path:
        """Resolve a directory-shaped alias without inventorying outside."""

        resolved = self._resolve_candidate(candidate)
        try:
            resolved.relative_to(self.workspace)
        except ValueError:
            # Following a credential directory outside to search for a later
            # symlink back in would cross the workspace inspection boundary.
            # Ignoring it is also unsafe, so refuse the operation instead.
            raise _UnsafeAliasInspection(
                "secret directory alias leaves the workspace"
            ) from None
        return resolved

    def _resolve_inside_candidate(self, candidate: Path, *, kind: str) -> Path:
        resolved = self._resolve_candidate(candidate)
        try:
            resolved.relative_to(self.workspace)
        except ValueError:
            raise _UnsafeAliasInspection(
                f"secret {kind} alias leaves the workspace"
            ) from None
        return resolved

    def _identity(self, candidate: Path, *, missing_ok: bool) -> tuple[int, int] | None:
        self._check_time()
        try:
            metadata = candidate.stat()
        except FileNotFoundError:
            if missing_ok:
                return None
            raise _UnsafeAliasInspection(
                "secret alias target changed during inspection"
            ) from None
        except OSError as error:
            raise _UnsafeAliasInspection(
                "secret alias inspection could not stat a target"
            ) from error
        return (int(metadata.st_dev), int(metadata.st_ino))

    @staticmethod
    def _policy_relative(path: Path, parent: Path) -> tuple[str, ...] | None:
        """Component-relative path under the policy's case-fold semantics."""

        path_parts = path.parts
        parent_parts = parent.parts
        if len(path_parts) < len(parent_parts):
            return None
        normalized_path = tuple(
            _normalize_policy_segment(part) for part in path_parts[: len(parent_parts)]
        )
        normalized_parent = tuple(
            _normalize_policy_segment(part) for part in parent_parts
        )
        if normalized_path != normalized_parent:
            return None
        return path_parts[len(parent_parts) :]

    def _target_ancestor_identities(
        self, target: Path
    ) -> tuple[tuple[Path, tuple[int, int]], ...]:
        """Existing target/ancestors, retaining target spelling for suffixes."""

        ancestors: list[tuple[Path, tuple[int, int]]] = []
        cursor = target
        while True:
            identity = self._identity(cursor, missing_ok=True)
            if identity is not None:
                ancestors.append((cursor, identity))
            if cursor == self.workspace:
                break
            parent = cursor.parent
            if parent == cursor:
                break
            cursor = parent
        return tuple(ancestors)

    @staticmethod
    def _secret_root_length(parts: tuple[str, ...]) -> int | None:
        """Length through the first credential-directory run in ``parts``."""

        normalized = tuple(_normalize_policy_segment(part) for part in parts)
        for start, segment in enumerate(normalized):
            if segment in _SECRET_DIR_SEGMENTS:
                return start + 1
            for sequence in _SECRET_DIR_SEQUENCES:
                span = len(sequence)
                if normalized[start : start + span] == sequence:
                    return start + span
        return None

    def _is_secret_basename(self, name: str) -> bool:
        normalized = _normalize_policy_segment(name)
        hard_secret = any(
            fnmatch.fnmatchcase(normalized, pattern) for pattern in _SECRET_BASENAMES
        )
        return hard_secret or (
            self._include_unattended_basenames
            and normalized in _UNATTENDED_SECRET_BASENAMES
        )

    def _entry_is_dir(self, entry: os.DirEntry[str], *, follow: bool) -> bool:
        try:
            return entry.is_dir(follow_symlinks=follow)
        except OSError as error:
            raise _UnsafeAliasInspection(
                "secret alias inspection could not classify an entry"
            ) from error

    def _entry_is_symlink(self, entry: os.DirEntry[str]) -> bool:
        try:
            return entry.is_symlink()
        except OSError as error:
            raise _UnsafeAliasInspection(
                "secret alias inspection could not classify a link"
            ) from error

    def _matching_children(self, directory: Path, wanted: str) -> list[Path]:
        """Complete direct-child scan used only below a `.config` symlink."""

        before = self._signature(directory, missing_ok=True)
        if before is None or not stat.S_ISDIR(before[-1]):
            return []
        matches: list[Path] = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    self._consume_entry()
                    if _normalize_policy_segment(entry.name) == wanted:
                        matches.append(directory / entry.name)
        except FileNotFoundError:
            return []
        except OSError as error:
            raise _UnsafeAliasInspection(
                "secret alias inspection could not scan a sequence directory"
            ) from error
        after = self._signature(directory, missing_ok=True)
        if before != after:
            raise _UnsafeAliasInspection(
                "secret alias inventory changed while it was scanned"
            )
        return matches

    def _scan_workspace(
        self,
    ) -> tuple[set[Path], set[Path], dict[Path, str], set[Path]]:
        """Return subtree, exact, sequence-prefix and symlink-prefix candidates."""

        subtree_candidates: set[Path] = set()
        exact_candidates: set[Path] = set()
        sequence_candidates: dict[Path, str] = {}
        sequence_aliases: set[Path] = set()
        stack = [self.workspace]
        visited: set[tuple[int, int]] = set()

        while stack:
            directory = stack.pop()
            before = self._signature(directory)
            assert before is not None
            if not stat.S_ISDIR(before[-1]):
                raise _UnsafeAliasInspection(
                    "secret alias inventory encountered a non-directory"
                )
            identity = (before[0], before[1])
            if identity in visited:
                continue
            visited.add(identity)
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        self._consume_entry()
                        path = directory / entry.name
                        relative = path.relative_to(self.workspace)
                        parts = relative.parts
                        root_length = self._secret_root_length(parts)
                        nofollow_directory = self._entry_is_dir(entry, follow=False)

                        if root_length is not None:
                            root = self.workspace.joinpath(*parts[:root_length])
                            subtree_candidates.add(root)
                            if len(parts) > root_length:
                                if self._entry_is_dir(entry, follow=True):
                                    subtree_candidates.add(path)
                                else:
                                    exact_candidates.add(path)
                        elif self._is_secret_basename(entry.name):
                            exact_candidates.add(path)

                        normalized = _normalize_policy_segment(entry.name)
                        sequence = next(
                            (
                                item
                                for item in _SECRET_DIR_SEQUENCES
                                if item[0] == normalized
                            ),
                            None,
                        )
                        if sequence is not None:
                            sequence_candidates[path] = normalized
                            if not nofollow_directory:
                                sequence_aliases.add(path)

                        if nofollow_directory:
                            stack.append(path)
            except OSError as error:
                raise _UnsafeAliasInspection(
                    "secret alias inventory could not scan the workspace"
                ) from error
            after = self._signature(directory)
            if before != after:
                raise _UnsafeAliasInspection(
                    "secret alias inventory changed while it was scanned"
                )

        return (
            subtree_candidates,
            exact_candidates,
            sequence_candidates,
            sequence_aliases,
        )

    def _collect_secret_descendants(
        self, roots: set[Path]
    ) -> tuple[set[Path], set[Path], set[tuple[int, int]], set[tuple[int, int]]]:
        """Expand secret roots and collect file identities below them.

        The workspace DFS intentionally does not follow `.ssh -> vault`, so it
        sees `vault/file` before knowing that the same tree is credential data.
        This bounded second pass over only the resolved secret roots records
        file identities too, preventing a hardlink outside the tree from
        shedding that classification.
        """

        canonical_roots = set(roots)
        exact_paths: set[Path] = set()
        root_identities: set[tuple[int, int]] = set()
        exact_identities: set[tuple[int, int]] = set()
        stack = list(roots)
        visited: set[tuple[int, int]] = set()

        while stack:
            directory = stack.pop()
            before = self._signature(directory, missing_ok=True)
            if before is None:
                continue
            identity = (before[0], before[1])
            if not stat.S_ISDIR(before[-1]):
                exact_paths.add(directory)
                exact_identities.add(identity)
                continue
            root_identities.add(identity)
            if identity in visited:
                continue
            visited.add(identity)
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        self._consume_entry()
                        path = directory / entry.name
                        if self._entry_is_dir(entry, follow=False):
                            stack.append(path)
                            continue
                        if self._entry_is_symlink(entry):
                            linked_root = self._resolve_subtree_candidate(path)
                            if linked_root not in canonical_roots:
                                canonical_roots.add(linked_root)
                                stack.append(linked_root)
                            continue
                        exact_paths.add(
                            self._resolve_inside_candidate(path, kind="file")
                        )
                        file_identity = self._identity(path, missing_ok=True)
                        if file_identity is not None:
                            exact_identities.add(file_identity)
            except OSError as error:
                raise _UnsafeAliasInspection(
                    "secret alias inspection could not scan a secret root"
                ) from error
            after = self._signature(directory, missing_ok=True)
            if before != after:
                raise _UnsafeAliasInspection(
                    "secret alias inventory changed while it was scanned"
                )

        return canonical_roots, exact_paths, root_identities, exact_identities

    def _prepare(self) -> None:
        if self._prepared:
            return
        (
            subtree_candidates,
            exact_candidates,
            sequence_candidates,
            sequence_aliases,
        ) = self._scan_workspace()

        sequence_parents: set[tuple[Path, tuple[str, ...], tuple[int, int] | None]] = (
            set()
        )
        for candidate, first in sequence_candidates.items():
            parent = self._resolve_subtree_candidate(candidate)
            parent_identity = self._identity(parent, missing_ok=True)
            for sequence in _SECRET_DIR_SEQUENCES:
                if sequence[0] == first:
                    sequence_parents.add((parent, sequence[1:], parent_identity))

        # A real `.config` directory was already walked above. A symlink was
        # intentionally not followed by the workspace DFS, so inspect only its
        # fixed sequence children and retain no ordinary entry.
        for candidate in sequence_aliases:
            first = _normalize_policy_segment(candidate.name)
            for sequence in _SECRET_DIR_SEQUENCES:
                if sequence[0] != first:
                    continue
                level = [candidate]
                for segment in sequence[1:]:
                    level = [
                        child
                        for directory in level
                        for child in self._matching_children(directory, segment)
                    ]
                subtree_candidates.update(level)

        canonical_roots = {
            self._resolve_subtree_candidate(path) for path in subtree_candidates
        }
        (
            canonical_roots,
            descendant_exact,
            root_identities,
            descendant_identities,
        ) = self._collect_secret_descendants(canonical_roots)
        self._secret_roots = tuple(sorted(canonical_roots, key=str))
        self._secret_root_identities = frozenset(root_identities)

        resolved_exact: set[Path] = set(descendant_exact)
        exact_identities: set[tuple[int, int]] = set(descendant_identities)
        for path in exact_candidates:
            resolved_exact.add(self._resolve_inside_candidate(path, kind="file"))
            identity = self._identity(path, missing_ok=True)
            if identity is not None:
                exact_identities.add(identity)
        self._secret_exact = frozenset(resolved_exact)
        self._secret_exact_identities = frozenset(exact_identities)
        self._sequence_parents = tuple(
            sorted(sequence_parents, key=lambda item: (str(item[0]), item[1]))
        )
        self._prepared = True

    def contains(self, target: Path) -> bool:
        """Whether a named credential directory resolves over ``target``.

        The lexical walk catches ``notes -> .ssh/known_hosts``. This snapshot
        additionally catches ``notes -> vault/known_hosts`` when a separate
        case-insensitive ``.ssh -> vault`` alias names the same canonical tree.
        """

        try:
            target.relative_to(self.workspace)
        except ValueError:
            return False
        self._prepare()
        if any(
            self._policy_relative(target, secret_file) == ()
            for secret_file in self._secret_exact
        ):
            return True
        ancestors = self._target_ancestor_identities(target)
        if ancestors and ancestors[0][0] == target:
            target_identity = ancestors[0][1]
        else:
            target_identity = None
        if target_identity in self._secret_exact_identities:
            return True
        for secret_root in self._secret_roots:
            if self._policy_relative(target, secret_root) is not None:
                return True
        if any(
            identity in self._secret_root_identities
            for _ancestor, identity in ancestors
        ):
            return True
        for parent, suffix, parent_identity in self._sequence_parents:
            remainder = self._policy_relative(target, parent)
            if remainder is not None:
                normalized = tuple(
                    _normalize_policy_segment(part) for part in remainder[: len(suffix)]
                )
                if normalized == suffix:
                    return True
            if parent_identity is None:
                continue
            for ancestor, identity in ancestors:
                if identity != parent_identity:
                    continue
                remainder = target.parts[len(ancestor.parts) :]
                normalized = tuple(
                    _normalize_policy_segment(part) for part in remainder[: len(suffix)]
                )
                if normalized == suffix:
                    return True
        return False


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

    def _resolved_path_checker(
        self, *, include_unattended_basenames: bool
    ) -> Callable[[Path], bool]:
        """Build one operation-scoped checker for actual file candidates.

        Bulk tools reuse its alias snapshot so every file is classified by the
        same directory-aware rule without rescanning the workspace. A fresh
        checker is created for every tool execution, and its bounded inventory
        completes before the first candidate decision.
        """

        workspace = self.workspace()
        aliases = _SecretAliasSnapshot(
            workspace,
            include_unattended_basenames=include_unattended_basenames,
        )
        predicate = (
            is_credential_path if include_unattended_basenames else is_secret_path
        )

        def is_secret(candidate: Path) -> bool:
            path = Path(candidate)
            if _traverses_secret_symlink_path(workspace, path):
                return True
            try:
                target = (path if path.is_absolute() else workspace / path).resolve()
                inside = target.relative_to(workspace)
            except ValueError:
                return True
            except (OSError, RuntimeError) as error:
                raise _UnsafeAliasInspection(
                    "secret alias inspection could not resolve a path"
                ) from error
            return predicate(str(inside)) or aliases.contains(target)

        return is_secret

    def resolved_secret_checker(self) -> Callable[[Path], bool]:
        """Return the hard secret guard used by interactive file tools."""

        return self._resolved_path_checker(include_unattended_basenames=False)

    def resolved_credential_checker(self) -> Callable[[Path], bool]:
        """Return the wider alias/inode guard used only by auto-review."""

        return self._resolved_path_checker(include_unattended_basenames=True)

    def resolve(self, relative: str, *, must_exist: bool = False) -> Path:
        """Resolve a path and reject parent, absolute, and symlink escapes."""
        workspace = self.workspace()
        path = Path(relative)
        traverses_secret = _traverses_secret_symlink_path(workspace, path)
        target = (path if path.is_absolute() else workspace / path).resolve()
        try:
            inside = target.relative_to(workspace)
        except ValueError:
            raise ValueError(
                f"path escapes the workspace: {relative!r} "
                "(stay inside your working dir)"
            )
        # The denylist applied to the RESOLVED path, not only to the string the
        # caller wrote. `HostDispatcher` pre-gates the raw argument, which a
        # symlink walks straight around: with the workspace at `$HOME` (what
        # the CLI does -- `agent/loop.py` sets it to the run cwd), a cell that
        # cannot read `~/.ssh/id_rsa` under the OS sandbox could link it to
        # `notes.txt` and have the unsandboxed daemon read it through
        # `read_file`. One static check here covers read, write, edit, glob,
        # grep, list_dir and web_download, which is the whole set that resolves.
        # Those callers still open by pathname after this check, so this is not
        # an atomic defence against a concurrent filesystem mutation.
        if (
            traverses_secret
            or is_secret_path(str(inside))
            or _SecretAliasSnapshot(workspace).contains(target)
        ):
            raise ValueError(
                "access to secret files (.env / keys / credential directories) "
                f"is blocked: {relative}"
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
    "is_credential_path",
    "is_secret_path",
]
