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

import base64
import hashlib
import json
from typing import Any, Mapping, Sequence

from openai4s.compute.states import LIVE_STATES

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


class ComputeRecordsUnavailable(Exception):
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
    """Parse the three paging parameters into `owner_tasks` keyword arguments.

    Strict where strictness is free: a non-numeric ``limit`` is refused here
    with a stable code rather than silently defaulted, because a page whose
    filter was ignored looks like a complete answer. The range and filter rules
    (a limit below 1 or an unknown ``state`` is refused, a limit above the
    ceiling is clamped) are enforced once, in `owner_tasks`, for every caller.
    """
    raw_limit = _first(q, "limit")
    limit = MAX_TASKS
    if raw_limit not in (None, ""):
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise InvalidTaskQuery("limit must be an integer", "invalid_limit") from exc
    raw_state = _first(q, "state")
    state = (raw_state or STATE_ALL).strip().lower()
    raw_cursor = _first(q, "cursor")
    return {
        "limit": limit,
        "state": state,
        "cursor": raw_cursor or None,
    }


#: A page cursor names a position as ``(created_at, job_id)`` and carries a
#: fingerprint of the frame and state filter it was minted for. It never carries
#: ``owner_key``: that is the session's workspace path — a leak, and useless as
#: authorization, because every query is scoped by the authenticated owner and
#: the anchor row is checked against that scope before the page is read.
def _cursor_fingerprint(frame_id: str, state: str) -> str:
    canonical = json.dumps(
        {"frame_id": str(frame_id), "state": str(state)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _encode_cursor(*, frame_id: str, state: str, created_at: int, job_id: str) -> str:
    """The opaque position of one row, in `artifact_index.encode_cursor`'s form."""
    raw = json.dumps(
        {
            "fp": _cursor_fingerprint(frame_id, state),
            "id": str(job_id),
            "t": int(created_at),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str, *, frame_id: str, state: str) -> tuple[int, str]:
    """Return the ``(created_at, job_id)`` anchor, or raise ``invalid_cursor``.

    A payload that cannot be decoded, was minted for another frame or state, or
    names no row is one answer: start again from the first page. Continuing from
    a position the server cannot vouch for would skip rows or walk a listing the
    client is not looking at, and both read as a complete answer.
    """
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - unreadable is 400, not page one
        raise InvalidTaskQuery("invalid cursor", "invalid_cursor") from exc
    if not isinstance(payload, dict):
        raise InvalidTaskQuery("invalid cursor", "invalid_cursor")
    if payload.get("fp") != _cursor_fingerprint(frame_id, state):
        raise InvalidTaskQuery("invalid cursor", "invalid_cursor")
    job_id = payload.get("id")
    created_at = payload.get("t")
    if not isinstance(job_id, str) or not job_id:
        raise InvalidTaskQuery("invalid cursor", "invalid_cursor")
    if isinstance(created_at, bool) or not isinstance(created_at, int):
        raise InvalidTaskQuery("invalid cursor", "invalid_cursor")
    return created_at, job_id


def owner_tasks(
    store: Any,
    owner_key: str | None,
    *,
    frame_id: str,
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

    The cursor is decoded here, then the server checks that its anchor row is in
    the caller's owner scope and carries the claimed ``created_at`` before page
    two is read. A rejected cursor is a 400; an unreadable record is a 503 and
    is never an empty page.
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

    after: tuple[int, str] | None = None
    if cursor:
        after = _decode_cursor(cursor, frame_id=frame_id, state=state)

    anchored: Any = None
    try:
        if after is not None:
            anchored = store.compute_job_created_at_for_owner(owner_key, after[1])
        rows, total_live_count, has_more = store.compute_jobs_page_for_owner(
            owner_key, limit=limit, state=state, after=after
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable record is not "no jobs"
        raise ComputeRecordsUnavailable(
            "compute task records could not be read"
        ) from exc

    if after is not None:
        if anchored is None:
            raise InvalidTaskQuery("invalid cursor", "invalid_cursor")
        try:
            anchored_at = int(anchored)
        except (TypeError, ValueError) as exc:  # pragma: no cover - repository bug
            raise InvalidTaskQuery("invalid cursor", "invalid_cursor") from exc
        if anchored_at != after[0]:
            raise InvalidTaskQuery("invalid cursor", "invalid_cursor")

    tasks = [public_task(row) for row in rows if isinstance(row, Mapping)]
    next_cursor = None
    if has_more and tasks:
        last = tasks[-1]
        next_cursor = _encode_cursor(
            frame_id=frame_id,
            state=state,
            created_at=int(last["created_at"]),
            job_id=str(last["job_id"]),
        )
    return {
        "tasks": tasks,
        "live_count": sum(1 for task in tasks if task.get("live")),
        # Said plainly, because the whole design rests on it: this response is
        # a read of what was last recorded, not a check of what is true now.
        "polled": False,
        "total_live_count": max(0, int(total_live_count)),
        "has_more": bool(has_more),
        "next_cursor": next_cursor,
    }


__all__ = [
    "ComputeRecordsUnavailable",
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
