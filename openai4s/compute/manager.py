"""Host-side remote-compute transport.

The worker's ``host.compute`` SDK routes every call to
``host_call("compute_<op>", [kw])``; the dispatcher forwards those here. This
module owns the real work the SDK only describes:

  * provider discovery — scan ``skills/remote-compute-<id>/provider.json`` for
    an ``id`` and a ``provider.py`` that exports ``PROVIDER``.
  * byoc transport      — spawn the confined ``openai4s_compute_provider``
    helper (oneshot mode) per op, staging inputs/outputs through a temp dir and
    handing the credential on the helper's stdin so the process environment is
    never a secret carrier.
  * ssh transport       — run a job script / one-off command over an SSH alias.
  * on-demand harvest   — ``result()`` polls the remote and unpacks terminal
    outputs into ``hpc/<job_id>/``.

Two provider families share one manager:
  "byoc:<id>"  bring-your-own-compute sandbox (e.g. "byoc:nvidia").
  "ssh:<alias>" a job over an existing SSH connection.

Terminal states are mutually exclusive and never optimistic:
``done`` (verified rc==0 and outputs harvested), ``failed`` (verified rc!=0),
``timed_out`` (a deadline/job-timeout sentinel fired), ``incomplete`` (the job
itself succeeded but its outputs could not be verified), ``cancelled``, and
``unknown``. ``unknown`` means the outcome could not be established — it is
*not* a synonym for failure, and it must never be resolved to success by
default. Every path that cannot produce an exit code lands there deliberately.

Jobs are durable. A remote job outlives this process — an ssh job keeps running
under ``nohup``, a byoc sandbox keeps billing — so every job is recorded in
``compute_jobs`` before it is submitted, its provider receipt (remote pid /
sandbox id) is stored on acknowledgement, and every transition appends to a
sequenced ``compute_job_events`` stream. A restart rehydrates whatever was live
and ``reconcile()`` surfaces it; nothing is resubmitted automatically, because
a job in ``submitted`` may or may not be running and guessing wrong costs either
a duplicate charge or a lost result.

Known limit, stated so it is not mistaken for a guarantee: no OS boundary is
applied to the byoc helper — see ``confinement_status``.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from openai4s.compute.safe_archive import UnsafeArchiveError, safe_extract_tar

# Repo-root openai4s_compute_provider (the confined helper package).
_HELPER_MAIN = str(
    Path(__file__).resolve().parent.parent.parent
    / "openai4s_compute_provider"
    / "__main__.py"
)

# Job-wrapper templates (ported alongside this module).
_TMPL_DIR = Path(__file__).resolve().parent / "templates"

# Mirrors OPENAI4S_KERNEL_SANDBOX's vocabulary: auto | enforce | off.
_CONFINEMENT_ENV = "OPENAI4S_COMPUTE_CONFINEMENT"
_VALID_CONFINEMENT = frozenset({"auto", "enforce", "off"})


def _confinement_mode(value: str | None = None) -> str:
    mode = str(value if value is not None else os.environ.get(_CONFINEMENT_ENV, "auto"))
    mode = mode.strip().lower() or "auto"
    if mode not in _VALID_CONFINEMENT:
        raise ComputeError(
            f"{_CONFINEMENT_ENV} must be one of "
            f"{', '.join(sorted(_VALID_CONFINEMENT))}; got {mode!r}",
            "invalid_request",
        )
    return mode


class ComputeError(RuntimeError):
    """Surface as {'error', 'error_kind', ...} on the wire; the SDK turns a
    non-status error into a RuntimeError carrying .error_kind."""

    def __init__(
        self, msg: str, kind: str = "transient", concurrency: dict | None = None
    ):
        super().__init__(msg)
        self.error_kind = kind
        self.concurrency = concurrency


# Ceiling on one direct scp. The job path stages inputs through a manifest and
# harvests through the safe-archive extractor; this compatibility surface has
# neither, so it gets an explicit cap instead of the implicit "whatever fits".
MAX_TRANSFER_BYTES = 512 * 1024 * 1024


def _safe_remote_path(value: str, *, label: str) -> str:
    """A remote path that cannot walk out of where the caller said it was going.

    `scp` happily accepts `../../etc/passwd` and a shell-quoted path is still a
    path — quoting stops word-splitting, not traversal. These are the same
    rejections the archive extractor applies, for the same reason: the string
    came from an agent, and the remote host is not ours to trust.
    """
    text = str(value or "").strip()
    if not text:
        raise ComputeError(f"{label} must not be empty", "invalid_request")
    if "\x00" in text:
        raise ComputeError(f"{label} must not contain a NUL byte", "invalid_request")
    if "\n" in text or "\r" in text:
        raise ComputeError(f"{label} must not contain a newline", "invalid_request")
    if ".." in Path(text).parts:
        raise ComputeError(
            f"{label} must not contain '..' ({text!r})", "invalid_request"
        )
    return text


def _safe_stage_name(value: str, *, label: str) -> str:
    """A staged input lands flat in the archive root, so its name is a name.

    ``work / dst`` is a join, and a join with an absolute path discards the
    left side entirely — ``work / "/etc/cron.d/x"`` is ``/etc/cron.d/x``. A
    relative ``../`` walks out just as effectively. Both write wherever the
    daemon can write, before the archive is ever built, and the caller picking
    the name is an agent.
    """
    text = str(value or "").strip()
    if not text:
        raise ComputeError(f"{label} must not be empty", "invalid_request")
    if "\x00" in text:
        raise ComputeError(f"{label} must not contain a NUL byte", "invalid_request")
    if text in (".", ".."):
        raise ComputeError(f"{label} must name a file ({text!r})", "invalid_request")
    if os.path.isabs(text) or Path(text).name != text:
        raise ComputeError(
            f"{label} must be a bare filename with no directory part ({text!r}); "
            f"staged inputs are placed flat in the job's work directory",
            "invalid_request",
        )
    return text


def _now_ms() -> int:
    return int(time.time() * 1000)


def _open_store(cfg: Any):
    """The Store this manager records jobs in.

    Resolved from cfg rather than injected because the dispatcher builds the
    manager lazily from cfg alone. Returns None rather than raising: a manager
    that cannot reach the database degrades to in-memory bookkeeping, which is
    the old behaviour — worse, but better than refusing to run a job at all.
    """
    try:
        from openai4s.store import get_store

        return get_store(cfg.db_path)
    except Exception:  # noqa: BLE001
        return None


def _discover_providers(skills_dir: Path) -> dict[str, dict]:
    """Map provider id -> {id, dir, provider_py, meta}. A provider is a
    ``remote-compute-<id>`` skill dir with a ``provider.json`` (declaring its
    ``id``) and a ``provider.py`` exporting ``PROVIDER``."""
    out: dict[str, dict] = {}
    if not skills_dir.exists():
        return out
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir() or not child.name.startswith("remote-compute-"):
            continue
        # ssh is a built-in family (no confined helper), not a byoc provider.
        if child.name == "remote-compute-ssh":
            continue
        pj = child / "provider.json"
        pp = child / "provider.py"
        if not (pj.exists() and pp.exists()):
            continue
        try:
            meta = json.loads(pj.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        pid = meta.get("id")
        if not pid:
            continue
        out[str(pid)] = {
            "id": str(pid),
            "dir": child,
            "provider_py": str(pp),
            "meta": meta,
        }
    return out


class ComputeManager:
    """One per session/kernel. Owns provider discovery and durable job
    bookkeeping. Thread-safe for the handful of ops the dispatcher drives.

    There is no background poller: ``result()`` is what probes the remote and
    harvests. A job nobody polls is never harvested, which is why the SDK
    tells the agent to call ``.result()`` again rather than to wait.
    """

    # Hard host-side ceiling on one helper op. The helper's own poll budget
    # bounds its .phase loop, not a wedged provider SDK socket.
    _HELPER_TIMEOUT_S = 300.0

    def __init__(self, cfg: Any, store: Any = None, workspace: Any = None):
        self.cfg = cfg
        # The containment base for the direct scp surface. None falls back to
        # the process cwd, which is right for the CLI and wrong for nothing —
        # the Web path supplies the session workspace explicitly.
        self._workspace = workspace
        self._providers = _discover_providers(Path(cfg.skills_dir))
        self._install_id = self._resolve_install_id()
        self._store = store if store is not None else _open_store(cfg)
        # In-memory view of the durable records. The database is the source of
        # truth; this is a cache so the hot path does not re-read on every poll.
        self._jobs: dict[str, dict] = {}
        # byoc sandbox reuse: provider-id -> sandbox_id (warm container).
        self._sandboxes: dict[str, str] = {}
        self._lock = threading.RLock()
        self._limit: int | None = None
        self._hpc_root = Path(cfg.data_dir) / "hpc"
        self._hpc_root.mkdir(parents=True, exist_ok=True)
        self._rehydrate()
        self._confinement_mode = _confinement_mode()
        # See _confinement_gate: no host-side byoc boundary exists yet, so the
        # helper is never asked to assert one it cannot have.
        self._require_confinement = False

    # --- durability -------------------------------------------------------
    def _rehydrate(self) -> None:
        """Load jobs that may still be live remotely.

        Without this a restart stranded every in-flight job: the ssh process
        kept running under nohup and the byoc sandbox kept billing, while
        `result()` answered "no such job" and `_live_count()` reset to zero —
        so the session would happily oversubscribe a provider that was still
        busy with work it had forgotten.
        """
        if self._store is None:
            return
        try:
            for record in self._store.live_compute_jobs():
                self._jobs[record["job_id"]] = self._from_record(record)
        except Exception:  # noqa: BLE001 - a broken record must not stop startup
            pass

    @staticmethod
    def _from_record(record: dict) -> dict:
        job = {
            "job_id": record["job_id"],
            "provider": record["provider"],
            "status": record.get("status") or "running",
            "outputs": record.get("outputs"),
            "idempotency_key": record.get("idempotency_key"),
            "receipt": record.get("receipt"),
            "recovered": True,
        }
        for key in ("alias", "workdir", "pid", "sandbox_id"):
            if record.get(key):
                job[key] = record[key]
        return job

    def _persist(self, job_id: str, **fields: Any) -> None:
        if self._store is None:
            return
        try:
            self._store.update_compute_job(job_id, **fields)
        except Exception:  # noqa: BLE001 - never fail an op on bookkeeping
            pass

    def _event(self, job_id: str, kind: str, payload: dict | None = None) -> None:
        if self._store is None:
            return
        try:
            self._store.append_compute_job_event(job_id, kind, payload)
        except Exception:  # noqa: BLE001
            pass

    def _fail_submit(
        self, job_id: str, exc: BaseException, *, sandbox_id: str | None = None
    ) -> None:
        """Give a submit that raised an honest terminal state, never `staging`.

        The distinction that matters is not failed-vs-succeeded but *definitely
        nothing happened* vs *something may be running out there*. Only the
        second costs money and needs reconciling, so anything short of an
        explicit provider rejection is recorded as ``unknown``:

        - the host deadline fired and we killed the helper mid-call, so the
          remote op may have landed after we stopped listening;
        - the helper died without writing a reply, same reasoning;
        - a sandbox was already created, which is a live billable resource
          regardless of why the later step failed.

        The sandbox id is persisted here for exactly that last case. Until now
        it lived only in the in-memory ``_sandboxes`` map, so a create that
        succeeded followed by a submit that failed left a sandbox nobody could
        name after a restart.
        """
        kind = getattr(exc, "error_kind", None)
        rejected = (
            isinstance(exc, ComputeError)
            and kind not in (None, "unknown_state")
            and not sandbox_id
        )
        fields: dict[str, Any] = {"reason": str(exc)}
        if sandbox_id:
            fields["sandbox_id"] = sandbox_id
            fields["receipt"] = sandbox_id
        if rejected:
            self._persist(job_id, status="failed", terminal_at=_now_ms(), **fields)
            self._event(job_id, "submit_rejected", {"error": str(exc), "kind": kind})
        else:
            self._persist(job_id, status="unknown", **fields)
            self._event(
                job_id,
                "submit_indeterminate",
                {"error": str(exc), "kind": kind, "sandbox_id": sandbox_id},
            )

    def _claim(self, provider: str, idempotency_key: str | None, outputs: Any) -> str:
        """Reserve a job row *before* the submit is attempted.

        The ordering is the whole point. A row written only after a successful
        submit would be missing for exactly the case that matters: the provider
        accepted the work and the response never came back. Reserving first
        means a crash anywhere in the submit path still leaves something to
        reconcile against, rather than an orphan that bills forever.
        """
        job_id = "job-" + uuid.uuid4().hex[:12]
        if self._store is None:
            return job_id
        if idempotency_key:
            existing = self._store.compute_job_by_idempotency_key(idempotency_key)
            if existing is not None:
                raise ComputeError(
                    f"a job for idempotency key {idempotency_key!r} already "
                    f"exists ({existing['job_id']}, status "
                    f"{existing.get('status')!r}); reconcile it instead of "
                    f"submitting again",
                    "duplicate_request",
                )
        try:
            self._store.create_compute_job(
                job_id=job_id,
                provider=provider,
                status="staging",
                idempotency_key=idempotency_key,
                outputs=outputs,
            )
        except Exception:  # noqa: BLE001 - degrade to in-memory rather than
            # refuse to run; a job we cannot record is still better than none.
            pass
        return job_id

    def reconcile(self, kw: dict | None = None) -> dict:
        """Report jobs that were live when the daemon last stopped.

        Deliberately does NOT resubmit anything. A job in `submitted` may or
        may not be running remotely, and guessing wrong costs either a
        duplicate charge or a lost result. The honest move is to surface each
        one with its receipt and let a poll — or a human — resolve it.
        """
        recovered = [job for job in self._jobs.values() if job.get("recovered")]
        return {
            "recovered": [
                {
                    "job_id": job["job_id"],
                    "provider": job["provider"],
                    "status": job.get("status"),
                    "receipt": job.get("receipt"),
                    "hint": (
                        "poll with .result() to resolve; it may have finished "
                        "while the daemon was down"
                    ),
                }
                for job in recovered
            ],
            "count": len(recovered),
        }

    def job_history(self, kw: dict) -> dict:
        """The append-only event stream for one job."""
        if self._store is None:
            return {"job_id": kw.get("job_id"), "events": []}
        return {
            "job_id": kw["job_id"],
            "events": self._store.compute_job_events(kw["job_id"]),
        }

    def confinement_status(self) -> dict:
        """Machine-readable posture for the UI/status surface.

        Deliberately reports ``enforced: False`` rather than staying silent —
        a user must not read "the helper ran" as "the helper was confined".
        """
        return {
            "mode": self._confinement_mode,
            "enforced": False,
            "state": "unavailable",
            "detail": (
                "byoc helper confinement is not implemented host-side: the "
                "helper is spawned without an OS boundary, so its confinement "
                "probe cannot pass. Remote compute remains a Prototype "
                f"capability. Set {_CONFINEMENT_ENV}=enforce to refuse byoc "
                "ops until a verified boundary exists."
            ),
        }

    def _confinement_gate(self, pid: str) -> None:
        """Fail closed when the caller demanded a boundary we cannot establish.

        The helper ships a confinement probe (`expect_confined`) and an exit
        code for failing it, but nothing on the host ever wraps the helper in
        a sandbox or supplies the probe's netns anchor — so confinement is a
        designed, not a built, boundary. `enforce` therefore refuses the op
        outright. That is the honest answer: passing `expect_confined=1` here
        would only make the helper kill itself with exit 71, and defaulting it
        on would break every byoc user while proving nothing.
        """
        if self._confinement_mode == "enforce":
            raise ComputeError(
                f"byoc provider {pid!r} refused: {_CONFINEMENT_ENV}=enforce "
                f"requires verified helper confinement, which this host cannot "
                f"establish (no OS boundary is applied to the provider helper). "
                f"Fix the deployment or set {_CONFINEMENT_ENV}=auto to accept "
                f"unconfined execution.",
                "confinement_unavailable",
            )

    # --- discovery / capability ------------------------------------------
    def has_any_provider(self) -> bool:
        return bool(self._providers) or self._has_ssh_skill()

    def _has_ssh_skill(self) -> bool:
        """The ssh:* family is enabled by the remote-compute-ssh skill being
        installed (it ships the worked example + gate), not merely by the user
        happening to have an ~/.ssh/config."""
        return (Path(self.cfg.skills_dir) / "remote-compute-ssh").is_dir()

    def provider_caps(self) -> dict:
        return {
            f"byoc:{pid}": p["meta"].get("max_concurrent")
            for pid, p in self._providers.items()
        }

    @staticmethod
    def _resolve_install_id() -> str:
        """A stable per-install id used as the byoc sandbox owner tag. Persist
        it under the data dir so reconcile can find sandboxes across runs."""
        env = os.environ.get("OPENAI4S_INSTALL_ID")
        if env:
            return env
        path = Path.home() / ".openai4s" / "install-id"
        try:
            if path.exists():
                return path.read_text("utf-8").strip()
            path.parent.mkdir(parents=True, exist_ok=True)
            iid = uuid.uuid4().hex
            path.write_text(iid, encoding="utf-8")
            return iid
        except OSError:
            return uuid.uuid4().hex

    # --- provider family routing -----------------------------------------
    def _split(self, provider: str) -> tuple[str, str]:
        fam, _, rest = provider.partition(":")
        if fam not in ("byoc", "ssh") or not rest:
            raise ComputeError(
                f"unknown provider target {provider!r}; expected "
                f"'byoc:<id>' or 'ssh:<alias>'",
                "invalid_request",
            )
        return fam, rest

    def _byoc(self, pid: str) -> dict:
        p = self._providers.get(pid)
        if p is None:
            raise ComputeError(
                f"byoc provider {pid!r} is not configured (no "
                f"skills/remote-compute-{pid}/provider.json found)",
                "not_found",
            )
        return p

    # --- concurrency ------------------------------------------------------
    def _live_count(self) -> int:
        return sum(
            1
            for j in self._jobs.values()
            if j.get("status") in ("submitted", "running", "queued")
        )

    def set_concurrency(self, kw: dict) -> dict:
        with self._lock:
            self._limit = int(kw["max_concurrent"])
        return {"live": self._live_count(), "limit": self._limit}

    def status(self, kw: dict) -> dict:
        return {
            "live": self._live_count(),
            "limit": self._limit,
            "daemon_live": True,
            "provider_caps": self.provider_caps(),
        }

    # --- byoc helper transport -------------------------------------------
    def _run_helper(
        self,
        prov: dict,
        op: str,
        req: dict,
        creds: dict,
        stage: Path,
        expect_confined: bool | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Spawn the confined helper in oneshot mode for one op. The credential
        rides on the helper's stdin (never its environment); req/reply cross
        via the stage dir.

        ``expect_confined`` defaults to the manager's policy rather than to
        False: an op that does not ask for confinement has not established it,
        so leaving the default off silently downgraded every call site.
        """
        if expect_confined is None:
            expect_confined = self._require_confinement
        (stage / "req.json").write_text(
            json.dumps({**req, "stage": str(stage), "install_id": self._install_id}),
            encoding="utf-8",
        )
        argv = [
            sys.executable,
            "-I",
            _HELPER_MAIN,
            "oneshot",
            prov["provider_py"],
            op,
            str(stage),
            "1" if expect_confined else "0",
        ]
        # Scrub inherited secrets from the child env; the helper's own prologue
        # also drops the provider's secret_env_prefixes.
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("NGC_", "NVIDIA_", "HF_"))
        }
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, env=env)
        proc.stdin.write((json.dumps({"op": "auth", **creds}) + "\n").encode("utf-8"))
        proc.stdin.close()
        # A hard host-side deadline. The helper has its own poll budget, but a
        # wedged exec stream (or a provider SDK blocking on a socket) leaves it
        # with none — and a bare wait() would block the dispatcher forever.
        deadline = timeout if timeout is not None else self._HELPER_TIMEOUT_S
        try:
            proc.wait(timeout=deadline)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            raise ComputeError(
                f"provider helper for op {op!r} exceeded the {deadline}s host "
                f"deadline and was killed; the remote operation may or may not "
                f"have taken effect",
                "unknown_state",
            )
        reply_path = stage / "reply.json"
        if not reply_path.exists():
            raise ComputeError(
                f"provider helper for op {op!r} exited (rc={proc.returncode}) "
                f"without a reply",
                "transient",
            )
        reply = json.loads(reply_path.read_text("utf-8"))
        if not reply.get("ok"):
            raise ComputeError(
                reply.get("msg") or "provider op failed",
                reply.get("kind") or "transient",
            )
        return reply

    def _provider_creds(self, prov: dict) -> dict:
        """Collect the provider's declared secret env vars into the auth
        payload the helper reads from stdin. The provider.json's
        ``helperEnv``/``secret_env`` lists which env keys to forward."""
        keys = prov["meta"].get("secret_env") or []
        return {k: os.environ[k] for k in keys if k in os.environ}

    # --- submit -----------------------------------------------------------
    def submit(self, kw: dict) -> dict:
        provider = kw["provider"]
        fam, rest = self._split(provider)
        with self._lock:
            if self._limit is not None and self._live_count() >= self._limit:
                raise ComputeError(
                    "session concurrency limit reached",
                    "session_concurrency_full",
                    {"live": self._live_count(), "limit": self._limit},
                )
        if fam == "ssh":
            return self._submit_ssh(rest, kw)
        return self._submit_byoc(rest, kw)

    def _stage_inputs(
        self, stage: Path, inputs: list | None, command: str, timeout_s: int
    ) -> Path:
        """Build the in.tar.gz the helper untars into /work: the wrapper, the
        run.sh (command), and every staged input flat in the root."""
        work = stage / "work"
        work.mkdir()
        wrapper = (_TMPL_DIR / "wrapper.sh.tmpl").read_text("utf-8")
        run = (
            (_TMPL_DIR / "run.sh.tmpl")
            .read_text("utf-8")
            .replace("{{COMMAND}}", command)
        )
        (work / "_openai4s_wrapper.sh").write_text(wrapper, encoding="utf-8")
        (work / "run.sh").write_text(run, encoding="utf-8")
        for inp in inputs or []:
            src = inp.get("src") or inp.get("remote_path")
            if not src:
                continue
            # A source that does not exist used to be skipped in silence, so
            # the job ran to completion against missing data and reported
            # success. Refusing here is the difference between a failed job
            # and a wrong result nobody questions.
            src_path = self._safe_local_path(src, label="input src", must_exist=True)
            dst = _safe_stage_name(
                inp.get("dst_filename") or Path(src).name, label="input dst_filename"
            )
            shutil.copy2(src_path, work / dst)
        tgz = stage / "in.tar.gz"
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(work, arcname=".")
        return tgz

    def _submit_byoc(self, pid: str, kw: dict) -> dict:
        prov = self._byoc(pid)
        self._confinement_gate(pid)
        creds = self._provider_creds(prov)
        job_id = self._claim(
            f"byoc:{pid}", kw.get("idempotency_key"), kw.get("outputs")
        )
        timeout_s = int(kw.get("timeout_seconds") or 14400)
        sid: str | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="openai4s-byoc-stage-") as td:
                stage = Path(td)
                # 1. create (or reuse) the sandbox.
                sid = self._sandboxes.get(pid) or kw.get("reuse_job_id")
                if not sid or not self._sandboxes.get(pid):
                    spec = (kw.get("provider_params") or {}).get(pid, {})
                    tags = {
                        "openai4s-session": self._install_id,
                        "openai4s-job": job_id,
                    }
                    rep = self._run_helper(
                        prov,
                        "create",
                        {"spec": spec, "tags": tags, "app_name": "openai4s"},
                        creds,
                        stage,
                        expect_confined=False,
                    )
                    sid = rep["sandbox_id"]
                    self._sandboxes[pid] = sid
                # 2. stage inputs then submit.
                self._stage_inputs(stage, kw.get("inputs"), kw["command"], timeout_s)
                self._run_helper(
                    prov,
                    "submit",
                    {"sandbox_id": sid, "timeout": timeout_s},
                    creds,
                    stage,
                )
        except BaseException as exc:
            # Without this the row stays at `staging` forever: no terminal
            # state, no event, and -- worse -- a sandbox the provider may
            # already be billing for, whose id lived only in the in-memory
            # `_sandboxes` map and vanished with the process. The ssh arm has
            # had this discipline since it was written (see `_submit_ssh`);
            # the byoc arm never did.
            self._fail_submit(job_id, exc, sandbox_id=sid)
            raise
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "provider": f"byoc:{pid}",
                "sandbox_id": sid,
                "status": "running",
                "outputs": kw.get("outputs"),
                "creds": bool(creds),
            }
        # The sandbox id is the receipt — it is what reconcile/terminate need to
        # reach this work after a restart, and what stops an orphaned sandbox
        # from billing unnoticed.
        self._persist(
            job_id,
            status="running",
            sandbox_id=sid,
            receipt=sid,
            submitted_at=_now_ms(),
        )
        self._event(job_id, "submitted", {"sandbox_id": sid})
        return {
            "job_id": job_id,
            "status": "running",
            "concurrency": {"live": self._live_count(), "limit": self._limit},
            "egress": prov["meta"].get("egress"),
        }

    # --- ssh --------------------------------------------------------------
    def _submit_ssh(self, alias: str, kw: dict) -> dict:
        job_id = self._claim(
            f"ssh:{alias}", kw.get("idempotency_key"), kw.get("outputs")
        )
        workdir = f"~/.openai4s-jobs/{job_id}"
        script = kw["command"]
        # The job body runs under a wrapper whose only added responsibility is
        # to record the terminal exit code. Without a .rc on disk a finished
        # job is indistinguishable from a killed one, and _result_ssh has no
        # honest state to report — so this write is what makes a *failed* ssh
        # job observable at all. It lands via .rc.tmp + mv so a reader never
        # sees a half-written code (mv is atomic within one filesystem).
        inner = (
            "bash run.sh > stdout.log 2> stderr.log; rc=$?; "
            'printf "%s" "$rc" > .rc.tmp; mv -f .rc.tmp .rc'
        )
        # The braces are load-bearing. `&` binds looser than `&&`, so
        # `mkdir && cd && cat > run.sh && nohup ... & echo $!` makes the WHOLE
        # and-list asynchronous — and POSIX assigns an async list's stdin to
        # /dev/null. `cat` then read nothing, run.sh was written empty, and
        # `bash run.sh` exited 0 without ever running the job. Grouping keeps
        # `&` scoped to the nohup alone, so `cat` stays in the foreground and
        # actually receives the script over the ssh channel.
        remote = (
            f"mkdir -p {workdir} && cd {workdir} && "
            f"cat > run.sh && rm -f .rc .rc.tmp && "
            f"{{ nohup bash -c {shlex.quote(inner)} >/dev/null 2>&1 & echo $!; }}"
        )
        try:
            proc = subprocess.run(
                ["ssh", alias, remote],
                input=script.encode("utf-8"),
                capture_output=True,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            # We do not know whether the remote shell ran. The claim row stays
            # at `staging` with the workdir recorded, which is what makes this
            # reconcilable instead of an orphan.
            self._persist(job_id, status="unknown", workdir=workdir, reason=str(e))
            self._event(job_id, "submit_indeterminate", {"error": str(e)})
            raise ComputeError(f"ssh submit failed: {e}", "unknown_state")
        if proc.returncode != 0:
            self._persist(job_id, status="failed", reason="ssh submit rejected")
            self._event(job_id, "submit_rejected", {"rc": proc.returncode})
            raise ComputeError(
                proc.stderr.decode("utf-8", "replace") or "ssh submit failed",
                "transient",
            )
        pid = proc.stdout.decode().strip()
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "provider": f"ssh:{alias}",
                "alias": alias,
                "workdir": workdir,
                "status": "running",
                "pid": pid,
                "outputs": kw.get("outputs"),
            }
        # The remote pid is the provider's receipt: evidence the job exists out
        # there, independent of anything this process chose to believe.
        self._persist(
            job_id,
            status="running",
            alias=alias,
            workdir=workdir,
            pid=pid,
            receipt=pid,
            submitted_at=_now_ms(),
        )
        self._event(job_id, "submitted", {"pid": pid, "workdir": workdir})
        return {
            "job_id": job_id,
            "status": "running",
            "remote_workdir": workdir,
            "concurrency": {"live": self._live_count(), "limit": self._limit},
        }

    # --- result / harvest -------------------------------------------------
    def result(self, kw: dict) -> dict:
        job_id = kw["job_id"]
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ComputeError(f"no such job {job_id!r}", "not_found")
        fam, rest = self._split(job["provider"])
        if fam == "ssh":
            return self._result_ssh(job)
        return self._result_byoc(job)

    def _result_byoc(self, job: dict) -> dict:
        prov = self._byoc(job["provider"].split(":", 1)[1])
        creds = self._provider_creds(prov)
        with tempfile.TemporaryDirectory(prefix="openai4s-byoc-stage-") as td:
            stage = Path(td)
            rep = self._run_helper(
                prov,
                "wait",
                {"sandbox_id": job["sandbox_id"], "poll_seconds": 5},
                creds,
                stage,
            )
            if not rep.get("ready"):
                return {
                    "status": "running",
                    "job_id": job["job_id"],
                    "hint": "job still running — call .result() again later",
                }
            exit_code = rep.get("job_exit_code")
            phase_err = rep.get("phase_read_error")
            if exit_code is None:
                # The helper reached a ready state but could not read a
                # terminal exit code out of .phase. Previously this fell
                # through to "done" because None is falsy — the single worst
                # false-success in this module.
                #
                # Not cached onto the job, for the same reason as the ssh path:
                # `unknown` is unresolved, not terminal, so a later poll must
                # stay free to resolve it. It also keeps the job inside
                # _live_count() — a job we cannot account for is conservatively
                # still occupying its slot, rather than freeing capacity we
                # have no evidence is free.
                return {
                    "status": "unknown",
                    "job_id": job["job_id"],
                    "exit_code": None,
                    "error_kind": "unknown_state",
                    "reason": phase_err
                    or "provider reported the job ready without a terminal exit code",
                    "stdout_tail": rep.get("stdout_tail", ""),
                    "stderr_tail": rep.get("stderr_tail", ""),
                    "left_on_remote": False,
                    "hint": (
                        "the job's outcome could not be established; treat any "
                        "harvested output as unverified"
                    ),
                }
            try:
                out_files = self._harvest(job["job_id"], stage)
                harvest_error = None
            except UnsafeArchiveError as e:
                out_files = []
                harvest_error = str(e)

        if rep.get("deadline_fired") or rep.get("job_timeout_fired"):
            status = "timed_out"
        elif exit_code == 0:
            status = "done"
        else:
            status = "failed"
        # `harvest_failed:<rc>` means the wrapper's own tar/mv lost the outputs,
        # so a rc==0 job still has nothing verified to show for it.
        if phase_err and status == "done":
            status = "incomplete"
        if harvest_error and status == "done":
            status = "incomplete"
        job["status"] = status
        job["exit_code"] = exit_code
        self._persist(
            job["job_id"],
            status=status,
            exit_code=exit_code,
            terminal_at=_now_ms(),
            reason=phase_err or harvest_error or "",
        )
        self._event(job["job_id"], status, {"exit_code": exit_code})
        result = {
            "status": status,
            "exit_code": exit_code,
            "output_files": out_files,
            "featured_files": out_files,
            "stdout_tail": rep.get("stdout_tail", ""),
            "stderr_tail": rep.get("stderr_tail", ""),
            "job_wall_s": rep.get("job_wall_s"),
            "left_on_remote": False,
        }
        if phase_err:
            result["phase_read_error"] = phase_err
        if harvest_error:
            result["harvest_error"] = harvest_error
            result["error_kind"] = "unsafe_archive"
        return result

    def _harvest(self, job_id: str, stage: Path) -> list[str]:
        """Unpack the remote's out.tar.gz into hpc/<job_id>/.

        Raises UnsafeArchiveError if the archive is hostile; the caller must
        treat that as a failed harvest, never a partial success.
        """
        dest = self._hpc_root / job_id
        dest.mkdir(parents=True, exist_ok=True)
        tgz = stage / "out.tar.gz"
        if not tgz.exists():
            return []
        safe_extract_tar(tgz, dest)
        return [str(p) for p in sorted(dest.rglob("*")) if p.is_file()]

    def _result_ssh(self, job: dict) -> dict:
        alias, workdir = job["alias"], job["workdir"]
        pid = job.get("pid") or ""
        # Probe ordering matters. `kill -0` is asked first because the wrapper
        # writes .rc *before* it exits: a live pid is authoritatively running,
        # and a dead pid means .rc is already durable if it will ever be. The
        # reverse order would race a just-finished job into `unknown`.
        probe = (
            f"if kill -0 {shlex.quote(pid)} 2>/dev/null; then echo RUNNING; "
            f"elif [ -f {workdir}/.rc ]; then cat {workdir}/.rc; "
            f"else echo NORC; fi"
        )
        try:
            check = subprocess.run(
                ["ssh", alias, probe], capture_output=True, timeout=30
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return self._ssh_unknown(job, f"status probe failed to run: {e}")
        if check.returncode != 0:
            # We never reached the host (network, auth, host down). The job's
            # real state is untouched by our inability to observe it, so this
            # is explicitly not a terminal answer.
            return self._ssh_unknown(
                job,
                "status probe exited "
                f"{check.returncode}: "
                f"{check.stderr.decode('utf-8', 'replace').strip() or 'no stderr'}",
            )
        out = check.stdout.decode("utf-8", "replace").strip()
        if out == "RUNNING":
            return {"status": "running", "job_id": job["job_id"]}
        if out == "NORC":
            # The process is gone but left no exit code: OOM-killed, host
            # rebooted, or SIGKILLed. Reporting success here is exactly the
            # false-success this state exists to prevent.
            return self._ssh_unknown(
                job,
                "remote process is no longer alive but wrote no exit code "
                "(killed, evicted, or the host restarted)",
            )
        try:
            exit_code = int(out)
        except ValueError:
            return self._ssh_unknown(job, f"unparseable exit code {out!r}")

        dest = self._hpc_root / job["job_id"]
        dest.mkdir(parents=True, exist_ok=True)
        harvest_error = self._scp_logs(alias, workdir, dest)
        status = "done" if exit_code == 0 else "failed"
        if harvest_error:
            # The job's own verdict is known and trustworthy; only our copy of
            # its logs is incomplete. Keep the verdict, but never claim a clean
            # harvest we did not get.
            status = "failed" if exit_code != 0 else "incomplete"
        job["status"] = status
        job["exit_code"] = exit_code
        self._persist(
            job["job_id"],
            status=status,
            exit_code=exit_code,
            terminal_at=_now_ms(),
            reason=harvest_error or "",
        )
        self._event(job["job_id"], status, {"exit_code": exit_code})
        result = {
            "status": status,
            "exit_code": exit_code,
            "output_files": [str(p) for p in sorted(dest.iterdir())],
            "featured_files": [],
            "remote_workdir": workdir,
            "left_on_remote": True,
        }
        if harvest_error:
            result["harvest_error"] = harvest_error
        return result

    def _ssh_unknown(self, job: dict, reason: str) -> dict:
        """Terminal-shaped answer for a job whose real state we cannot observe.

        `unknown` is deliberately distinct from `failed`: the job may well have
        succeeded. It means *we have no evidence either way*, and the caller
        must reconcile rather than assume. It is never cached onto the job, so
        a later poll can still resolve it.
        """
        return {
            "status": "unknown",
            "job_id": job["job_id"],
            "exit_code": None,
            "error_kind": "unknown_state",
            "reason": reason,
            "remote_workdir": job.get("workdir"),
            "left_on_remote": True,
            "hint": (
                "the remote job's outcome could not be established — inspect "
                f"{job.get('workdir')} on the host before re-submitting, as "
                "the original job may still have run to completion"
            ),
        }

    def _scp_logs(self, alias: str, workdir: str, dest: Path) -> str | None:
        """Copy stdout/stderr back. Returns an error string, or None on success."""
        try:
            proc = subprocess.run(
                [
                    "scp",
                    "-O",
                    "-q",
                    f"{alias}:{workdir}/stdout.log",
                    f"{alias}:{workdir}/stderr.log",
                    str(dest),
                ],
                capture_output=True,
                timeout=120,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return f"log harvest failed to run: {e}"
        if proc.returncode != 0:
            return (
                f"log harvest exited {proc.returncode}: "
                f"{proc.stderr.decode('utf-8', 'replace').strip() or 'no stderr'}"
            )
        return None

    # --- cancel / close / ssh command / scp -------------------------------
    def cancel(self, kw: dict) -> dict:
        with self._lock:
            job = self._jobs.get(kw["job_id"])
        if job is None:
            raise ComputeError(f"no such job {kw['job_id']!r}", "not_found")
        fam, rest = self._split(job["provider"])
        if fam == "ssh":
            try:
                proc = subprocess.run(
                    ["ssh", job["alias"], f"kill -TERM {shlex.quote(job['pid'])}"],
                    capture_output=True,
                    timeout=30,
                )
            except (subprocess.TimeoutExpired, OSError) as e:
                raise ComputeError(
                    f"cancel could not reach {job['alias']}: {e}; the remote "
                    f"job may still be running",
                    "unknown_state",
                )
            if proc.returncode != 0:
                # A kill we could not deliver is not a cancellation. Claiming
                # one leaves the caller believing the allocation is freed.
                raise ComputeError(
                    f"cancel failed on {job['alias']} (exit {proc.returncode}): "
                    f"{proc.stderr.decode('utf-8', 'replace').strip() or 'no stderr'}"
                    f"; the remote job may still be running",
                    "unknown_state",
                )
        else:
            prov = self._byoc(rest)
            with tempfile.TemporaryDirectory(prefix="openai4s-byoc-stage-") as td:
                self._run_helper(
                    prov,
                    "terminate",
                    {"sandbox_id": job["sandbox_id"]},
                    self._provider_creds(prov),
                    Path(td),
                )
        job["status"] = "cancelled"
        self._persist(job["job_id"], status="cancelled", terminal_at=_now_ms())
        self._event(job["job_id"], "cancelled")
        return {"status": "cancelled"}

    def close(self, kw: dict) -> dict:
        provider = kw["provider"]
        fam, rest = self._split(provider)
        terminated = True
        if fam == "byoc":
            sid = self._sandboxes.get(rest)
            if sid:
                prov = self._byoc(rest)
                with tempfile.TemporaryDirectory(prefix="openai4s-byoc-stage-") as td:
                    try:
                        self._run_helper(
                            prov,
                            "terminate",
                            {"sandbox_id": sid},
                            self._provider_creds(prov),
                            Path(td),
                        )
                    except ComputeError:
                        # Drop the id only once the provider has confirmed the
                        # sandbox is gone. Forgetting it on a failed terminate
                        # is how a sandbox bills forever with nothing left in
                        # this process able to name it.
                        terminated = False
                if terminated:
                    self._sandboxes.pop(rest, None)
        for jid in kw.get("job_ids") or []:
            j = self._jobs.get(jid)
            if j and j.get("status") in ("submitted", "running", "queued"):
                j["status"] = "closed"
                # In-memory only meant a restart rehydrated the job as live,
                # so it kept occupying a concurrency slot and kept being
                # reconciled against a provider that had already released it.
                self._persist(jid, status="closed", terminal_at=_now_ms())
                self._event(jid, "closed", {"provider": provider})
        return {"status": "closed", "sandbox_released": terminated}

    def ssh(self, kw: dict) -> dict:
        """One synchronous command (call_command). byoc runs it inside the
        warm sandbox; ssh runs it over the alias."""
        provider = kw["provider"]
        fam, rest = self._split(provider)
        cmd = kw["command"]
        timeout_s = int(kw.get("timeout_seconds") or 60)
        if fam == "ssh":
            shell = ["ssh"]
            if kw.get("login_shell"):
                shell += ["-t"]
            shell += [rest, cmd]
            # Audited before it runs, not after: a command that hangs or kills
            # the daemon must still leave a record that it was attempted.
            self._audit("compute_ssh_command", alias=rest, command=cmd[:2000])
            proc = subprocess.run(shell, capture_output=True, timeout=timeout_s)
            return {
                "stdout": proc.stdout.decode("utf-8", "replace")[:65536],
                "stderr": proc.stderr.decode("utf-8", "replace")[:65536],
                "exit_code": proc.returncode,
            }
        raise ComputeError(
            "call_command on a byoc provider requires a live sandbox; "
            "submit a job instead",
            "invalid_request",
        )

    def scp(self, kw: dict) -> dict:
        """Direct file transfer over an ssh alias.

        A compatibility surface, deliberately kept but no longer looser than the
        job path it sits beside: paths are checked, size is capped, and every
        call is audited. Previously it forwarded an agent-supplied string
        straight to `scp`.
        """
        if self._split(kw["provider"])[0] != "ssh":
            raise ComputeError("download/upload is ssh-only", "invalid_request")
        alias = kw["provider"].split(":", 1)[1]
        remote = _safe_remote_path(kw.get("remote"), label="remote path")

        if kw["direction"] == "down":
            local = self._safe_local_path(
                kw.get("local") or Path(remote).name, label="local path"
            )
            self._audit("compute_scp_download", alias=alias, remote=remote)
            self._run_scp(
                ["scp", "-O", "-q", f"{alias}:{remote}", str(local)],
                f"download {remote!r} from {alias}",
            )
            self._enforce_transfer_cap(local)
            return {"local": str(local)}

        local = self._safe_local_path(kw["local"], label="local path", must_exist=True)
        self._enforce_transfer_cap(local)
        self._audit("compute_scp_upload", alias=alias, remote=remote)
        self._run_scp(
            ["scp", "-O", "-q", str(local), f"{alias}:{remote}"],
            f"upload to {remote!r} on {alias}",
        )
        return {"remote": remote}

    def _safe_local_path(
        self, value: Any, *, label: str, must_exist: bool = False
    ) -> Path:
        """Resolve a local path and require it to stay inside the workspace.

        Without this, `direction="down"` with `local="/etc/cron.d/x"` writes
        wherever the daemon can write — the agent choosing the destination is
        the whole risk. Symlinks are resolved BEFORE the containment check, so a
        link planted inside the workspace cannot redirect the write outside it.
        """
        text = str(value or "").strip()
        if not text:
            raise ComputeError(f"{label} must not be empty", "invalid_request")
        if "\x00" in text:
            raise ComputeError(
                f"{label} must not contain a NUL byte", "invalid_request"
            )
        base = Path(self._workspace or Path.cwd()).resolve()
        candidate = Path(text)
        resolved = base / candidate if not candidate.is_absolute() else candidate
        resolved = resolved.resolve()
        if resolved != base and base not in resolved.parents:
            raise ComputeError(
                f"{label} must stay inside the workspace ({resolved} is outside "
                f"{base})",
                "invalid_request",
            )
        if must_exist and not resolved.is_file():
            raise ComputeError(f"{label} {text!r} is not a file", "invalid_request")
        return resolved

    @staticmethod
    def _enforce_transfer_cap(path: Path) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size > MAX_TRANSFER_BYTES:
            raise ComputeError(
                f"transfer of {size} bytes exceeds the {MAX_TRANSFER_BYTES} byte "
                f"cap for the direct scp surface; stage it through a job instead",
                "invalid_request",
            )

    def _audit(self, event: str, **fields: Any) -> None:
        """Record a direct-surface call. Redaction is the emitter's job."""
        try:
            from openai4s.observability import log_event

            log_event(event, **fields)
        except Exception:  # noqa: BLE001 - auditing must not fail the operation
            pass

    @staticmethod
    def _run_scp(argv: list[str], what: str) -> None:
        """Run one scp, raising on failure.

        Returning a path the transfer never produced is what made a failed copy
        look like a delivered file to the caller.
        """
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise ComputeError(f"{what} timed out after 300s", "transient")
        except OSError as e:
            raise ComputeError(f"{what} could not start: {e}", "transient")
        if proc.returncode != 0:
            raise ComputeError(
                f"{what} failed (scp exited {proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace').strip() or 'no stderr'}",
                "transient",
            )
