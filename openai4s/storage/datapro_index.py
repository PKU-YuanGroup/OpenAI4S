"""Complete, content-addressed indexing for successful DataPro responses.

The repository deliberately indexes canonical JSON rather than a selected set
of DataPro fields.  DataPro can add datasets and response fields without a
client release; a field whitelist here would silently turn that evolution into
data loss.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from collections.abc import Callable, Mapping
from typing import Any

DATAPRO_INDEX_DDL = (
    """CREATE TABLE IF NOT EXISTS datapro_index_batches (
        batch_id           TEXT PRIMARY KEY,
        query              TEXT NOT NULL,
        project_id         TEXT NOT NULL DEFAULT 'default',
        root_frame_id      TEXT,
        artifact_id        TEXT,
        structured_content TEXT NOT NULL,
        search_text        TEXT NOT NULL,
        source_leaf_count  INTEGER NOT NULL,
        indexed_leaf_count INTEGER NOT NULL,
        source_digest      TEXT NOT NULL,
        indexed_digest     TEXT NOT NULL,
        entry_count        INTEGER NOT NULL,
        created_at         INTEGER NOT NULL,
        updated_at         INTEGER NOT NULL
    )""",
    """CREATE INDEX IF NOT EXISTS ix_datapro_batches_scope
       ON datapro_index_batches(project_id, root_frame_id, created_at)""",
    """CREATE INDEX IF NOT EXISTS ix_datapro_batches_artifact
       ON datapro_index_batches(artifact_id)""",
    """CREATE TABLE IF NOT EXISTS datapro_index_entries (
        entry_id      TEXT PRIMARY KEY,
        batch_id      TEXT NOT NULL,
        json_pointer  TEXT NOT NULL,
        content_json  TEXT NOT NULL,
        context_json  TEXT,
        search_text   TEXT NOT NULL,
        created_at    INTEGER NOT NULL,
        UNIQUE(batch_id, json_pointer),
        FOREIGN KEY(batch_id) REFERENCES datapro_index_batches(batch_id)
            ON DELETE CASCADE
    )""",
    """CREATE INDEX IF NOT EXISTS ix_datapro_entries_batch
       ON datapro_index_entries(batch_id, json_pointer)""",
)

DATAPRO_INDEX_SCHEMA = ";\n".join(DATAPRO_INDEX_DDL) + ";\n"

# The HTTP transport rejects a response above 4 MiB, but a valid JSON array of
# tiny objects can still contain hundreds of thousands of logical records.
# Refuse such a derived-index explosion as one explicit failed batch; never
# silently index only a prefix and never hold the Store lock for that workload.
MAX_LOGICAL_ENTRIES = 10_000
MAX_INDEX_LEAVES = 100_000
# A search token is a mapping *key* or a scalar, not a leaf: ``_search_tokens``
# emits both halves of every dict entry where ``_leaf_tokens`` emits one token
# per leaf.  Budgeting them against ``MAX_INDEX_LEAVES`` made the effective
# ceiling twice as tight for record-shaped content, so a response the manifest
# check had just accepted was rejected here.
MAX_SEARCH_TOKENS = 2 * MAX_INDEX_LEAVES + 1
MAX_SEARCH_DOCUMENT_CHARS = 16 * 1024 * 1024


class DataProIndexCapacity(ValueError):
    """A response is too large to derive an index from.

    Distinct from an integrity failure: the response itself is fine, only the
    derived index is refused.  Callers deliver the data and record that
    search-by-keyword over it is unavailable, rather than discarding a search
    the connector answered successfully.
    """


def create_datapro_index_schema(connection: sqlite3.Connection) -> None:
    """Create the DataPro index tables without committing the caller transaction."""

    for statement in DATAPRO_INDEX_DDL:
        connection.execute(statement)


def _canonical(value: Any) -> str:
    """Return strict canonical JSON, rejecting values the wire cannot preserve."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_value(value: Any) -> Any:
    """Normalize mappings/sequences to exactly the JSON value that is stored."""

    return json.loads(_canonical(value))


def _pointer_part(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _leaf_tokens(value: Any, pointer: str = ""):
    """Yield every scalar and empty container with its key path."""

    if isinstance(value, dict):
        if not value:
            yield f"{pointer}\u0000{{}}"
        for key in sorted(value):
            child = pointer + "/" + _pointer_part(key)
            yield from _leaf_tokens(value[key], child)
    elif isinstance(value, list):
        if not value:
            yield f"{pointer}\u0000[]"
        for index, item in enumerate(value):
            yield from _leaf_tokens(item, pointer + f"/{index}")
    else:
        yield f"{pointer}\u0000{_canonical(value)}"


def _manifest(value: Any) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    for token in _leaf_tokens(value):
        count += 1
        if count > MAX_INDEX_LEAVES:
            raise DataProIndexCapacity(
                "DataPro response exceeds the safe index leaf limit of "
                f"{MAX_INDEX_LEAVES}"
            )
        if count > 1:
            digest.update(b"\n")
        digest.update(token.encode("utf-8"))
    return count, digest.hexdigest()


def _search_tokens(value: Any):
    """Flatten every key and scalar without JSON-escaping its characters.

    Canonical JSON is the durable completeness source, but it is the wrong
    search representation: a literal quote, newline, or backslash is escaped
    in JSON and therefore cannot be found by the original text.  This lossless
    derivative retains each key and scalar exactly as a SQLite TEXT token.
    """

    if isinstance(value, dict):
        if not value:
            yield "{}"
        for key, item in value.items():
            yield str(key)
            yield from _search_tokens(item)
    elif isinstance(value, list):
        if not value:
            yield "[]"
        for item in value:
            yield from _search_tokens(item)
    elif isinstance(value, str):
        yield value
    else:
        yield _canonical(value)


def _search_document(value: Any) -> str:
    # The separator cannot make one source token overwrite another.  The
    # character-level encoding removes NUL (which SQLite LIKE truncates) while
    # leaving ordinary text unchanged, including ASCII letters, quotes,
    # newlines, backslashes and wildcard characters.
    output = io.StringIO()
    count = 0
    written = 0
    for token in _search_tokens(value):
        count += 1
        if count > MAX_SEARCH_TOKENS:
            raise DataProIndexCapacity(
                "DataPro response exceeds the safe search-token indexing limit"
            )
        encoded = _search_escape(token)
        added = len(encoded) + (1 if count > 1 else 0)
        written += added
        if written > MAX_SEARCH_DOCUMENT_CHARS:
            raise DataProIndexCapacity(
                "DataPro response exceeds the safe derived search-document limit"
            )
        if count > 1:
            output.write("\x1e")
        output.write(encoded)
    return output.getvalue()


def _structured_payload(value: Any) -> dict[str, Any]:
    """Locate DataPro ``structuredContent`` inside a stored result envelope.

    Direct repository callers historically supplied the structured object
    itself. Real MCP paths now persist the complete redacted result envelope so
    ``content``, ``text`` and future result fields participate in the same
    completeness manifest. Both shapes remain readable.
    """

    if not isinstance(value, dict):
        return {}
    direct = value.get("structuredContent")
    if isinstance(direct, dict):
        return direct
    raw = value.get("raw")
    if isinstance(raw, dict) and isinstance(raw.get("structuredContent"), dict):
        return raw["structuredContent"]
    return value if "code" in value else {}


def _logical_entries(structured: Any) -> list[tuple[str, Any, Any | None]]:
    """Project known list envelopes while retaining a whole-object fallback.

    A top-level ``items`` record is one logical hit.  DataPro's dataset-list
    envelope nests its records at ``item.data.items``; each nested occurrence
    gets its own JSON pointer and therefore its own identity, even when two
    occurrences have byte-identical content.
    """

    projected: list[tuple[str, Any, Any | None]] = []

    def append(entry: tuple[str, Any, Any | None]) -> None:
        if len(projected) >= MAX_LOGICAL_ENTRIES:
            raise DataProIndexCapacity(
                "DataPro response exceeds the safe logical-entry indexing limit "
                f"of {MAX_LOGICAL_ENTRIES}"
            )
        projected.append(entry)

    if isinstance(structured, dict) and isinstance(structured.get("items"), list):
        for outer_index, item in enumerate(structured["items"]):
            base_pointer = f"/items/{outer_index}"
            nested = item.get("data") if isinstance(item, dict) else None
            nested_items = nested.get("items") if isinstance(nested, dict) else None
            if isinstance(nested_items, list) and nested_items:
                for inner_index, child in enumerate(nested_items):
                    append(
                        (
                            base_pointer + f"/data/items/{inner_index}",
                            child,
                            # The wrapper is already present, losslessly, in the
                            # batch. Repeating it for every child turns a 100 KB
                            # wrapper with 50 records into roughly 5 MB on disk.
                            # ``_decode_entry`` reconstructs the same context
                            # projection from the batch at read time.
                            None,
                        )
                    )
            else:
                append((base_pointer, item, None))
    if not projected:
        append(("", structured, None))
    return projected


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_SEARCH_ESCAPE = "\ue000"
_SEARCH_NUL = "\ue001"
_SEARCH_RESERVED = "\ue002"


def _search_escape(value: str) -> str:
    """Return a NUL-free, substring-preserving search representation.

    SQLite LIKE stops at an embedded NUL.  Encoding only NUL and the three
    reserved private-use code points keeps every ordinary character unchanged,
    so LIKE retains its existing ASCII case-folding and the caller's literal
    ``%``/``_`` semantics. Reserved characters are escaped too, making the
    transform reversible rather than conflating user text with the NUL marker.
    """

    encoded: list[str] = []
    for character in value:
        if character == "\x00":
            encoded.extend((_SEARCH_ESCAPE, _SEARCH_NUL))
        elif character in {_SEARCH_ESCAPE, _SEARCH_NUL, _SEARCH_RESERVED}:
            encoded.extend((_SEARCH_ESCAPE, _SEARCH_RESERVED, character))
        else:
            encoded.append(character)
    return "".join(encoded)


def _wrapper_context(structured: Any, pointer: str) -> Any | None:
    """Rebuild a nested item's wrapper projection without persisting a copy."""

    structured = _structured_payload(structured)
    parts = pointer.split("/")
    if len(parts) != 6 or parts[1] != "items" or parts[3:5] != ["data", "items"]:
        return None
    try:
        wrapper = structured["items"][int(parts[2])]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not isinstance(wrapper, dict):
        return None
    context = _json_value(wrapper)
    data = context.get("data")
    if isinstance(data, dict):
        data.pop("items", None)
    return context


class DataProIndexRepository:
    """Persist and search complete, redacted DataPro ``structuredContent``."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: Any,
        *,
        clock_ms: Callable[[], int],
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms

    def ingest(
        self,
        *,
        query: str,
        structured_content: Mapping[str, Any],
        project_id: str,
        root_frame_id: str | None,
        artifact_id: str | None = None,
        occurrence_id: str | None = None,
        source_content: Any | None = None,
    ) -> dict[str, Any]:
        normalized = _json_value(structured_content)
        if not isinstance(normalized, dict):
            raise TypeError("structured_content must be a JSON object")
        if type(normalized.get("code")) is not int or normalized.get("code") != 0:
            raise ValueError(
                "DataPro index accepts only a strict integer code-0 response"
            )
        # The logical-record projection is driven by structuredContent, but the
        # completeness source is the entire redacted MCP result. Otherwise a
        # unique text/content/extension field could be saved to an Artifact yet
        # remain absent from the index while the receipt still claimed complete.
        indexed_source = (
            normalized if source_content is None else _json_value(source_content)
        )
        source_json = _canonical(indexed_source)
        # Search uses a separate, unescaped derivative so every original key
        # and scalar remains discoverable even when it contains JSON syntax.
        # The canonical source remains the completeness and round-trip oracle.
        source_leaf_count, source_digest = _manifest(indexed_source)
        indexed_value = json.loads(source_json)
        indexed_leaf_count, indexed_digest = _manifest(indexed_value)
        if source_leaf_count != indexed_leaf_count or source_digest != indexed_digest:
            raise RuntimeError("DataPro index completeness check failed")
        search_text = _search_document([str(query), indexed_source])

        identity = _canonical(
            {
                "project_id": project_id,
                "query": str(query),
                "root_frame_id": root_frame_id,
                "occurrence_id": occurrence_id,
                "structured_content_sha256": hashlib.sha256(
                    source_json.encode("utf-8")
                ).hexdigest(),
            }
        )
        batch_id = "dpb-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        logical = _logical_entries(normalized)
        now = self._clock_ms()
        rows: list[tuple[str, str, str, str | None, str]] = []
        for pointer, content, context in logical:
            content_json = _canonical(content)
            context_json = _canonical(context) if context is not None else None
            entry_search = _search_document(
                {"context": context, "item": content}
                if context is not None
                else content
            )
            entry_id = (
                "dpe-"
                + hashlib.sha256(
                    f"{batch_id}\u0000{pointer}".encode("utf-8")
                ).hexdigest()
            )
            rows.append((entry_id, pointer, content_json, context_json, entry_search))

        with self._lock:
            try:
                self._connection.execute("SAVEPOINT datapro_index_ingest")
                self._connection.execute(
                    "INSERT OR IGNORE INTO datapro_index_batches("
                    "batch_id,query,project_id,root_frame_id,artifact_id,"
                    "structured_content,search_text,source_leaf_count,"
                    "indexed_leaf_count,source_digest,indexed_digest,entry_count,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        batch_id,
                        str(query),
                        project_id,
                        root_frame_id,
                        artifact_id,
                        source_json,
                        search_text,
                        source_leaf_count,
                        indexed_leaf_count,
                        source_digest,
                        indexed_digest,
                        len(rows),
                        now,
                        now,
                    ),
                )
                if artifact_id is not None:
                    self._connection.execute(
                        "UPDATE datapro_index_batches SET artifact_id=?,updated_at=? "
                        "WHERE batch_id=?",
                        (artifact_id, now, batch_id),
                    )
                for entry_id, pointer, content_json, context_json, entry_search in rows:
                    self._connection.execute(
                        "INSERT OR IGNORE INTO datapro_index_entries("
                        "entry_id,batch_id,json_pointer,content_json,context_json,"
                        "search_text,created_at) VALUES(?,?,?,?,?,?,?)",
                        (
                            entry_id,
                            batch_id,
                            pointer,
                            content_json,
                            context_json,
                            entry_search,
                            now,
                        ),
                    )
                batch = self._connection.execute(
                    "SELECT * FROM datapro_index_batches WHERE batch_id=?",
                    (batch_id,),
                ).fetchone()
                stored_entries = self._connection.execute(
                    "SELECT json_pointer,content_json,context_json FROM "
                    "datapro_index_entries WHERE batch_id=? ORDER BY json_pointer",
                    (batch_id,),
                ).fetchall()
                expected = sorted(
                    (pointer, content_json, context_json)
                    for _, pointer, content_json, context_json, _ in rows
                )
                actual = [
                    (row["json_pointer"], row["content_json"], row["context_json"])
                    for row in stored_entries
                ]
                if batch is None or actual != expected:
                    raise RuntimeError(
                        "DataPro logical-entry completeness check failed"
                    )
                if (
                    batch["structured_content"] != source_json
                    or batch["search_text"] != search_text
                    or batch["source_leaf_count"] != source_leaf_count
                    or batch["indexed_leaf_count"] != indexed_leaf_count
                    or batch["source_digest"] != source_digest
                    or batch["indexed_digest"] != indexed_digest
                    or batch["entry_count"] != len(rows)
                ):
                    raise RuntimeError("DataPro batch identity collision")
                self._connection.execute("RELEASE SAVEPOINT datapro_index_ingest")
            except Exception:
                self._connection.execute("ROLLBACK TO SAVEPOINT datapro_index_ingest")
                self._connection.execute("RELEASE SAVEPOINT datapro_index_ingest")
                raise
        return self._receipt(dict(batch))

    def link_artifact(self, batch_id: str, artifact_id: str | None) -> dict[str, Any]:
        now = self._clock_ms()
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE datapro_index_batches SET artifact_id=?,updated_at=? "
                "WHERE batch_id=?",
                (artifact_id, now, batch_id),
            )
            self._connection.commit()
            if cursor.rowcount != 1:
                raise KeyError(f"no such DataPro index batch {batch_id!r}")
        batch = self.get_batch(batch_id)
        assert batch is not None
        return self._receipt(batch)

    def delete_for_artifact(self, artifact_id: str) -> None:
        """Forget index content whose user-visible saved result was deleted."""

        with self._lock:
            try:
                self._connection.execute("SAVEPOINT datapro_index_artifact_delete")
                self._connection.execute(
                    "DELETE FROM datapro_index_entries WHERE batch_id IN "
                    "(SELECT batch_id FROM datapro_index_batches WHERE artifact_id=?)",
                    (artifact_id,),
                )
                self._connection.execute(
                    "DELETE FROM datapro_index_batches WHERE artifact_id=?",
                    (artifact_id,),
                )
                self._connection.execute(
                    "RELEASE SAVEPOINT datapro_index_artifact_delete"
                )
            except Exception:
                self._connection.execute(
                    "ROLLBACK TO SAVEPOINT datapro_index_artifact_delete"
                )
                self._connection.execute(
                    "RELEASE SAVEPOINT datapro_index_artifact_delete"
                )
                raise

    def delete_batch(self, batch_id: str) -> None:
        """Compensate a failed product save without leaving a searchable ghost."""

        with self._lock:
            try:
                self._connection.execute("SAVEPOINT datapro_index_batch_delete")
                self._connection.execute(
                    "DELETE FROM datapro_index_entries WHERE batch_id=?", (batch_id,)
                )
                self._connection.execute(
                    "DELETE FROM datapro_index_batches WHERE batch_id=?", (batch_id,)
                )
                self._connection.execute("RELEASE SAVEPOINT datapro_index_batch_delete")
            except Exception:
                self._connection.execute(
                    "ROLLBACK TO SAVEPOINT datapro_index_batch_delete"
                )
                self._connection.execute("RELEASE SAVEPOINT datapro_index_batch_delete")
                raise

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM datapro_index_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if row is None:
                return None
            entries = self._connection.execute(
                "SELECT * FROM datapro_index_entries WHERE batch_id=? "
                "ORDER BY json_pointer",
                (batch_id,),
            ).fetchall()
        value = self._decode_batch(dict(row))
        value["entries"] = [
            self._decode_entry(
                dict(entry), structured_content=value["structured_content"]
            )
            for entry in entries
        ]
        value["complete"] = self._is_complete(value)
        return value

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        project_id: str | None = None,
        root_frame_id: str | None = None,
        include_context: bool = False,
    ) -> dict[str, Any]:
        text = str(query).strip()
        result_limit = max(0, int(limit))
        if not text:
            return {"items": [], "total": 0, "truncated": False}
        where = [
            "(e.search_text LIKE ? ESCAPE '\\' " "OR b.search_text LIKE ? ESCAPE '\\'"
        ]
        content_pattern = _like_pattern(_search_escape(text))
        params: list[Any] = [content_pattern, content_pattern]
        if "\x00" not in text:
            # The original query is not canonical JSON, so keep its raw
            # literal-LIKE semantics. Never send NUL to LIKE: SQLite truncates
            # it and would return false positives. NUL-bearing DataPro content
            # is covered by the canonical patterns above.
            where[0] += " OR b.query LIKE ? ESCAPE '\\'"
            params.append(_like_pattern(text))
        where[0] += ")"
        if root_frame_id is not None:
            where.extend(["b.root_frame_id=?", "b.project_id=?"])
            params.extend([root_frame_id, project_id or "default"])
        elif project_id is not None:
            where.append("b.project_id=?")
            params.append(project_id)
        predicate = " AND ".join(where)
        with self._lock:
            total = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM datapro_index_entries e JOIN "
                    "datapro_index_batches b ON b.batch_id=e.batch_id WHERE "
                    + predicate,
                    tuple(params),
                ).fetchone()[0]
            )
            # `b.search_text` is the whole batch flattened, so a batch-level hit
            # makes the predicate true for *every* entry under it. Ordering by
            # pointer alone then let a batch's non-matching siblings fill the
            # LIMIT and push the entry the user actually searched for out of the
            # result entirely. Rank a direct entry hit first so the matching
            # child can never be hidden by its own batch's text.
            rows = self._connection.execute(
                "SELECT e.*,b.query,b.project_id,b.root_frame_id,b.artifact_id,"
                "b.batch_id,"
                "CASE WHEN e.search_text LIKE ? ESCAPE '\\' THEN 0 ELSE 1 END "
                "AS entry_match_rank "
                "FROM datapro_index_entries e JOIN datapro_index_batches b "
                "ON b.batch_id=e.batch_id WHERE "
                + predicate
                + " ORDER BY entry_match_rank,b.created_at DESC,e.json_pointer "
                "LIMIT ?",
                tuple([content_pattern] + params + [result_limit]),
            ).fetchall()
            # A nested DataPro list can have a large wrapper shared by many
            # child entries. Selecting ``structured_content`` through the join
            # repeats those bytes once per hit before Python even decodes them.
            # Fetch and parse each returned batch once instead.
            batch_ids = list(dict.fromkeys(str(row["batch_id"]) for row in rows))
            structured_by_batch: dict[str, Any] = {}
            if batch_ids:
                placeholders = ",".join("?" for _ in batch_ids)
                batch_rows = self._connection.execute(
                    "SELECT batch_id,structured_content FROM "
                    "datapro_index_batches WHERE batch_id IN (" + placeholders + ")",
                    tuple(batch_ids),
                ).fetchall()
                structured_by_batch = {
                    str(row["batch_id"]): json.loads(row["structured_content"])
                    for row in batch_rows
                }
        return {
            "items": [
                self._decode_entry(
                    dict(row),
                    structured_content=structured_by_batch.get(str(row["batch_id"])),
                    include_context=include_context,
                )
                for row in rows
            ],
            "total": total,
            "truncated": total > len(rows),
        }

    @staticmethod
    def _decode_entry(
        row: dict[str, Any],
        *,
        structured_content: Any | None = None,
        include_context: bool = True,
    ) -> dict[str, Any]:
        row.pop("entry_match_rank", None)  # ordering only; never part of the row
        row["content"] = json.loads(row.pop("content_json"))
        context_json = row.pop("context_json")
        structured_json = row.pop("structured_content", None)
        structured = (
            json.loads(structured_json)
            if structured_json is not None
            else structured_content or {}
        )
        if include_context:
            row["context"] = (
                json.loads(context_json)
                if context_json is not None
                else _wrapper_context(structured, str(row.get("json_pointer") or ""))
            )
        structured = _structured_payload(structured)
        dataset_type = (
            structured.get("type")
            or structured.get("dataset_type")
            or structured.get("datasetType")
        )
        items = structured.get("items")
        if dataset_type is None and isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict):
                dataset_type = (
                    first.get("type")
                    or first.get("dataset_type")
                    or first.get("datasetType")
                )
        row["dataset_type"] = dataset_type
        row.pop("search_text", None)
        return row

    @staticmethod
    def _decode_batch(row: dict[str, Any]) -> dict[str, Any]:
        row["structured_content"] = json.loads(row["structured_content"])
        row.pop("search_text", None)
        return row

    @staticmethod
    def _is_complete(batch: Mapping[str, Any]) -> bool:
        return bool(
            batch.get("source_leaf_count") == batch.get("indexed_leaf_count")
            and batch.get("source_digest") == batch.get("indexed_digest")
            and batch.get("entry_count", 0) >= 1
        )

    @classmethod
    def _receipt(cls, batch: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "batch_id": batch["batch_id"],
            "entry_count": int(batch["entry_count"]),
            "source_leaf_count": int(batch["source_leaf_count"]),
            "indexed_leaf_count": int(batch["indexed_leaf_count"]),
            "source_digest": batch["source_digest"],
            "indexed_digest": batch["indexed_digest"],
            "complete": cls._is_complete(batch),
        }


__all__ = [
    "DATAPRO_INDEX_DDL",
    "DATAPRO_INDEX_SCHEMA",
    "MAX_LOGICAL_ENTRIES",
    "MAX_INDEX_LEAVES",
    "MAX_SEARCH_DOCUMENT_CHARS",
    "DataProIndexRepository",
    "create_datapro_index_schema",
]
