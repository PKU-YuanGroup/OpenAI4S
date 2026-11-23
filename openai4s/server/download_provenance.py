"""Where a downloaded file came from (SCI-10).

A file saved by the ``web_download`` tool is captured at the native
control-tool boundary, where there is no Cell. Its Provenance view therefore
had nothing to say -- "No producing code is included", "produced by a
non-Cell action" -- and never named the URL it came from. The download is a
fact the Host saw, so it is written onto the captured version's ``source``
right after capture, and the lineage projection hands it to the viewer:

- the URL the tool was asked for,
- when it was saved, the size and SHA-256 of the bytes that were captured,
- how the request was approved: the approval card the user answered for this
  exact tool call (matched by its call id), or that none was asked because an
  existing rule or the network settings allowed it.

Nothing here grants, re-checks or re-downloads anything; it records.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

DOWNLOAD_TOOL = "web_download"


def _iso(epoch_seconds: float) -> str:
    return (
        datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _approval(store: Any, root_frame_id: str, call_id: str) -> dict[str, Any]:
    """The approval card answered for this call, or "no prompt"."""

    lister = getattr(store, "list_permission_requests", None)
    if call_id and callable(lister):
        for request in lister(root_frame_id=root_frame_id) or ():
            if (
                str(request.get("tool") or "") == DOWNLOAD_TOOL
                and call_id in (request.get("tool_call_id"), request.get("action_id"))
                and request.get("state") == "allowed"
            ):
                return {
                    "how": "approved",
                    "scope": request.get("scope") or "once",
                    "target": request.get("target") or "",
                }
    return {"how": "no_prompt"}


def record_download_sources(
    store: Any,
    *,
    root_frame_id: str,
    call_id: str,
    arguments: Mapping[str, Any] | None,
    artifacts: Iterable[Mapping[str, Any]],
    now: Callable[[], float],
) -> None:
    """Write the download record onto the version(s) the download produced."""

    args = arguments if isinstance(arguments, Mapping) else {}
    url = str(args.get("url") or "")
    if not url:
        return
    rows = [item for item in artifacts if item.get("version_id")]
    wanted = str(args.get("path") or "").lstrip("./")
    if len(rows) > 1 and wanted:
        rows = [item for item in rows if str(item.get("filename") or "") == wanted]
    approval = _approval(store, root_frame_id, call_id)
    saved_at = _iso(now())
    for item in rows:
        version_id = str(item["version_id"])
        meta = store.version_meta(version_id) or {}
        store.set_version_source(
            version_id,
            {
                "download": {
                    "tool": DOWNLOAD_TOOL,
                    "url": url,
                    "downloaded_at": saved_at,
                    "size_bytes": meta.get("size_bytes", item.get("size_bytes")),
                    "sha256": meta.get("checksum") or item.get("checksum"),
                    "content_type": meta.get("content_type")
                    or item.get("content_type"),
                    "approval": approval,
                }
            },
        )


def download_view(source: Any) -> dict[str, Any] | None:
    """The download record a version's ``source`` carries, or None.

    ``source`` is stored as canonical JSON text; a decoded mapping is accepted
    too.
    """

    if isinstance(source, str):
        try:
            source = json.loads(source)
        except ValueError:
            return None
    if not isinstance(source, Mapping):
        return None
    record = source.get("download")
    if not isinstance(record, Mapping) or record.get("tool") != DOWNLOAD_TOOL:
        return None
    return dict(record)


__all__ = ["DOWNLOAD_TOOL", "download_view", "record_download_sources"]
