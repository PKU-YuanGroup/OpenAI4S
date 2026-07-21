"""Two-phase publish + SnapshotLease lifecycle for web shares.

A share is created/updated in one FIFO execution ticket so the frozen
:class:`ShareProjection` reflects a single consistent writer window.  The bundle
and viewer document derive from that one projection and never re-read the live
Store.  Each snapshot lands in an immutable version directory; a ``current.json``
pointer is swapped atomically, and old snapshots are reclaimed only when no
reader lease still holds them.

The service is infrastructure-neutral: FIFO admission, the tunnel client, and
the active-branch resolver are injected, so the whole publish/lease/GC algorithm
is directly testable without the gateway or a live relay.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openai4s.server.share_projection import ShareProjection, ShareProjectionBuilder

# run_in_ticket(root_frame_id, branch_id, fn) -> fn(cancel_event); fn does the
# live-Store read (build) under FIFO admission.  A passthrough is valid for tests.
TicketRunner = Callable[[str, str, Callable[[Any], ShareProjection]], ShareProjection]
PublicUrl = Callable[[str], str]
ActiveBranch = Callable[[str], str]

# Sentinel: "keep the existing expiry" on update (distinct from None = no expiry).
_KEEP: Any = object()


def new_share_id() -> str:
    """128-bit unguessable, DNS-label-safe id: 26 chars of [a-z2-7]."""

    raw = secrets.token_bytes(16)
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()


class TunnelPort:
    """Minimal desired-state tunnel interface (see share/tunnel.py)."""

    def add_share(self, share_id: str) -> None:  # pragma: no cover - protocol
        ...

    def remove_share(self, share_id: str) -> None:  # pragma: no cover - protocol
        ...


class ShareService:
    def __init__(
        self,
        store: Any,
        *,
        builder: ShareProjectionBuilder,
        shares_dir: str | Path,
        public_url: PublicUrl,
        active_branch: ActiveBranch,
        run_in_ticket: TicketRunner | None = None,
        tunnel: TunnelPort | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.store = store
        self.builder = builder
        self.shares_dir = Path(shares_dir).expanduser().resolve()
        self.shares_dir.mkdir(parents=True, exist_ok=True)
        self._public_url = public_url
        self._active_branch = active_branch
        self._run_in_ticket = run_in_ticket or self._inline_ticket
        self.tunnel = tunnel
        self._clock = clock or time.time

        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._lease_guard = threading.Lock()
        self._leases: dict[str, dict[str, int]] = {}
        self._sweeper: threading.Thread | None = None
        self._sweeper_stop: threading.Event | None = None

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _inline_ticket(
        root_frame_id: str,
        branch_id: str,
        fn: Callable[[Any], ShareProjection],
    ) -> ShareProjection:
        return fn(threading.Event())

    def _lock_for(self, share_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(share_id)
            if lock is None:
                lock = self._locks[share_id] = threading.Lock()
            return lock

    def _dir(self, share_id: str) -> Path:
        return self.shares_dir / share_id

    def _snapshots_dir(self, share_id: str) -> Path:
        return self._dir(share_id) / "snapshots"

    def _current_path(self, share_id: str) -> Path:
        return self._dir(share_id) / "current.json"

    def _new_snapshot_id(self) -> str:
        return f"{int(self._clock() * 1000)}-{secrets.token_hex(4)}"

    @staticmethod
    def _fsync_file(path: Path) -> None:
        try:
            fd = os.open(str(path), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        try:
            fd = os.open(str(path), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass

    # ---------------------------------------------------------------- publish
    def create(
        self,
        root_frame_id: str,
        *,
        title: str | None = None,
        expires_at: int | None = None,
    ) -> dict[str, Any]:
        if self.store.active_share_for_frame(root_frame_id) is not None:
            existing = self.store.active_share_for_frame(root_frame_id)
            raise ShareConflict(str(existing["share_id"]))
        return self._publish(
            new_share_id(),
            root_frame_id,
            title=title,
            create=True,
            expires_at=expires_at,
        )

    def update(self, share_id: str, *, expires_at: Any = _KEEP) -> dict[str, Any]:
        row = self.store.get_share(share_id)
        if row is None or row["status"] == "revoked":
            raise KeyError(share_id)
        # A bare update refreshes the snapshot and keeps the existing expiry;
        # passing expires_at (int or None) overrides it.
        resolved = row.get("expires_at") if expires_at is _KEEP else expires_at
        return self._publish(
            share_id,
            str(row["root_frame_id"]),
            title=row.get("title"),
            create=False,
            expires_at=resolved,
        )

    def _publish(
        self,
        share_id: str,
        root_frame_id: str,
        *,
        title: str | None,
        create: bool,
        expires_at: int | None = None,
    ) -> dict[str, Any]:
        lock = self._lock_for(share_id)
        with lock:
            snapshot_id = self._new_snapshot_id()
            # 1. DB intent (publishing). The partial unique index rejects a second
            #    active share for the same frame.
            self.store.begin_share_publish(
                share_id=share_id,
                root_frame_id=root_frame_id,
                title=title,
                pending_snapshot_id=snapshot_id,
                expires_at=expires_at,
            )
            try:
                branch = self._active_branch(root_frame_id)

                def _build(cancel_event: Any) -> ShareProjection:
                    return self.builder.build(
                        root_frame_id, branch, cancel_event=cancel_event
                    )

                # 2. Build the frozen projection inside a FIFO ticket.
                projection = self._run_in_ticket(root_frame_id, branch, _build)
                # Serialize deterministically from the frozen projection (no live
                # Store reads); both artefacts share one projection identity.
                bundle = self.builder.serialize_package(projection)
                meta = self._meta(share_id, root_frame_id, title, projection, bundle)
                view = self.builder.serialize_view(projection, bundle=meta["bundle"])
                # 3-4. Write immutable snapshot dir, then swap the pointer.
                self._write_snapshot(
                    share_id, snapshot_id, projection, bundle, view, meta
                )
                self._swap_pointer(share_id, snapshot_id, projection.projection_id)
                # 5. DB ready.
                self.store.mark_share_ready(
                    share_id,
                    snapshot_id=snapshot_id,
                    bundle_sha256=bundle["sha256"],
                    bundle_size=bundle["size_bytes"],
                    projection_id=projection.projection_id,
                    counts=meta["counts"],
                )
            except Exception:
                # Roll back: drop the half-written snapshot dir and either delete
                # the never-ready row (create) or restore the prior ready state.
                self._discard_snapshot(share_id, snapshot_id)
                if create:
                    self.store.delete_share(share_id)
                else:
                    self._reconcile_row_after_failure(share_id)
                raise
        # 6. Tunnel desired-state (non-blocking; reachability shown via status).
        if self.tunnel is not None:
            try:
                self.tunnel.add_share(share_id)
            except Exception:  # noqa: BLE001 - reachability is surfaced separately
                pass
        # 7. GC old snapshots with no active lease.
        self._gc(share_id)
        return self.public_record(share_id)

    def _meta(
        self,
        share_id: str,
        root_frame_id: str,
        title: str | None,
        projection: ShareProjection,
        bundle: dict[str, Any],
    ) -> dict[str, Any]:
        now = int(self._clock() * 1000)
        counts = dict(projection.counts)
        counts["excluded"] = dict(projection.excluded)
        return {
            "share_id": share_id,
            "root_frame_id": root_frame_id,
            "title": title,
            "projection_id": projection.projection_id,
            "url": self._public_url(share_id),
            "session": {
                "name": projection.frame_meta.get("name"),
                "task_summary": projection.frame_meta.get("task_summary"),
                "model": projection.frame_meta.get("model"),
                "updated_at": projection.frame_meta.get("updated_at"),
            },
            "bundle": {
                "filename": bundle["filename"],
                "sha256": bundle["sha256"],
                "size_bytes": bundle["size_bytes"],
                "schema_version": bundle["schema_version"],
            },
            "counts": counts,
            "excluded": dict(projection.excluded),
            "updated_at": now,
            "expires_at": (self.store.get_share(share_id) or {}).get("expires_at"),
        }

    def _write_snapshot(
        self,
        share_id: str,
        snapshot_id: str,
        projection: ShareProjection,
        bundle: dict[str, Any],
        view: bytes,
        meta: dict[str, Any],
    ) -> None:
        tmp = self._dir(share_id) / "tmp" / secrets.token_hex(8)
        tmp.mkdir(parents=True, exist_ok=True)
        (tmp / "bundle.zip").write_bytes(bundle["data"])
        self._fsync_file(tmp / "bundle.zip")
        (tmp / "view.json").write_bytes(view)
        self._fsync_file(tmp / "view.json")
        (tmp / "meta.json").write_bytes(
            json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
        self._fsync_file(tmp / "meta.json")
        art_dir = tmp / "artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        for key, data in projection.artifact_bytes.items():
            # keys are "artifact-data/<sha256>"
            sha = key.split("/", 1)[-1]
            (art_dir / sha).write_bytes(data)
            self._fsync_file(art_dir / sha)
        self._fsync_dir(art_dir)
        self._fsync_dir(tmp)
        snapshots = self._snapshots_dir(share_id)
        snapshots.mkdir(parents=True, exist_ok=True)
        target = snapshots / snapshot_id
        os.rename(tmp, target)
        self._fsync_dir(snapshots)

    def _swap_pointer(
        self, share_id: str, snapshot_id: str, projection_id: str
    ) -> None:
        current = self._current_path(share_id)
        tmp = current.with_suffix(".json.tmp")
        tmp.write_bytes(
            json.dumps(
                {"snapshot_id": snapshot_id, "projection_id": projection_id},
                sort_keys=True,
            ).encode("utf-8")
        )
        self._fsync_file(tmp)
        os.replace(tmp, current)
        self._fsync_dir(self._dir(share_id))
        with self._lease_guard:
            self._leases.setdefault(share_id, {})

    # ---------------------------------------------------------------- revoke
    def revoke(self, share_id: str) -> dict[str, Any]:
        row = self.store.get_share(share_id)
        if row is None:
            return {"ok": True, "already": True}
        already = row["status"] == "revoked"
        lock = self._lock_for(share_id)
        with lock:
            self.store.mark_share_revoked(share_id)
        if self.tunnel is not None:
            try:
                self.tunnel.remove_share(share_id)
            except Exception:  # noqa: BLE001
                pass
        self._gc(share_id, drop_all=True)
        return {"ok": True, "already": already}

    def revoke_for_session(self, root_frame_id: str) -> None:
        for row in self.store.list_shares_for_frame(root_frame_id):
            if row["status"] != "revoked":
                try:
                    self.revoke(str(row["share_id"]))
                except Exception:  # noqa: BLE001 - deletion must proceed
                    pass
            self.store.delete_share(str(row["share_id"]))
        self._remove_share_tree(root_frame_id)

    def _remove_share_tree(self, root_frame_id: str) -> None:
        for row in self.store.list_shares_for_frame(root_frame_id):
            path = self._dir(str(row["share_id"]))
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)

    # ---------------------------------------------------------------- reads
    def list_for_frame(self, root_frame_id: str) -> list[dict[str, Any]]:
        return [
            self._public_row(row)
            for row in self.store.list_shares_for_frame(root_frame_id)
        ]

    def list_all(self) -> list[dict[str, Any]]:
        return [self._public_row(row) for row in self.store.list_shares()]

    def public_record(self, share_id: str) -> dict[str, Any]:
        row = self.store.get_share(share_id)
        if row is None:
            raise KeyError(share_id)
        return self._public_row(row)

    def _public_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "share_id": row["share_id"],
            "url": self._public_url(str(row["share_id"])),
            "status": row["status"],
            "title": row.get("title"),
            "snapshot_id": row.get("snapshot_id"),
            "bundle_sha256": row.get("bundle_sha256"),
            "size_bytes": row.get("bundle_size"),
            "projection_id": row.get("projection_id"),
            "counts": row.get("counts") or {},
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "revoked_at": row.get("revoked_at"),
            "expires_at": row.get("expires_at"),
        }

    # ---------------------------------------------------------------- leases
    def acquire(self, share_id: str) -> tuple[str, Path] | None:
        """Resolve current.json and pin its snapshot for one request."""

        row = self.store.get_share(share_id)
        if row is None or row["status"] != "ready":
            return None
        pointer = self._read_pointer(share_id)
        if pointer is None:
            return None
        snapshot_id = pointer
        snapshot_dir = self._snapshots_dir(share_id) / snapshot_id
        if not snapshot_dir.is_dir():
            return None
        with self._lease_guard:
            leases = self._leases.setdefault(share_id, {})
            leases[snapshot_id] = leases.get(snapshot_id, 0) + 1
        return snapshot_id, snapshot_dir

    def release(self, share_id: str, snapshot_id: str) -> None:
        with self._lease_guard:
            leases = self._leases.get(share_id)
            if not leases:
                return
            count = leases.get(snapshot_id, 0) - 1
            if count <= 0:
                leases.pop(snapshot_id, None)
            else:
                leases[snapshot_id] = count

    def _read_pointer(self, share_id: str) -> str | None:
        try:
            data = json.loads(self._current_path(share_id).read_bytes())
        except (OSError, TypeError, ValueError):
            return None
        snapshot_id = data.get("snapshot_id")
        return str(snapshot_id) if snapshot_id else None

    def _lease_count(self, share_id: str, snapshot_id: str) -> int:
        with self._lease_guard:
            return self._leases.get(share_id, {}).get(snapshot_id, 0)

    # ---------------------------------------------------------------- GC
    def _gc(self, share_id: str, *, drop_all: bool = False) -> None:
        snapshots = self._snapshots_dir(share_id)
        if not snapshots.is_dir():
            if drop_all:
                self._maybe_remove_dir(share_id)
            return
        current = None if drop_all else self._read_pointer(share_id)
        for child in list(snapshots.iterdir()):
            if not child.is_dir():
                continue
            sid = child.name
            if sid == current:
                continue
            if self._lease_count(share_id, sid) > 0:
                continue
            shutil.rmtree(child, ignore_errors=True)
        # Clean stale tmp on every GC pass.
        tmp = self._dir(share_id) / "tmp"
        if tmp.is_dir():
            for child in list(tmp.iterdir()):
                shutil.rmtree(child, ignore_errors=True)
        if drop_all:
            self._maybe_remove_dir(share_id)

    def _maybe_remove_dir(self, share_id: str) -> None:
        with self._lease_guard:
            if self._leases.get(share_id):
                return
        path = self._dir(share_id)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    def _discard_snapshot(self, share_id: str, snapshot_id: str) -> None:
        target = self._snapshots_dir(share_id) / snapshot_id
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        tmp = self._dir(share_id) / "tmp"
        if tmp.is_dir():
            for child in list(tmp.iterdir()):
                shutil.rmtree(child, ignore_errors=True)

    def _reconcile_row_after_failure(self, share_id: str) -> None:
        row = self.store.get_share(share_id)
        if row is None:
            return
        prior = row.get("snapshot_id")
        if prior and (self._snapshots_dir(share_id) / str(prior)).is_dir():
            self.store.mark_share_ready(
                share_id,
                snapshot_id=str(prior),
                bundle_sha256=row.get("bundle_sha256") or "",
                bundle_size=int(row.get("bundle_size") or 0),
                projection_id=row.get("projection_id") or "",
                counts=row.get("counts") or {},
            )
        else:
            self.store.mark_share_failed(share_id)

    # ---------------------------------------------------------------- expiry
    def sweep_expired(self, *, now_ms: int | None = None) -> list[str]:
        """Revoke every share whose ``expires_at`` has passed."""

        now = now_ms if now_ms is not None else int(self._clock() * 1000)
        revoked: list[str] = []
        for row in self.store.list_expired_shares(now):
            share_id = str(row["share_id"])
            try:
                self.revoke(share_id)
                revoked.append(share_id)
            except Exception:  # noqa: BLE001 - one bad row must not stop the sweep
                pass
        return revoked

    def start_sweeper(self, *, interval: float = 60.0) -> None:
        if self._sweeper is not None and self._sweeper.is_alive():
            return
        stop = self._sweeper_stop = threading.Event()

        def _loop() -> None:
            while not stop.wait(interval):
                try:
                    self.sweep_expired()
                except Exception:  # noqa: BLE001 - the sweeper must never die
                    pass

        self._sweeper = threading.Thread(
            target=_loop, name="openai4s-share-sweeper", daemon=True
        )
        self._sweeper.start()

    def stop_sweeper(self) -> None:
        if self._sweeper_stop is not None:
            self._sweeper_stop.set()
        self._sweeper = None

    # ---------------------------------------------------------------- restore
    def restore(self) -> list[str]:
        """Recover after a crash/restart; return the desired active share ids."""

        desired: list[str] = []
        for row in self.store.list_shares(include_revoked=True):
            share_id = str(row["share_id"])
            status = row["status"]
            # Always sweep stale staging.
            tmp = self._dir(share_id) / "tmp"
            if tmp.is_dir():
                for child in list(tmp.iterdir()):
                    shutil.rmtree(child, ignore_errors=True)

            if status == "revoked":
                path = self._dir(share_id)
                if path.exists():
                    shutil.rmtree(path, ignore_errors=True)
                continue
            if status == "ready":
                if self._snapshot_ok(share_id, row.get("snapshot_id"), row):
                    self._gc(share_id)
                    desired.append(share_id)
                else:
                    self.store.mark_share_failed(share_id)
                continue
            if status == "publishing":
                self._recover_publishing(share_id, row)
                refreshed = self.store.get_share(share_id)
                if refreshed and refreshed["status"] == "ready":
                    self._gc(share_id)
                    desired.append(share_id)
        # Auto-revoke anything that expired while the daemon was down, so an
        # expired share never comes back online after a restart.
        expired = set(self.sweep_expired())
        return [share_id for share_id in desired if share_id not in expired]

    def _recover_publishing(self, share_id: str, row: dict[str, Any]) -> None:
        pointer = self._read_pointer(share_id)
        if pointer and self._snapshot_ok(share_id, pointer, row, match_sha=False):
            bundle = self._snapshots_dir(share_id) / pointer / "bundle.zip"
            import hashlib

            sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
            self.store.mark_share_ready(
                share_id,
                snapshot_id=pointer,
                bundle_sha256=sha,
                bundle_size=bundle.stat().st_size,
                projection_id=row.get("projection_id") or "",
                counts=row.get("counts") or {},
            )
            return
        prior = row.get("snapshot_id")
        if prior and self._snapshot_ok(share_id, prior, row):
            self.store.mark_share_ready(
                share_id,
                snapshot_id=str(prior),
                bundle_sha256=row.get("bundle_sha256") or "",
                bundle_size=int(row.get("bundle_size") or 0),
                projection_id=row.get("projection_id") or "",
                counts=row.get("counts") or {},
            )
        else:
            self.store.mark_share_failed(share_id)

    def _snapshot_ok(
        self,
        share_id: str,
        snapshot_id: Any,
        row: dict[str, Any],
        *,
        match_sha: bool = True,
    ) -> bool:
        if not snapshot_id:
            return False
        snap = self._snapshots_dir(share_id) / str(snapshot_id)
        bundle = snap / "bundle.zip"
        if not bundle.is_file() or not (snap / "view.json").is_file():
            return False
        if match_sha and row.get("bundle_sha256"):
            import hashlib

            if hashlib.sha256(bundle.read_bytes()).hexdigest() != row["bundle_sha256"]:
                return False
        pointer = self._read_pointer(share_id)
        return pointer == str(snapshot_id)


class ShareConflict(RuntimeError):
    """A frame already has an active share; the caller should update it."""

    def __init__(self, existing_share_id: str) -> None:
        super().__init__(f"active share already exists: {existing_share_id}")
        self.existing_share_id = existing_share_id


__all__ = ["ShareConflict", "ShareService", "TunnelPort", "new_share_id"]
