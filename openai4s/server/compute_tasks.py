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

#: How many jobs one response may carry. Remote work accumulates over a long
#: project and the panel is a summary, not an archive.
MAX_TASKS = 200

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


def owner_tasks(
    store: Any, owner_key: str | None, *, limit: int = MAX_TASKS
) -> dict[str, Any]:
    """One session's remote jobs, read from the durable record only.

    Nothing here contacts a remote, so calling it costs nothing and changes
    nothing. `refreshed_at` is deliberately absent: the record's own
    `updated_at` is when the state was last *established*, and stamping a read
    time next to it would let a stale row look freshly confirmed.
    """
    try:
        rows = store.compute_jobs_for_owner(owner_key, limit)
    except Exception:  # noqa: BLE001 - an unreadable record must not blank the page
        rows = []
    tasks = [public_task(row) for row in rows if isinstance(row, Mapping)]
    return {
        "tasks": tasks,
        "live_count": sum(1 for task in tasks if task.get("live")),
        # Said plainly, because the whole design rests on it: this response is
        # a read of what was last recorded, not a check of what is true now.
        "polled": False,
    }


__all__ = [
    "MAX_TASKS",
    "MAX_TEXT_CHARS",
    "PUBLIC_FIELDS",
    "owner_tasks",
    "public_task",
]
