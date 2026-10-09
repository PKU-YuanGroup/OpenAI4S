"""Local project history views backed by portable on-disk records."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from openai4s.project_folders import ProjectFolderError, project_folder_path
from openai4s.server import contract
from openai4s.server.project_folder_routes import require_local_request

_LIST = contract.RouteSpec(
    "projects.history", "GET", r"/projects/([^/]+)/history", mutates=False
)
_SAVE = contract.RouteSpec(
    "projects.history.save", "POST", r"/projects/([^/]+)/history", mutates=True
)
_SESSION = contract.RouteSpec(
    "projects.history.session",
    "GET",
    r"/projects/([^/]+)/history/([^/]+)",
    mutates=False,
)
_FILE = contract.RouteSpec(
    "projects.history.file",
    "GET",
    r"/projects/([^/]+)/history/([^/]+)/file",
    mutates=False,
)
ROUTES = contract.validate_routes((_LIST, _SAVE, _SESSION, _FILE))


def _can_continue(store: Any, project_id: str, session_id: str) -> bool:
    frame = store.get_frame(session_id)
    return bool(
        frame
        and (frame.get("root_frame_id") or session_id) == session_id
        and frame.get("project_id") == project_id
    )


def _summary(store: Any, project_id: str, row: dict) -> dict:
    session_id = str(row["session_id"])
    return {
        "session_id": session_id,
        "title": str(row.get("title") or session_id),
        "updated_at": row.get("updated_at"),
        "revision": str(row.get("revision_id") or ""),
        "message_count": int(row.get("message_count") or 0),
        "cell_count": int(row.get("cell_count") or 0),
        "file_count": int(row.get("file_count") or 0),
        "can_continue": _can_continue(store, project_id, session_id),
    }


def history_listing(
    service: Any, store: Any, project_id: str, *, autosave: Any = None
) -> dict:
    # One read of the session indexes serves both the list and the status.
    listing = service.list_sessions(project_id)
    rows = None if listing.get("error") else listing["sessions"]
    status = (
        autosave.status(project_id, sessions=rows)
        if autosave is not None
        else service.status(project_id, sessions=rows)
    )
    state = str(status.get("state") or "pending")
    error = listing.get("error") or status.get("error")
    if error:
        state = "error"
    elif not status.get("enabled"):
        state = "unavailable"
    elif state in ("unchanged", "ok"):
        state = "saved"
    elif state == "ready":
        state = "saved" if status.get("last_saved_at") else "pending"
    return {
        "directory": str(status.get("history_path") or ""),
        "status": state,
        "last_saved_at": status.get("last_saved_at"),
        "error": str(error) if error else None,
        "sessions": [_summary(store, project_id, row) for row in listing["sessions"]],
        "truncated": bool(listing.get("truncated", False)),
    }


def history_session(
    service: Any, store: Any, project_id: str, session_id: str, revision: str | None
) -> dict:
    record = service.read_session(project_id, session_id, revision_id=revision)
    _require_read(record)
    return {
        "session_id": session_id,
        "title": str(record.get("title") or session_id),
        "revision": str(record["revision_id"]),
        "branch_id": str(record.get("branch_id") or ""),
        "saved_at": record.get("saved_at", record.get("created_at")),
        "can_continue": _can_continue(store, project_id, session_id),
        "revisions": [
            {"revision": row["revision_id"], "saved_at": row.get("created_at")}
            for row in record.get("revisions", [])
        ],
        "messages": [
            {
                "role": str(row.get("role") or "unknown"),
                "content": str(row.get("content") or ""),
                "created_at": row.get("created_at"),
            }
            for row in record.get("messages", [])
        ],
        "cells": [
            {
                "language": str(row.get("language") or "python"),
                "source": str(row.get("code", row.get("source")) or ""),
                "stdout": str(row.get("stdout") or ""),
                "stderr": str(row.get("stderr") or ""),
                "error": str(row.get("error") or ""),
            }
            for row in record.get("cells", [])
        ],
        "files": record.get("files", record.get("artifacts", [])),
        "settings": record.get("settings", {}).get("project", {}),
        "omissions": record.get("omissions", []),
        "read_only": True,
        "untrusted": True,
    }


def _require_read(record: dict) -> None:
    if record.get("state") == "error":
        raise ProjectFolderError(
            int(record.get("status") or 403),
            str(record.get("error") or "project history cannot be accessed"),
            str(record.get("code") or "project_history_denied"),
        )


def handle(
    handler: Any, method: str, sub: str, q: dict, cfg: Any, store: Any, runner: Any
) -> bool:
    listing = _LIST.match(method, sub)
    save = _SAVE.match(method, sub)
    session = _SESSION.match(method, sub)
    file = _FILE.match(method, sub)
    match = listing or save or session or file
    if match is None:
        return False
    try:
        require_local_request(handler, cfg)
        project_id = unquote(match.group(1))
        project_folder_path(store, cfg, project_id)
        service = runner.project_history
        revision = (q.get("revision") or [None])[0] or None
        if listing or save:
            saved = None
            if save:
                autosave = getattr(runner, "project_history_autosave", None)
                saved = (
                    autosave.flush_project(project_id, timeout=30.0)
                    if autosave is not None
                    else service.sync_project(project_id, include_sessions=True)
                )
                if saved.get("state") != "error" and autosave is not None:
                    autosave.clear_error(project_id)
            result = history_listing(
                service,
                store,
                project_id,
                autosave=getattr(runner, "project_history_autosave", None),
            )
            if saved is not None and saved.get("state") == "error":
                result["status"] = "error"
                result["error"] = str(
                    saved.get("error") or "Project history save failed"
                )
            if save and result["status"] == "error":
                # `status` on an error envelope is the HTTP integer. Keep
                # the archive's domain status nested so clients never need
                # to guess which meaning a failed save returned.
                handler._json(
                    {
                        "error": result["error"],
                        "code": "project_history_save_failed",
                        "history": result,
                    },
                    503,
                )
            else:
                handler._json(result)
        elif session:
            handler._json(
                history_session(
                    service, store, project_id, unquote(match.group(2)), revision
                )
            )
        else:
            relative = (q.get("path") or [""])[0]
            record = service.read_file(
                project_id, unquote(match.group(2)), revision_id=revision, path=relative
            )
            _require_read(record)
            handler._send(
                200,
                record["data"],
                "application/octet-stream",
                {
                    "Content-Disposition": "attachment; filename*=UTF-8''"
                    + quote(Path(relative).name, safe=""),
                    "Cache-Control": "no-store",
                },
            )
    except ProjectFolderError as error:
        handler._json({"error": str(error), "code": error.code}, error.status)
    except FileNotFoundError:
        handler._json({"error": "project history not found", "code": "not_found"}, 404)
    except (OSError, ValueError, RuntimeError):
        handler._json(
            {
                "error": "project history cannot be accessed",
                "code": "project_history_denied",
            },
            403,
        )
    return True
