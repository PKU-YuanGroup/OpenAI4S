"""Generation-aware context compaction and content-addressed output archives.

The public ``estimate_tokens`` / ``should_compact`` / ``safe_keep_recent`` /
``keep_recent_by_tokens`` / ``compact`` entry points remain compatible with
the original implementation.
The V2 helpers make the policy's previously implicit contracts explicit:

* text, images, native tool calls, and provider wire state are budgeted
  independently;
* oversized result content is stored below the caller-authorized compaction
  directory and replaced by a bounded preview plus a SHA-256 reference;
* native assistant/tool batches and code/observation pairs are indivisible;
* summaries are normalized into a structured, generation-aware handoff; and
* compaction archives carry branch/ledger/recovery metadata without importing
  the persistence layer.

Only JSON-compatible values are archived.  This module deliberately has no
Store, Gateway, provider, or kernel dependency.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from openai4s.config import Config
from openai4s.llm import chat, get_model_capabilities
from openai4s.prompts import SUMMARY_FORK

_SUMMARY_SYSTEM = SUMMARY_FORK

# A result larger than this is already costly enough to dominate several
# ordinary turns.  Callers can override it through ``CompactionPolicy`` or the
# public helper without adding a Config field (and thus without breaking old
# Config-like test doubles).
DEFAULT_LARGE_OUTPUT_CHARS = 16_384
DEFAULT_PREVIEW_CHARS = 768
IMAGE_TOKEN_ESTIMATE = 1_024
_SUMMARY_TOOL_ARG_CHARS = 2_000
_SUMMARY_MESSAGE_KEYS = (
    "role",
    "content",
    "name",
    "tool_call_id",
    "is_error",
    "compaction_handoff",
)

HANDOFF_FIELDS = (
    "Objective",
    "Constraints",
    "Decisions",
    "Done",
    "In Progress",
    "Blocked",
    "Next Move",
    "Key Artifacts",
    "Active Kernel Generation",
)

_CODE_FENCE_RE = re.compile(r"(^|\n)\s*`{3,}(?:python|py|r)\s*\n", re.IGNORECASE)
_HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Hangul jamo, CJK punctuation, hiragana/katakana, bopomofo, Hangul
# compatibility jamo, extension A, unified ideographs, Hangul syllables,
# compatibility ideographs, half/fullwidth forms, and the supplementary
# ideographic planes.  Runs are matched rather than single characters: a
# per-character ``findall`` over a megabyte of Chinese allocates one string
# per ideograph just to count them, and this runs on every estimate.
_CJK_RUN_RE = re.compile(
    "[\u1100-\u11ff\u3000-\u303f\u3040-\u30ff\u3100-\u312f\u3130-\u318f"
    "\u31a0-\u31bf\u3400-\u4dbf\u4e00-\u9fff\ua960-\ua97f\uac00-\ud7af"
    "\ud7b0-\ud7ff\uf900-\ufaff\uff00-\uffef\U00020000-\U0003ffff]+"
)
# The framing that tells the model this system message is compacted history
# rather than standing instruction. Restore must reproduce it byte for byte,
# so it is a shared constant instead of a literal in each of the two paths.
COMPACTION_NOTE_PREFIX = (
    "[compacted history — earlier atomic action groups were archived "
    "and summarized; runtime continuity is stated explicitly below]\n\n"
)
_PREVIOUS_HANDOFF_PREFIX = (
    "PREVIOUS HANDOFF (authoritative; carry every fact forward; "
    "Decisions, Done and Key Artifacts are append-only — never drop an item):\n"
)


class CompactionSummaryError(RuntimeError):
    """The compaction LLM returned an empty or truncated-incomplete summary."""


class CompactionCancelled(RuntimeError):
    """The run was cancelled between summary chunks; nothing was adopted."""


@dataclass(frozen=True)
class ContextEstimate:
    """Approximate provider input cost split by independently useful class."""

    text: int = 0
    images: int = 0
    tool_schemas: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    artifact_refs: int = 0
    wire_state: int = 0
    #: The system prompt, kept apart from conversation text because the two
    #: answer to different remedies. Standing context -- memory, skills,
    #: specialists, connectors, environments -- is rebuilt from scratch every
    #: turn and compaction never touches it. Counted inside ``text``, a large
    #: system prompt read as "your conversation is long", and the user reached
    #: for the one tool that cannot help.
    system_prompt: int = 0

    @property
    def total(self) -> int:
        return (
            self.text
            + self.images
            + self.tool_schemas
            + self.tool_calls
            + self.tool_results
            + self.artifact_refs
            + self.wire_state
            + self.system_prompt
        )

    def as_dict(self) -> dict[str, int]:
        return {**asdict(self), "total": self.total}


@dataclass(frozen=True)
class ContextSegment:
    """One message range that compaction must never split."""

    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class CompactionArchiveMetadata:
    """Persistence-neutral linkage recorded beside every compaction archive.

    Store-backed callers can project their durable identifiers into this
    value.  Local/legacy callers may leave every field unset.  The recovery
    pointer intentionally remains JSON-shaped rather than depending on a
    repository model that does not exist in the local runtime.
    """

    branch: str | None = None
    ledger_cursor: Any = None
    recovery_pointer: Any = None
    active_kernel_generation: Any = None
    previous_kernel_generation: Any = None
    kernel_restarted: bool = False

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any] | "CompactionArchiveMetadata" | None
    ) -> "CompactionArchiveMetadata":
        if isinstance(value, cls):
            return value
        source = dict(value or {})
        nested = source.get("compaction")
        if isinstance(nested, Mapping):
            source = {**source, **dict(nested)}

        active = _first_present(
            source,
            "active_kernel_generation",
            "kernel_generation",
            "generation_id",
            "generation",
        )
        previous = _first_present(
            source,
            "previous_kernel_generation",
            "prior_kernel_generation",
        )
        restarted = bool(
            source.get("kernel_restarted")
            or source.get("runtime_restarted")
            or (
                active is not None
                and previous is not None
                and str(active) != str(previous)
            )
        )
        branch = _first_present(source, "branch", "branch_id")
        return cls(
            branch=None if branch is None else str(branch),
            ledger_cursor=_first_present(
                source, "ledger_cursor", "action_ledger_cursor"
            ),
            recovery_pointer=_first_present(
                source,
                "recovery_pointer",
                "recovery_checkpoint",
                "checkpoint_id",
            ),
            active_kernel_generation=active,
            previous_kernel_generation=previous,
            kernel_restarted=restarted,
        )

    @property
    def namespace_continuity_known(self) -> bool:
        return self.active_kernel_generation is not None and not self.kernel_restarted

    def as_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


def _first_present(source: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _json_safe(value: Any) -> Any:
    """Return a deterministic JSON-compatible projection for audit files."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return repr(value)


def _json_text(value: Any) -> str:
    return json.dumps(
        _json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _chars_to_tokens(value: str) -> int:
    """CJK characters cost 1 token each; remaining characters 4-to-1.

    Empty input stays 0.  Any non-empty string is at least 1.  ASCII rounding
    matches the historical ``(len + 3) // 4`` ceiling so 4000 Latin characters
    still estimate as 1000 tokens.
    """
    if not value:
        return 0
    if value.isascii():
        return max(1, (len(value) + 3) // 4)
    cjk = sum(len(run) for run in _CJK_RUN_RE.findall(value))
    rest = len(value) - cjk
    return max(1, cjk + (rest + 3) // 4)


def _content_estimate(content: Any) -> tuple[int, int]:
    """Return ``(text_tokens, image_tokens)`` for provider-style content."""
    if content is None:
        return 0, 0
    if isinstance(content, str):
        return _chars_to_tokens(content), 0
    if isinstance(content, Sequence) and not isinstance(
        content, (str, bytes, bytearray)
    ):
        text_tokens = 0
        image_tokens = 0
        for block in content:
            if isinstance(block, Mapping):
                kind = str(block.get("type") or "").lower()
                is_image = kind in {
                    "image",
                    "image_url",
                    "input_image",
                    "output_image",
                } or any(key in block for key in ("image_url", "image", "source"))
                if is_image:
                    image_tokens += IMAGE_TOKEN_ESTIMATE
                    # Data URLs/base64 consume provider input in proportion to
                    # their payload; remote URLs keep the fixed image estimate.
                    serialized = _json_text(block)
                    if "base64" in serialized or "data:image" in serialized:
                        image_tokens += _chars_to_tokens(serialized)
                    continue
                block_text = block.get("text")
                if isinstance(block_text, str):
                    text_tokens += _chars_to_tokens(block_text)
                else:
                    text_tokens += _chars_to_tokens(_json_text(block))
            else:
                text_tokens += _chars_to_tokens(str(block))
        return text_tokens, image_tokens
    return _chars_to_tokens(_json_text(content)), 0


def _artifact_reference_tokens(message: Mapping[str, Any]) -> int:
    refs: list[Any] = []
    for key in ("artifact_ref", "artifact_refs", "artifacts"):
        value = message.get(key)
        if value not in (None, "", [], {}):
            refs.append(value)
    content = message.get("content")
    if isinstance(content, Sequence) and not isinstance(
        content, (str, bytes, bytearray)
    ):
        refs.extend(
            block
            for block in content
            if isinstance(block, Mapping)
            and str(block.get("type") or "").lower()
            in {"artifact", "artifact_ref", "file", "input_file"}
        )
    return _chars_to_tokens(_json_text(refs)) if refs else 0


def estimate_context(
    messages: Iterable[Mapping[str, Any]],
    tool_schemas: Iterable[Mapping[str, Any]] = (),
) -> ContextEstimate:
    """Estimate context by text/image/tool-call/provider-state components."""
    text = images = tool_calls = tool_results = artifact_refs = wire_state = 0
    system_prompt = 0
    for message in messages:
        content_text, content_images = _content_estimate(message.get("content"))
        # Eight framing tokens preserves the old API's conservative per-message
        # overhead and is accounted as text rather than a fifth hidden bucket.
        role = message.get("role")
        if role == "tool":
            tool_results += content_text + 8
        elif role == "system":
            system_prompt += content_text + 8
        else:
            text += content_text + 8
        images += content_images
        artifact_refs += _artifact_reference_tokens(message)
        if message.get("tool_calls"):
            tool_calls += _chars_to_tokens(_json_text(message["tool_calls"])) + 4
        if message.get("wire_state"):
            wire_state += _chars_to_tokens(_json_text(message["wire_state"])) + 4
    schema_tokens = sum(
        _chars_to_tokens(_json_text(schema)) + 4 for schema in tool_schemas
    )
    return ContextEstimate(
        text=text,
        images=images,
        tool_schemas=schema_tokens,
        tool_calls=tool_calls,
        tool_results=tool_results,
        artifact_refs=artifact_refs,
        wire_state=wire_state,
        system_prompt=system_prompt,
    )


def estimate_tokens(messages: list[dict]) -> int:
    """Backward-compatible total for :func:`estimate_context`."""
    return estimate_context(messages).total


def should_compact(
    messages: list[dict],
    cfg: Config,
    *,
    tool_schemas: Iterable[Mapping[str, Any]] = (),
    context_budget: int | None = None,
) -> bool:
    """True once estimated provider input crosses trigger ratio * window."""
    window = int(context_budget or cfg.context_window_tokens)
    budget = int(window * cfg.compaction_trigger_ratio)
    return estimate_context(messages, tool_schemas).total > budget


def _has_code_action(message: Mapping[str, Any]) -> bool:
    return message.get("role") == "assistant" and bool(
        _CODE_FENCE_RE.search(str(message.get("content") or ""))
    )


def segment_messages(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[ContextSegment, ...]:
    """Partition messages into atomic replay/compaction segments.

    An assistant declaration and every adjacent tool result are one segment.
    A Python/R code reply and its immediately following user observation are
    another.  Orphan contiguous tool results remain grouped defensively.
    """
    segments: list[ContextSegment] = []
    index = 0
    size = len(messages)
    while index < size:
        message = messages[index]
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            end = index + 1
            while end < size and messages[end].get("role") == "tool":
                end += 1
            segments.append(ContextSegment(index, end, "assistant_tool_group"))
            index = end
            continue
        if _has_code_action(message):
            end = index + 1
            if end < size and messages[end].get("role") == "user":
                end += 1
            segments.append(ContextSegment(index, end, "code_observation"))
            index = end
            continue
        if role == "tool":
            end = index + 1
            while end < size and messages[end].get("role") == "tool":
                end += 1
            segments.append(ContextSegment(index, end, "orphan_tool_results"))
            index = end
            continue
        segments.append(ContextSegment(index, index + 1, "message"))
        index += 1
    return tuple(segments)


def safe_keep_recent(messages: list[dict], minimum: int = 4) -> int:
    """Return an atomic tail of at least ``minimum`` messages.

    This generalizes the old assistant/tool-only guard to code/observation
    pairs while retaining the same return type and calling convention.
    """
    if minimum < 0:
        raise ValueError("minimum must be non-negative")
    if minimum == 0 or not messages:
        return 0
    start = max(0, len(messages) - minimum)
    for segment in segment_messages(messages):
        if segment.start < start < segment.end:
            start = segment.start
            break
    return len(messages) - start


def keep_recent_by_tokens(messages: list[dict], token_budget: int) -> int:
    """Return an atomic tail whose unexpanded estimate fits ``token_budget``.

    Messages are accumulated from the end until adding one more would exceed
    the budget.  The count is then expanded with :func:`safe_keep_recent` so
    a code/observation pair or assistant/tool batch is never split.  The
    expansion may push the kept estimate above ``token_budget``.
    """
    if token_budget < 0:
        raise ValueError("token_budget must be non-negative")
    if token_budget == 0 or not messages:
        return 0
    kept = 0
    tokens = 0
    for index in range(len(messages) - 1, -1, -1):
        cost = estimate_context([messages[index]]).total
        if tokens + cost > token_budget:
            break
        tokens += cost
        kept += 1
    return safe_keep_recent(messages, kept)


def _existing_handoff_text(messages: Sequence[Mapping[str, Any]]) -> str | None:
    # The note's banner is framing for the live model, not handoff content.
    # Shown under "PREVIOUS HANDOFF (carry every fact forward)" the
    # summarizer echoes it, and compact() would then prepend it a second
    # time while the ledger restore prepends it once.
    parts = [
        str(message.get("content") or "").removeprefix(COMPACTION_NOTE_PREFIX)
        for message in messages
        if message.get("compaction_handoff")
    ]
    text = "\n\n".join(part for part in parts if part.strip())
    return text or None


def _summary_pieces(
    messages: Sequence[Mapping[str, Any]], metadata: CompactionArchiveMetadata
) -> list[tuple[list[dict], int]]:
    """Atomic segments, each priced as the transcript text the model receives.

    The cost is the serialized transcript form, not the raw message estimate:
    the JSON wrapping is part of what the request carries.  The runtime
    header ``_summary_input`` prepends is priced once per request by the
    caller, not once per segment here.
    """
    del metadata  # the header is the caller's reserve, not a per-piece cost
    pieces: list[tuple[list[dict], int]] = []
    for segment in segment_messages(messages):
        piece = list(messages[segment.start : segment.end])
        pieces.append((piece, _chars_to_tokens(_summary_transcript(piece))))
    return pieces


def _next_summary_batch(
    pieces: Sequence[tuple[list[dict], int]], start: int, limit: int
) -> tuple[list[dict], int]:
    """Take segments from ``start`` while they fit ``limit``; at least one.

    A single atomic segment that itself exceeds the limit becomes its own
    batch; segments are never split.  Returns the batch and the next index.
    """
    batch: list[dict] = []
    used = 0
    index = start
    while index < len(pieces):
        piece, cost = pieces[index]
        if batch and used + cost > limit:
            break
        batch.extend(piece)
        used += cost
        index += 1
        if used > limit:
            break
    return batch, index


def _compaction_windows(
    projected: list[dict], *, keep_recent: int, context_budget: int
) -> tuple[list[dict], list[dict], list[dict]] | None:
    """Return ``(head, middle, tail)`` or ``None`` when compacting is a no-op.

    Head is the first two non-handoff messages.  Handoff notes are stripped
    from middle.  The tail is ``max(keep_recent, 0.25 * context_budget)``;
    a token tail that would leave no middle falls back to ``keep_recent``.
    """
    head: list[dict] = []
    head_last_index = -1
    for index, message in enumerate(projected):
        if message.get("compaction_handoff"):
            continue
        head.append(message)
        head_last_index = index
        if len(head) == 2:
            break
    if len(head) < 2:
        return None
    middle_start = head_last_index + 1
    count_keep = safe_keep_recent(projected, keep_recent)
    token_keep = keep_recent_by_tokens(projected, int(0.25 * context_budget))
    tail_count = max(count_keep, token_keep)
    tail_start = len(projected) - tail_count
    if tail_start <= middle_start:
        tail_count = count_keep
        tail_start = len(projected) - tail_count
        if tail_start <= middle_start:
            return None
    middle = [
        projected[index]
        for index in range(middle_start, tail_start)
        if not projected[index].get("compaction_handoff")
    ]
    if not middle:
        return None
    return head, middle, projected[tail_start:]


def _summary_preamble(prev: str | None) -> str:
    """The previous handoff as it precedes a chunk, or ``""`` for the first.

    Built once so the reserve that sizes the chunk and the request body are
    the same bytes.
    """
    return _PREVIOUS_HANDOFF_PREFIX + prev + "\n\n" if prev else ""


def _summary_user_content(
    batch: Sequence[Mapping[str, Any]],
    metadata: CompactionArchiveMetadata,
    prev: str | None,
) -> str:
    return _summary_preamble(prev) + _summary_input(batch, metadata)


def _content_chars(content: Any) -> int:
    return len(content) if isinstance(content, str) else len(_json_text(content))


def _large_output_candidate(messages: Sequence[Mapping[str, Any]], index: int) -> bool:
    message = messages[index]
    role = message.get("role")
    if role == "tool":
        return True
    if role == "assistant":
        # Preserve executable source and native declarations in active context.
        return not _has_code_action(message) and not message.get("tool_calls")
    if role != "user" or index < 2:
        return False
    content = message.get("content")
    # Only the runtime's own observation shapes are outputs.  Every user-role
    # message the runtime synthesizes after a code action carries one of
    # these prefixes; a user's own next prompt after a Stop-cancelled cell
    # (no observation between them) and a pinned figure's multimodal parts
    # are not, and must never be replaced by a preview marker.
    if not isinstance(content, str):
        return False
    return content.startswith(("[Observation]", "[Tool Results]", "[Tool result]"))


def _confine(root: Path, relative: str | Path, what: str) -> Path:
    """Resolve ``relative`` below ``root`` or raise ``ValueError``.

    Both blob roots (the host archive and the kernel workspace) share this
    one check so neither can be hardened without the other.
    """
    base = root.expanduser().resolve()
    candidate = (base / relative).resolve()
    if base != candidate and base not in candidate.parents:
        raise ValueError(f"archive path escaped the {what}")
    return candidate


def _confined_blob_path(archive_dir: Path, digest: str) -> Path:
    if not _HEX_SHA256_RE.fullmatch(digest):
        raise ValueError("archive digest must be a lowercase SHA-256 hex string")
    return _confine(
        archive_dir,
        Path("blobs") / digest[:2] / f"{digest}.json",
        "authorized compaction directory",
    )


_WORKSPACE_CONTEXT_DIR = ".openai4s/context"


def _content_blob_payload(
    content: Any,
    message: Mapping[str, Any],
    metadata: CompactionArchiveMetadata,
) -> tuple[str, dict[str, Any]]:
    safe_content = _json_safe(content)
    canonical = _json_text(safe_content).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    payload = {
        "schema_version": 2,
        "kind": "context_content_blob",
        "sha256": digest,
        "content": safe_content,
        "message": {
            key: _json_safe(message[key])
            for key in ("role", "name", "tool_call_id", "wire_id", "is_error")
            if key in message
        },
        "metadata": metadata.as_dict(),
    }
    return digest, payload


def _verify_blob_payload(existing: Any, digest: str) -> Any:
    """Return the ``content`` of a blob payload whose digest checks out.

    The one check behind both the read path (``load_archived_content``) and
    the dedup-write path (``_verify_existing_blob``): the recorded digest
    must match and the content must hash to it.
    """
    if not isinstance(existing, Mapping) or existing.get("sha256") != digest:
        raise ValueError("archived content digest metadata does not match")
    content = existing.get("content")
    canonical = _json_text(content).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != digest:
        raise ValueError("archived content failed SHA-256 verification")
    return content


class _TornBlob(ValueError):
    """A file at a digest path that is not a blob at all (torn or garbage)."""


_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
# Every component below a blob root is opened by descriptor with O_NOFOLLOW,
# so a rename between the name check and the write cannot redirect it.  The
# platforms without ``dir_fd`` never spawn a kernel (native Windows is
# refused), so the path-based fallback there guards nothing that runs.
# ``os.stat(..., follow_symlinks=False)`` rather than ``os.lstat``: the older
# python-build-standalone macOS builds (3.10-3.12) leave ``lstat`` out of
# ``supports_dir_fd`` while ``stat`` with ``dir_fd`` is there on every one.
_DIR_FD_OK = (
    os.name == "posix"
    and all(
        fn in os.supports_dir_fd
        for fn in (os.open, os.mkdir, os.stat, os.unlink, os.rename)
    )
    and os.stat in os.supports_follow_symlinks
)


def _read_bounded(name: str, limit: int, *, dir_fd: int | None) -> bytes:
    """Read at most ``limit`` bytes without following a symlink or blocking."""
    flags = os.O_RDONLY | _O_NONBLOCK | _O_NOFOLLOW | _O_CLOEXEC
    fd = os.open(name, flags, dir_fd=dir_fd)
    try:
        chunks: list[bytes] = []
        remaining = limit
        while remaining > 0:
            piece = os.read(fd, min(65_536, remaining))
            if not piece:
                break
            chunks.append(piece)
            remaining -= len(piece)
        return b"".join(chunks)
    finally:
        os.close(fd)


def content_digest(content: Any) -> str:
    """The SHA-256 every archive path names for ``content``."""
    return hashlib.sha256(_json_text(content).encode("utf-8")).hexdigest()


def _verify_existing_blob(
    name: str, payload: Mapping[str, Any], *, dir_fd: int | None
) -> None:
    """Refuse a pre-existing file at a digest path unless it is that content.

    The workspace copy lives where a cell can write, so a file already at
    ``<sha256>.json`` cannot be trusted for its name: a Skill could plant
    different bytes there and the marker would then endorse them as the host
    archive.  The digest is recomputed from the file's ``content`` the same
    way ``load_archived_content`` checks it.

    Nor can the *object* be trusted: a FIFO there would park the daemon's
    turn thread in a plain read forever, a symlink would read some other
    file, and a huge file would be swallowed whole.  ``lstat`` first, a
    bounded no-follow non-blocking read second, JSON last.  ``name`` is
    relative to ``dir_fd`` when one is given.
    """
    digest = str(payload.get("sha256") or "")
    expected = len(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    limit = 2 * expected + 1_048_576
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as error:
        raise _TornBlob("existing archive blob is unreadable") from error
    if not stat.S_ISREG(info.st_mode):
        raise _TornBlob("existing archive blob is not a regular file")
    if info.st_size > limit:
        raise ValueError("existing archive blob is larger than this content")
    try:
        raw = _read_bounded(name, limit, dir_fd=dir_fd)
        existing = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as error:
        raise _TornBlob("existing archive blob is unreadable") from error
    try:
        _verify_blob_payload(existing, digest)
    except ValueError as error:
        raise ValueError("existing archive blob does not match its digest") from error


@contextlib.contextmanager
def _open_below(root: Path, relative_dir: PurePosixPath) -> Iterator[int]:
    """Yield a descriptor for ``root/relative_dir``, created if missing.

    ``_confine`` checks the *name* once.  The workspace is agent-writable, so
    a persistent cell can swap ``.openai4s`` or ``context`` for a symlink
    between that check and the write; a path-based ``mkdir``/``replace``
    would follow it and land the blob wherever the daemon can write.  Each
    component is opened relative to its parent's descriptor with
    ``O_NOFOLLOW`` and ``O_DIRECTORY``: a symlink is ELOOP, a file is
    ENOTDIR, and the bytes go into the directory that was actually checked.
    """
    # The root is host-chosen and already resolved; creating it by name is
    # not the race.  Everything below it is opened by descriptor.
    os.makedirs(root, exist_ok=True)
    handles = [os.open(str(root), os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC)]
    try:
        for part in relative_dir.parts:
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, dir_fd=handles[-1])
            handles.append(
                os.open(
                    part,
                    os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC,
                    dir_fd=handles[-1],
                )
            )
        yield handles[-1]
    finally:
        for handle in reversed(handles):
            with contextlib.suppress(OSError):
                os.close(handle)


def _write_exclusive_json_at(
    dir_fd: int | None, name: str, payload: Mapping[str, Any], *, host_owned: bool
) -> None:
    """Write ``payload`` at ``name`` atomically, or dedupe against what is there.

    Only a complete file ever appears at the digest path: the bytes go to a
    sibling temp file and are renamed into place, so a crash, ENOSPC, or a
    concurrent writer can never leave a torn ``<sha256>.json`` that every
    later externalization would then reject forever.  A file already there
    is accepted only if it *is* this content.  A torn one (not JSON at all,
    or not a regular file) is replaced on both roots.  A well-formed file
    with different content is replaced only under the host-owned archive; in
    the agent-writable workspace it is left alone and the caller records no
    reference to it.  ``name`` is relative to ``dir_fd``; with no descriptor
    it is a full path.
    """
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        try:
            _verify_existing_blob(name, payload, dir_fd=dir_fd)
            return
        except _TornBlob:
            pass
        except ValueError:
            if not host_owned:
                raise
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    base, leaf = os.path.split(name)
    tmp = os.path.join(base, f".{leaf}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(
        tmp,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC,
        0o644,
        dir_fd=dir_fd,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if dir_fd is None:
            os.replace(tmp, name)
        else:
            # rename(2) replaces an existing entry atomically on POSIX; the
            # descriptor pins the directory the name check looked at.
            os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dir_fd)
        raise


def _write_json_below(
    root: Path, relative: str, payload: Mapping[str, Any], *, host_owned: bool
) -> None:
    rel = PurePosixPath(relative)
    if not _DIR_FD_OK:
        if not host_owned:
            # Without descriptors the write would trust the name it checked;
            # the kernel-readable copy is best-effort, so it is skipped here
            # rather than written where a cell could redirect it.
            raise OSError("workspace blob needs dir_fd support to be written safely")
        target = root.joinpath(*rel.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_exclusive_json_at(None, str(target), payload, host_owned=host_owned)
        return
    with _open_below(root, rel.parent) as dir_fd:
        _write_exclusive_json_at(dir_fd, rel.name, payload, host_owned=host_owned)


def _write_content_blob(
    archive_dir: Path,
    digest: str,
    payload: Mapping[str, Any],
) -> str:
    _confined_blob_path(archive_dir, digest)  # digest shape and containment by name
    rel = f"blobs/{digest[:2]}/{digest}.json"
    _write_json_below(archive_dir.expanduser().resolve(), rel, payload, host_owned=True)
    return rel


def _workspace_context_ref(digest: str) -> str:
    """Return a workspace-relative, content-addressed archive path.

    The filename is the SHA-256 hex digest alone.  The returned string is
    always posix-style and never includes an absolute path or ``$HOME``.
    """
    if not _HEX_SHA256_RE.fullmatch(digest):
        raise ValueError("archive digest must be a lowercase SHA-256 hex string")
    return f"{_WORKSPACE_CONTEXT_DIR}/{digest}.json"


def _confined_workspace_blob_path(workspace: Path, digest: str) -> Path:
    return _confine(workspace, _workspace_context_ref(digest), "kernel workspace")


def _write_workspace_content_blob(
    workspace: Path | str,
    digest: str,
    payload: Mapping[str, Any],
) -> str:
    rel = _workspace_context_ref(digest)
    root = Path(workspace)
    _confined_workspace_blob_path(root, digest)  # containment by name, once
    _write_json_below(root.expanduser().resolve(), rel, payload, host_owned=False)
    return rel


def _preview(content: Any, limit: int) -> str:
    text = content if isinstance(content, str) else _json_text(content)
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def _read_back_hint(workspace_ref: str | None) -> str | None:
    """How a Python or R cell opens the workspace copy.

    Anchored on ``OPENAI4S_WORKSPACE``, which is exported into every Python
    and R worker's environment: the hint survives a cell that changed
    directory, works in a kernel that has no ``host`` object (R), and never
    prints an absolute path or ``$HOME`` into the context.
    """
    if workspace_ref is None:
        return None
    return (
        f"read the full text: JSON at {workspace_ref!r} under the kernel "
        "workspace ($OPENAI4S_WORKSPACE), key 'content' -- Python: import json, "
        "os; json.load(open(os.path.join(os.environ['OPENAI4S_WORKSPACE'], "
        f"{workspace_ref!r})))['content']; R: jsonlite::fromJSON(file.path("
        f"Sys.getenv('OPENAI4S_WORKSPACE'), {workspace_ref!r}))$content"
    )


def _archived_marker(
    title: str,
    fields: Sequence[tuple[str, Any]],
    preview: str,
    guidance: str | None,
) -> str:
    """The one marker shape every archive branch renders.

    ``guidance`` is the ``[system]`` sentence telling the model the marker is
    a preview and how to read the bytes back; the archive-only branch has
    none because its ``archive_ref`` is relative to a host directory no cell
    can open, and its exact text is a frozen pre-retrieval contract.
    """
    lines = [title, *(f"{key}: {value}" for key, value in fields)]
    lines.append(f"preview: {preview}")
    if guidance:
        lines.append(guidance)
    return "\n".join(lines)


def externalize_large_outputs(
    messages: Sequence[Mapping[str, Any]],
    archive_dir: Path | str | None,
    *,
    threshold_chars: int = DEFAULT_LARGE_OUTPUT_CHARS,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
    archive_metadata: Mapping[str, Any] | CompactionArchiveMetadata | None = None,
    artifact_archiver: (
        Callable[[Any, Mapping[str, Any], dict[str, Any]], Mapping[str, Any]] | None
    ) = None,
    workspace: Path | str | None = None,
) -> list[dict]:
    """Archive oversized outputs and return context-safe message copies.

    No write occurs when ``archive_dir``, ``artifact_archiver``, and
    ``workspace`` are all absent.  Paths are derived solely from a validated
    digest; message content can never choose an output path.  Markers and
    ``content_archive`` never carry an absolute path or ``$HOME``.
    """
    if threshold_chars <= 0:
        raise ValueError("threshold_chars must be positive")
    if preview_chars < 0:
        raise ValueError("preview_chars must be non-negative")
    result = [dict(message) for message in messages]
    workspace_root = Path(workspace) if workspace else None
    if archive_dir is None and artifact_archiver is None and workspace_root is None:
        return messages if isinstance(messages, list) else result
    root = Path(archive_dir) if archive_dir is not None else None
    metadata = CompactionArchiveMetadata.from_mapping(archive_metadata)
    changed = False
    for index, message in enumerate(messages):
        content = message.get("content")
        if content is None or not _large_output_candidate(messages, index):
            continue
        original_chars = _content_chars(content)
        if original_chars <= threshold_chars:
            continue
        digest, payload = _content_blob_payload(content, message, metadata)
        preview = _preview(content, preview_chars)
        # The kernel-readable copy is written on every path, and best-effort:
        # the workspace is agent-writable, so a cell can leave
        # ``.openai4s/context`` occupied, unreadable, or symlinked out (a
        # symlink loop is a RuntimeError from Path.resolve on 3.10-3.12), and
        # losing the copy must not lose the compaction (the archive or the
        # Artifact still holds the bytes).
        workspace_ref: str | None = None
        if workspace_root is not None:
            try:
                workspace_ref = _write_workspace_content_blob(
                    workspace_root, digest, payload
                )
            except (OSError, ValueError, RuntimeError):
                workspace_ref = None
        read_back = _read_back_hint(workspace_ref)
        if artifact_archiver is not None:
            archived = dict(
                artifact_archiver(
                    content,
                    message,
                    {
                        "sha256": digest,
                        "original_chars": original_chars,
                        "metadata": metadata.as_dict(),
                    },
                )
            )
            artifact_id = str(archived.get("artifact_id") or "")
            version_id = str(archived.get("version_id") or "")
            if not artifact_id or not version_id:
                raise ValueError("artifact archiver must return artifact_id/version_id")
            result[index]["content"] = _archived_marker(
                "[Large output archived as Artifact]",
                (
                    ("artifact_id", artifact_id),
                    ("version_id", version_id),
                    ("sha256", digest),
                    ("original_chars", original_chars),
                ),
                preview,
                "[system] This is a preview, not the output. Do not infer what "
                "is in the gap. From a Python cell, read it back with "
                f"open(host.artifact_path({version_id!r})).read() "
                "(JSON, key 'content')" + (f"; or {read_back}." if read_back else "."),
            )
            artifact_ref = {
                "artifact_id": artifact_id,
                "version_id": version_id,
                "sha256": digest,
                "original_chars": original_chars,
            }
            result[index]["artifact_refs"] = [artifact_ref]
            archive = dict(artifact_ref)
            if workspace_ref is not None:
                archive["workspace_ref"] = workspace_ref
            result[index]["content_archive"] = archive
        else:
            relative_path: str | None = None
            if root is not None:
                relative_path = _write_content_blob(root, digest, payload)
            if workspace_ref is None and relative_path is None:
                # Nothing durable holds the bytes; the output stays inline.
                continue
            if workspace_ref is not None:
                # The marker names only the path a cell can open; the host
                # ``archive_ref`` rides on ``content_archive`` for the host.
                result[index]["content"] = _archived_marker(
                    "[Large output archived]",
                    (("sha256", digest), ("original_chars", original_chars)),
                    preview,
                    "[system] This is a preview, not the output. Do not infer "
                    f"what is in the gap; {read_back}.",
                )
                archive = {
                    "sha256": digest,
                    "original_chars": original_chars,
                    "workspace_ref": workspace_ref,
                }
                if relative_path is not None:
                    archive["archive_ref"] = relative_path
                result[index]["content_archive"] = archive
            else:
                assert relative_path is not None
                result[index]["content"] = _archived_marker(
                    "[Large output archived]",
                    (
                        ("sha256", digest),
                        ("archive_ref", relative_path),
                        ("original_chars", original_chars),
                    ),
                    preview,
                    None,
                )
                result[index]["content_archive"] = {
                    "sha256": digest,
                    "archive_ref": relative_path,
                    "original_chars": original_chars,
                }
        changed = True
    if changed:
        return result
    return messages if isinstance(messages, list) else result


def load_archived_content(archive_dir: Path | str, digest: str) -> Any:
    """Resolve a content hash only inside an authorized compaction directory."""
    path = _confined_blob_path(Path(archive_dir), digest)
    return _verify_blob_payload(json.loads(path.read_text("utf-8")), digest)


def _runtime_handoff_value(metadata: CompactionArchiveMetadata) -> str:
    active = metadata.active_kernel_generation
    if active is None:
        return (
            "Unknown — in-memory variables are NOT assumed to exist; recover "
            "from workspace files, Artifacts, or an explicit recovery record."
        )
    if metadata.kernel_restarted:
        previous = metadata.previous_kernel_generation
        prior = f" (previous: {previous})" if previous is not None else ""
        return (
            f"{active}{prior} — the Kernel restarted; variables from earlier "
            "generations are NOT available."
        )
    return (
        f"{active} — continuity is reported for this generation; verify a "
        "variable before relying on it."
    )


def _handoff_titles_complete(text: str) -> bool:
    lowered = text.lower()
    return all(field.lower() in lowered for field in HANDOFF_FIELDS[:-1])


def _normalize_handoff(summary: str, metadata: CompactionArchiveMetadata) -> str:
    """Guarantee every machine-consumed handoff field and runtime truth."""
    text = (summary or "").strip()
    if not text:
        raise CompactionSummaryError("compaction summary was empty")
    if _handoff_titles_complete(text):
        # The model may have inferred stale runtime state.  Remove its Active
        # Kernel Generation section and append the host-authored fact instead.
        active_pattern = re.compile(
            r"(?ims)^#{0,3}\s*Active Kernel Generation\s*:?.*?(?=^#{0,3}\s*"
            + "|".join(re.escape(field) for field in HANDOFF_FIELDS[:-1])
            + r"\s*:|\Z)"
        )
        text = active_pattern.sub("", text).strip()
        return (
            text
            + "\n\n## Active Kernel Generation\n"
            + _runtime_handoff_value(metadata)
        )

    fields = {
        "Objective": "- Continue the original user objective retained above.",
        "Constraints": "- Preserve the explicit constraints in the retained task.",
        "Decisions": "- No additional structured decision was recorded.",
        "Done": text,
        "In Progress": "- Not recorded.",
        "Blocked": "- None recorded.",
        "Next Move": "- Re-evaluate the latest retained action group.",
        "Key Artifacts": "- See content hashes and Artifact references in context.",
        "Active Kernel Generation": _runtime_handoff_value(metadata),
    }
    return "\n\n".join(f"## {field}\n{fields[field]}" for field in HANDOFF_FIELDS)


def _truncate_summary_arg(value: Any, limit: int = _SUMMARY_TOOL_ARG_CHARS) -> Any:
    """Cut a tool argument to ``limit`` characters, marker included.

    Non-string arguments are rendered with the module's canonical
    ``_json_text`` so the cut point does not depend on insertion order.
    """
    if isinstance(value, str):
        safe: Any = value
        rendered = value
    else:
        safe = _json_safe(value)
        rendered = _json_text(safe)
    if len(rendered) <= limit:
        return safe
    marker = f"... [truncated, original_chars={len(rendered)}]"
    return rendered[: max(0, limit - len(marker))] + marker


def _summary_tool_call(call: Any) -> Any:
    if not isinstance(call, Mapping):
        return _json_safe(call)
    function = call.get("function")
    function_map = function if isinstance(function, Mapping) else {}
    slim: dict[str, Any] = {}
    name = call.get("name", function_map.get("name"))
    if name is not None:
        slim["name"] = name
    if "arguments" in call:
        slim["arguments"] = _truncate_summary_arg(call["arguments"])
    elif "arguments" in function_map:
        slim["arguments"] = _truncate_summary_arg(function_map["arguments"])
    if "raw_arguments" in call:
        slim["raw_arguments"] = _truncate_summary_arg(call["raw_arguments"])
    return slim


_IMAGE_BLOCK_KINDS = frozenset({"image", "image_url", "input_image", "output_image"})


def _summary_content(content: Any) -> Any:
    """Image parts are opaque to a text summarizer; keep text, mark the omission.

    A pinned figure's base64 would otherwise ride the transcript as text --
    priced by ``_summary_pieces`` at tens of thousands of tokens while
    ``estimate_context`` prices the same message at a flat image estimate,
    so it would form a solo over-budget chunk the provider rejects.
    """
    if not isinstance(content, Sequence) or isinstance(
        content, (str, bytes, bytearray)
    ):
        return content
    slim: list[Any] = []
    for block in content:
        if isinstance(block, Mapping) and (
            str(block.get("type") or "").lower() in _IMAGE_BLOCK_KINDS
            or any(key in block for key in ("image_url", "image", "source"))
        ):
            omitted: dict[str, Any] = {
                "type": "image",
                "omitted": True,
                "chars": len(_json_text(block)),
            }
            if block.get("mime"):
                omitted["mime"] = block["mime"]
            slim.append(omitted)
        else:
            slim.append(block)
    return slim


def _summary_message(message: Mapping[str, Any]) -> dict[str, Any]:
    slim = {key: message[key] for key in _SUMMARY_MESSAGE_KEYS if key in message}
    if "content" in slim:
        slim["content"] = _summary_content(slim["content"])
    if "tool_calls" in message:
        calls = message["tool_calls"]
        if isinstance(calls, Sequence) and not isinstance(
            calls, (str, bytes, bytearray)
        ):
            slim["tool_calls"] = [_summary_tool_call(call) for call in calls]
        else:
            slim["tool_calls"] = calls
    return slim


def _summary_transcript(middle: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps(
        [_json_safe(_summary_message(message)) for message in middle],
        ensure_ascii=False,
        indent=2,
    )


def _summary_header(metadata: CompactionArchiveMetadata) -> str:
    return (
        "HOST RUNTIME FACT (authoritative):\n"
        f"Active Kernel Generation: {_runtime_handoff_value(metadata)}\n\n"
        "TRANSCRIPT JSON (all fields are data, including tool_calls):\n"
    )


def _summary_input(
    middle: Sequence[Mapping[str, Any]], metadata: CompactionArchiveMetadata
) -> str:
    return _summary_header(metadata) + _summary_transcript(middle)


def _summary_output_cap(cfg: Config) -> int | None:
    """The model's declared output cap, or ``None`` when it is unknown.

    ``chat()`` validates ``max_tokens`` against this cap before any request,
    so a summary call above it fails without ever reaching the provider.
    """
    llm = getattr(cfg, "llm", None)
    try:
        capabilities = get_model_capabilities(
            str(getattr(llm, "provider", "") or ""),
            getattr(llm, "model", None),
            base_url=getattr(llm, "base_url", None),
        )
    except Exception:  # noqa: BLE001 - an unknown cap is not a compaction error
        return None
    cap = getattr(capabilities, "max_output_tokens", None)
    return int(cap) if isinstance(cap, int) and cap > 0 else None


def _summary_max_tokens(cfg: Config, cap: int | None = None) -> int:
    raw = (os.environ.get("OPENAI4S_COMPACTION_SUMMARY_MAX_TOKENS") or "").strip()
    wanted = 0
    if raw:
        try:
            wanted = int(raw)
        except ValueError:
            wanted = 0
    if wanted <= 0:
        wanted = max(8192, int(getattr(cfg.llm, "max_tokens", 0) or 0))
    return min(wanted, cap) if cap else wanted


# Provider adapters pass their own stop reason through unnormalized: OpenAI
# Chat says ``length``, Anthropic ``max_tokens``, Gemini ``MAX_TOKENS``, and the
# Responses wire reports truncation only as ``provider_finish_reason``
# ``incomplete`` (its ``finish_reason`` is always ``stop``).
_TRUNCATED_FINISH = frozenset(
    {"length", "max_tokens", "max_output_tokens", "incomplete"}
)


def _summary_truncated(reply: Mapping[str, Any]) -> bool:
    return any(
        str(reply.get(key) or "").strip().lower() in _TRUNCATED_FINISH
        for key in ("finish_reason", "provider_finish_reason")
    )


def _require_usable_summary(reply: Mapping[str, Any]) -> str:
    text = str(reply.get("content", "") or "").strip()
    finish_reason = reply.get("finish_reason")
    if not text:
        raise CompactionSummaryError(
            f"compaction summary was empty (finish_reason={finish_reason!r})"
        )
    if _summary_truncated(reply) and not _handoff_titles_complete(text):
        raise CompactionSummaryError(
            "compaction summary was truncated before all handoff fields "
            f"(finish_reason={finish_reason!r})"
        )
    return text


# Explicit and low.  Every JSON wire substitutes the session's
# ``cfg.temperature`` when the kwarg is omitted, so "no temperature" is not
# something a caller can ask for, and a summarizer is where determinism pays.
_SUMMARY_TEMPERATURE = 0.2


def _summary_chunk(
    request: list[dict[str, Any]],
    cfg: Config,
    max_tokens: int,
    cap: int | None,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    """One chunk summary; a truncated reply gets one retry at double budget.

    ``cap`` is the bound the retry may grow to: the model's declared output
    cap, or the room the window leaves when that cap is unknown.  The retry
    is issued on *any* truncation, headings or not: a cut that leaves every
    heading in place lands in the last, append-only sections (Key
    Artifacts), and carried forward as "authoritative" it never comes back.
    A reply still truncated after the retry is accepted when its headings
    are complete; that is the best the budget allows.
    """
    extra: dict[str, Any] = {}
    if should_cancel is not None:
        extra["should_cancel"] = should_cancel
    reply = chat(
        request,
        cfg.llm,
        max_tokens=max_tokens,
        temperature=_SUMMARY_TEMPERATURE,
        **extra,
    )
    if _summary_truncated(reply):
        retry = min(2 * max_tokens, cap) if cap else 2 * max_tokens
        if retry > max_tokens:
            reply = chat(
                request,
                cfg.llm,
                max_tokens=retry,
                temperature=_SUMMARY_TEMPERATURE,
                **extra,
            )
    return _require_usable_summary(reply)


def compact(
    messages: list[dict],
    cfg: Config,
    *,
    keep_recent: int = 4,
    archive_dir: Path | str | None = None,
    archive_metadata: Mapping[str, Any] | CompactionArchiveMetadata | None = None,
    large_output_chars: int = DEFAULT_LARGE_OUTPUT_CHARS,
    artifact_archiver: (
        Callable[[Any, Mapping[str, Any], dict[str, Any]], Mapping[str, Any]] | None
    ) = None,
    archive_sink: Callable[[Mapping[str, Any]], Any] | None = None,
    tool_schemas: Iterable[Mapping[str, Any]] = (),
    context_budget: int | None = None,
    workspace: Path | str | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[dict]:
    """Return a shorter, replay-safe message list or a no-op projection.

    ``should_cancel`` is polled between summary chunks and handed to each
    ``chat()``; a cancelled run raises :class:`CompactionCancelled` instead
    of finishing N blocking summary calls the user already stopped.
    """
    metadata = CompactionArchiveMetadata.from_mapping(archive_metadata)
    projected = externalize_large_outputs(
        messages,
        archive_dir,
        threshold_chars=large_output_chars,
        archive_metadata=metadata,
        artifact_archiver=artifact_archiver,
        workspace=workspace,
    )
    # A provider that answers 0 (a deployment whose max_output equals its
    # window) means "unknown", not "no room": fall back to the config window.
    budget = int(
        context_budget
        if context_budget is not None and int(context_budget) > 0
        else cfg.context_window_tokens
    )
    windows = _compaction_windows(
        projected, keep_recent=keep_recent, context_budget=budget
    )
    if windows is None:
        return projected
    head, middle, tail = windows

    prev = _existing_handoff_text(projected)
    pieces = _summary_pieces(middle, metadata)
    if not pieces:
        return projected

    # Each request carries the previous handoff ahead of the chunk, and that
    # handoff is bounded only by the summary output cap, so the chunk is
    # sized against what is left of the budget after it -- never below a
    # floor, so an outsized handoff shortens chunks rather than zeroing them.
    chunk_budget = min(48_000, int(0.3 * budget))
    chunk_floor = max(1, chunk_budget // 6)
    header = _summary_header(metadata)
    header_tokens = _chars_to_tokens(header)
    chunk_estimates: list[int] = []
    raw_summary = ""
    cap = _summary_output_cap(cfg)
    # An unknown cap (local and private endpoints report none) means the
    # window is shared by prompt and completion: bound the request by what
    # the window leaves after the chunk rather than by nothing, so a small
    # self-hosted window is not asked for 8192 completion tokens it cannot
    # hold.  A declared cap is authoritative and untouched.
    room = budget - chunk_budget - _chars_to_tokens(_SUMMARY_SYSTEM) - header_tokens
    bound = cap if cap else max(1, room)
    max_tokens = _summary_max_tokens(cfg, bound)
    index = 0
    while index < len(pieces):
        if should_cancel is not None and should_cancel():
            raise CompactionCancelled("run cancelled between summary chunks")
        preamble = _summary_preamble(prev)
        reserve = _chars_to_tokens(preamble) + header_tokens
        batch, index = _next_summary_batch(
            pieces, index, max(chunk_floor, chunk_budget - reserve)
        )
        chunk_estimates.append(estimate_context(batch).total)
        raw_summary = _summary_chunk(
            [
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": preamble + _summary_input(batch, metadata)},
            ],
            cfg,
            max_tokens,
            bound,
            should_cancel,
        )
        prev = raw_summary
    # Idempotent like the ledger restore: a handoff that already carries the
    # banner (a model reproducing framing it saw elsewhere) is not prefixed
    # twice, so the in-memory note and the restored note stay byte-identical.
    handoff = (
        _normalize_handoff(raw_summary, metadata)
        .removeprefix(COMPACTION_NOTE_PREFIX)
        .strip()
    )
    note = {
        "role": "system",
        "content": COMPACTION_NOTE_PREFIX + handoff,
        "compaction_handoff": True,
    }
    result = head + [note] + tail

    if archive_dir is not None:
        _archive(
            Path(archive_dir),
            middle,
            raw_summary,
            handoff,
            metadata,
            estimate_context(projected, tool_schemas),
            estimate_context(result, tool_schemas),
            archive_sink=archive_sink,
            summary_chunk_estimates=chunk_estimates,
        )
    return result


def _archive(
    archive_dir: Path,
    middle: list[dict],
    summary: str,
    handoff: str | None = None,
    metadata: CompactionArchiveMetadata | None = None,
    before: ContextEstimate | None = None,
    after: ContextEstimate | None = None,
    *,
    archive_sink: Callable[[Mapping[str, Any]], Any] | None = None,
    summary_chunk_estimates: Sequence[int] | None = None,
) -> Path:
    """Write one raw compaction archive and return its path.

    Positional ``middle, summary`` remain supported for older private callers.
    ``summary_chunks`` is derived from the estimates so the two can never
    disagree; a caller that records no estimates records zero chunks.
    """
    estimates = [int(value) for value in (summary_chunk_estimates or ())]
    archive_dir = archive_dir.expanduser().resolve()
    archive_dir.mkdir(parents=True, exist_ok=True)
    safe_middle = [_json_safe(message) for message in middle]
    payload_digest = hashlib.sha256(
        _json_text({"summary": summary, "compacted_messages": safe_middle}).encode(
            "utf-8"
        )
    ).hexdigest()
    stamp = int(time.time() * 1000)
    path = archive_dir / f"compaction-{stamp}-{payload_digest[:12]}.json"
    payload = {
        "schema_version": 2,
        "archive_id": payload_digest,
        "created_at_ms": stamp,
        "metadata": (metadata or CompactionArchiveMetadata()).as_dict(),
        "summary": summary,
        "handoff": handoff if handoff is not None else summary,
        "summary_chunks": len(estimates),
        "summary_chunk_estimates": estimates,
        "context_estimate_before": (before or estimate_context(middle)).as_dict(),
        "context_estimate_after": (after or ContextEstimate()).as_dict(),
        "compacted_messages": safe_middle,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    if archive_sink is not None:
        archive_sink(payload)
    return path


__all__ = [
    "COMPACTION_NOTE_PREFIX",
    "CompactionArchiveMetadata",
    "CompactionCancelled",
    "CompactionSummaryError",
    "content_digest",
    "ContextEstimate",
    "ContextSegment",
    "DEFAULT_LARGE_OUTPUT_CHARS",
    "HANDOFF_FIELDS",
    "compact",
    "estimate_context",
    "estimate_tokens",
    "externalize_large_outputs",
    "keep_recent_by_tokens",
    "load_archived_content",
    "safe_keep_recent",
    "segment_messages",
    "should_compact",
]
