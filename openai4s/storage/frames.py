"""Project, frame, message, activity-step, and cell-log persistence."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from typing import Any, Callable

from openai4s.execution.dependencies import (
    REPLAY_POLICIES,
    VISIBILITIES,
    analyze_code,
    default_replay_policy,
    default_visibility,
    normalize_string_list,
)
from openai4s.storage.deletion import SessionDeletionRepository


class FrameRepository:
    """Own the persisted conversation hierarchy on a Store connection.

    The repository shares ``Store``'s SQLite connection and re-entrant lock.
    Project and frame deletion remain aggregate operations because their legacy
    transaction deletes every row owned by that lifecycle boundary.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        lock: Any,
        *,
        clock_ms: Callable[[], int],
        get_frame: Callable[[str], dict | None] | None = None,
        resolve_frame_scope: Callable[..., dict] | None = None,
        get_project: Callable[[str], dict | None] | None = None,
    ) -> None:
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms
        self._get_frame = get_frame
        self._resolve_scope = resolve_frame_scope
        self._get_project = get_project
        self._deletions = SessionDeletionRepository(connection, lock)

    # --- frames ------------------------------------------------------
    def new_frame(
        self,
        *,
        parent_id: str | None = None,
        project_id: str = "default",
        kind: str = "turn",
        name: str | None = None,
        model: str | None = None,
        depth: int = 0,
        status: str = "processing",
    ) -> str:
        frame_id = f"f-{uuid.uuid4().hex[:12]}"
        if parent_id is None:
            root = frame_id
        else:
            get_frame = self._get_frame or self.get_frame
            parent = get_frame(parent_id)
            if parent is None:
                # Preserve the legacy orphan fallback during delete/delegate
                # races: an orphan becomes its own root.
                root = frame_id
            else:
                resolve_scope = self._resolve_scope or self.resolve_frame_scope
                scope = resolve_scope(
                    parent_id,
                    fallback_project=project_id,
                )
                root = scope["root_frame_id"] or frame_id
                project_id = scope["project_id"]
        now = self._clock_ms()
        self._execute(
            "INSERT INTO frames(frame_id,parent_id,project_id,root_frame_id,kind,"
            "name,model,status,depth,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                frame_id,
                parent_id,
                project_id,
                root,
                kind,
                name,
                model,
                status,
                depth,
                now,
                now,
            ),
        )
        return frame_id

    def resolve_frame_scope(
        self,
        frame_id: str | None,
        *,
        fallback_project: str = "default",
    ) -> dict:
        """Resolve actor, root session, and root-owned project dynamically."""
        if not frame_id:
            return {
                "frame_id": frame_id,
                "root_frame_id": frame_id,
                "project_id": fallback_project,
            }
        with self._lock:
            frame = self._connection.execute(
                "SELECT frame_id,root_frame_id,project_id FROM frames "
                "WHERE frame_id=?",
                (frame_id,),
            ).fetchone()
            if not frame:
                return {
                    "frame_id": frame_id,
                    "root_frame_id": frame_id,
                    "project_id": fallback_project,
                }
            root_frame_id = frame["root_frame_id"] or frame["frame_id"]
            root = self._connection.execute(
                "SELECT project_id FROM frames WHERE frame_id=?",
                (root_frame_id,),
            ).fetchone()
        return {
            "frame_id": frame["frame_id"],
            "root_frame_id": root_frame_id,
            "project_id": (
                (root["project_id"] if root else None)
                or frame["project_id"]
                or fallback_project
            ),
        }

    def update_frame(self, frame_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = self._clock_ms()
        columns = ", ".join(f"{key}=?" for key in fields)
        self._execute(
            f"UPDATE frames SET {columns} WHERE frame_id=?",
            (*fields.values(), frame_id),
        )

    def add_frame_tokens(
        self,
        frame_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE frames SET input_tokens=COALESCE(input_tokens,0)+?,"
                "output_tokens=COALESCE(output_tokens,0)+?,"
                "cost_usd=COALESCE(cost_usd,0)+?,updated_at=? WHERE frame_id=?",
                (
                    input_tokens,
                    output_tokens,
                    cost_usd,
                    self._clock_ms(),
                    frame_id,
                ),
            )
            self._connection.commit()

    # --- projects ----------------------------------------------------
    def create_project(
        self,
        *,
        name: str,
        description: str = "",
        context: str = "",
        project_id: str | None = None,
        is_example: bool = False,
    ) -> dict:
        project_id = project_id or f"proj_{uuid.uuid4().hex[:12]}"
        now = self._clock_ms()
        self._execute(
            "INSERT OR REPLACE INTO projects(project_id,name,description,context,"
            "is_example,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (
                project_id,
                name,
                description,
                context,
                1 if is_example else 0,
                now,
                now,
            ),
        )
        get_project = self._get_project or self.get_project
        return get_project(project_id) or {}

    def get_project(self, project_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM projects WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_project(self, project_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = self._clock_ms()
        columns = ", ".join(f"{key}=?" for key in fields)
        self._execute(
            f"UPDATE projects SET {columns} WHERE project_id=?",
            (*fields.values(), project_id),
        )

    def delete_project(self, project_id: str) -> dict:
        return self._deletions.delete_project(project_id)

    def project_session_ids(self, project_id: str) -> list[str]:
        return self._deletions.project_session_ids(project_id)

    def list_projects(self) -> list[dict]:
        """Return projects with conversation count and last activity."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC"
            ).fetchall()
            projects = []
            for row in rows:
                project = dict(row)
                aggregate = self._connection.execute(
                    "SELECT COUNT(*) AS n, MAX(updated_at) AS last FROM frames "
                    "WHERE project_id=? AND parent_id IS NULL",
                    (project["project_id"],),
                ).fetchone()
                project["conversation_count"] = aggregate["n"] or 0
                project["last_active_at"] = aggregate["last"] or project["updated_at"]
                projects.append(project)
        return projects

    # --- messages ----------------------------------------------------
    def add_message(
        self,
        *,
        root_frame_id: str,
        branch_id: str | None = None,
        role: str,
        content: str,
        frame_id: str | None = None,
        metadata: dict | None = None,
        created_at: int | None = None,
    ) -> dict:
        now = created_at if created_at is not None else self._clock_ms()
        message_id = f"m-{uuid.uuid4().hex[:12]}"
        branch_id = branch_id or root_frame_id
        with self._lock:
            seq = self._connection.execute(
                "SELECT COALESCE(MAX(seq),-1)+1 AS s FROM messages "
                "WHERE root_frame_id=?",
                (root_frame_id,),
            ).fetchone()["s"]
            self._connection.execute(
                "INSERT INTO messages(message_id,root_frame_id,branch_id,frame_id,"
                "seq,role,content,metadata,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    message_id,
                    root_frame_id,
                    branch_id,
                    frame_id,
                    seq,
                    role,
                    content,
                    json.dumps(metadata, ensure_ascii=False) if metadata else None,
                    now,
                ),
            )
            self._connection.commit()
        return {
            "message_id": message_id,
            "root_frame_id": root_frame_id,
            "branch_id": branch_id,
            "seq": seq,
            "role": role,
            "content": content,
            "created_at": now,
        }

    def list_messages(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        start: int = 0,
        limit: int | None = 300,
    ) -> list[dict]:
        where = "root_frame_id=?"
        params: list[Any] = [root_frame_id]
        if branch_id is not None:
            where += " AND branch_id=?"
            params.append(branch_id)
        suffix = ""
        if limit is not None:
            suffix = " LIMIT ? OFFSET ?"
            params.extend((max(0, int(limit)), max(0, int(start))))
        with self._lock:
            rows = self._connection.execute(
                "SELECT role,content,metadata,created_at,seq FROM messages WHERE "
                + where
                + " ORDER BY seq ASC"
                + suffix,
                tuple(params),
            ).fetchall()
        values = [dict(row) for row in rows]
        return values[start:] if limit is None and start else values

    def list_message_boundaries(
        self,
        root_frame_id: str,
        *,
        branch_id: str | None = None,
        start: int = 0,
        limit: int | None = 300,
    ) -> list[dict]:
        """Return public message identities plus exact fork proof, if present."""

        where = "m.root_frame_id=?"
        params: list[Any] = [root_frame_id]
        if branch_id is not None:
            where += " AND m.branch_id=?"
            params.append(branch_id)
        suffix = ""
        if limit is not None:
            suffix = " LIMIT ? OFFSET ?"
            params.extend((max(0, int(limit)), max(0, int(start))))
        with self._lock:
            rows = self._connection.execute(
                "SELECT m.message_id,m.root_frame_id,m.branch_id,m.seq,m.role,"
                "m.content,m.created_at,(SELECT "
                "c.checkpoint_id FROM session_checkpoints AS c WHERE "
                "c.root_frame_id=m.root_frame_id AND c.source_kind='message' "
                "AND c.source_id=m.message_id LIMIT 1) AS fork_checkpoint_id "
                "FROM messages AS m WHERE " + where + " ORDER BY m.seq ASC" + suffix,
                tuple(params),
            ).fetchall()
        values = [dict(row) for row in rows]
        return values[start:] if limit is None and start else values

    def message_count(self, root_frame_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE root_frame_id=?",
                (root_frame_id,),
            ).fetchone()
        return row["n"] or 0

    def cell_count(self, root_frame_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS n FROM execution_log WHERE root_frame_id=?",
                (root_frame_id,),
            ).fetchone()
        return row["n"] or 0

    def latest_state_revision(self, root_frame_id: str) -> int:
        """Return the durable session revision cursor used for the next Cell.

        Indexed historical rows are authoritative.  A count fallback reserves
        ordinals for older unindexed rows without fabricating per-row revision
        metadata for them.
        """

        with self._lock:
            row = self._connection.execute(
                "SELECT (SELECT COUNT(*) FROM execution_log WHERE root_frame_id=?) "
                "AS n,(SELECT MAX(COALESCE(state_revision,cell_index,0)) FROM "
                "execution_log WHERE root_frame_id=?) AS logged_revision,"
                "(SELECT MAX(a.state_revision) FROM execution_attempts AS a "
                "JOIN action_groups AS g ON g.group_id=a.group_id "
                "WHERE g.root_frame_id=?) AS attempt_revision",
                (root_frame_id, root_frame_id, root_frame_id),
            ).fetchone()
        return max(
            int(row["n"] or 0),
            int(row["logged_revision"] or 0),
            int(row["attempt_revision"] or 0),
        )

    # --- semantic activity steps ------------------------------------
    def add_step(
        self,
        *,
        step_id: str,
        frame_id: str,
        kind: str,
        title: str | None = None,
        input: dict | None = None,
        status: str = "running",
    ) -> dict:
        now = self._clock_ms()
        with self._lock:
            seq = self._connection.execute(
                "SELECT COALESCE(MAX(seq),-1)+1 AS s FROM frame_steps "
                "WHERE frame_id=?",
                (frame_id,),
            ).fetchone()["s"]
            self._connection.execute(
                "INSERT INTO frame_steps(step_id,frame_id,seq,kind,title,input,"
                "output,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    step_id,
                    frame_id,
                    seq,
                    kind,
                    title,
                    json.dumps(input, ensure_ascii=False, default=str)
                    if input is not None
                    else None,
                    None,
                    status,
                    now,
                    now,
                ),
            )
            self._connection.commit()
        return {"step_id": step_id, "seq": seq, "created_at": now}

    def update_step(
        self,
        step_id: str,
        *,
        status: str | None = None,
        output: dict | None = None,
        title: str | None = None,
        summary: str | None = None,
    ) -> None:
        now = self._clock_ms()
        sets, params = [], []
        if status is not None:
            sets.append("status=?")
            params.append(status)
        if title is not None:
            sets.append("title=?")
            params.append(title)
        if summary is not None:
            sets.append("summary=?")
            params.append(summary)
        if output is not None:
            sets.append("output=?")
            params.append(json.dumps(output, ensure_ascii=False, default=str))
        sets.append("updated_at=?")
        params.append(now)
        params.append(step_id)
        with self._lock:
            self._connection.execute(
                f"UPDATE frame_steps SET {','.join(sets)} WHERE step_id=?",
                params,
            )
            self._connection.commit()

    def list_steps(
        self,
        frame_id: str,
        *,
        start: int = 0,
        limit: int = 800,
    ) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT step_id,seq,kind,title,summary,input,output,status,"
                "created_at FROM frame_steps WHERE frame_id=? ORDER BY seq ASC "
                "LIMIT ? OFFSET ?",
                (frame_id, limit, max(0, start)),
            ).fetchall()
        steps = []
        for row in rows:
            step = dict(row)
            for key in ("input", "output"):
                if step.get(key):
                    try:
                        step[key] = json.loads(step[key])
                    except (ValueError, TypeError):
                        pass
            steps.append(step)
        return steps

    def step_count(self, frame_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS n FROM frame_steps WHERE frame_id=?",
                (frame_id,),
            ).fetchone()
        return row["n"] or 0

    # --- frame browse/detail/search ----------------------------------
    def browse_frames(
        self,
        *,
        project_id: str | None = "default",
        status: str | None = None,
        roots_only: bool = True,
        limit: int = 50,
        before: tuple[int, str] | None = None,
    ) -> list[dict]:
        """Newest-first page of frames.

        ``before`` is a keyset cursor — the ``(created_at, frame_id)`` of the
        last row of the previous page — not an offset. An offset would skip or
        repeat rows whenever a frame is created or deleted between pages, which
        for a session list is routine rather than exotic.

        The ``frame_id`` tiebreaker is what makes the cursor sound at all:
        ``created_at`` is a millisecond timestamp and two sessions created in
        the same millisecond are not rare (a script, a test, a fast fork). With
        ordering by timestamp alone their relative order is undefined, so a
        cursor could land in the middle of a tie and silently drop the rest of
        it.
        """
        clauses, params = [], []
        if project_id and project_id != "all":
            clauses.append("project_id=?")
            params.append(project_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        if roots_only:
            clauses.append("parent_id IS NULL")
        if before is not None:
            before_created, before_id = before
            clauses.append("(created_at < ? OR (created_at = ? AND frame_id < ?))")
            params.extend([before_created, before_created, before_id])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                "SELECT frame_id,parent_id,root_frame_id,project_id,kind,name,"
                "task_summary,model,status,depth,input_tokens,output_tokens,"
                "cost_usd,created_at,updated_at FROM frames"
                + where
                + " ORDER BY created_at DESC, frame_id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def frame_detail(
        self,
        frame_id: str,
        *,
        page: int = 0,
        page_size: int = 50,
    ) -> dict | None:
        """Return frame metadata, paged cells, and direct children."""
        with self._lock:
            frame = self._connection.execute(
                "SELECT * FROM frames WHERE frame_id=?",
                (frame_id,),
            ).fetchone()
            if frame is None:
                return None
            total = self._connection.execute(
                "SELECT COUNT(*) AS n FROM execution_log WHERE frame_id=?",
                (frame_id,),
            ).fetchone()["n"]
            cells = self._connection.execute(
                "SELECT producing_cell_id,cell_seq,origin,code,stdout,stderr,"
                "error,interrupted,wall_s,cpu_s,created_at FROM execution_log "
                "WHERE frame_id=? ORDER BY created_at ASC LIMIT ? OFFSET ?",
                (frame_id, page_size, page * page_size),
            ).fetchall()
            children = self._connection.execute(
                "SELECT frame_id,kind,name,status,depth FROM frames "
                "WHERE parent_id=? ORDER BY created_at ASC",
                (frame_id,),
            ).fetchall()
        page_count = max(1, (total + page_size - 1) // page_size)
        return {
            "frame": dict(frame),
            "cells": [dict(cell) for cell in cells],
            "children": [dict(child) for child in children],
            "page": page,
            "page_size": page_size,
            "n_pages": page_count,
            "total_cells": total,
            "last_page": page >= page_count - 1,
        }

    def search_frames(
        self,
        pattern: str,
        *,
        project_id: str | None = "default",
        limit: int = 50,
    ) -> list[dict]:
        """Regex-search frame names and cell code/stdout."""
        regex = re.compile(pattern, re.IGNORECASE)
        clauses, params = [], []
        if project_id and project_id != "all":
            clauses.append("f.project_id=?")
            params.append(project_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT f.frame_id,f.kind,f.name,f.status,f.depth,"
                "f.project_id,f.created_at FROM frames f "
                "LEFT JOIN execution_log e ON e.frame_id=f.frame_id"
                + where
                + " ORDER BY f.created_at DESC",
                tuple(params),
            ).fetchall()
            matches = []
            for row in rows:
                haystack = [row["name"] or ""]
                cells = self._connection.execute(
                    "SELECT code,stdout FROM execution_log WHERE frame_id=?",
                    (row["frame_id"],),
                ).fetchall()
                for cell in cells:
                    haystack.append(cell["code"] or "")
                    haystack.append(cell["stdout"] or "")
                if regex.search("\n".join(haystack)):
                    matches.append(dict(row))
                if len(matches) >= limit:
                    break
        return matches

    # --- execution log ----------------------------------------------
    def log_cell(
        self,
        *,
        frame_id: str | None,
        code: str,
        result: dict,
        origin: str = "agent",
        cell_seq: int | None = None,
        project_id: str = "default",
        root_frame_id: str | None = None,
        cell_index: int | None = None,
        state_revision: int | None = None,
        kernel_id: str = "python",
        language: str = "python",
        visibility: str | None = None,
        pin: bool = False,
        replay_policy: str | None = None,
        figures: list | None = None,
        files_read: list | None = None,
        files_written: list | None = None,
    ) -> str:
        cell_id = result.get("id") or f"c-{uuid.uuid4().hex[:12]}"
        if visibility is None:
            visibility = default_visibility(origin)
        if visibility not in VISIBILITIES:
            raise ValueError(f"unknown Cell visibility: {visibility}")
        if type(pin) is not bool:
            raise TypeError("pin must be a boolean")
        if replay_policy is None:
            replay_policy = default_replay_policy(visibility)
        if replay_policy not in REPLAY_POLICIES:
            raise ValueError(f"unknown Cell replay_policy: {replay_policy}")
        dependencies = analyze_code(code, language)
        usage = result.get("usage") or {}
        status = (
            "interrupted"
            if result.get("interrupted")
            else ("error" if result.get("error") else "ok")
        )
        with self._lock:
            reserved = self._connection.execute(
                "SELECT state_revision FROM execution_attempts "
                "WHERE producing_cell_id=? AND state_revision IS NOT NULL "
                "ORDER BY attempt_ordinal DESC LIMIT 1",
                (cell_id,),
            ).fetchone()
        reserved_revision = (
            int(reserved["state_revision"]) if reserved is not None else None
        )
        if state_revision is None:
            state_revision = (
                reserved_revision if reserved_revision is not None else cell_index
            )
        elif reserved_revision is not None and state_revision != reserved_revision:
            raise ValueError("state_revision must match the durable execution attempt")
        latest_revision = (
            self.latest_state_revision(root_frame_id) if root_frame_id else 0
        )
        if state_revision is None and root_frame_id:
            state_revision = latest_revision + 1
        if state_revision is not None:
            if isinstance(state_revision, bool) or not isinstance(state_revision, int):
                raise TypeError("state_revision must be an integer")
            if state_revision < 1:
                raise ValueError("state_revision must be positive")
            if (
                root_frame_id
                and reserved_revision is None
                and state_revision <= latest_revision
            ):
                raise ValueError(
                    "state_revision must advance the session revision cursor"
                )
        # Execution history is an append-only audit record. A duplicate Cell ID
        # means the caller is attempting to overwrite an already-observed
        # execution, which must fail loudly instead of silently replacing its
        # source, output, error, provenance, or timestamp.
        self._execute(
            "INSERT INTO execution_log(producing_cell_id,frame_id,"
            "root_frame_id,project_id,cell_seq,cell_index,state_revision,"
            "kernel_id,language,"
            "status,origin,code,code_hash,visibility,pin,replay_policy,"
            "variable_reads,variable_writes,variable_deletes,"
            "mutation_uncertain,stdout,stderr,error,figures,files_read,"
            "files_written,interrupted,wall_s,cpu_s,peak_rss_kb,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                cell_id,
                frame_id,
                root_frame_id,
                project_id,
                cell_seq,
                cell_index,
                state_revision,
                kernel_id,
                language,
                status,
                origin,
                code,
                dependencies.code_hash,
                visibility,
                1 if pin else 0,
                replay_policy,
                json.dumps(dependencies.reads, ensure_ascii=False),
                json.dumps(dependencies.writes, ensure_ascii=False),
                json.dumps(dependencies.deletes, ensure_ascii=False),
                1 if dependencies.uncertain else 0,
                result.get("stdout"),
                result.get("stderr"),
                result.get("error"),
                json.dumps(figures or [], ensure_ascii=False),
                json.dumps(files_read or [], ensure_ascii=False),
                json.dumps(files_written or [], ensure_ascii=False),
                1 if result.get("interrupted") else 0,
                usage.get("wall_s"),
                usage.get("cpu_s"),
                usage.get("peak_rss_kb"),
                self._clock_ms(),
            ),
        )
        return cell_id

    def list_cells(
        self, root_frame_id: str, *, branch_id: str | None = None
    ) -> list[dict]:
        """Return a session's notebook execution log oldest first."""
        branch_filter = ""
        params: list[Any] = [root_frame_id]
        if branch_id is not None:
            branch_filter = (
                " AND (EXISTS (SELECT 1 FROM execution_attempts AS ba "
                "JOIN action_groups AS bg ON bg.group_id=ba.group_id "
                "WHERE ba.producing_cell_id=e.producing_cell_id "
                "AND bg.root_frame_id=? AND bg.branch_id=?)"
            )
            params.extend((root_frame_id, branch_id))
            if branch_id == root_frame_id:
                branch_filter += (
                    " OR NOT EXISTS (SELECT 1 FROM execution_attempts AS legacy "
                    "WHERE legacy.producing_cell_id=e.producing_cell_id)"
                )
            branch_filter += ")"
        with self._lock:
            rows = self._connection.execute(
                "SELECT e.producing_cell_id,e.cell_index,e.state_revision,"
                "e.kernel_id,e.language,e.status,e.origin,e.code,e.stdout,"
                "e.code_hash,e.visibility,e.pin,e.replay_policy,"
                "e.variable_reads,e.variable_writes,e.variable_deletes,"
                "e.mutation_uncertain,e.stderr,e.error,e.figures,e.files_read,e.files_written,"
                "e.cpu_s,e.peak_rss_kb,e.created_at,(SELECT a.generation_id "
                "FROM execution_attempts AS a WHERE a.producing_cell_id="
                "e.producing_cell_id AND a.generation_id IS NOT NULL "
                "ORDER BY a.attempt_ordinal DESC LIMIT 1) AS generation_id "
                "FROM execution_log AS e WHERE e.root_frame_id=? " + branch_filter + " "
                "ORDER BY COALESCE(e.state_revision,e.cell_index) ASC,"
                "e.created_at ASC,e.producing_cell_id ASC",
                tuple(params),
            ).fetchall()
        cells = []
        for row in rows:
            cell = dict(row)
            for key in (
                "figures",
                "files_read",
                "files_written",
                "variable_reads",
                "variable_writes",
                "variable_deletes",
            ):
                try:
                    cell[key] = json.loads(cell.get(key) or "[]")
                except (TypeError, ValueError):
                    cell[key] = []
            for key in (
                "variable_reads",
                "variable_writes",
                "variable_deletes",
            ):
                cell[key] = list(normalize_string_list(cell.get(key)))
            cell["pin"] = bool(cell.get("pin"))
            cell["mutation_uncertain"] = bool(cell.get("mutation_uncertain"))
            if cell.get("state_revision") is None:
                cell["state_revision"] = cell.get("cell_index")
            cells.append(cell)
        return cells

    def cell_detail(self, producing_cell_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT e.*,(SELECT a.generation_id FROM execution_attempts AS a "
                "WHERE a.producing_cell_id=e.producing_cell_id "
                "AND a.generation_id IS NOT NULL ORDER BY a.attempt_ordinal DESC "
                "LIMIT 1) AS generation_id FROM execution_log AS e "
                "WHERE e.producing_cell_id=?",
                (producing_cell_id,),
            ).fetchone()
        if not row:
            return None
        cell = dict(row)
        for key in (
            "figures",
            "files_read",
            "files_written",
            "variable_reads",
            "variable_writes",
            "variable_deletes",
        ):
            try:
                cell[key] = json.loads(cell.get(key) or "[]")
            except (TypeError, ValueError):
                cell[key] = []
        for key in (
            "variable_reads",
            "variable_writes",
            "variable_deletes",
        ):
            cell[key] = list(normalize_string_list(cell.get(key)))
        cell["pin"] = bool(cell.get("pin"))
        cell["mutation_uncertain"] = bool(cell.get("mutation_uncertain"))
        if cell.get("state_revision") is None:
            cell["state_revision"] = cell.get("cell_index")
        return cell

    def delete_frame(self, frame_id: str) -> dict[str, Any]:
        """Delete one complete root-session aggregate in a single transaction."""

        return self._deletions.delete_session(frame_id)

    def get_frame(self, frame_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM frames WHERE frame_id=?",
                (frame_id,),
            ).fetchone()
        return dict(row) if row else None

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._connection.execute(sql, params)
            self._connection.commit()


__all__ = ["FrameRepository"]
