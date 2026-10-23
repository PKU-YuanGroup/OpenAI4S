"""The read-only view of one session's remote compute work.

A remote job outlives the turn that launched it, the kernel, and the daemon.
`ComputeManager` already keeps a durable record of each one — but the only way
to see it was to be the agent, inside a cell, calling `host.compute`. A person
whose GPU job has been running for two hours had nowhere to look.

The obvious way to give them one is a page that polls. That is the wrong shape
here, for a reason specific to this system rather than a general dislike of
polling: **the probe is the harvest**. `ComputeManager.result()` is what
contacts the remote, and contacting the remote is what pulls files back into
the workspace, writes artifacts, and closes the job. There is no read-only
probe to poll with. A page that refreshed itself would silently harvest into a
session nobody was looking at, and a background poller would bill a provider
on a schedule the user never chose.

So this module reads the durable record and nothing else. It has no import of
`ComputeManager`, is handed a `Store` rather than a session runtime, and
therefore *cannot* advance a job's state however it is called — the guarantee
is structural rather than a promise about call order. Advancing state is a
separate, explicit action.

Scoping is by `owner_key`, which is the session's workspace path. A job that
belongs to another session is not listed, not counted, and not reported as
hidden: the count itself would tell one session how much remote work another
is doing.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from openai4s.compute.states import LIVE_STATES
from openai4s.storage.compute_jobs import ComputeCursorError

#: How many jobs one response may carry. Remote work accumulates over a long
#: project and the panel is a summary, not an archive.
MAX_TASKS = 200


class InvalidTaskQuery(ValueError):
    """A paging parameter the route refuses, with a stable machine code.

    `code` is the contract; `limit` and `state` are validated here rather than
    in the gateway so the same rules hold for every caller, and so a bad
    parameter can never be mistaken for an unreadable record (which is a 503,
    not a 400).
    """

    def __init__(self, message: str, code: str = "invalid_query") -> None:
        super().__init__(message)
        self.code = code


class ComputeRecordUnavailable(RuntimeError):
    """The durable record could not be read.

    Distinct from "no jobs" on purpose: an unreadable store must never render
    as an empty task list with a zero live count, because that is
    indistinguishable from a healthy session with nothing running — and a user
    reading "0 in progress" while a GPU job bills is the failure this whole
    module is built around. The message is fixed text; nothing from the
    exception (SQL, database path, provider detail) reaches the client.
    """


#: The filters `GET .../compute/tasks` accepts. `all` is the record; `live`
#: narrows the page to jobs that may still be consuming a remote resource.
STATE_ALL = "all"
STATE_LIVE = "live"
TASK_STATES: tuple[str, ...] = (STATE_ALL, STATE_LIVE)

#: What a client may see about one job. The stored record also holds `pid`,
#: `pgid`, `sandbox_id`, `receipt` and the raw `outputs` spec — process
#: identifiers and provider handles that a panel cannot act on and that exist
#: to let the manager cancel and harvest. Rendering them would publish the
#: shape of someone's cluster to anything that reads the page.
PUBLIC_FIELDS: tuple[str, ...] = (
    "job_id",
    "provider",
    "status",
    "reason",
    "termination_reason",
    "exit_code",
    "created_at",
    "updated_at",
    "submitted_at",
    "terminal_at",
    "integrity_sha256",
)

#: Per-value ceiling for the free-text fields. `reason` is written by provider
#: code and can carry a whole stderr tail.
MAX_TEXT_CHARS = 500


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_TEXT_CHARS:
        return value[:MAX_TEXT_CHARS] + "…"
    return value


def _manifest_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    """How much a harvest produced, without the paths.

    A file list is the useful part of a manifest and also the part that names
    directories on someone's cluster. The count and total size answer "did my
    outputs come back", which is the question the panel exists for.
    """
    manifest = record.get("artifact_manifest")
    if not isinstance(manifest, Sequence) or isinstance(manifest, (str, bytes)):
        return {"file_count": 0, "total_bytes": 0}
    total = 0
    for item in manifest:
        if isinstance(item, Mapping):
            try:
                total += int(item.get("size") or 0)
            except (TypeError, ValueError):
                continue
    return {"file_count": len(manifest), "total_bytes": total}


def public_task(record: Mapping[str, Any]) -> dict[str, Any]:
    """The client-safe projection of one durable compute job."""
    out: dict[str, Any] = {}
    for field in PUBLIC_FIELDS:
        value = record.get(field)
        if value in (None, ""):
            continue
        out[field] = _clip(value)
    status = str(record.get("status") or "")
    out["status"] = status or "unknown"
    # `unknown` is not a failure and must never be rendered as one: it means
    # the remote could not be reached, and the job may well be running and
    # billing. The state machine says so; a UI that guessed would say the
    # opposite of the truth in the case that costs money.
    out["live"] = status in LIVE_STATES
    out["terminal"] = bool(record.get("terminal_at")) and status not in LIVE_STATES
    out["outputs"] = _manifest_summary(record)
    return out


def _first(q: Mapping[str, Any], name: str) -> str | None:
    raw = q.get(name)
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
    if raw is None:
        return None
    return str(raw)


def parse_options(q: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the three paging parameters into keyword arguments.

    Strict where strictness is free: a non-numeric ``limit``, a limit below 1
    and an unknown ``state`` are refused with a stable code rather than
    silently defaulted, because a page whose filter was ignored looks like a
    complete answer. ``limit`` above the ceiling is clamped instead — asking
    for more is not an attack on an endpoint that refuses it.
    """
    raw_limit = _first(q, "limit")
    limit = MAX_TASKS
    if raw_limit not in (None, ""):
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise InvalidTaskQuery("limit must be an integer", "invalid_limit") from exc
        if limit < 1:
            raise InvalidTaskQuery("limit must be at least 1", "invalid_limit")
        limit = min(limit, MAX_TASKS)
    raw_state = _first(q, "state")
    state = (raw_state or STATE_ALL).strip().lower()
    if state not in TASK_STATES:
        raise InvalidTaskQuery("state must be all or live", "invalid_state")
    raw_cursor = _first(q, "cursor")
    return {
        "limit": limit,
        "state": state,
        "cursor": raw_cursor or None,
    }


def owner_tasks(
    store: Any,
    owner_key: str | None,
    *,
    limit: int = MAX_TASKS,
    state: str = STATE_ALL,
    cursor: str | None = None,
) -> dict[str, Any]:
    """One page of one session's remote jobs, read from the durable record only.

    Nothing here contacts a remote, so calling it costs nothing and changes
    nothing. `refreshed_at` is deliberately absent: the record's own
    `updated_at` is when the state was last *established*, and stamping a read
    time next to it would let a stale row look freshly confirmed.

    `live_count` keeps its original meaning — live jobs *on this page* — so a
    client that only knows the old shape reads exactly what it did before.
    `total_live_count` is the same-owner, all-states total, and is the only
    field that may be shown as a global "in progress" number; `has_more` and
    `next_cursor` let a client walk the record without claiming the page is all
    of it.
    """
    if state not in TASK_STATES:
        raise InvalidTaskQuery("state must be all or live", "invalid_state")
    try:
        limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise InvalidTaskQuery("limit must be an integer", "invalid_limit") from exc
    if limit < 1:
        raise InvalidTaskQuery("limit must be at least 1", "invalid_limit")
    limit = min(limit, MAX_TASKS)
    try:
        page = store.compute_jobs_page_for_owner(
            owner_key, limit=limit, state=state, cursor=cursor
        )
    except ComputeCursorError as exc:
        raise InvalidTaskQuery(
            "cursor does not name a position in this listing", "invalid_cursor"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - an unreadable record is not "no jobs"
        raise ComputeRecordUnavailable("compute records could not be read") from exc
    rows = page.get("tasks") if isinstance(page, Mapping) else None
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ComputeRecordUnavailable("compute records could not be read")
    tasks = [public_task(row) for row in rows if isinstance(row, Mapping)]
    try:
        total_live_count = int(page.get("total_live_count") or 0)
    except (TypeError, ValueError) as exc:  # pragma: no cover - repository bug
        raise ComputeRecordUnavailable("compute records could not be read") from exc
    return {
        "tasks": tasks,
        "live_count": sum(1 for task in tasks if task.get("live")),
        # Said plainly, because the whole design rests on it: this response is
        # a read of what was last recorded, not a check of what is true now.
        "polled": False,
        "total_live_count": max(0, total_live_count),
        "has_more": bool(page.get("has_more")),
        "next_cursor": page.get("next_cursor") or None,
    }


__all__ = [
    "ComputeRecordUnavailable",
    "InvalidTaskQuery",
    "MAX_TASKS",
    "MAX_TEXT_CHARS",
    "PUBLIC_FIELDS",
    "STATE_ALL",
    "STATE_LIVE",
    "TASK_STATES",
    "owner_tasks",
    "parse_options",
    "public_task",
]
