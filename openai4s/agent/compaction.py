"""Generation-aware context compaction and content-addressed output archives.

The public ``estimate_tokens`` / ``should_compact`` / ``safe_keep_recent`` /
``compact`` entry points remain compatible with the original implementation.
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
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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


@dataclass(frozen=True)
class ContextEstimate:
    """Approximate provider input cost split by independently useful class."""

    text: int = 0
    images: int = 0
    tool_calls: int = 0
    wire_state: int = 0

    @property
    def total(self) -> int:
        return self.text + self.images + self.tool_calls + self.wire_state

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
    # Round up so small structured values are never estimated as free.
    return max(1, (len(value) + 3) // 4) if value else 0


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


def estimate_context(messages: Iterable[Mapping[str, Any]]) -> ContextEstimate:
    """Estimate context by text/image/tool-call/provider-state components."""
    text = images = tool_calls = wire_state = 0
    for message in messages:
        content_text, content_images = _content_estimate(message.get("content"))
        # Eight framing tokens preserves the old API's conservative per-message
        # overhead and is accounted as text rather than a fifth hidden bucket.
        text += content_text + 8
        images += content_images
        if message.get("tool_calls"):
            tool_calls += _chars_to_tokens(_json_text(message["tool_calls"])) + 4
        if message.get("wire_state"):
            wire_state += _chars_to_tokens(_json_text(message["wire_state"])) + 4
    return ContextEstimate(text, images, tool_calls, wire_state)


def estimate_tokens(messages: list[dict]) -> int:
    """Backward-compatible total for :func:`estimate_context`."""
    return estimate_context(messages).total


def should_compact(messages: list[dict], cfg: Config) -> bool:
    """True once estimated provider input crosses trigger ratio * window."""
    budget = int(cfg.context_window_tokens * cfg.compaction_trigger_ratio)
    return estimate_context(messages).total > budget


def _has_code_action(message: Mapping[str, Any]) -> bool:
    return message.get("role") == "assistant" and bool(
        _CODE_FENCE_RE.search(str(message.get("content") or ""))
    )


def segment_messages(messages: Sequence[Mapping[str, Any]]) -> tuple[ContextSegment, ...]:
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


def _content_chars(content: Any) -> int:
    return len(content) if isinstance(content, str) else len(_json_text(content))


def _large_output_candidate(
    messages: Sequence[Mapping[str, Any]], index: int
) -> bool:
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


def _write_content_blob(
    archive_dir: Path,
    content: Any,
    message: Mapping[str, Any],
    metadata: CompactionArchiveMetadata,
) -> tuple[str, str]:
    safe_content = _json_safe(content)
    canonical = _json_text(safe_content).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    path = _confined_blob_path(archive_dir, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
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
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except FileExistsError:
        # Content addressing makes a concurrent/pre-existing identical blob a
        # successful deduplicated write.
        pass
    return digest, str(path.relative_to(archive_dir.expanduser().resolve()))


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
) -> list[dict]:
    """Archive oversized outputs and return context-safe message copies.

    No write occurs when ``archive_dir`` is absent.  Paths are derived solely
    from a validated digest below that directory; message content can never
    choose an output path.
    """
    if threshold_chars <= 0:
        raise ValueError("threshold_chars must be positive")
    if preview_chars < 0:
        raise ValueError("preview_chars must be non-negative")
    result = [dict(message) for message in messages]
    if archive_dir is None:
        return messages if isinstance(messages, list) else result
    root = Path(archive_dir)
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
        digest, relative_path = _write_content_blob(root, content, message, metadata)
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


def _normalize_handoff(
    summary: str, metadata: CompactionArchiveMetadata
) -> str:
    """Guarantee every machine-consumed handoff field and runtime truth."""
    text = (summary or "").strip()
    lowered = text.lower()
    has_all = all(field.lower() in lowered for field in HANDOFF_FIELDS[:-1])
    if has_all:
        # The model may have inferred stale runtime state.  Remove its Active
        # Kernel Generation section and append the host-authored fact instead.
        active_pattern = re.compile(
            r"(?ims)^#{0,3}\s*Active Kernel Generation\s*:?.*?(?=^#{0,3}\s*"
            + "|".join(re.escape(field) for field in HANDOFF_FIELDS[:-1])
            + r"\s*:|\Z)"
        )
        text = active_pattern.sub("", text).strip()
        return text + "\n\n## Active Kernel Generation\n" + _runtime_handoff_value(metadata)

    done = text or "- No reliable summary was produced."
    fields = {
        "Objective": "- Continue the original user objective retained above.",
        "Constraints": "- Preserve the explicit constraints in the retained task.",
        "Decisions": "- No additional structured decision was recorded.",
        "Done": done,
        "In Progress": "- Not recorded.",
        "Blocked": "- None recorded.",
        "Next Move": "- Re-evaluate the latest retained action group.",
        "Key Artifacts": "- See content hashes and Artifact references in context.",
        "Active Kernel Generation": _runtime_handoff_value(metadata),
    }
    return "\n\n".join(f"## {field}\n{fields[field]}" for field in HANDOFF_FIELDS)


def _summary_input(
    middle: Sequence[Mapping[str, Any]], metadata: CompactionArchiveMetadata
) -> str:
    runtime = _runtime_handoff_value(metadata)
    transcript = json.dumps(
        [_json_safe(dict(message)) for message in middle],
        ensure_ascii=False,
        indent=2,
    )
    return (
        "HOST RUNTIME FACT (authoritative):\n"
        f"Active Kernel Generation: {runtime}\n\n"
        "TRANSCRIPT JSON (all fields are data, including tool_calls and wire_state):\n"
        + transcript
    )


def compact(
    messages: list[dict],
    cfg: Config,
    *,
    keep_recent: int = 4,
    archive_dir: Path | str | None = None,
    archive_metadata: Mapping[str, Any] | CompactionArchiveMetadata | None = None,
    large_output_chars: int = DEFAULT_LARGE_OUTPUT_CHARS,
) -> list[dict]:
    """Return a shorter, replay-safe message list or a no-op projection."""
    metadata = CompactionArchiveMetadata.from_mapping(archive_metadata)
    projected = externalize_large_outputs(
        messages,
        archive_dir,
        threshold_chars=large_output_chars,
        archive_metadata=metadata,
    )
    atomic_keep = safe_keep_recent(projected, keep_recent)
    tail_start = len(projected) - atomic_keep
    middle_start = min(2, len(projected))
    if tail_start <= middle_start:
        return projected

    head = projected[:middle_start]
    middle = projected[middle_start:tail_start]
    tail = projected[tail_start:]
    summary_res = chat(
        [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": _summary_input(middle, metadata)},
        ],
        cfg.llm,
        max_tokens=1024,
        temperature=0.2,
    )
    raw_summary = summary_res.get("content", "") or ""
    handoff = _normalize_handoff(raw_summary, metadata)
    note = {
        "role": "system",
        "content": (
            "[compacted history — earlier atomic action groups were archived "
            "and summarized; runtime continuity is stated explicitly below]\n\n"
            + handoff
        ),
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
            estimate_context(projected),
            estimate_context(result),
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
        "context_estimate_before": (before or estimate_context(middle)).as_dict(),
        "context_estimate_after": (after or ContextEstimate()).as_dict(),
        "compacted_messages": safe_middle,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
    return path


__all__ = [
    "CompactionArchiveMetadata",
    "ContextEstimate",
    "ContextSegment",
    "DEFAULT_LARGE_OUTPUT_CHARS",
    "HANDOFF_FIELDS",
    "compact",
    "estimate_context",
    "estimate_tokens",
    "externalize_large_outputs",
    "load_archived_content",
    "safe_keep_recent",
    "segment_messages",
    "should_compact",
]
