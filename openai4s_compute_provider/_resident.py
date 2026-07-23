"""ByocResident — the confined process that hosts a provider.

Two entrypoints share one prologue:
  - run_oneshot() per-op helper (argv + stage/req.json -> stage/reply.json),
    spawned once per op by the host's BYOC transport.
  - run_repl()    long-lived compute-provider kernel (cell-by-cell,
    idle-timeout), spawned by the host's kernel manager.

Secret scrubbing is two-staged: ``__main__`` runs the provider-agnostic
``scrub_secret_env()`` baseline BEFORE it imports provider.py, and the prologue
here re-scrubs with the provider's own declared ``secret_env_prefixes`` before
reading the credential (stdin for oneshot, fd-3 for repl). Together they keep
provider code from reading credential-shaped or known-prefix environment
variables (a name-based heuristic — a secret in an unrecognized variable name
is NOT scrubbed), and the credential itself is never placed in the
environment. run_oneshot self-enforces confinement
(exit 71) before touching stdin; run_repl reports it via {ready, confined} for
the host to gate.
"""
from __future__ import annotations

import ctypes
import json
import os
import resource
import signal
import sys
import threading
import time
import traceback
from typing import Any, NoReturn

from ._channel import ScrubWriter, fmt_bytes, read_auth, write_event, write_ready
from ._constants import (
    BASE_ERROR_KINDS,
    BASELINE_SECRET_PREFIXES,
    CHUNK,
    COMPRESSED_CAP_DEFAULT,
    CRED_KEY_RE,
    EXIT_PROTOCOL,
    EXIT_UNCONFINED,
    IDLE_TIMEOUT_S,
    STAGE_PREFIX,
    TAIL_BYTES,
    TAIL_RING_BYTES,
    WORK,
)
from ._protocol import ByocError, ByocProvider


def scrub_secret_env(extra_prefixes: tuple[str, ...] = ()) -> None:
    """Delete secret-bearing environment variables from ``os.environ`` in place.

    Called TWICE, so provider top-level code cannot read credential-shaped or
    known-prefix environment variables:

      1. From ``__main__`` BEFORE the provider module is imported — with only
         the provider-agnostic baseline (this is what makes "scrub before any
         provider import" literally true, since the provider's own declared
         prefixes are unknowable until its module is loaded).
      2. From ``ByocResident._prologue`` before the credential is read — with
         the loaded provider's ``secret_env_prefixes`` folded in.

    A variable is removed when its NAME matches ``CRED_KEY_RE`` (``*_API_KEY``,
    ``*_TOKEN``, ``*_SECRET`` …) OR starts with a baseline / provider secret
    prefix. This is a name-based heuristic: a secret stored under a name that
    matches neither rule is NOT scrubbed. Everything else survives — e.g.
    ``OPENAI4S_HOST_NETNS_INO`` (the confinement probe's anchor) and
    ``HTTP_PROXY``/``HTTPS_PROXY``. Note ``NVIDIA_VISIBLE_DEVICES`` IS removed
    (the ``NVIDIA_`` prefix catches it, deliberately — the confined helper
    does no GPU work of its own).
    """
    prefixes = BASELINE_SECRET_PREFIXES + tuple(extra_prefixes)
    for k in list(os.environ):
        if k.startswith(prefixes) or CRED_KEY_RE.search(k):
            os.environ.pop(k, None)


class ByocResident:
    def __init__(self, provider: ByocProvider, *, idle_timeout_s: int = IDLE_TIMEOUT_S):
        self._p = provider
        self._idle_s = idle_timeout_s
        self._idle_timer: threading.Timer | None = None
        self._creds: dict[str, str] = {}
        self._scrub: list[str] = []
        self._install_id: str = ""

    # ── lifecycle ────────────────────────────────────────────────────────────

    def run_repl(self) -> NoReturn:
        self._prologue()
        self._handshake()
        # The host's interrupt() sends SIGINT to abort a running cell —
        # restore the default handler so it raises KeyboardInterrupt inside
        # the cell instead of killing the kernel.
        signal.signal(signal.SIGINT, signal.default_int_handler)
        self._p.install_unauth_hook(self._on_auth_expired)
        sys.stdout = ScrubWriter(sys.stdout, self._p.token_scrub_regex)
        sys.stderr = ScrubWriter(sys.stderr, self._p.token_scrub_regex)
        # The worker moves protocol writes off fd 1 to a high-fd wrapper —
        # publish the scrub hook so that wrapper is filtered too. This also
        # covers the stdout_chunk live stream so every protocol byte passes
        # the courtesy filter.
        sys._openai4s_protocol_stdout_wrap = (  # type: ignore[attr-defined]
            lambda s: ScrubWriter(s, self._p.token_scrub_regex)
        )
        self._arm_idle()
        self._serve_repl()

    def run_oneshot(self, argv: list[str]) -> NoReturn:
        signal.signal(signal.SIGINT, lambda *_: os._exit(EXIT_PROTOCOL))
        op, stage, expect_confined = argv[1], argv[2], argv[3] == "1"
        self._prologue()
        if expect_confined and not self._probe_confined():
            os._exit(EXIT_UNCONFINED)
        self._creds = read_auth(fd=0)
        self._scrub = [v for v in self._creds.values() if isinstance(v, str) and v]
        try:
            if op not in self._OPS:
                raise ByocError("invalid_request", f"unknown op {op!r}")
            self._p.apply_auth(self._creds)
            self._p.import_and_patch()
            with open(os.path.join(stage, "req.json"), encoding="utf-8") as f:
                req = json.load(f)
            # Host-configured app name rides every op's req.json beside
            # install_id. Optional provider hook — providers without an app
            # concept simply don't define it.
            set_app = getattr(self._p, "set_app_name", None)
            if callable(set_app):
                set_app(req.get("app_name"))
            # Previously-configured app names — list_owned scans them so an
            # app rename never strands sandboxes.
            set_prior = getattr(self._p, "set_prior_app_names", None)
            if callable(set_prior):
                set_prior(req.get("prior_app_names"))
            # Host-configured provider environment. Absent -> the provider
            # omits the kwarg everywhere so the SDK resolves its own default.
            set_env = getattr(self._p, "set_environment", None)
            if callable(set_env):
                set_env(req.get("environment"))
            reply = getattr(self, f"_op_{op}")(req)
        except ByocError as e:
            sys.stderr.write(self._redact(traceback.format_exc()))
            kind = e.kind if e.kind in BASE_ERROR_KINDS else "transient"
            reply = {"ok": False, "kind": kind, "msg": self._redact(e.msg)}
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(self._redact(traceback.format_exc()))
            reply = {"ok": False, "kind": "transient", "msg": self._redact(repr(e))}
        fd = os.open(
            os.path.join(stage, "reply.json"),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(reply, f)
        os._exit(0)

    # ── prologue (runs before any third-party import) ────────────────────────

    def _prologue(self) -> None:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        # __main__ already ran the baseline scrub before importing the provider
        # module; re-run it here with the provider's own declared prefixes so
        # the credential (read next, from stdin/fd-3) can never coexist with a
        # provider-namespaced secret in the environment.
        scrub_secret_env(self._p.secret_env_prefixes)
        if sys.platform == "linux":
            try:
                ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)
            except Exception:
                pass
        elif sys.platform == "darwin":
            try:
                ctypes.CDLL(None).ptrace(31, 0, 0, 0)
            except Exception:
                pass
        signal.signal(signal.SIGTERM, lambda *_: os._exit(EXIT_PROTOCOL))

    def _probe_confined(self) -> bool:
        if sys.platform == "darwin":
            # The macOS sandbox has no netns; the byoc profile's strongest
            # invariant is `(deny file-read* (subpath $HOME))`. Probe by
            # attempting a $HOME listdir — EPERM <=> sandbox applied.
            try:
                os.listdir(os.path.expanduser("~"))
                return False
            except PermissionError:
                return True
            except Exception:
                return False
        # Linux: the invariant is the *filesystem* one, matching macOS. A
        # network-namespace check was the original design, and a helper whose
        # whole job is calling a provider's REST API cannot live in an empty
        # netns without a host egress proxy in front of it — so it could never
        # pass, and Linux stayed unconfined waiting for a decision. Network
        # isolation is now a separate capability that the host reports on
        # explicitly; this asks only whether the user's home has been replaced.
        #
        # `$HOME` under bwrap's `--tmpfs` is readable and *empty*, not EPERM, so
        # emptiness cannot be the test — an empty home is a legitimate home.
        # The host passes the device id of the real home and a differing one in
        # here means the tmpfs is mounted. Same shape as the netns-inode anchor
        # this replaces: the value has to come from outside, because a confined
        # process cannot obtain it.
        home = os.path.expanduser("~")
        host_dev = os.environ.get("OPENAI4S_HOST_HOME_DEV")
        if host_dev:
            try:
                return os.stat(home).st_dev != int(host_dev)
            except (OSError, ValueError):
                return False
        # No anchor supplied: fall back to the netns comparison a host a
        # release behind may still be establishing.
        try:
            mine = os.stat("/proc/self/ns/net").st_ino
        except OSError:
            return True
        host = os.environ.get("OPENAI4S_HOST_NETNS_INO")
        if host:
            return mine != int(host)
        try:
            return mine != os.stat("/proc/1/ns/net").st_ino
        except OSError:
            return True

    def _handshake(self) -> None:
        write_ready(confined=self._probe_confined())
        self._creds = read_auth()
        self._p.apply_auth(self._creds)
        self._p.import_and_patch()

    # ── repl mode ────────────────────────────────────────────────────────────

    def _arm_idle(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        t = threading.Timer(self._idle_s, self._on_idle)
        t.daemon = True
        t.start()
        self._idle_timer = t

    def _on_idle(self) -> NoReturn:
        write_event("idle_exit")
        os._exit(0)

    def _on_auth_expired(self) -> NoReturn:
        write_event("auth_expired")
        os._exit(0)

    def _serve_repl(self) -> NoReturn:
        # The compute-provider kernel reuses the worker's stdin/stdout JSON
        # cell loop verbatim — this entrypoint only owns prologue + handshake
        # + idle timer. Import and hand off so the cell protocol stays
        # single-sourced. The worker sits at <root>/kernel/worker.py relative
        # to <root>/openai4s_compute_provider/.
        worker = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "openai4s",
            "kernel",
            "worker.py",
        )
        ns: dict[str, Any] = {"__name__": "__main__", "__file__": worker}
        with open(worker, "r", encoding="utf-8") as f:
            code = compile(f.read(), worker, "exec")

        def readline_with_idle() -> str:
            # Idle window is *between* cells (waiting for input), not during
            # one — so a 30-min image build doesn't trip the 15-min timer.
            # Lazy lookup: the worker publishes its private dup as
            # sys._openai4s_protocol_stdin inside main(), after we exec it.
            # Never fall back to a captured sys.stdin reader: a second reader
            # over fd 0 can consume the host's next request.
            self._arm_idle()
            try:
                proto = getattr(sys, "_openai4s_protocol_stdin", None)
                if proto is None:
                    raise OSError(
                        "protocol stdin not published — worker recovery "
                        "will republish"
                    )
                line = proto.readline()
            finally:
                self._idle_timer.cancel()
            return line

        sys._openai4s_protocol_readline = readline_with_idle  # type: ignore[attr-defined]
        exec(code, ns)
        os._exit(0)

    # ── oneshot mode (per-job helper) ────────────────────────────────────────

    _OPS = (
        "create",
        "submit",
        "wait",
        "probe_many",
        "reconcile",
        "terminate",
        "tail",
        "list_dir",
        "list_volumes",
        "read_file",
    )

    def _op_create(self, req: dict[str, Any]) -> dict[str, Any]:
        sid = self._p.create_sandbox(
            req["spec"],
            req["install_id"],
            tags=req.get("tags") or {},
        )
        # Write sid to stage/sandbox_id IMMEDIATELY so the host can terminate
        # it if an abort (Stop -> SIGTERM) lands between here and reply.json
        # being written. SIGTERM -> os._exit (see _prologue), so use
        # low-level os.write (no Python-layer buffering to lose).
        try:
            stage = self._stage(req)
            fd = os.open(
                os.path.join(stage, "sandbox_id"),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            try:
                os.write(fd, sid.encode("utf-8"))
            finally:
                os.close(fd)
        except Exception:
            pass  # best-effort; reply.json is the canonical path
        try:
            owner = self._p.read_owner(sid)
        except Exception:
            self._best_effort_terminate(sid, stage=self._stage(req))
            raise
        if owner != req["install_id"]:
            gone = self._best_effort_terminate(sid, stage=self._stage(req))
            raise ByocError(
                "ownership_mismatch",
                f"created sandbox {sid} but its owner tag read back as "
                f"{owner!r}, not this openai4s install — refusing to "
                f"proceed (sandbox has been "
                + ("terminated" if gone else "left running — terminate it by hand")
                + ").",
            )
        return {"ok": True, "sandbox_id": sid}

    def _best_effort_terminate(self, sid: str, stage: str | None = None) -> bool:
        """Terminate, and report whether it was *confirmed*.

        The host reads ``stage/sandbox_id`` to learn about a sandbox a dying
        helper never got to mention. Clearing that file on a confirmed
        terminate is what keeps the signal honest in both directions: present
        means "may still exist and may still bill", absent means "nothing was
        left behind". Swallowing the terminate failure and clearing anyway
        would recreate the orphan the file exists to prevent.
        """
        try:
            self._p.terminate(sid)
        except Exception:
            return False
        if stage:
            try:
                os.unlink(os.path.join(stage, "sandbox_id"))
            except OSError:
                pass
        return True

    @staticmethod
    def _drain_wait(r) -> int:
        # A process handle's wait() blocks until captured stdout is drained.
        for _ in r.stdout:
            pass
        return r.wait()

    def _op_submit(self, req: dict[str, Any]) -> dict[str, Any]:
        sid = req["sandbox_id"]
        stage = self._stage(req)
        if self._p.read_owner(sid) != req["install_id"]:
            raise ByocError("ownership_mismatch", self._owner_msg(sid))
        in_tgz = os.path.join(stage, "in.tar.gz")
        # Defensive fallback only — the host always sends `timeout`.
        job_timeout = int(req.get("timeout") or 14400)
        # Deadline plumbing for the wrapper's watchdog. All int()-coerced
        # before interpolation; 0 means "absent" and the wrapper falls back
        # to its own defaults (no watchdog without a deadline).
        deadline_epoch = int(req.get("sandbox_deadline_epoch") or 0)
        remaining_s = int(req.get("sandbox_remaining_s") or 0)
        harvest_margin = int(req.get("harvest_margin_s") or 0)
        term_grace = int(req.get("term_grace_s") or 0)
        # In-place-upgrade guard: the wrapper inside in.tar.gz comes from the
        # SUBMITTING host (its build may be old and frozen in memory since
        # boot), while this helper is re-read from disk per spawn. An OLD
        # wrapper has no watchdog and ignores the env — pairing it with the
        # new outer-timeout-free launch line would leave NOTHING enforcing a
        # deadline. Detect vintage by the env knob in the staged wrapper
        # text; unreadable -> assume old, because the legacy outer timeout(1)
        # is tolerable to a NEW wrapper while the new launch form is a silent
        # no-enforcement hole for an OLD one.
        wrapper_new = False
        try:
            import tarfile  # noqa: PLC0415

            with tarfile.open(in_tgz, "r:gz") as tf:
                # Stream members and STOP at the first exact-name match:
                # getmembers() would decompress the ENTIRE archive (multi-GB
                # for the very training workloads this protects) just to
                # enumerate headers. The wrapper is written FIRST and its
                # name is unique, so the first match is the real wrapper.
                for member in tf:
                    name = member.name
                    if name.startswith("./"):
                        name = name[2:]
                    if name != "_openai4s_wrapper.sh":
                        continue
                    fobj = tf.extractfile(member)
                    wrapper_new = (
                        fobj is not None and b"OPENAI4S_JOB_TIMEOUT_S" in fobj.read()
                    )
                    break
        except Exception:
            wrapper_new = False

        def _disarm_keepalive() -> None:
            # Staging or launch failed after the untar wrote run.sh: /work now
            # has run.sh present and .phase absent — exactly the PID-1 idle
            # watchdog's "job running" keepalive predicate, which would disarm
            # the idle self-exit FOREVER on a sandbox no host path will
            # terminate. Best-effort remove run.sh so the watchdog regains
            # authority.
            try:
                c = self._p.exec(sid, ["bash", "-c", f"rm -f {WORK}/run.sh"])
                self._drain_wait(c)
            except Exception:
                pass

        # Exec 1 of 2 — stream + untar the inputs. The upload is SPLIT from
        # the wrapper launch: env values are interpolated into the command
        # text before an exec starts, so a combined exec would freeze the
        # remaining-seconds anchor at its pre-upload host snapshot. Uploading
        # first lets the anchor be recomputed fresh below.
        try:
            with open(in_tgz, "rb") as f:
                r = self._p.exec(
                    sid,
                    [
                        "bash",
                        "-c",
                        f"rm -rf {WORK} && mkdir {WORK} && cd {WORK} && tar -xzf -",
                    ],
                    stdin=iter(lambda: f.read(CHUNK), b""),
                )
            rc = self._drain_wait(r)
        except Exception:
            _disarm_keepalive()
            raise
        finally:
            try:
                os.unlink(in_tgz)
            except OSError:
                pass
        if rc != 0:
            _disarm_keepalive()
            raise ByocError(
                "invalid_request",
                f"untar to {WORK} failed (rc={rc}; rc=127 -> image missing "
                f"tar/bash, rc=2 -> corrupt input archive, rc=1 -> {WORK} not "
                f"writable)",
            )
        # POST-upload recompute of the relative watchdog anchor: fresh
        # remaining life from the absolute deadline on this clock. The wrapper
        # re-anchors it on the SANDBOX clock within the launch exec's dispatch
        # latency, so the derived deadline tracks the true wall no matter how
        # long staging really took. max(1,·): a deadline already past arms the
        # watchdog immediately — staging-margin enforcement, not an error.
        if remaining_s > 0 and deadline_epoch > 0:
            remaining_s = max(1, deadline_epoch - int(time.time()))
        wrapper_env = f"OPENAI4S_JOB_TIMEOUT_S={job_timeout}"
        # RELATIVE remaining-seconds is the authority (the wrapper anchors it
        # on the SANDBOX clock — a host-clock epoch comparison would shrink
        # the staging margin by any clock skew); the epoch rides along as the
        # wrapper's min() cross-check and as the fallback for wrapper skew.
        if remaining_s > 0:
            wrapper_env += f" OPENAI4S_SANDBOX_REMAINING_S={remaining_s}"
        if deadline_epoch > 0:
            wrapper_env += f" OPENAI4S_SANDBOX_DEADLINE_EPOCH={deadline_epoch}"
        if harvest_margin > 0:
            wrapper_env += f" OPENAI4S_HARVEST_MARGIN_S={harvest_margin}"
        if term_grace > 0:
            wrapper_env += f" OPENAI4S_TERM_GRACE_S={term_grace}"
        launch = (
            f"setsid env {wrapper_env} "
            if wrapper_new
            else f"setsid env {wrapper_env} " f"timeout --kill-after=30 {job_timeout} "
        )
        # Exec 2 of 2 — detach the wrapper. setsid + fd redirects let it
        # survive this exec stream closing when the helper exits; the outer
        # shell returns as soon as it backgrounds. The deadline is
        # wrapper-owned: timeout(1) lives INSIDE the wrapper wrapping
        # `bash run.sh` only, so a job/deadline kill can never take the
        # tar->.phase staging down with it.
        try:
            r2 = self._p.exec(
                sid,
                [
                    "bash",
                    "-c",
                    f"cd {WORK} && {{ {launch}"
                    "bash _openai4s_wrapper.sh </dev/null >/dev/null 2>&1 & }",
                ],
            )
            rc2 = self._drain_wait(r2)
        except Exception:
            _disarm_keepalive()
            raise
        if rc2 != 0:
            # Practically only a cd/parse failure can land here ({ cmd & }
            # forks before it can fail) — definitive, so no resubmit hint.
            _disarm_keepalive()
            raise ByocError(
                "invalid_request",
                f"wrapper launch failed (rc={rc2}; inputs were staged to "
                f"{WORK} but the job did not start)",
            )
        # Additive diagnostics field (old hosts ignore it): which launch form
        # ran — selftests pin the vintage-detection arms on it.
        return {"ok": True, "wrapper_deadline_aware": wrapper_new}

    def _probe_one(
        self, sid: str, *, poll_s: int, flags: bool = True
    ) -> dict[str, Any]:
        """Probe-only core shared by `_op_wait(probe_only=True)` and
        `_op_probe_many`. Ownership check + bounded `.phase` poll + tails;
        never streams `out.tar.gz`. Raises ByocError for ownership/not_found —
        callers map that into the per-slot error shape."""
        if self._p.read_owner(sid) != self._install_id:
            raise ByocError("ownership_mismatch", self._owner_msg(sid))
        # Bounded poll: the wrapper writes .phase last (after out.tar.gz mv),
        # so its presence means the harvest is complete and atomic.
        probe = self._p.exec(
            sid,
            [
                "bash",
                "-c",
                f"for i in $(seq {max(1, poll_s // 2)}); do "
                f"[ -f {WORK}/.phase ] && exit 0; sleep 2; done; exit 2",
            ],
        )
        if self._drain_wait(probe) != 0:
            return {"ok": True, "ready": False}
        # Forwarded user creds (HF_TOKEN etc.) live only in $WORK/.job_env on
        # the sandbox; pull them into the scrub set so _tails() redacts them.
        # Source .job_env in a fresh shell and emit env -0 — NUL-delimited
        # K=V handles multiline values that line-wise parsing cannot. Only
        # credential-shaped keys are scrubbed so benign agent job_env survives.
        try:
            envcat = self._p.exec(
                sid,
                ["bash", "-c", f"set -a; source {WORK}/.job_env 2>/dev/null; env -0"],
            )
            envdump = b"".join(envcat.stdout)
            envcat.wait()
            for entry in envdump.split(b"\0"):
                k, eq, v = entry.partition(b"=")
                if not eq:
                    continue
                ks = k.decode("utf-8", "replace")
                if not CRED_KEY_RE.search(ks):
                    continue
                vs = v.decode("utf-8", "replace")
                if len(vs) >= 8:
                    self._scrub.append(vs)
        except Exception:
            pass
        # Read .phase first (poll already confirmed it exists) so
        # job_rc / job_wall_s survive a cap-exceeded break or stream error.
        job_rc: int | None = None
        job_wall_s: int | None = None
        phase_err: str | None = None
        # EXEC failures are retried once and then surfaced as a RETRIABLE
        # probe error: the poll just proved .phase exists, which proves
        # out.tar.gz was atomically staged — a blip on this one cat must
        # re-probe next tick, never flow into a terminal null-rc
        # classification whose skip-harvest arm would destroy the staged
        # outputs. PARSE failures keep the structured phase_read_error path.
        phase: str | None = None
        cat_err: Exception | None = None
        for _ in range(2):
            try:
                phasecat = self._p.exec(sid, ["cat", f"{WORK}/.phase"])
                phase = b"".join(phasecat.stdout).decode("ascii", "replace").strip()
                phasecat.wait()
                cat_err = None
                break
            except Exception as e:
                cat_err = e
        if cat_err is not None:
            return {
                "ok": False,
                "kind": "transient",
                "msg": self._redact(f".phase read failed twice: {cat_err!r}"),
            }
        assert phase is not None
        tag, _, rest = phase.partition(":")
        parts = rest.split(":")
        if tag in ("done", "harvest_failed"):
            try:
                job_rc = int(parts[0])
                if len(parts) > 1:
                    job_wall_s = int(parts[1])
            except ValueError:
                phase_err = self._redact(f"unparseable .phase fields: {phase!r}")
            if job_rc is not None and tag == "harvest_failed":
                phase_err = self._redact(
                    "tar/mv failed in wrapper (likely disk-full or "
                    f"read-only /work); job rc was {parts[0]}"
                )
        else:
            phase_err = self._redact(f"unrecognized .phase content: {phase!r}")
        # Deadline sentinels — additive fields; old hosts drop them at their
        # probe-reply allowlist. deadline_fired: the container-deadline
        # watchdog TERMed the workload (host classifies rc 143/137 + sentinel
        # as timed_out). job_timeout_fired: the per-job timeout(1) escalated
        # to KILL on a TERM-resistant workload. One exec for both.
        #
        # Staleness gate: a sentinel is only honored when it is NOT newer than
        # .phase. The wrapper scrubs forgeries and writes .phase LAST, so a
        # legitimate sentinel always predates the marker — anything re-created
        # afterwards reads as newer and is ignored.
        deadline_fired = False
        job_timeout_fired = False
        # Classification is one-shot at the terminal probe, so a swallowed
        # exec blip here would silently read as deadline_fired=false —
        # committing a genuine watchdog kill as `failed` and skipping
        # terminate-after-harvest. Retry once, then surface as a RETRIABLE
        # probe error so the poller re-probes next tick.
        flags_err: Exception | None = None
        # Harvest path skips this exec entirely (flags=False): classification
        # was already committed by the earlier status() probe, the sentinel
        # fields in a harvest head are never consumed, and a transient
        # double-failure here would hard-fail the whole harvest op.
        for _ in range(2 if flags else 0):
            try:
                flags = self._p.exec(
                    sid,
                    [
                        "bash",
                        "-c",
                        f"[ -f {WORK}/.deadline_fired ] && "
                        f"[ ! {WORK}/.deadline_fired -nt {WORK}/.phase ] && "
                        "printf D; "
                        f"[ -f {WORK}/.job_timeout_fired ] && "
                        f"[ ! {WORK}/.job_timeout_fired -nt {WORK}/.phase ] && "
                        "printf J; "
                        "true",
                    ],
                )
                flag_bytes = b"".join(flags.stdout)
                flags.wait()
                deadline_fired = b"D" in flag_bytes
                job_timeout_fired = b"J" in flag_bytes
                flags_err = None
                break
            except Exception as e:
                flags_err = e
        if flags and flags_err is not None:
            return {
                "ok": False,
                "kind": "transient",
                "msg": self._redact(
                    f"deadline-sentinel read failed twice: {flags_err!r}"
                ),
            }
        tails = self._tails(sid)
        out: dict[str, Any] = {
            "ok": True,
            "ready": True,
            "job_exit_code": job_rc,
            "job_wall_s": job_wall_s,
            "deadline_fired": deadline_fired,
            "job_timeout_fired": job_timeout_fired,
            **tails,
        }
        if phase_err:
            out["phase_read_error"] = phase_err
        return out

    def _op_wait(self, req: dict[str, Any]) -> dict[str, Any]:
        sid = req["sandbox_id"]
        stage = self._stage(req)
        self._install_id = req["install_id"]
        poll_s = int(req.get("poll_seconds") or 30)
        head = self._probe_one(sid, poll_s=poll_s, flags=bool(req.get("probe_only")))
        # probe_only: caller wants status (job_exit_code/tails/wall_s) without
        # streaming out.tar.gz — used by the host's status() so the poller can
        # cheaply detect terminal state and only stream the tarball once, in
        # the separate harvest() call.
        if req.get("probe_only") or not head.get("ready"):
            return head
        job_rc = head.get("job_exit_code")
        job_wall_s = head.get("job_wall_s")
        phase_err = head.get("phase_read_error")
        tails = {k: head[k] for k in ("stdout_tail", "stderr_tail")}
        cap = int(req.get("output_cap_bytes") or COMPRESSED_CAP_DEFAULT)
        written = 0
        rc = -1
        stream_err: tuple[str, str] | None = None
        try:
            r = self._p.exec(sid, ["cat", f"{WORK}/out.tar.gz"])
            with open(os.path.join(stage, "out.tar.gz"), "wb") as out:
                for chunk in r.stdout:
                    out.write(chunk)
                    written += len(chunk)
                    if written > cap:
                        stream_err = (
                            "result_rejected",
                            f"compressed harvest exceeds {fmt_bytes(cap)} cap",
                        )
                        break
            # wait() blocks until stdout is drained — discard the rest so the
            # cap path doesn't deadlock the helper. Guard the drain so a
            # stream-reset here doesn't clobber a cap-hit diagnosis.
            try:
                for _ in r.stdout:
                    pass
                rc = r.wait()
            except Exception:
                pass
        except Exception as e:
            if stream_err is None:
                stream_err = (
                    "transient",
                    self._redact(f"harvest stream failed: {e!r}"),
                )
        if stream_err:
            kind, msg = stream_err
            return {
                "ok": False,
                "kind": kind,
                "msg": msg,
                "job_exit_code": job_rc,
                "job_wall_s": job_wall_s,
                "bytes_written": written,
                **tails,
            }
        out: dict[str, Any] = {
            "ok": True,
            "ready": True,
            "exit_code": rc,
            "job_exit_code": job_rc,
            "job_wall_s": job_wall_s,
            "bytes_written": written,
            **tails,
        }
        if phase_err:
            out["phase_read_error"] = phase_err
        return out

    def _op_probe_many(self, req: dict[str, Any]) -> dict[str, Any]:
        """Batched probe-only for the host's statusBatch(): probe N sandboxes
        in one helper round-trip so the poller's tick spawns one confined
        subprocess per provider, not one per sandbox. Per-slot errors are
        caught and returned as `{ok:False, kind, msg}` so one bad sandbox
        doesn't kill the batch."""
        self._install_id = req["install_id"]
        sids = req.get("sandbox_ids")
        if not isinstance(sids, list):
            raise ByocError("invalid_request", "sandbox_ids must be a list")
        # poll_s=2 keeps the per-sandbox latency the same as the single-probe
        # path. The batch is sequential — provider .exec() implementations
        # open one channel per sandbox, and N parallel channels under one
        # helper would just push the spawn cost into the SDK's concurrency cap.
        results: list[dict[str, Any]] = []
        for sid in sids:
            if not isinstance(sid, str):
                results.append(
                    {
                        "sandbox_id": str(sid),
                        "ok": False,
                        "kind": "invalid_request",
                        "msg": "sandbox_id must be a string",
                    }
                )
                continue
            try:
                r = self._probe_one(sid, poll_s=2)
            except ByocError as e:
                kind = e.kind if e.kind in BASE_ERROR_KINDS else "transient"
                r = {"ok": False, "kind": kind, "msg": self._redact(e.msg)}
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "kind": "transient", "msg": self._redact(repr(e))}
            results.append({"sandbox_id": sid, **r})
        return {"ok": True, "results": results}

    def _op_reconcile(self, req: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "sandboxes": self._p.list_owned(req["install_id"])}

    def _op_list_dir(self, req: dict[str, Any]) -> dict[str, Any]:
        fn = getattr(self._p, "list_dir", None)
        if not callable(fn):
            raise ByocError(
                "invalid_request",
                "this byoc provider has no persistent store to browse",
            )
        limit = req.get("limit")
        entries = fn(
            str(req["root"]),
            str(req.get("path") or "/"),
            limit=int(limit) if limit is not None else None,
        )
        return {"ok": True, "entries": entries}

    def _op_list_volumes(self, req: dict[str, Any]) -> dict[str, Any]:
        fn = getattr(self._p, "list_volumes", None)
        if not callable(fn):
            raise ByocError(
                "invalid_request",
                "this byoc provider has no persistent store to browse",
            )
        return {"ok": True, "volumes": fn()}

    def _op_read_file(self, req: dict[str, Any]) -> dict[str, Any]:
        """Stream a file from the provider's persistent store to
        ``stage/out.bin`` so the host can import/download it. The host passes
        ``cap_bytes`` (the browser-download or import limit); the stream is cut
        off there with ``result_rejected`` so a misclick on a multi-GB blob
        doesn't fill the daemon's tmp."""
        fn = getattr(self._p, "read_file", None)
        if not callable(fn):
            raise ByocError(
                "invalid_request",
                "this byoc provider has no persistent store to read from",
            )
        stage = self._stage(req)
        cap = int(req.get("cap_bytes") or COMPRESSED_CAP_DEFAULT)
        written = 0
        fd = os.open(
            os.path.join(stage, "out.bin"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        try:
            for chunk in fn(str(req["root"]), str(req.get("path") or "/")):
                if not isinstance(chunk, (bytes, bytearray)):
                    chunk = bytes(chunk)
                os.write(fd, chunk)
                written += len(chunk)
                if written > cap:
                    raise ByocError(
                        "result_rejected",
                        f"file exceeds the {fmt_bytes(cap)} transfer cap",
                    )
        finally:
            os.close(fd)
        return {"ok": True, "size": written}

    def _op_tail(self, req: dict[str, Any]) -> dict[str, Any]:
        """Follow ``/work/{stdout,stderr}.log`` in the sandbox and stream each
        line to this process's own stdout as newline-delimited JSON
        ``{"s":"out"|"err","c":text}`` — one record per log line, with
        ``_redact()`` applied before it leaves the confined helper.

        This op is unlike every other in ``_OPS``: it does not return under
        normal operation. The host reads our stdout line-by-line for as long
        as a viewer is subscribed and SIGKILLs the helper when the last viewer
        disconnects, so ``run_oneshot`` never reaches its ``reply.json`` write.
        We only fall through and return ``{"ok": True}`` when both follows have
        ended on their own — which in practice means the sandbox is gone.
        """
        sid = req["sandbox_id"]
        if self._p.read_owner(sid) != req["install_id"]:
            raise ByocError("ownership_mismatch", self._owner_msg(sid))
        lock = threading.Lock()
        out = os.fdopen(sys.stdout.fileno(), "w", buffering=1, encoding="utf-8")

        def emit(tag: str, line: str) -> None:
            rec = json.dumps({"s": tag, "c": self._redact(line)}, separators=(",", ":"))
            with lock:
                out.write(rec + "\n")

        def follow(tag: str, log_path: str) -> None:
            try:
                r = self._p.exec(
                    sid, ["tail", "-c", str(TAIL_RING_BYTES), "-F", log_path]
                )
                buf = b""
                for chunk in r.stdout:
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    for ln in lines:
                        emit(tag, ln.decode("utf-8", "replace"))
            except Exception as e:  # noqa: BLE001 — teardown surfaces here
                emit(tag, f"[tail ended: {self._redact(repr(e))}]")

        threads = [
            threading.Thread(
                target=follow, args=("out", f"{WORK}/stdout.log"), daemon=True
            ),
            threading.Thread(
                target=follow, args=("err", f"{WORK}/stderr.log"), daemon=True
            ),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return {"ok": True}

    def _op_terminate(self, req: dict[str, Any]) -> dict[str, Any]:
        sid = req["sandbox_id"]
        if self._p.read_owner(sid) != req["install_id"]:
            raise ByocError("ownership_mismatch", self._owner_msg(sid))
        self._p.terminate(sid)
        return {"ok": True}

    @staticmethod
    def _stage(req: dict[str, Any]) -> str:
        # Host-supplied via stage/req.json and the sandbox confines writes
        # anyway, but reject anything outside the expected mkdtemp prefix so a
        # protocol bug can't be levered into an arbitrary-path open. realpath
        # both sides — on macOS /tmp is a symlink to /private/tmp.
        stage = req.get("stage")
        if not isinstance(stage, str):
            raise ByocError("invalid_request", "bad stage path")
        real = os.path.realpath(stage)
        prefix = os.path.join(
            os.path.realpath(os.path.dirname(STAGE_PREFIX)),
            os.path.basename(STAGE_PREFIX),
        )
        if not (real.startswith(prefix) and os.path.isdir(real)):
            raise ByocError("invalid_request", "bad stage path")
        return real

    @staticmethod
    def _owner_msg(sid: str) -> str:
        return (
            f"sandbox {sid} is not tagged for this openai4s install — "
            f"refusing to touch it. Either it was created outside openai4s "
            f"or by another machine; compute.create() will return a fresh one."
        )

    def _tails(self, sid: str) -> dict[str, str]:
        sep = b"\0---SEP---\0"
        try:
            r = self._p.exec(
                sid,
                [
                    "bash",
                    "-c",
                    f"tail -c {TAIL_BYTES} {WORK}/stdout.log 2>/dev/null;"
                    f" printf '\\0---SEP---\\0';"
                    f" tail -c {TAIL_BYTES} {WORK}/stderr.log 2>/dev/null",
                ],
            )
            buf = b"".join(r.stdout)
            r.wait()
        except Exception:
            return {"stdout_tail": "", "stderr_tail": ""}
        out, _, err = buf.partition(sep)
        return {
            "stdout_tail": self._redact(out.decode("utf-8", "replace")),
            "stderr_tail": self._redact(err.decode("utf-8", "replace")),
        }

    def _redact(self, s: str) -> str:
        s = self._p.token_scrub_regex.sub("***", s)
        for v in self._scrub:
            s = s.replace(v, "***")
        return "".join(c if c.isprintable() or c in "\n\t\r" else "?" for c in s)[
            :TAIL_BYTES
        ]
