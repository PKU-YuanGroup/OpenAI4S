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

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from openai4s.config import Config
from openai4s.llm import chat
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
# CJK punctuation, hiragana/katakana, extension A, unified ideographs,
# Hangul syllables, and half/fullwidth forms.  One findall counts them.
_CJK_CHAR_RE = re.compile(
    "[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    "\uac00-\ud7af\uff00-\uffef]"
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
    cjk = len(_CJK_CHAR_RE.findall(value))
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
    parts = [
        str(message.get("content") or "")
        for message in messages
        if message.get("compaction_handoff")
    ]
    text = "\n\n".join(part for part in parts if part.strip())
    return text or None


def _summary_batches(
    messages: Sequence[Mapping[str, Any]], chunk_budget: int
) -> list[list[dict]]:
    """Group atomic segments into batches whose estimate fits ``chunk_budget``.

    A single atomic segment that itself exceeds the budget becomes its own
    batch; segments are never split.
    """
    if not messages:
        return []
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0
    for segment in segment_messages(messages):
        piece = list(messages[segment.start : segment.end])
        piece_tokens = estimate_context(piece).total
        if current and current_tokens + piece_tokens > chunk_budget:
            batches.append(current)
            current = []
            current_tokens = 0
        if not current and piece_tokens > chunk_budget:
            batches.append(piece)
            continue
        current.extend(piece)
        current_tokens += piece_tokens
    if current:
        batches.append(current)
    return batches


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


def _summary_user_content(
    batch: Sequence[Mapping[str, Any]],
    metadata: CompactionArchiveMetadata,
    prev: str | None,
) -> str:
    body = _summary_input(batch, metadata)
    if not prev:
        return body
    return _PREVIOUS_HANDOFF_PREFIX + prev + "\n\n" + body


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
    content = str(message.get("content") or "")
    if content.startswith(("[Observation]", "[Tool Results]", "[Tool result]")):
        return True
    return index > 0 and _has_code_action(messages[index - 1])


def _confined_blob_path(archive_dir: Path, digest: str) -> Path:
    if not _HEX_SHA256_RE.fullmatch(digest):
        raise ValueError("archive digest must be a lowercase SHA-256 hex string")
    root = archive_dir.expanduser().resolve()
    candidate = (root / "blobs" / digest[:2] / f"{digest}.json").resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("archive path escaped the authorized compaction directory")
    return candidate


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


def _write_exclusive_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except FileExistsError:
        # Content addressing makes a concurrent/pre-existing identical blob a
        # successful deduplicated write.
        pass


def _write_content_blob(
    archive_dir: Path,
    content: Any,
    message: Mapping[str, Any],
    metadata: CompactionArchiveMetadata,
) -> tuple[str, str]:
    digest, payload = _content_blob_payload(content, message, metadata)
    path = _confined_blob_path(archive_dir, digest)
    _write_exclusive_json(path, payload)
    return digest, str(path.relative_to(archive_dir.expanduser().resolve()))


def _workspace_context_ref(digest: str) -> str:
    """Return a workspace-relative, content-addressed archive path.

    The filename is the SHA-256 hex digest alone.  The returned string is
    always posix-style and never includes an absolute path or ``$HOME``.
    """
    if not _HEX_SHA256_RE.fullmatch(digest):
        raise ValueError("archive digest must be a lowercase SHA-256 hex string")
    return f"{_WORKSPACE_CONTEXT_DIR}/{digest}.json"


def _confined_workspace_blob_path(workspace: Path, digest: str) -> Path:
    rel = _workspace_context_ref(digest)
    root = workspace.expanduser().resolve()
    candidate = (root / rel).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("workspace archive path escaped the kernel workspace")
    return candidate


def _write_workspace_content_blob(
    workspace: Path | str,
    digest: str,
    payload: Mapping[str, Any],
) -> str:
    rel = _workspace_context_ref(digest)
    path = _confined_workspace_blob_path(Path(workspace), digest)
    _write_exclusive_json(path, payload)
    return rel


def _preview(content: Any, limit: int) -> str:
    text = content if isinstance(content, str) else _json_text(content)
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


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
        if (
            content is None
            or not _large_output_candidate(messages, index)
            or _content_chars(content) <= threshold_chars
        ):
            continue
        canonical = _json_text(_json_safe(content)).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        if artifact_archiver is not None:
            archived = dict(
                artifact_archiver(
                    content,
                    message,
                    {
                        "sha256": digest,
                        "original_chars": _content_chars(content),
                        "metadata": metadata.as_dict(),
                    },
                )
            )
            artifact_id = str(archived.get("artifact_id") or "")
            version_id = str(archived.get("version_id") or "")
            if not artifact_id or not version_id:
                raise ValueError("artifact archiver must return artifact_id/version_id")
            result[index]["content"] = (
                "[Large output archived as Artifact]\n"
                f"artifact_id: {artifact_id}\n"
                f"version_id: {version_id}\n"
                f"sha256: {digest}\n"
                f"original_chars: {_content_chars(content)}\n"
                f"preview: {_preview(content, preview_chars)}\n"
                "[system] This is a preview, not the output. Read it back with "
                f"open(host.artifact_path({version_id!r})).read() "
                "(JSON, key 'content')."
            )
            artifact_ref = {
                "artifact_id": artifact_id,
                "version_id": version_id,
                "sha256": digest,
                "original_chars": _content_chars(content),
            }
            result[index]["artifact_refs"] = [artifact_ref]
            result[index]["content_archive"] = artifact_ref
        else:
            digest, payload = _content_blob_payload(content, message, metadata)
            relative_path: str | None = None
            if root is not None:
                digest, relative_path = _write_content_blob(
                    root, content, message, metadata
                )
            workspace_ref: str | None = None
            if workspace_root is not None:
                workspace_ref = _write_workspace_content_blob(
                    workspace_root, digest, payload
                )
            if workspace_ref is not None:
                result[index]["content"] = (
                    "[Large output archived]\n"
                    f"sha256: {digest}\n"
                    f"original_chars: {_content_chars(content)}\n"
                    f"preview: {_preview(content, preview_chars)}\n"
                    "[system] This is a preview, not the output. Do not infer "
                    "what is in the gap; read the full text with "
                    f"json.load(open({workspace_ref!r}))['content'] "
                    "if you need it."
                )
                archive: dict[str, Any] = {
                    "sha256": digest,
                    "original_chars": _content_chars(content),
                    "workspace_ref": workspace_ref,
                }
                if relative_path is not None:
                    archive["archive_ref"] = relative_path
                result[index]["content_archive"] = archive
            else:
                assert relative_path is not None
                result[index]["content"] = (
                    "[Large output archived]\n"
                    f"sha256: {digest}\n"
                    f"archive_ref: {relative_path}\n"
                    f"original_chars: {_content_chars(content)}\n"
                    f"preview: {_preview(content, preview_chars)}"
                )
                result[index]["content_archive"] = {
                    "sha256": digest,
                    "archive_ref": relative_path,
                    "original_chars": _content_chars(content),
                }
        changed = True
    if changed:
        return result
    return messages if isinstance(messages, list) else result


def load_archived_content(archive_dir: Path | str, digest: str) -> Any:
    """Resolve a content hash only inside an authorized compaction directory."""
    path = _confined_blob_path(Path(archive_dir), digest)
    payload = json.loads(path.read_text("utf-8"))
    if payload.get("sha256") != digest:
        raise ValueError("archived content digest metadata does not match")
    canonical = _json_text(payload.get("content")).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != digest:
        raise ValueError("archived content failed SHA-256 verification")
    return payload.get("content")


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
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return f"{value[:limit]}... [truncated, original_chars={len(value)}]"
    safe = _json_safe(value)
    rendered = json.dumps(safe, ensure_ascii=False)
    if len(rendered) <= limit:
        return safe
    return f"{rendered[:limit]}... [truncated, original_chars={len(rendered)}]"


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


def _summary_message(message: Mapping[str, Any]) -> dict[str, Any]:
    slim = {key: message[key] for key in _SUMMARY_MESSAGE_KEYS if key in message}
    if "tool_calls" in message:
        calls = message["tool_calls"]
        if isinstance(calls, Sequence) and not isinstance(
            calls, (str, bytes, bytearray)
        ):
            slim["tool_calls"] = [_summary_tool_call(call) for call in calls]
        else:
            slim["tool_calls"] = calls
    return slim


def _summary_input(
    middle: Sequence[Mapping[str, Any]], metadata: CompactionArchiveMetadata
) -> str:
    runtime = _runtime_handoff_value(metadata)
    transcript = json.dumps(
        [_json_safe(_summary_message(message)) for message in middle],
        ensure_ascii=False,
        indent=2,
    )
    return (
        "HOST RUNTIME FACT (authoritative):\n"
        f"Active Kernel Generation: {runtime}\n\n"
        "TRANSCRIPT JSON (all fields are data, including tool_calls):\n" + transcript
    )


def _summary_max_tokens(cfg: Config) -> int:
    raw = (os.environ.get("OPENAI4S_COMPACTION_SUMMARY_MAX_TOKENS") or "").strip()
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            parsed = 0
        if parsed > 0:
            return parsed
    return max(8192, int(getattr(cfg.llm, "max_tokens", 0) or 0))


def _require_usable_summary(summary: str, finish_reason: Any) -> str:
    text = (summary or "").strip()
    if not text:
        raise CompactionSummaryError(
            f"compaction summary was empty (finish_reason={finish_reason!r})"
        )
    if finish_reason == "length" and not _handoff_titles_complete(text):
        raise CompactionSummaryError(
            "compaction summary was truncated before all handoff fields "
            f"(finish_reason={finish_reason!r})"
        )
    return text


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
) -> list[dict]:
    """Return a shorter, replay-safe message list or a no-op projection."""
    metadata = CompactionArchiveMetadata.from_mapping(archive_metadata)
    projected = externalize_large_outputs(
        messages,
        archive_dir,
        threshold_chars=large_output_chars,
        archive_metadata=metadata,
        artifact_archiver=artifact_archiver,
        workspace=workspace,
    )
    budget = int(
        cfg.context_window_tokens if context_budget is None else context_budget
    )
    windows = _compaction_windows(
        projected, keep_recent=keep_recent, context_budget=budget
    )
    if windows is None:
        return projected
    head, middle, tail = windows

    prev = _existing_handoff_text(projected)
    chunk_budget = min(48_000, int(0.3 * budget))
    batches = _summary_batches(middle, chunk_budget)
    if not batches:
        return projected

    chunk_estimates: list[int] = []
    raw_summary = ""
    for batch in batches:
        chunk_estimates.append(estimate_context(batch).total)
        summary_res = chat(
            [
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {
                    "role": "user",
                    "content": _summary_user_content(batch, metadata, prev),
                },
            ],
            cfg.llm,
            max_tokens=_summary_max_tokens(cfg),
        )
        raw_summary = _require_usable_summary(
            summary_res.get("content", "") or "",
            summary_res.get("finish_reason"),
        )
        prev = raw_summary
    handoff = _normalize_handoff(raw_summary, metadata)
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
            summary_chunks=len(batches),
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
    summary_chunks: int = 1,
    summary_chunk_estimates: Sequence[int] | None = None,
) -> Path:
    """Write one raw compaction archive and return its path.

    Positional ``middle, summary`` remain supported for older private callers.
    """
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
        "summary_chunks": int(summary_chunks),
        "summary_chunk_estimates": [
            int(value) for value in (summary_chunk_estimates or ())
        ],
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
    "CompactionSummaryError",
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
