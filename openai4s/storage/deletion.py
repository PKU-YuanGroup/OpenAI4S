"""Exact lifecycle deletion for one scientific session or whole project.

SQLite foreign keys are intentionally absent from the compatibility schema, so
deletion must name every owned aggregate explicitly.  This repository owns that
single-transaction boundary and returns filesystem candidates to the server;
it never unlinks a path itself.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any


class SessionDeletionRepository:
    """Delete durable session aggregates without crossing ownership scopes."""

    def __init__(self, connection: sqlite3.Connection, lock: Any) -> None:
        self._connection = connection
        self._lock = lock

    def project_session_ids(self, project_id: str) -> list[str]:
        """Return only canonical roots owned by ``project_id``."""

        with self._lock:
            rows = self._connection.execute(
                "SELECT frame_id FROM frames WHERE project_id=? "
                "AND (root_frame_id=frame_id OR root_frame_id IS NULL) "
                "ORDER BY created_at,frame_id",
                (project_id,),
            ).fetchall()
        return [str(row["frame_id"]) for row in rows]

    def delete_session(self, root_frame_id: str) -> dict[str, Any]:
        """Delete exactly one root session and return safe-cleanup candidates."""

        root_frame_id = self._required("root_frame_id", root_frame_id)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT frame_id,root_frame_id,project_id FROM frames "
                    "WHERE frame_id=?",
                    (root_frame_id,),
                ).fetchone()
                if row is None:
                    self._connection.commit()
                    return self._empty_result(root_frame_id=root_frame_id)
                canonical = str(row["root_frame_id"] or row["frame_id"])
                if canonical != root_frame_id:
                    raise ValueError("session deletion requires a root frame id")
                frame_ids = self._frame_ids_for_roots_locked((root_frame_id,))
                artifact_ids = self._artifact_ids_locked(
                    "root_frame_id=?", (root_frame_id,)
                )
                result = self._delete_aggregate_locked(
                    root_frame_ids=(root_frame_id,),
                    frame_ids=frame_ids,
                    artifact_ids=artifact_ids,
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return {
            **result,
            "deleted": True,
            "project_id": str(row["project_id"]),
        }

    def delete_project(self, project_id: str) -> dict[str, Any]:
        """Delete a project using the same complete session aggregate boundary."""

        project_id = self._required("project_id", project_id)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                project = self._connection.execute(
                    "SELECT project_id FROM projects WHERE project_id=?",
                    (project_id,),
                ).fetchone()
                root_frame_ids = tuple(self.project_session_ids(project_id))
                frame_ids = tuple(
                    str(row["frame_id"])
                    for row in self._connection.execute(
                        "SELECT frame_id FROM frames WHERE project_id=? "
                        "ORDER BY created_at,frame_id",
                        (project_id,),
                    ).fetchall()
                )
                artifact_ids = self._artifact_ids_locked("project_id=?", (project_id,))
                result = self._delete_aggregate_locked(
                    root_frame_ids=root_frame_ids,
                    frame_ids=frame_ids,
                    artifact_ids=artifact_ids,
                    project_id=project_id,
                )
                self._delete_counted(
                    result["deleted_rows"],
                    "permission_rules",
                    "scope='project' AND scope_id=?",
                    (project_id,),
                )
                self._delete_counted(
                    result["deleted_rows"],
                    "capability_states",
                    "scope='project' AND scope_id=?",
                    (project_id,),
                )
                self._delete_counted(
                    result["deleted_rows"],
                    "capability_events",
                    "scope='project' AND scope_id=?",
                    (project_id,),
                )
                for table in ("folders", "notes", "memories"):
                    self._delete_counted(
                        result["deleted_rows"],
                        table,
                        "project_id=?",
                        (project_id,),
                    )
                self._delete_counted(
                    result["deleted_rows"],
                    "projects",
                    "project_id=?",
                    (project_id,),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return {
            **result,
            "deleted": project is not None,
            "project_id": project_id,
        }

    def _delete_aggregate_locked(
        self,
        *,
        root_frame_ids: Iterable[str],
        frame_ids: Iterable[str],
        artifact_ids: Iterable[str],
        project_id: str | None = None,
    ) -> dict[str, Any]:
        roots = self._unique(root_frame_ids)
        frames = self._unique(frame_ids)
        artifacts = self._unique(artifact_ids)
        deleted_rows: dict[str, int] = {}

        version_rows = []
        if artifacts:
            version_rows = self._connection.execute(
                "SELECT version_id,path,snapshot_path,env_snapshot_id "
                f"FROM artifact_versions WHERE artifact_id IN {self._marks(artifacts)}",
                artifacts,
            ).fetchall()
        version_ids = self._unique(row["version_id"] for row in version_rows)
        cell_ids = ()
        if roots or frames:
            clauses: list[str] = []
            params: tuple[Any, ...] = ()
            if roots:
                clauses.append(f"root_frame_id IN {self._marks(roots)}")
                params += roots
            if frames:
                clauses.append(f"frame_id IN {self._marks(frames)}")
                params += frames
            cell_ids = self._unique(
                row["producing_cell_id"]
                for row in self._connection.execute(
                    "SELECT producing_cell_id FROM execution_log WHERE "
                    + " OR ".join(clauses),
                    params,
                ).fetchall()
            )
        env_snapshot_ids = self._unique(
            row["env_snapshot_id"] for row in version_rows if row["env_snapshot_id"]
        )
        path_candidates = self._unique(
            path
            for row in version_rows
            for path in (row["path"], row["snapshot_path"])
            if path
        )
        tree_ids = ()
        if roots:
            tree_ids = self._unique(
                row["workspace_tree_id"]
                for row in self._connection.execute(
                    "SELECT workspace_tree_id FROM session_checkpoints "
                    f"WHERE root_frame_id IN {self._marks(roots)} "
                    "AND workspace_tree_id IS NOT NULL",
                    roots,
                ).fetchall()
            )
        group_ids = ()
        if roots:
            group_ids = self._unique(
                row["group_id"]
                for row in self._connection.execute(
                    "SELECT group_id FROM action_groups "
                    f"WHERE root_frame_id IN {self._marks(roots)}",
                    roots,
                ).fetchall()
            )

        if group_ids:
            for table in ("action_events", "execution_attempts"):
                self._delete_counted(
                    deleted_rows,
                    table,
                    f"group_id IN {self._marks(group_ids)}",
                    group_ids,
                )
        if roots:
            root_where = f"root_frame_id IN {self._marks(roots)}"
            for table in (
                "action_groups",
                "kernel_generations",
                "recovery_journal",
                "snapshot_operations",
                "checkpoint_state_snapshots",
                "session_checkpoints",
                "session_branch_selection",
                "session_branches",
                "delegation_steering",
                "delegation_children",
                "delegation_sessions",
                "messages",
                "shares",
            ):
                self._delete_counted(deleted_rows, table, root_where, roots)

        if version_ids or frames or cell_ids:
            lineage_clauses: list[str] = []
            lineage_params: tuple[Any, ...] = ()
            if version_ids:
                lineage_clauses.extend(
                    (
                        f"input_version_id IN {self._marks(version_ids)}",
                        f"output_version_id IN {self._marks(version_ids)}",
                    )
                )
                lineage_params += version_ids + version_ids
            if frames:
                lineage_clauses.append(f"frame_id IN {self._marks(frames)}")
                lineage_params += frames
            if cell_ids:
                lineage_clauses.append(f"producing_cell_id IN {self._marks(cell_ids)}")
                lineage_params += cell_ids
            self._delete_counted(
                deleted_rows,
                "lineage_edges",
                " OR ".join(lineage_clauses),
                lineage_params,
            )
        if artifacts:
            artifact_where = f"artifact_id IN {self._marks(artifacts)}"
            self._delete_counted(
                deleted_rows, "artifact_versions", artifact_where, artifacts
            )

        if roots or artifacts:
            clauses: list[str] = []
            params: tuple[Any, ...] = ()
            if roots:
                clauses.append(f"root_frame_id IN {self._marks(roots)}")
                params += roots
            if artifacts:
                clauses.append(f"artifact_id IN {self._marks(artifacts)}")
                params += artifacts
            self._delete_counted(
                deleted_rows, "annotations", " OR ".join(clauses), params
            )
        if artifacts:
            self._delete_counted(
                deleted_rows,
                "artifacts",
                f"artifact_id IN {self._marks(artifacts)}",
                artifacts,
            )
        if env_snapshot_ids:
            self._delete_counted(
                deleted_rows,
                "env_snapshots",
                f"snapshot_id IN {self._marks(env_snapshot_ids)} AND NOT EXISTS "
                "(SELECT 1 FROM artifact_versions WHERE "
                "artifact_versions.env_snapshot_id=env_snapshots.snapshot_id)",
                env_snapshot_ids,
            )

        if frames or roots:
            if roots and frames:
                execution_where = (
                    f"root_frame_id IN {self._marks(roots)} OR "
                    f"frame_id IN {self._marks(frames)}"
                )
                execution_params = roots + frames
            elif roots:
                execution_where = f"root_frame_id IN {self._marks(roots)}"
                execution_params = roots
            else:
                execution_where = f"frame_id IN {self._marks(frames)}"
                execution_params = frames
            self._delete_counted(
                deleted_rows,
                "execution_log",
                execution_where,
                execution_params,
            )
        if frames:
            frame_where = f"frame_id IN {self._marks(frames)}"
            for table in ("host_call_log", "frame_steps", "plans"):
                self._delete_counted(deleted_rows, table, frame_where, frames)
            self._delete_counted(
                deleted_rows,
                "compaction_archives",
                frame_where,
                frames,
            )

        if roots or frames:
            clauses = []
            params = ()
            if roots:
                clauses.append(f"root_frame_id IN {self._marks(roots)}")
                params += roots
            if frames:
                clauses.append(f"frame_id IN {self._marks(frames)}")
                params += frames
            self._delete_counted(
                deleted_rows,
                "permission_requests",
                " OR ".join(clauses),
                params,
            )
            scope_ids = self._unique((*roots, *frames))
            self._delete_counted(
                deleted_rows,
                "permission_rules",
                f"scope='conversation' AND scope_id IN {self._marks(scope_ids)}",
                scope_ids,
            )
        if roots:
            for table in ("capability_states", "capability_events"):
                self._delete_counted(
                    deleted_rows,
                    table,
                    f"scope='session' AND scope_id IN {self._marks(roots)}",
                    roots,
                )
            self._delete_counted(
                deleted_rows,
                "capability_manifests",
                f"session_id IN {self._marks(roots)}",
                roots,
            )
            for root in roots:
                for key in (
                    f"review:auto:{root}",
                    f"review:model:{root}",
                    f"delegation:{root}",
                    f"session:import-quarantine:{root}",
                ):
                    self._delete_counted(deleted_rows, "settings", "key=?", (key,))
        for frame_id in frames:
            escaped_frame_id = (
                frame_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            self._delete_counted(
                deleted_rows,
                "settings",
                "key LIKE ? ESCAPE '\\'",
                (f"fb:{escaped_frame_id}:%",),
            )

        if project_id is not None:
            for table in (
                "compaction_archives",
                "permission_requests",
                "plans",
                "execution_log",
            ):
                self._delete_counted(deleted_rows, table, "project_id=?", (project_id,))
            self._delete_counted(
                deleted_rows,
                "capability_manifests",
                "project_id=?",
                (project_id,),
            )
        if frames:
            self._delete_counted(
                deleted_rows,
                "frames",
                f"frame_id IN {self._marks(frames)}",
                frames,
            )

        stale_paths = tuple(
            path
            for path in path_candidates
            if self._connection.execute(
                "SELECT 1 FROM artifact_versions WHERE path=? OR snapshot_path=? "
                "LIMIT 1",
                (path, path),
            ).fetchone()
            is None
        )
        retained_tree_ids = self._unique(
            row["workspace_tree_id"]
            for row in self._connection.execute(
                "SELECT DISTINCT workspace_tree_id FROM session_checkpoints "
                "WHERE workspace_tree_id IS NOT NULL"
            ).fetchall()
        )
        retained_paths = self._unique(
            path
            for row in self._connection.execute(
                "SELECT path,snapshot_path FROM artifact_versions"
            ).fetchall()
            for path in (row["path"], row["snapshot_path"])
            if path
        )
        return {
            "root_frame_ids": list(roots),
            "frame_ids": list(frames),
            "artifact_ids": list(artifacts),
            "version_ids": list(version_ids),
            "stale_paths": list(stale_paths),
            "cas_tree_ids": list(tree_ids),
            "retained_cas_tree_ids": list(retained_tree_ids),
            "retained_paths": list(retained_paths),
            "deleted_rows": deleted_rows,
        }

    def _artifact_ids_locked(
        self, where: str, params: tuple[Any, ...]
    ) -> tuple[str, ...]:
        return self._unique(
            row["artifact_id"]
            for row in self._connection.execute(
                f"SELECT artifact_id FROM artifacts WHERE {where}", params
            ).fetchall()
        )

    def _frame_ids_for_roots_locked(self, roots: tuple[str, ...]) -> tuple[str, ...]:
        if not roots:
            return ()
        return self._unique(
            row["frame_id"]
            for row in self._connection.execute(
                f"SELECT frame_id FROM frames WHERE frame_id IN {self._marks(roots)} "
                f"OR root_frame_id IN {self._marks(roots)}",
                roots + roots,
            ).fetchall()
        )

    def _delete_counted(
        self,
        totals: dict[str, int],
        table: str,
        where: str,
        params: tuple[Any, ...],
    ) -> None:
        cursor = self._connection.execute(f"DELETE FROM {table} WHERE {where}", params)
        totals[table] = totals.get(table, 0) + max(0, int(cursor.rowcount))

    @staticmethod
    def _marks(values: tuple[Any, ...]) -> str:
        if not values:
            raise ValueError("SQL value list must not be empty")
        return "(" + ",".join("?" for _ in values) + ")"

    @staticmethod
    def _unique(values: Iterable[Any]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(str(value) for value in values if value))

    @staticmethod
    def _required(name: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _empty_result(*, root_frame_id: str) -> dict[str, Any]:
        return {
            "deleted": False,
            "root_frame_ids": [root_frame_id],
            "frame_ids": [],
            "artifact_ids": [],
            "version_ids": [],
            "stale_paths": [],
            "cas_tree_ids": [],
            "retained_cas_tree_ids": [],
            "retained_paths": [],
            "deleted_rows": {},
        }


__all__ = ["SessionDeletionRepository"]
