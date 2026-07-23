"""Worker-side ``host.compute`` namespace and remote-job handles."""

from __future__ import annotations

from typing import Any, Callable

# Imported rather than restated: a second copy of the vocabulary is how the
# host and this namespace came to disagree about which states are final.
# ``states`` is pure constants with no dependencies, so it is safe to reach
# for from inside the kernel worker.
from openai4s.compute.states import TERMINAL_STATES as _TERMINAL_STATUSES


class SessionConcurrencyFull(RuntimeError):
    """Raised by submit_job(on_full="raise") when this session's
    set_concurrency_limit is reached. Carries .live and .limit."""

    def __init__(self, live: Any, limit: Any):
        self.live = live
        self.limit = limit
        super().__init__(f"session concurrency limit reached (live={live}/{limit})")


class _ComputeJob:
    """One submitted job. Returned by ComputeInstance.submit_job(); recover
    later via host.compute.create(provider).attach_job(job_id)."""

    def __init__(
        self,
        call: Callable[[str, list], Any],
        provider: str,
        job_id: str,
        workdir: str | None = None,
    ):
        self._call = call
        self._provider = provider
        self._job_id = job_id
        self._workdir = workdir
        self.result_dict: dict | None = None
        self.output_files: list = []
        self.featured_files: list = []
        # The host-authored egress posture of a byoc sandbox at creation.
        self.egress = None

    def __repr__(self) -> str:
        eg = f" egress={self.egress!r}" if self.egress else ""
        return (
            f"<host.compute.Job {self._provider}/{self._job_id} "
            f"state={self.status}{eg} — recover with "
            f"host.compute.create({self._provider!r})"
            f".attach_job({self._job_id!r})>"
        )

    def _compute_call(self, op: str, kw: dict) -> Any:
        return _compute_call(self._call, op, kw)

    @property
    def job_id(self) -> str:
        return self._job_id

    id = job_id

    @property
    def exit_code(self):
        """Return the terminal exit code, warning once while unavailable."""
        result = self.result_dict or {}
        exit_code = result.get("exit_code")
        if exit_code is None and result.get("status") in (
            None,
            "submitted",
            "running",
            "queued",
            "harvesting",
            "pending",
        ):
            if not getattr(self, "_exit_code_warned", False):
                self._exit_code_warned = True
                import sys

                print(
                    "[host.compute] .exit_code is None until the job is "
                    "terminal — call .result() again from a later cell",
                    file=sys.stderr,
                )
        return exit_code

    @property
    def status(self) -> str:
        return (self.result_dict or {}).get("status", "submitted")

    @property
    def workdir(self):
        """Absolute job workdir on the remote host (under scratch). Populated
        from the submit/result() response; None for an attached job until
        result() is called."""
        return self._workdir

    def result(self) -> dict:
        """Poll the job once, for all provider families (``ssh:*``, ``byoc:*``).

        **This call is what drives the job forward.** It probes the remote and,
        once the work is terminal, harvests the outputs into ``hpc/<job_id>/``.
        Nothing happens in the background: there is no daemon poller and no
        notification. To wait for a long job, call ``.result()`` again from a
        later cell until ``status`` is terminal.

        While the job is still running this returns ``{'status': 'running',
        ...}``. Once terminal it returns ``{status, exit_code, output_files,
        featured_files, left_on_remote, left_on_remote_files?, remote_workdir,
        stdout_tail?,
        stderr_tail?, job_wall_s?, ...}``. Does NOT raise on
        ``status='failed'`` or ``status='timed_out'`` — read ``exit_code`` /
        ``error_kind``.
        """
        # Cache only a genuinely terminal result. This used to test against
        # "running", "timeout" and "harvesting" — two of which the host has
        # never produced, so the check was narrower than it looked and any
        # unrecognised live state was cached as final.
        if self.result_dict and self.result_dict.get("status") in _TERMINAL_STATUSES:
            return self.result_dict
        result = self._compute_call(
            "result", {"job_id": self._job_id, "provider": self._provider}
        )
        self.result_dict = result
        self.output_files = result.get("output_files", [])
        self.featured_files = result.get("featured_files", [])
        if not self._workdir:
            self._workdir = result.get("remote_workdir")
        if self.egress is None and result.get("egress"):
            self.egress = result["egress"]
        hint = result.get("egress_hint")
        if hint and not getattr(self, "_egress_hint_printed", False):
            self._egress_hint_printed = True
            print(hint)
        return result

    def cancel(self) -> Any:
        """Terminate the running job (scheduler cancel / process-group
        SIGTERM / sandbox terminate). This is the ONLY thing that kills a job
        — the user's Stop button, a kernel crash, or exiting the ``repl`` cell
        never auto-cancel; the remote work keeps running and keeps billing
        until you poll it terminal or cancel it. Call this (or ``c.close()``)
        when you actually want to stop the remote work and free the
        allocation."""
        return self._compute_call(
            "cancel", {"job_id": self._job_id, "provider": self._provider}
        )


class _ComputeInstance:
    """Dispatch to one provider. Bind once with create(); each method's
    approval modal still gates the actual remote exec. For byoc:* providers,
    pass provider_params to create() — e.g.
    create('byoc:nvidia', provider_params={'nvidia': {'model': 'boltz2',
    'gpu': 'A100'}}). ``timeout`` (container lifetime, seconds) is optional;
    omitted, it fills from the provider's Settings default."""

    def __init__(
        self,
        call: Callable[[str, list], Any],
        provider: str,
        provider_params: dict | None = None,
    ):
        self._call = call
        self._provider = provider
        self._provider_params = _normalize_provider_params(provider, provider_params)
        self._jobs: list[_ComputeJob] = []
        self._reuse_via: str | None = None

    def __enter__(self) -> "_ComputeInstance":
        return self

    def __exit__(self, *_) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        provider_params = self._provider_params or {}
        provider_id = self._provider.split(":")[-1]
        bits = []
        for key in ("image", "gpu", "model"):
            value = (provider_params.get(provider_id) or {}).get(key)
            if value:
                bits.append(f"{key}={value!r}")
        extra = (" " + " ".join(bits)) if bits else ""
        return f"<host.compute {self._provider}{extra}>"

    def _compute_call(self, op: str, kw: dict) -> Any:
        return _compute_call(self._call, op, kw)

    def submit_job(
        self,
        *,
        command,
        intent,
        inputs=None,
        outputs=None,
        environment=None,
        harvest=None,
        timeout_seconds=None,
        scheduler=None,
        tier=None,
        credentials=None,
        on_full="wait",
        timeout=None,
        timeout_s=None,
        env=None,
        idempotency_key=None,
    ):
        """Stage inputs, write the job script, dispatch. Approval-gated.
        Returns immediately after dispatch. When a session
        ``set_concurrency_limit`` is in effect and the cap is full, the host
        refuses with ``error_kind="session_concurrency_full"`` and this call's
        ``on_full`` decides what happens: ``"wait"`` (default) retries with
        jittered exponential backoff until a slot opens; ``"raise"`` raises
        ``SessionConcurrencyFull(live, limit)`` immediately.

        Print the returned Job — its repr is the recovery handle — then end
        the cell. From a later cell, call ``.result()`` to poll; that call is
        what probes the remote and harvests the outputs. Nothing runs in the
        background on the host's behalf, so a job you never poll is never
        harvested.

        For byoc providers, the FIRST submit_job() on a handle creates the
        container; subsequent calls reuse it (weights/caches stay warm).
        Sequential only — submit job N+1 only after ``.result()`` reports job
        N terminal, since each submit wipes /work.

          intent — REQUIRED one-line approval-modal headline; name tool,
            target, scale, e.g. ``intent='run boltz2 on 3 seqs (A100, ~8min)'``.
          command — job script. Scheduler directives at the top; cwd is a
            fresh per-job workdir; address inputs as './<dst_filename>'.
          inputs — list of {src, dst_filename} (workspace-relative path or
            {{artifact:ID}}, staged from this machine), {version_id,
            dst_filename} (artifact by version id, staged), or {remote_path,
            dst_filename} (absolute path already on the host, symlinked). A
            bare string entry ``'path/to/file'`` is coerced to
            ``{'src': 'path/to/file'}``. dst_filename is a bare filename (no
            '/'); inputs stage flat into the workdir root.
          outputs — glob list. Bare string = featured; {glob, visibility:
            'hidden'} = diagnostic; {glob, residency:'remote'} = leave on host
            (SSH providers only).
          harvest — {exclude:['work/**'], max_file_mb, max_total_mb}.
          timeout_seconds — optional per-job runaway guard (aliases:
            ``timeout=``, ``timeout_s=``).
          environment — remote env name (alias: ``env=``).
          credentials — list of credential NAMES (e.g. ['HF_TOKEN']) to
            forward from the host's credential store into the remote job's
            env. Host-resolved; the agent never sees the values. byoc only.
          on_full — "wait" (default) | "raise".
          idempotency_key — a caller-chosen id for this logical piece of work.
            A second submit under the same key is refused with
            ``error_kind="duplicate_request"`` naming the original job,
            including after a daemon restart. Pass one whenever a retry is
            possible: without it there is no basis to tell a retry from a
            genuinely new job, and the retry becomes a second remote run.
        """
        import itertools as _itertools
        import random as _random
        import time as _time

        if timeout_seconds is None:
            timeout_seconds = timeout if timeout is not None else timeout_s
        if environment is None:
            environment = env
        if inputs:
            if isinstance(inputs, (str, dict)):
                inputs = [inputs]
            normalized = []
            for item in inputs:
                if isinstance(item, str):
                    item = {"src": item}
                if isinstance(item, dict) and "src" in item:
                    item = {**item, "src": _relativize_local(item["src"])}
                normalized.append(item)
            inputs = normalized
        for attempt in _itertools.count():
            try:
                result = self._compute_call(
                    "submit",
                    {
                        "provider": self._provider,
                        "command": command,
                        "intent": intent,
                        "inputs": inputs,
                        "outputs": outputs,
                        "environment": environment,
                        "harvest": harvest,
                        "timeout_seconds": timeout_seconds,
                        "scheduler": scheduler,
                        "tier": tier,
                        "credentials": credentials,
                        "provider_params": self._provider_params,
                        "reuse_job_id": self._reuse_via,
                        "idempotency_key": idempotency_key,
                    },
                )
                break
            except RuntimeError as error:
                error_kind = getattr(error, "error_kind", None)
                if error_kind == "session_concurrency_full":
                    concurrency = getattr(error, "concurrency", None) or {}
                    live = concurrency.get("live")
                    limit = concurrency.get("limit")
                    if on_full == "raise":
                        raise SessionConcurrencyFull(live, limit) from None
                    if attempt == 0:
                        print(f"[concurrency] {live}/{limit} full — waiting for a slot")
                    _time.sleep(
                        min(2 * 1.5 ** min(attempt, 20), 20) + _random.uniform(0, 2)
                    )
                    continue
                if error_kind in {
                    "not_found",
                    "ownership_mismatch",
                    "provider_degraded",
                    "result_rejected",
                    "reuse_not_found",
                    "transient",
                    "reuse_window_exhausted",
                }:
                    self._reuse_via = None
                raise
        if "job_id" not in result:
            raise RuntimeError(result.get("message") or "submit cancelled")
        job = _ComputeJob(
            self._call,
            self._provider,
            result["job_id"],
            result.get("remote_workdir"),
        )
        job._concurrency = result.get("concurrency")
        concurrency = result.get("concurrency") or {}
        if concurrency.get("limit") is not None:
            print(
                f"[concurrency] live={concurrency.get('live')}/"
                f"{concurrency.get('limit')}"
            )
        self._jobs.append(job)
        self._reuse_via = result["job_id"]
        note = result.get("system_note")
        if note:
            print(f"[note] {note}")
        job.egress = result.get("egress")
        if job.egress:
            print(f"[egress] sandbox egress at creation: {job.egress}")
        print(job)
        return job

    def call_command(
        self,
        command,
        *,
        intent,
        login_shell=False,
        timeout_seconds=60,
    ) -> dict:
        """One synchronous command on the host (60s, 64KB cap). Approval-gated.
        For introspection — not job dispatch (use .submit_job()). Set
        login_shell=True when you need module/conda on PATH.
        Returns {stdout, stderr, exit_code}."""
        return self._compute_call(
            "ssh",
            {
                "provider": self._provider,
                "command": command,
                "intent": intent,
                "login_shell": login_shell,
                "timeout_seconds": timeout_seconds,
            },
        )

    def download(self, remote, local=None) -> Any:
        """Copy one file from the host to your workspace. ``remote`` is ANY
        readable absolute path on the host. Paths inside scratch_root/
        data_roots transfer silently; any other path raises an approval card
        the user clicks Allow/Deny on. local=None saves as the basename."""
        return self._compute_call(
            "scp",
            {
                "provider": self._provider,
                "direction": "down",
                "remote": remote,
                "local": local,
            },
        )

    def upload(self, local, remote) -> Any:
        """Copy one workspace file to the host's scratch tree (256MB cap).
        Path-jailed at both ends. For job inputs, DON'T upload first —
        submit_job(inputs=[...]) stages for you. This is for one-off
        placement."""
        return self._compute_call(
            "scp",
            {
                "provider": self._provider,
                "direction": "up",
                "local": _relativize_local(local),
                "remote": remote,
            },
        )

    def close(self) -> Any:
        """Terminate this handle's container (byoc) and cancel any
        still-running jobs, then remove their workdirs (ssh). Every handle
        ends with close() — after its last job. For byoc the container bills
        until close(), ~30 min of idle, or the container timeout — whichever
        comes first. Close once ``.result()`` has reported the job terminal,
        the harvest is confirmed, and you've saved anything the globs
        missed."""
        self._reuse_via = None
        return self._compute_call(
            "close",
            {
                "provider": self._provider,
                "job_ids": [job._job_id for job in self._jobs],
            },
        )

    def attach_job(self, job_id) -> _ComputeJob:
        """Recover a job submitted in an earlier cell. Tracked by this
        instance so .close() cleans it up too; subsequent submit_job() reuses
        its container."""
        job = _ComputeJob(self._call, self._provider, job_id)
        self._jobs.append(job)
        self._reuse_via = job_id
        return job


class _Compute:
    """host.compute — remote compute dispatch. host.compute.create(target) ->
    ComputeInstance. Lifecycle: prepare input files in a ``python`` cell ->
    switch to the ``repl`` tool -> c = host.compute.create("<provider>")
    (e.g. "ssh:myhost", "byoc:nvidia") -> c.submit_job(command=..., intent=...,
    inputs=[{src, dst_filename}, ...]) or c.call_command(cmd, intent=...).
    submit_job is keyword-only and ``command`` is required. Jobs run remotely
    and the cell returns immediately; poll ``.result()`` from a later cell to
    harvest the outputs into your workspace. Nothing harvests on its own."""

    def __init__(self, host_call: Callable[[str, list], Any]):
        self._call = host_call

    def create(
        self,
        provider: str,
        provider_params: dict | None = None,
    ) -> _ComputeInstance:
        """Create a Compute handle for a registered provider target.
        provider — target string, e.g. "ssh:<alias>" or "byoc:nvidia".
        provider_params — optional provider-specific dict (image, gpu, model,
        volumes, ...). Returns a ComputeInstance with submit_job /
        call_command / download / upload / attach_job methods."""
        return _ComputeInstance(self._call, provider, provider_params)

    def set_concurrency_limit(self, max_concurrent: int) -> Any:
        """Cap LIVE compute jobs across this whole session (the frame tree
        rooted at the orchestrator). Any frame may LOWER it; only the
        orchestrator (root frame) may RAISE it. Sub-agents inherit
        automatically. Submits past the cap are refused with
        ``error_kind="session_concurrency_full"``; ``submit_job`` retries with
        jittered backoff by default (``on_full="wait"``)."""
        return _compute_call(
            self._call,
            "set_concurrency",
            {"max_concurrent": int(max_concurrent)},
        )

    def status(self) -> dict:
        """{live, limit, daemon_live, provider_caps} for this session's root.
        ``limit`` is None when no cap is set. ``provider_caps`` is each enabled
        provider's own max-concurrent ceiling."""
        return _compute_call(self._call, "status", {})

    def reconcile(self) -> dict:
        """Jobs that were still live when the daemon last stopped.

        A remote job outlives the daemon, so a restart can find work it did not
        start still running. Returns ``{recovered: [{job_id, provider, status,
        receipt, hint}], count}``.

        Nothing is resubmitted: a job may or may not still be running, and
        guessing wrong costs either a duplicate charge or a lost result. Poll a
        recovered job with ``.result()`` — it may have finished while the daemon
        was down.
        """
        return _compute_call(self._call, "reconcile", {})

    def job_history(self, job_id: str) -> dict:
        """The append-only, sequenced event stream for one job.

        ``{job_id, events: [{seq, kind, at, payload?}]}``. A status says where a
        job is; this says how it got there.
        """
        return _compute_call(self._call, "job_history", {"job_id": job_id})

    def __repr__(self) -> str:
        return (
            "<host.compute — create(target) -> ComputeInstance; "
            "set_concurrency_limit(n); status(); reconcile(); "
            "help(host.compute) for the lifecycle>"
        )


def _relativize_local(p: Any) -> Any:
    """Agent often passes os.path.abspath(...) which is the VM-side path the
    host can't see. Strip the cwd prefix so the host gets a workspace-relative
    path it can resolve. Absolute paths under one of the host-allowlisted
    artifact roots (env: OPENAI4S_ARTIFACTS_ROOTS, colon-separated) pass
    through as-is — the host resolves them through the artifact-store DB and
    never opens the agent-supplied path directly."""
    import os

    if not isinstance(p, str):
        return p
    cwd_prefix = os.getcwd() + "/"
    if p.startswith(cwd_prefix):
        return p[len(cwd_prefix) :]
    if p.startswith("/"):
        if "/mnt/artifacts/" in p:
            return p
        roots = [
            os.path.realpath(root)
            for root in os.environ.get("OPENAI4S_ARTIFACTS_ROOTS", "").split(":")
            if root
        ]
        real = os.path.realpath(p)
        for root in roots:
            try:
                if os.path.commonpath([real, root]) == root:
                    return p
            except ValueError:
                continue
        raise ValueError(
            "local path must be workspace-relative, {'version_id': ...}, "
            "{{artifact:ID}}, or under OPENAI4S_ARTIFACTS_ROOTS "
            f"(got absolute path {p!r}; roots={roots or 'unset'})"
        )
    return p


def _normalize_provider_params(provider: str, pp: Any) -> dict | None:
    """None-drop inside the nested family block ({'nvidia':{'gpu':None}} ->
    {'nvidia':{}}). Anything outside the family key is passed through verbatim
    so the host's stray-param check rejects it loudly. No flat->nested
    auto-wrap — a flat {'gpu':'A100'} lands as a stray at the host check.
    Idempotent."""
    if not pp:
        return None
    if not isinstance(pp, dict):
        raise TypeError(
            "provider_params must be a dict ({'%s': {'gpu':'A100', ...}}); "
            "got %r" % (provider.split(":", 1)[-1], pp)
        )
    family = provider.split(":", 1)[-1]
    strays = {
        key: value for key, value in pp.items() if key != family and value is not None
    }
    inner = pp.get(family)
    if inner is None:
        return strays or None
    if not isinstance(inner, dict):
        raise TypeError(
            "provider_params['%s'] must be a dict; got %r" % (family, inner)
        )
    inner = {key: value for key, value in inner.items() if value is not None}
    normalized = ({family: inner} if inner else {}) | strays
    return normalized or None


def _compute_call(
    host_call: Callable[[str, list], Any],
    op: str,
    kw: dict,
) -> Any:
    """Route a compute op through host_call("compute_<op>", [kw]).

    Drops None kwargs (same shape as a tool call where optional params were
    omitted). Handlers return {error: "..."} for soft failures (decline, bad
    arg); surface as an exception unless it's a status-carrying result the
    caller is expected to inspect (.result()'s exit_code != 0)."""
    kw = {
        key: value for key, value in kw.items() if value is not None and key != "self"
    }
    result = host_call(f"compute_{op}", [kw])
    if isinstance(result, dict) and result.get("error") and "status" not in result:
        error = RuntimeError(f"host.compute.{op}: {result['error']}")
        error.error_kind = result.get("error_kind")
        error.concurrency = result.get("concurrency")
        raise error
    return result


__all__ = [
    "SessionConcurrencyFull",
    "_Compute",
    "_ComputeInstance",
    "_ComputeJob",
    "_compute_call",
    "_normalize_provider_params",
    "_relativize_local",
]
