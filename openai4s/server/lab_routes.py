"""Session Lab REST adapters and bounded, metadata-only WebSocket hints."""

from __future__ import annotations

import hashlib
import secrets
import threading
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from openai4s.host.lab import LabService
from openai4s.lab.export import export_run
from openai4s.lab.models import CommandOrigin, ErrorCode, LabCaller, LabError
from openai4s.store import get_store

from . import contract
from .errors import GatewayError

ROUTES = contract.validate_routes(
    (
        contract.RouteSpec("lab.list", "GET", r"/frames/([^/]+)/lab", mutates=False),
        contract.RouteSpec(
            "lab.describe", "GET", r"/frames/([^/]+)/lab/devices/([^/]+)", mutates=False
        ),
        contract.RouteSpec(
            "lab.create", "POST", r"/frames/([^/]+)/lab/runs", mutates=True
        ),
        contract.RouteSpec(
            "lab.run", "GET", r"/frames/([^/]+)/lab/runs/([^/]+)", mutates=False
        ),
        contract.RouteSpec(
            "lab.commands",
            "GET",
            r"/frames/([^/]+)/lab/runs/([^/]+)/commands",
            mutates=False,
        ),
        contract.RouteSpec(
            "lab.observations",
            "GET",
            r"/frames/([^/]+)/lab/runs/([^/]+)/observations",
            mutates=False,
        ),
        contract.RouteSpec(
            "lab.execute",
            "POST",
            r"/frames/([^/]+)/lab/runs/([^/]+)/commands",
            mutates=True,
        ),
        contract.RouteSpec(
            "lab.reconcile",
            "POST",
            r"/frames/([^/]+)/lab/runs/([^/]+)/commands/([^/]+)/reconcile",
            mutates=True,
        ),
        contract.RouteSpec(
            "lab.stop", "POST", r"/frames/([^/]+)/lab/runs/([^/]+)/stop", mutates=True
        ),
        contract.RouteSpec(
            "lab.export",
            "POST",
            r"/frames/([^/]+)/lab/runs/([^/]+)/export",
            mutates=True,
        ),
        contract.RouteSpec(
            "lab.events", "GET", r"/frames/([^/]+)/lab/events", mutates=False
        ),
    )
)

_ERROR_STATUS = {
    ErrorCode.INVALID_PARAMETERS: 422,
    ErrorCode.MODE_MISMATCH: 422,
    ErrorCode.RUN_NOT_FOUND: 404,
    ErrorCode.DEVICE_NOT_FOUND: 404,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.REPLAY_FORBIDDEN: 403,
    ErrorCode.PROVIDER_UNAVAILABLE: 503,
    ErrorCode.ADAPTER_MISMATCH: 503,
    ErrorCode.PERSISTENCE_UNAVAILABLE: 503,
}


def _invalid(message: str) -> LabError:
    return LabError(ErrorCode.INVALID_PARAMETERS, message)


def _body(handler: Any, allowed: set[str], required: set[str] | None = None) -> dict:
    try:
        body = handler._body()
    except GatewayError as exc:
        if exc.code == 400:
            raise _invalid("Invalid Lab request body") from None
        raise
    if (
        not isinstance(body, dict)
        or set(body) - allowed
        or not (required or set()) <= set(body)
    ):
        raise _invalid("Invalid Lab request fields")
    return body


def _query(q: dict, name: str, default: Any = None) -> Any:
    values = q.get(name)
    if values is None:
        return default
    if len(values) != 1:
        raise _invalid("Repeated Lab query parameter")
    return values[0]


def _integer(q: dict, name: str, default: int) -> int:
    value = _query(q, name, str(default))
    try:
        return int(value)
    except (ValueError, TypeError):
        raise _invalid("Invalid Lab pagination") from None


def _full(q: dict) -> bool:
    value = _query(q, "full", "true")
    if value not in ("true", "false", "1", "0"):
        raise _invalid("Invalid full")
    return value in ("true", "1")


def handle(self: Any, method: str, sub: str, q: dict, runner: Any) -> bool:
    """Reply to one declared route, otherwise leave dispatch to the gateway."""
    for route in ROUTES:
        match = route.match(method, sub)
        if match:
            break
    else:
        return False
    fid = match.group(1)
    store = get_store(runner.cfg.db_path)
    frame = store.get_frame(fid)
    if frame is None or (frame.get("root_frame_id") or fid) != fid:
        raise GatewayError(404, "session not found")
    owner = store.team.session_owner(fid) if runner.cfg.team_mode else None
    caller = LabCaller(
        fid,
        fid,
        owner["user_id"] if owner else None,
        CommandOrigin.MANUAL_UI,
        None,
        None,
    )
    try:
        manager = runner.lab_manager
        name = route.name
        if name == "lab.list":
            result = {
                "devices": manager.list_devices(caller),
                "runs": manager.list_runs(caller, limit=50),
                "latest_event_seq": manager.events(caller, after_seq=0, limit=1)[
                    "latest_event_seq"
                ],
            }
        elif name == "lab.describe":
            result = {
                "descriptor": manager.describe(
                    caller, match.group(2), _query(q, "profile")
                )
            }
        elif name == "lab.create":
            body = _body(
                self,
                {"device_id", "profile", "seed", "budgets", "idempotency_key"},
                {"device_id", "profile", "idempotency_key"},
            )
            # Deletion marks admission closed before taking this gate, and the
            # manager tombstones the root in its deletion hook: an opening that
            # already won is discarded when it finishes, a later one is refused
            # without spawning a provider. The gate covers admission only, so a
            # deletion never waits for a provider's (up to 180 s) opening.
            with runner._lab_creations.session(fid):
                refusal = creation_refusal(runner, fid)
            if refusal is not None:
                raise GatewayError(*refusal)
            result = _ui_observation(
                manager,
                caller,
                None,
                manager.create_run(caller, body),
            )
        elif name == "lab.run":
            run_id = match.group(2)
            result = manager.observe(caller, run_id, full=True)
            result["descriptor"] = manager.describe_run(caller, run_id)
            result["commands"] = manager.commands(
                caller,
                run_id,
                after_seq=max(0, result["run"]["command_count"] - 50),
                limit=50,
            )["commands"]
        elif name == "lab.commands":
            result = manager.commands(
                caller,
                match.group(2),
                after_seq=_integer(q, "after_seq", 0),
                limit=_integer(q, "limit", 50),
            )
        elif name == "lab.observations":
            result = manager.observations(
                caller,
                match.group(2),
                after_sequence=_integer(q, "after_sequence", -1),
                limit=_integer(q, "limit", 20),
                full=_full(q),
            )
        elif name == "lab.execute":
            body = _body(
                self,
                {
                    "operation",
                    "source",
                    "target",
                    "parameters",
                    "expected_revision",
                    "idempotency_key",
                },
                {"operation", "expected_revision", "idempotency_key"},
            )
            run_id = match.group(2)
            try:
                result = manager.execute(
                    caller,
                    {
                        "source": None,
                        "target": None,
                        "parameters": {},
                        **body,
                        "run_id": run_id,
                    },
                )
            except LabError as exc:
                if exc.code is not ErrorCode.OUTCOME_UNKNOWN:
                    raise
                result = manager.status(caller, run_id, exc.details["command_id"])
            result = _ui_observation(manager, caller, run_id, result)
        elif name == "lab.reconcile":
            _body(self, set())
            result = _ui_observation(
                manager,
                caller,
                match.group(2),
                manager.status(caller, match.group(2), match.group(3)),
            )
        elif name == "lab.stop":
            body = _body(self, {"reason"})
            reason = body.get("reason")
            if reason is not None and not isinstance(reason, str):
                raise _invalid("Invalid stop reason")
            result = manager.stop(caller, match.group(2), reason)
        elif name == "lab.export":
            body = _body(self, {"include_evaluation"})
            service = LabService(lambda: manager, lambda: caller)
            service.exporter = lambda actor, run_id, include: export_for_session(
                runner, actor, run_id, include
            )
            result = service.call("export", {"run_id": match.group(2), **body})
            if "error" in result:
                raise LabError(ErrorCode(result["error_kind"]), result["error"])
        else:
            result = manager.events(
                caller,
                after_seq=_integer(q, "after_seq", 0),
                limit=_integer(q, "limit", 200),
            )
        self._json(result)
    except LabError as exc:
        error = {"error": exc.message, "code": exc.code.value}
        if exc.details is not None:
            error["details"] = exc.details
        self._json(error, _ERROR_STATUS.get(exc.code, 503))
    return True


def export_for_session(
    runner: Any,
    caller: LabCaller,
    run_id: str,
    include_evaluation: bool,
    execution_bound: bool | None = None,
    *,
    session: Any = None,
) -> dict[str, Any]:
    """Shared REST/native/SDK synchronous capture and post-commit association.

    Exact fingerprint claims already fence nested child captures. Reuse that
    mechanism for these nested Host-owned captures so the enclosing native or
    Python Cell sweep cannot register unchanged export bytes a second time.
    A subsequent actual file write invalidates the claim and is captured normally.
    """
    from .artifacts import _PinnedUploadDirectory, _PinnedUploadFile

    store = runner.store
    scope = store.resolve_frame_scope(caller.root_frame_id)
    st = session or runner._state(caller.root_frame_id, scope["project_id"])
    runner.require_session_writable(caller.root_frame_id, "exporting Lab evidence")
    lease = (
        st.trusted_capture.external_mutation()
        if execution_bound is None
        else st.trusted_capture.foreground_mutation(execution_bound=execution_bound)
    )
    events: list[dict[str, Any]] = []
    try:
        # Lock ordering matches Artifact writes: writer first, then Store. No
        # enclosing SQL transaction: each Artifact/ledger write commits itself.
        with lease, runner.artifacts.writer_transaction(), store._lock:
            st.workspace.mkdir(parents=True, exist_ok=True)
            with _PinnedUploadDirectory.open_under(
                st.workspace, (), create=False
            ) as directory:

                def commit(
                    kind: str, suffix: str, content: str, source: dict[str, Any]
                ) -> dict[str, Any]:
                    filename = (
                        "lab-"
                        + hashlib.sha256(run_id.encode()).hexdigest()[:24]
                        + "-"
                        + suffix
                    )
                    path = st.workspace / filename
                    temporary = st.workspace / (".lab-export-" + secrets.token_hex(12))
                    data = content.encode("utf-8")
                    checksum = hashlib.sha256(data).hexdigest()
                    published = False
                    try:
                        with _PinnedUploadFile.create(directory, temporary) as staged:
                            staged.write(data)
                            directory.assert_current()
                            directory.replace(temporary, path)
                            published = True
                            directory.fsync()
                            staged.verified_bytes(named_as=path, checksum=checksum)
                            frozen = runner.artifacts.freeze_capture_snapshot(
                                filename, path
                            )
                            record = runner.artifacts.register_file(
                                st,
                                path,
                                None,
                                events.append,
                                producer_frame_id=caller.frame_id,
                                source=source,
                                expected_checksum=checksum,
                                frozen_snapshot=frozen,
                            )
                            if not record:
                                raise LabError(
                                    ErrorCode.PERSISTENCE_UNAVAILABLE,
                                    "Lab export capture failed",
                                )
                            if execution_bound is not None:
                                runner.artifacts.claim_delegated_artifacts(
                                    [record], workspace=st.workspace
                                )
                            staged.verified_bytes(named_as=path, checksum=checksum)
                            directory.assert_current()
                            return record
                    except BaseException:
                        if published and execution_bound is not None:
                            # A concurrent write can occur between registration
                            # and claiming. If final verification refuses it,
                            # never leave a successful claim for uncaptured bytes.
                            try:
                                runner.artifacts._put_delegated_claim(
                                    path, workspace=st.workspace, failed=True
                                )
                            except Exception:
                                # Preserve the primary error. Claim-capacity
                                # failures already fence the entire workspace.
                                pass
                        raise
                    finally:
                        directory.unlink(temporary, missing_ok=True)

                result = export_run(
                    store.lab, caller, run_id, include_evaluation, commit
                )
        for event in events:
            runner.hub.broadcast(caller.root_frame_id, event)
        return result
    except LabError:
        raise
    except Exception:
        # No provider values, filesystem paths or raw database errors escape.
        raise LabError(
            ErrorCode.PERSISTENCE_UNAVAILABLE,
            "Lab export could not commit all evidence",
        ) from None


def _ui_observation(
    manager: Any, caller: LabCaller, run_id: str | None, result: dict
) -> dict:
    """A command envelope as the workbench needs it (CONTRACT §10 UI view).

    The manager's command results are the agent view, where an array over 256
    elements (GenWurtz `layers` is 3x100) arrives as a summary. The UI draws
    the vessels from it, so it gets that same observation in full.
    """
    observation = result.get("observation")
    if observation is None:
        return result
    page = manager.observations(
        caller,
        run_id or result["run"]["run_id"],
        after_sequence=observation["sequence"] - 1,
        limit=1,
        full=True,
    )["observations"]
    return {**result, "observation": page[0] if page else observation}


def creation_refusal(runner: Any, root_frame_id: str) -> tuple[int, str] | None:
    """Why a run may not be created for this root now, or None.

    Shared by every entry point (REST and the session's tools/SDK), always
    under that root's LabCreationGate, so a deletion that has begun refuses
    every later admission. An opening already admitted is not waited for:
    the manager's tombstone for the deleted root discards it.
    """
    store = get_store(runner.cfg.db_path)
    with runner._lock:
        current = store.get_frame(root_frame_id)
        if current is None:
            return 404, "session not found"
        if (
            runner._closed
            or root_frame_id in runner._deleting_sessions
            or current.get("project_id") in runner._deleting_projects
        ):
            return 409, "session deletion or shutdown is in progress"
    return None


class SessionLabManager:
    """The daemon's one manager as a session's tools and host.lab see it.

    Creating a run takes the same per-root gate and admission check as the
    REST route; everything else is the manager's own. Without this a tool or
    SDK create could open a provider after the deletion hook ran and before
    the ledger rows were deleted, leaving a live provider with no run row.
    """

    def __init__(self, runner: Any) -> None:
        self._runner = runner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runner.lab_manager, name)

    def create_run(self, caller: LabCaller, request: dict[str, Any]) -> dict[str, Any]:
        root = caller.root_frame_id
        with self._runner._lab_creations.session(root):
            refusal = creation_refusal(self._runner, root)
        if refusal is not None:
            raise LabError(ErrorCode.PROVIDER_UNAVAILABLE, refusal[1])
        return self._runner.lab_manager.create_run(caller, request)


class LabCreationGate:
    """Serialize each root's provider opening with its deletion hook only."""

    def __init__(self):
        self._lock = threading.Lock()
        self._roots: dict[str, tuple[Any, int]] = {}

    @contextmanager
    def session(self, root_frame_id: str):
        with self._lock:
            lock, users = self._roots.get(root_frame_id, (threading.Lock(), 0))
            self._roots[root_frame_id] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._lock:
                _, users = self._roots[root_frame_id]
                if users == 1:
                    del self._roots[root_frame_id]
                else:
                    self._roots[root_frame_id] = (lock, users - 1)


class LabUpdateEmitter:
    """Trailing 250 ms batches; never publish provider data or read via manager."""

    def __init__(
        self,
        *,
        emit: Callable,
        latest_seq: Callable,
        session_exists: Callable,
        timer_factory=threading.Timer,
    ):
        self._emit = emit
        self._latest_seq = latest_seq
        self._session_exists = session_exists
        self._timer_factory = timer_factory
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str | None, Any]] = {}
        self._deleted: set[str] = set()
        self._failed_deletions: set[str] = set()
        self._closed = False

    def changed(self, root_frame_id: str, run_id: str) -> None:
        with self._lock:
            if self._closed:
                return
            if root_frame_id in self._deleted:
                if root_frame_id not in self._failed_deletions:
                    return
                # Deletion may fail before commit or during file cleanup.
                # Only an existing root can resume. A broken Store read is
                # retried on the next change, never interpreted as existence.
                if not self._session_exists(root_frame_id):
                    return
                self._deleted.discard(root_frame_id)
                self._failed_deletions.discard(root_frame_id)
            pending = self._pending.get(root_frame_id)
            if pending is not None:
                previous, timer = pending
                self._pending[root_frame_id] = (
                    previous if previous == run_id else None,
                    timer,
                )
                return
            timer = self._timer_factory(0.25, lambda: self._flush(root_frame_id, timer))
            timer.daemon = True
            self._pending[root_frame_id] = (run_id, timer)
            timer.start()

    def _flush(self, root_frame_id: str, timer: Any) -> None:
        # Serialize publication with shutdown and new batches. A new timer
        # starts only after this send, so sends stay at least 250 ms apart.
        with self._lock:
            pending = self._pending.get(root_frame_id)
            if self._closed or pending is None or pending[1] is not timer:
                return
            self._pending.pop(root_frame_id)
            run_id, _timer = pending
            try:
                self._emit(
                    root_frame_id,
                    {
                        "type": "lab_update",
                        "root_frame_id": root_frame_id,
                        "frame_id": root_frame_id,
                        "run_id": run_id,
                        "latest_event_seq": self._latest_seq(root_frame_id),
                    },
                )
            except Exception:
                # This is a hint. Durable REST state remains authoritative.
                pass

    def drop_session(self, root_frame_id: str) -> None:
        with self._lock:
            # A provider call interrupted by deletion can unwind and notify
            # after this hook. Root IDs are not reused: never resurrect its
            # WebSocket resume window with that late completion hint.
            self._deleted.add(root_frame_id)
            self._failed_deletions.discard(root_frame_id)
            pending = self._pending.pop(root_frame_id, None)
            if pending is not None:
                pending[1].cancel()

    def deletion_failed(self, root_frame_id: str) -> None:
        with self._lock:
            if root_frame_id in self._deleted:
                self._failed_deletions.add(root_frame_id)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for _run_id, timer in self._pending.values():
                timer.cancel()
            self._pending.clear()
