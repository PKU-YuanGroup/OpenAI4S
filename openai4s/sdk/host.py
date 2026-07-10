"""host.* SDK — worker-side facade.

Runs inside the kernel worker. Every method routes through the injected
`host_call(method, args)` RPC back to the host-side dispatcher: the SDK layer
is thin, all real work is host-side.

v0.1 surface: host.llm, host.artifacts, host.artifact_path, host.delegate,
host.submit_output. Enough to prove the Code-as-Action loop end-to-end.
"""
from __future__ import annotations

from typing import Any, Callable

# --- wire codec: SDK snake_case <-> wire camelCase (strict) -----
#
# openai4s's SDK layer speaks snake_case, but the host-side schema validator
# is camelCase and strict (unknown keys are rejected). Two rules each accessor
# must honor:
# 1. map its own top-level keys snake_case -> camelCase before the wire;
# 2. DROP keys whose value is None — a Python None becomes JSON null, and the
#    optional-string validator REJECTS null; the field must be OMITTED, not sent.
# The codec only touches the top-level keys of the arg dict; nested payloads
# (messages, output schemas, user data) are passed through verbatim.


def _to_camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(p[:1].upper() + p[1:] for p in rest)


def _to_snake(camel: str) -> str:
    out: list[str] = []
    for ch in camel:
        if ch.isupper():
            out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
    return "".join(out)


def _wire(**kwargs: Any) -> dict:
    """Build a wire dict: snake->camel on keys, drop any key whose value is None."""
    return {_to_camel(k): v for k, v in kwargs.items() if v is not None}


def encode_args(args: list) -> list:
    """Encode an args list for the wire (SDK side).

    For each dict element: camelCase its top-level keys and DROP any key whose
    value is None. Nested values (messages, schemas, user data) are untouched —
    each accessor maps only its own top-level keys.
    """
    out = []
    for a in args:
        if isinstance(a, dict):
            out.append({_to_camel(k): v for k, v in a.items() if v is not None})
        else:
            out.append(a)
    return out


def decode_args(args: list) -> list:
    """Host-side inverse of `encode_args`: camel->snake the top-level keys.

    Applied at the dispatcher boundary so every `_m_*` handler keeps reading
    snake_case keys, symmetric with `encode_args` and touching top-level only.
    """
    out = []
    for a in args:
        if isinstance(a, dict):
            out.append({_to_snake(k): v for k, v in a.items()})
        else:
            out.append(a)
    return out


# capability gate: the analysis kernel (python/R) is spliced with only the
# ANALYSIS subset. These control-plane accessors are NOT attached there, so a
# reference is a genuine AttributeError (symbol absent) — not a runtime if-check.
_ANALYSIS_DENY = frozenset(
    {
        "frames",
        "query",
        "mcp",
        "compute",
        "delegate",
        "children",
        "collect",
        "stop_child",
        "send_message",
        "delegation_stats",
    }
)


class _Skills:
    """host.skills.* — SKILL.md lifecycle (list/get/read/edit/publish/delete).

    Mirrors openai4s's skills.py draft-authoring flow. `edit` with old_string=None
    creates/overwrites; otherwise it does a str_replace. Writing kernel.py runs
    the sidecar structure gate and returns its {ok, error?} result.
    """

    def __init__(self, host_call: Callable[[str, list], Any]):
        self._call = host_call

    def list(self) -> list[dict]:
        """Lightweight catalog: name/description/origin/has_kernel."""
        return self._call("skills_list", [])

    def get(self, name: str) -> dict:
        """Metadata for one skill (no file bodies)."""
        return self._call("skills_get", [name])

    def read(self, name: str, path: str = "SKILL.md") -> str:
        """Read a file inside a skill dir (SKILL.md or kernel.py)."""
        return self._call("skills_read", [{"name": name, "path": path}])

    def edit(
        self, name: str, path: str, content: str, old_string: str | None = None
    ) -> dict:
        """Create/overwrite (old_string=None) or str_replace inside a skill file.

        Returns {"ok",...}; when path is kernel.py the result also carries
        "sidecar_gate": {ok, error?}. Read-only origins (openai4s) are rejected.
        """
        return self._call(
            "skills_edit",
            [
                {
                    "name": name,
                    "path": path,
                    "content": content,
                    "old_string": old_string,
                }
            ],
        )

    def publish(self, name: str) -> dict:
        """Promote a draft skill to `personal` origin (makes it retrievable)."""
        return self._call("skills_publish", [name])

    def delete(self, name: str) -> dict:
        """Delete a skill dir. Read-only origins (openai4s) are rejected."""
        return self._call("skills_delete", [name])


class _Query:
    """host.query — read-only SQL over the SQLite data model.

    Callable: host.query("SELECT...", params=[...], limit=100, df=False).
    denylisted tables (memories/host_call_log) are refused; 5s timeout.
    """

    def __init__(self, host_call: Callable[[str, list], Any]):
        self._call = host_call

    def __call__(
        self,
        sql: str,
        params: list | None = None,
        limit: int | None = None,
        df: bool = False,
        scope: str = "project",
    ) -> Any:
        res = self._call(
            "query",
            [
                {
                    "sql": sql,
                    "params": params or [],
                    "limit": limit,
                    "df": df,
                    "scope": scope,
                }
            ],
        )
        if df:
            try:
                import pandas as pd  # noqa: F401

                return pd.DataFrame(res["rows"], columns=res["columns"])
            except ImportError:
                return res
        return res

    def schema(self) -> dict:
        """{table: [col,...]} for readable tables."""
        return self._call("query_schema", [])


class _LineageEntry(dict):
    """Return type of host.lineage[vid] — a dict with attribute access."""

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as e:  # noqa: TRY003
            raise AttributeError(item) from e


class _Lineage:
    """host.lineage[version_id] accessor + graph traversal."""

    def __init__(self, host_call: Callable[[str, list], Any]):
        self._call = host_call

    def __getitem__(self, version_id: str) -> _LineageEntry:
        return _LineageEntry(self._call("lineage_get", [version_id]))

    def graph(
        self,
        version_id: str,
        direction: str = "up",
        max_depth: int | None = None,
        max_nodes: int | None = None,
    ) -> dict:
        return self._call(
            "lineage_graph",
            [
                {
                    "version_id": version_id,
                    "direction": direction,
                    "max_depth": max_depth,
                    "max_nodes": max_nodes,
                }
            ],
        )


class _Endpoints:
    """host.endpoints.* — managed inference endpoints."""

    def __init__(self, host_call: Callable[[str, list], Any]):
        self._call = host_call

    def list(self) -> list[dict]:
        return self._call("endpoints_list", [])

    def free_port(self) -> int:
        """Reserve a free port from the 20000-29999 band (port = mutex)."""
        return self._call("endpoints_free_port", [])

    def register(self, name: str, **spec: Any) -> dict:
        spec["name"] = name
        return self._call("endpoints_register", [spec])

    def status(self, name: str) -> dict:
        return self._call("endpoints_status", [name])

    def probe(self, name: str) -> dict:
        """Poll the live route for HTTP 200; flips status to 'live'."""
        return self._call("endpoints_probe", [name])


class _Credentials:
    """host.credentials.* — secret vault, never persisted."""

    def __init__(self, host_call: Callable[[str, list], Any]):
        self._call = host_call

    def set(self, name: str, value: str) -> dict:
        return self._call("credentials_set", [{"name": name, "value": value}])

    def get(self, name: str) -> str:
        return self._call("credentials_get", [name])["value"]

    def list(self) -> list[str]:
        return self._call("credentials_list", [])


class _Mcp:
    """host.mcp.* — Model Context Protocol connectors."""

    def __init__(self, host_call: Callable[[str, list], Any]):
        self._call = host_call

    def list(self) -> list[dict]:
        """Enabled connectors: [{id, name, description}, ...]."""
        return self._call("mcp_list", [])

    def tools(self, server: str) -> Any:
        """Discover a connector's tools: {tools: [{name, description, inputSchema}]}."""
        return self._call("mcp_tools", [server])

    def call(self, server: str, tool: str, args: dict | None = None) -> Any:
        """Invoke a connector tool → {is_error, text, raw}."""
        return self._call(
            "mcp_call", [{"server": server, "tool": tool, "args": args or {}}]
        )


class _Env:
    """host.env.* — inspect + select the PREBUILT runtime environments.

    Several environments ship ready (general data-science, structural biology,
    phylogenetics, R). Pick the one that already has what you need with
    `host.env.use("struct")` instead of pip-installing into one kernel every task.
    """

    def __init__(self, host_call: Callable[[str, list], Any]):
        self._call = host_call

    def list(self, packages: list[str] | None = None) -> dict:
        """The prebuilt environments (name, language, python_version, notable
        packages, description) + which one this kernel currently uses. Pass
        `packages` to also get per-env has/missing and a `recommend`ed env.
        Returns {environments:[…], current, recommend, missing}."""
        return self._call("env_list", [{"packages": list(packages or [])}])

    def use(self, name: str) -> dict:
        """Switch the notebook kernel to a prebuilt environment for the following
        cells. Call it in its OWN cell, then import in a new one (the switch is
        applied before the next cell). Returns {ok, env, note}."""
        return self._call("env_use", [{"name": name}])

    def list_dependencies(self, packages: list[str] | None = None) -> dict:
        """Which of `packages` are missing from each environment. Alias of
        list(packages) kept for compatibility."""
        return self._call("env_list", [{"packages": list(packages or [])}])

    def create(
        self, name: str | None = None, packages: list[str] | None = None
    ) -> dict:
        """Install extra packages into the CURRENT kernel (pip) when no prebuilt
        env has them. Prefer host.env.use() first. Returns {name, installed, ok}."""
        return self._call(
            "env_setup", [{"name": name, "packages": list(packages or [])}]
        )


class _App:
    """host.app.* — UI tile rendering."""

    def __init__(self, host_call: Callable[[str, list], Any]):
        self._call = host_call

    def render(self, kind: str, payload: Any) -> dict:
        return self._call("app_render", [{"kind": kind, "payload": payload}])

    def tiles(self) -> list[dict]:
        return self._call("app_tiles", [])


# --- remote compute (host.compute) -----------------------------------
#
# Gated on the host advertising a remote-compute provider. Installs
# host.compute = namespace with one constructor (create). Two provider
# families: "ssh:<alias>" runs jobs over an SSH connection; "byoc:<id>"
# provisions a bring-your-own-compute sandbox (e.g. "byoc:nvidia").
# Every method routes through host_call("compute_<op>", [kw])
# back to the host-side dispatcher, which owns the real remote work.


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
        # byoc only — the egress posture the job's sandbox was CREATED with
        # (e.g. "allowlist (2 domains)" or "blocked (no outbound network)"),
        # host-authored at submit time. It describes THIS sandbox for its
        # whole life: changing the provider's Egress setting later affects
        # only sandboxes created afterwards, never this one. None for
        # non-byoc providers and for sandboxes that predate the policy stamp.
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

    # Alias — .id is what callers reach for.
    id = job_id

    @property
    def exit_code(self):
        """None until the job is terminal. Call .result() after the
        compute_done notification to populate."""
        r = self.result_dict or {}
        ec = r.get("exit_code")
        if ec is None and r.get("status") in (
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
                    "terminal — call .result() after wait_for_notification",
                    file=sys.stderr,
                )
        return ec

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
        """Non-blocking read of the job's result dict for all provider
        families (``ssh:*`` and ``byoc:*``). Returns the persisted result if
        the daemon's poller has already harvested it; otherwise
        ``{'status': 'running', ...}`` with guidance to use the
        ``wait_for_notification`` brain-tool.

        The daemon's background poller polls the remote, harvests into
        ``hpc/<job_id>/``, and emits a ``compute_done`` notification when done
        — exit this cell and use ``wait_for_notification``; the payload
        includes ``featured_files`` so you can ``save_artifacts(...)`` without
        re-entering the kernel. Returns ``{status, exit_code, output_files,
        featured_files, left_on_remote, remote_workdir, stdout_tail?,
        stderr_tail?, job_wall_s?, ...}`` once terminal. Does NOT raise on
        ``status='failed'`` or ``status='timed_out'`` — read ``exit_code`` /
        ``error_kind``.
        """
        if self.result_dict and self.result_dict.get("status") not in (
            "running",
            "timeout",
            "harvesting",
        ):
            return self.result_dict
        r = self._compute_call(
            "result", {"job_id": self._job_id, "provider": self._provider}
        )
        self.result_dict = r
        self.output_files = r.get("output_files", [])
        self.featured_files = r.get("featured_files", [])
        if not self._workdir:
            self._workdir = r.get("remote_workdir")
        # A job recovered with attach_job() never saw a submit reply, so
        # backfill the birth-egress line from the persisted result: None must
        # keep meaning "no fence", never "we lost track of one".
        if self.egress is None and r.get("egress"):
            self.egress = r["egress"]
        # Host-authored egress attribution: present only when this job ran
        # under an egress fence AND its outcome looks like the fence. Printed
        # once so it lands in the transcript next to the output.
        hint = r.get("egress_hint")
        if hint and not getattr(self, "_egress_hint_printed", False):
            self._egress_hint_printed = True
            print(hint)
        return r

    def cancel(self) -> Any:
        """Terminate the running job (scheduler cancel / process-group
        SIGTERM / sandbox terminate). This is the ONLY thing that kills a job
        — the user's Stop button, a kernel crash, or exiting the ``repl`` cell
        never auto-cancel; the daemon's poller keeps tracking and will harvest
        the job when it finishes. Call this (or ``c.close()``) when you
        actually want to stop the remote work and free the allocation."""
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
        pp = self._provider_params or {}
        pid = self._provider.split(":")[-1]
        bits = []
        for k in ("image", "gpu", "model"):
            v = (pp.get(pid) or {}).get(k)
            if v:
                bits.append(f"{k}={v!r}")
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
    ):
        """Stage inputs, write the job script, dispatch. Approval-gated.
        Returns immediately after dispatch. When a session
        ``set_concurrency_limit`` is in effect and the cap is full, the host
        refuses with ``error_kind="session_concurrency_full"`` and this call's
        ``on_full`` decides what happens: ``"wait"`` (default) retries with
        jittered exponential backoff until a slot opens; ``"raise"`` raises
        ``SessionConcurrencyFull(live, limit)`` immediately.

        Print the returned Job (its repr is the recovery handle), end the
        cell, and use the ``wait_for_notification`` brain-tool to park until
        the daemon's poller emits the ``compute_done`` notification. Call
        ``.result()`` for a non-blocking read of the harvested result dict.
        For byoc providers, the FIRST submit_job() on a handle creates the
        container; subsequent calls reuse it (weights/caches stay warm).
        Sequential only — submit job N+1 AFTER job N's ``compute_done``
        notification arrives, since each submit wipes /work.

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
        """
        import itertools as _itertools
        import random as _random
        import time as _time

        # kwarg aliases normalized here, never sent.
        if timeout_seconds is None:
            timeout_seconds = timeout if timeout is not None else timeout_s
        if environment is None:
            environment = env
        if inputs:
            # Bare string -> {'src': str}; _relativize_local on src.
            # Container-type guard: a top-level string/dict would otherwise
            # iterate chars/keys.
            if isinstance(inputs, (str, dict)):
                inputs = [inputs]
            norm = []
            for inp in inputs:
                if isinstance(inp, str):
                    inp = {"src": inp}
                if isinstance(inp, dict) and "src" in inp:
                    inp = {**inp, "src": _relativize_local(inp["src"])}
                norm.append(inp)
            inputs = norm
        for attempt in _itertools.count():
            try:
                r = self._compute_call(
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
                    },
                )
                break
            except RuntimeError as e:
                ek = getattr(e, "error_kind", None)
                if ek == "session_concurrency_full":
                    c = getattr(e, "concurrency", None) or {}
                    live, limit = c.get("live"), c.get("limit")
                    if on_full == "raise":
                        raise SessionConcurrencyFull(live, limit) from None
                    if attempt == 0:
                        print(
                            f"[concurrency] {live}/{limit} full — "
                            f"waiting for a slot"
                        )
                    _time.sleep(
                        min(2 * 1.5 ** min(attempt, 20), 20) + _random.uniform(0, 2)
                    )
                    continue
                # Only drop the warm-container anchor if the error suggests
                # the container itself is gone — pre-create failures (declined
                # approval, at-cap, not-configured, bad params) shouldn't cost
                # the user a cold start on retry.
                if ek in {
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
        if "job_id" not in r:
            raise RuntimeError(r.get("message") or "submit cancelled")
        job = _ComputeJob(
            self._call, self._provider, r["job_id"], r.get("remote_workdir")
        )
        job._concurrency = r.get("concurrency")
        conc = r.get("concurrency") or {}
        if conc.get("limit") is not None:
            print(f"[concurrency] live={conc.get('live')}/{conc.get('limit')}")
        self._jobs.append(job)
        self._reuse_via = r["job_id"]
        note = r.get("system_note")
        if note:
            print(f"[note] {note}")
        job.egress = r.get("egress")
        if job.egress:
            print(f"[egress] sandbox egress at creation: {job.egress}")
        # Print the recovery handle so it survives in the transcript even if a
        # later line in this cell raises before the agent prints it.
        print(job)
        return job

    def call_command(
        self, command, *, intent, login_shell=False, timeout_seconds=60
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
        comes first. Close once compute_done has arrived, the harvest is
        confirmed, and you've saved anything the globs missed."""
        self._reuse_via = None
        return self._compute_call(
            "close",
            {"provider": self._provider, "job_ids": [j._job_id for j in self._jobs]},
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
    submit_job is keyword-only and ``command`` is required. Jobs run remotely;
    the cell returns immediately and the daemon posts a ``compute_done``
    notification when outputs are harvested into your workspace."""

    def __init__(self, host_call: Callable[[str, list], Any]):
        self._call = host_call

    def create(
        self, provider: str, provider_params: dict | None = None
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
            self._call, "set_concurrency", {"max_concurrent": int(max_concurrent)}
        )

    def status(self) -> dict:
        """{live, limit, daemon_live, provider_caps} for this session's root.
        ``limit`` is None when no cap is set. ``provider_caps`` is each enabled
        provider's own max-concurrent ceiling."""
        return _compute_call(self._call, "status", {})

    def __repr__(self) -> str:
        return (
            "<host.compute — create(target) -> ComputeInstance; "
            "set_concurrency_limit(n); status(); "
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
        # Legacy VM-side bind-mount of the artifact store.
        if "/mnt/artifacts/" in p:
            return p
        roots = [
            os.path.realpath(x)
            for x in os.environ.get("OPENAI4S_ARTIFACTS_ROOTS", "").split(":")
            if x
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
    fam = provider.split(":", 1)[-1]
    strays = {k: v for k, v in pp.items() if k != fam and v is not None}
    inner = pp.get(fam)
    if inner is None:
        return strays or None
    if not isinstance(inner, dict):
        raise TypeError("provider_params['%s'] must be a dict; got %r" % (fam, inner))
    inner = {k: v for k, v in inner.items() if v is not None}
    out = ({fam: inner} if inner else {}) | strays
    return out or None


def _compute_call(host_call: Callable[[str, list], Any], op: str, kw: dict) -> Any:
    """Route a compute op through host_call("compute_<op>", [kw]).

    Drops None kwargs (same shape as a tool call where optional params were
    omitted). Handlers return {error: "..."} for soft failures (decline, bad
    arg); surface as an exception unless it's a status-carrying result the
    caller is expected to inspect (.result()'s exit_code != 0)."""
    kw = {k: v for k, v in kw.items() if v is not None and k != "self"}
    r = host_call(f"compute_{op}", [kw])
    if isinstance(r, dict) and r.get("error") and "status" not in r:
        e = RuntimeError(f"host.compute.{op}: {r['error']}")
        e.error_kind = r.get("error_kind")
        e.concurrency = r.get("concurrency")
        raise e
    return r


class _Host:
    def __init__(
        self,
        host_call: Callable[[str, list], Any],
        denied: frozenset[str] = frozenset(),
    ):
        # Wrap the raw RPC so every SDK call encodes its args for the wire
        # (snake->camel + drop-None) exactly once, transparently to accessors.
        def _encoded_call(method: str, args: list) -> Any:
            return host_call(method, encode_args(args))

        # `_denied` is the set of control-plane symbols this kernel was NOT
        # spliced with. Set FIRST so __getattribute__ can consult it
        # while the rest of __init__ runs.
        object.__setattr__(self, "_denied", frozenset(denied))
        self._call = _encoded_call
        self.skills = _Skills(self._call)
        # query/mcp are control-plane; only attach when not denied so that a
        # denied kernel has no such attribute at all (genuine AttributeError).
        if "query" not in self._denied:
            self.query = _Query(self._call)
        if "mcp" not in self._denied:
            self.mcp = _Mcp(self._call)
        # remote compute is control-plane and host-gated: only attach when the
        # kernel was spliced with it AND the host advertises a provider (the
        # manager passes compute in `denied` when no provider is configured).
        if "compute" not in self._denied:
            self.compute = _Compute(self._call)
        self.lineage = _Lineage(self._call)
        self.endpoints = _Endpoints(self._call)
        self.credentials = _Credentials(self._call)
        self.app = _App(self._call)
        self.env = _Env(self._call)

    def __getattribute__(self, name: str) -> Any:
        # Enforce the splice gate: a denied control-plane symbol is absent,
        # exactly as if its SDK fragment had never been concatenated in. This
        # covers class-level methods (delegate/children/...) that can't simply
        # be left unset in __init__.
        if name != "_denied" and not name.startswith("__"):
            denied = object.__getattribute__(self, "__dict__").get("_denied")
            if denied and name in denied:
                raise AttributeError(
                    f"'_Host' object has no attribute {name!r} "
                    f"(not available in this kernel)"
                )
        return object.__getattribute__(self, name)

    # --- model access -----------------------------------------------------
    def llm(
        self,
        request: str | dict | list,
        *,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        max_concurrency: int | None = None,
    ) -> Any:
        """Sub-LLM call. Three input shapes, return type mirrors the input:

        - str -> single prompt, returns str
        - dict -> single request {"prompt"|"messages",...}, returns str
        - list -> parallel fan-out, returns list[str] (host caps concurrency)

        The system prompt is host-controlled; passing a `system` role here is
        rejected fail-fast.
        """

        def _norm(item: str | dict) -> dict:
            if isinstance(item, str):
                return {"messages": [{"role": "user", "content": item}]}
            if isinstance(item, dict):
                if "messages" in item:
                    msgs = item["messages"]
                elif "prompt" in item:
                    msgs = [{"role": "user", "content": item["prompt"]}]
                else:
                    raise ValueError("host.llm dict needs 'prompt' or 'messages'")
                for m in msgs:
                    if m.get("role") == "system":
                        raise ValueError(
                            "host.llm: 'system' role is not allowed; the system "
                            "prompt is host-controlled"
                        )
                out = {"messages": msgs}
                for k in ("max_tokens", "temperature"):
                    if k in item:
                        out[k] = item[k]
                return out
            raise TypeError(f"host.llm: unsupported request type {type(item)!r}")

        if isinstance(request, list):
            specs = [_norm(it) for it in request]
            return self._call(
                "llm",
                [
                    {
                        "batch": specs,
                        "max_tokens": max_tokens,
                        "temperature": temperature,
                        "max_concurrency": max_concurrency,
                    }
                ],
            )
        spec = _norm(request)
        spec.setdefault("max_tokens", max_tokens)
        spec.setdefault("temperature", temperature)
        return self._call("llm", [spec])

    # --- artifacts / data discovery --------------------------------------
    def artifacts(self, **filters: Any) -> dict:
        """List versioned artifacts (cross-session store). Returns {count, artifacts:[...]}."""
        return self._call("artifacts", [filters])

    def artifact_path(self, version_id: str) -> str:
        """Resolve a version_id / artifact_id to a local filesystem path."""
        return self._call("artifact_path", [version_id])

    def save_artifact(
        self,
        path: str,
        filename: str | None = None,
        *,
        content_type: str | None = None,
        input_version_ids: list[str] | None = None,
        producing_cell_id: str | None = None,
        priority: int = 0,
    ) -> dict:
        """Register a workspace file as a versioned artifact. Returns {version_id,...}.

        `input_version_ids` records data lineage edges from those inputs to
        this output.
        """
        return self._call(
            "save_artifact",
            [
                {
                    "path": path,
                    "filename": filename,
                    "content_type": content_type,
                    "input_version_ids": input_version_ids or [],
                    "producing_cell_id": producing_cell_id,
                    "priority": priority,
                }
            ],
        )

    def view_image(
        self, version_id: str | None = None, *, path: str | None = None
    ) -> dict:
        """Render an image artifact in the host UI."""
        return self._call("view_image", [{"version_id": version_id, "path": path}])

    def artifact_marker(self, version_id: str) -> str:
        """Build a `{{artifact:VID}}` marker literal for a version id.

        Use this to embed a data artifact into a message/prompt handed to a
        delegate; the marker is resolved to the artifact content downstream.
        Constructed at runtime so the marker prefix never appears verbatim in
        source (avoids the kernel's static marker scanner).
        """
        return self._call("artifact_marker", [version_id])

    def frames(
        self,
        *,
        frame_id: str | None = None,
        pattern: str | None = None,
        project_id: str = "default",
        status: str | None = None,
        roots_only: bool = True,
        page: int = 0,
        page_size: int = 50,
        limit: int = 50,
    ) -> Any:
        """Inspect the turn/delegate tree. Three modes:

        - `frame_id=...` -> detail view (cells oldest-first; newest = last page)
        - `pattern=...` -> regex search over frame names + cell code/stdout
        - neither -> browse (roots by default; project_id='all' spans all)
        """
        return self._call(
            "frames",
            [
                {
                    "frame_id": frame_id,
                    "pattern": pattern,
                    "project_id": project_id,
                    "status": status,
                    "roots_only": roots_only,
                    "page": page,
                    "page_size": page_size,
                    "limit": limit,
                }
            ],
        )

    # --- model / identity / capabilities ----------------------------
    def current_model(self) -> str:
        """Resolve the current (expensive, high-reasoning) model id at runtime."""
        return self._call("current_model", [])

    def list_models(self) -> list[dict]:
        """Available model ids. Never hardcode a model id — resolve here."""
        return self._call("list_models", [])

    def capabilities(self) -> dict:
        """Probe host capabilities."""
        return self._call("capabilities", [])

    def get_user_email(self) -> str:
        """Return the user's contact email, or raise (deny-on-failure)."""
        return self._call("get_user_email", [])

    # --- backgrounded cells: peek / interrupt --------------------
    def exec_background(self, code: str, *, origin: str = "agent") -> dict:
        """Launch a long-running cell WITHOUT blocking; returns {exec_id}."""
        return self._call("exec_background", [{"code": code, "origin": origin}])

    def exec_peek(self, exec_id: str) -> dict:
        """Non-blocking read of a backgrounded cell's accumulated stdout."""
        return self._call("exec_peek", [exec_id])

    def exec_interrupt(self, exec_id: str) -> dict:
        """Stop a backgrounded cell (one-shot SIGINT; kernel stays alive)."""
        return self._call("exec_interrupt", [exec_id])

    def exec_list(self) -> list[dict]:
        return self._call("exec_list", [])

    # --- delegation (fan-out) --------------------------------------------
    def delegate(
        self,
        request: Any,
        *,
        task: str | None = None,
        name: str | None = None,
        context_summary: str | None = None,
        output_schema: dict | None = None,
        wait: bool = True,
    ) -> Any:
        """Spawn sub-agent(s). str/dict -> single; list -> list of results.

        wait=True (default) blocks for the result(s); wait=False returns child
        handle(s) immediately for later host.collect. output_schema, when
        given, forces each child to submit_output matching that schema.
        """
        return self._call(
            "delegate",
            [
                {
                    "request": request,
                    "task": task,
                    "name": name,
                    "context_summary": context_summary,
                    "output_schema": output_schema,
                    "wait": wait,
                }
            ],
        )

    def children(self) -> list[dict]:
        """List this agent's currently-tracked sub-agent children."""
        return self._call("children", [])

    def collect(
        self, child_ids: list[str] | str | None = None, *, timeout: float | None = None
    ) -> Any:
        """Block for async (wait=False) children's results by id."""
        if isinstance(child_ids, str):
            child_ids = [child_ids]
        return self._call("collect", [{"child_ids": child_ids, "timeout": timeout}])

    def stop_child(self, child_id: str) -> dict:
        """Signal a running child to stop."""
        return self._call("stop_child", [child_id])

    def send_message(self, child_id: str, message: str) -> dict:
        """Steer a running child by sending it a message (direct parent→child)."""
        return self._call("send_message", [{"child_id": child_id, "message": message}])

    def delegation_stats(self) -> dict:
        """Aggregate stats over this agent's delegation subtree."""
        return self._call("delegation_stats", [])

    # --- structured output ------------------------------------------------
    def submit_output(
        self,
        output: Any,
        completion_bullets: list[str],
        *,
        output_schema: dict | None = None,
    ) -> dict:
        """Submit the task's structured result + human-facing completion bullets.

        completion_bullets must be 1-4 past-tense, verb-first strings. If
        output_schema is given, `output` is validated against it. A validation
        failure returns {"error":...} so the model can retry.
        """
        return self._call(
            "submit_output",
            [
                {
                    "output": output,
                    "completion_bullets": completion_bullets,
                    "output_schema": output_schema,
                }
            ],
        )

    # --- skill retrieval (progressive disclosure) ------------------------
    def search_skills(self, query: str, *, limit: int = 5) -> list[dict]:
        """Retrieve full recipes for skills matching `query` (keyword overlap).

        The system prompt only lists skill names + one-line summaries; call this
        to pull the full doc of relevant skills on demand. Each result has
        {name, origin, description, import, score, doc, sidecar_gate}. Never use
        a skill you have not retrieved here.
        """
        return self._call("search_skills", [{"query": query, "limit": limit}])

    def skill(self, name: str) -> dict:
        """Load one skill's full recipe by exact name (opencode `skill` tool)."""
        return self.skills.get(name)

    def load_skill(self, name: str) -> dict:
        """Load a skill's full SKILL.md guidance by name (fuzzy). Surfaces a
        'Loading <skill> skill guidance' step. Returns {name, description,
        content}. Read it, then follow it while doing the analysis."""
        return self._call("load_skill", [name])

    # --- opencode-parity harness tools -----------------------------------
    # A Code-as-Action cell can call these directly; they mirror opencode's
    # bash/read/write/edit/glob/grep/list/webfetch/websearch/todo tools. File +
    # shell ops are confined to your working directory (the session workspace).
    def bash(
        self, command: str, *, timeout: float = 120, workdir: str | None = None
    ) -> dict:
        """Run a shell command in the workspace. Returns
        {exit_code, stdout, stderr}. Networking is available."""
        return self._call(
            "bash", [{"command": command, "timeout": timeout, "workdir": workdir}]
        )

    def read_file(self, path: str, *, offset: int = 0, limit: int = 2000) -> dict:
        """Read a workspace file (optionally a line window). Returns {content,...}."""
        return self._call(
            "read_file", [{"path": path, "offset": offset, "limit": limit}]
        )

    def write_file(self, path: str, content: str) -> dict:
        """Write (create/overwrite) a workspace file. Captured as an artifact."""
        return self._call("write_file", [{"path": path, "content": content}])

    def edit_file(
        self, path: str, old_string: str, new_string: str, *, replace_all: bool = False
    ) -> dict:
        """Exact-string replace in a workspace file (opencode `edit`)."""
        return self._call(
            "edit_file",
            [
                {
                    "path": path,
                    "old_string": old_string,
                    "new_string": new_string,
                    "replace_all": replace_all,
                }
            ],
        )

    def glob(self, pattern: str, *, path: str | None = None) -> dict:
        """Filename glob within the workspace, e.g. glob('**/*.csv')."""
        return self._call("glob", [{"pattern": pattern, "path": path}])

    def grep(
        self, pattern: str, *, path: str | None = None, include: str | None = None
    ) -> dict:
        """Regex content search within the workspace (opencode `grep`)."""
        return self._call(
            "grep", [{"pattern": pattern, "path": path, "include": include}]
        )

    def list_dir(self, path: str = ".") -> dict:
        """List a workspace directory."""
        return self._call("list_dir", [{"path": path}])

    def web_fetch(
        self,
        url: str,
        *,
        format: str = "markdown",
        timeout: float = 30,
        max_chars: int = 20000,
    ) -> dict:
        """Fetch a URL and return its content as markdown/text/html/json."""
        return self._call(
            "web_fetch",
            [
                {
                    "url": url,
                    "format": format,
                    "timeout": timeout,
                    "max_chars": max_chars,
                }
            ],
        )

    def web_search(
        self, query: str, *, num_results: int = 8, timeout: float = 20
    ) -> dict:
        """Live web search (keyless). Returns {results:[{title,url,snippet}]}."""
        return self._call(
            "web_search",
            [{"query": query, "num_results": num_results, "timeout": timeout}],
        )

    def remote_gpu_status(self) -> dict:
        """Inspect configured remote GPU hosts and registered services.

        Use this before GPU-only workflows. If a host exists but a service such
        as `fold` or `score_mutations` is missing, delegate provisioning instead
        of reporting that the task is impossible.
        """
        return self._call("remote_gpu_status", [{}])

    def register_remote_capability(
        self,
        alias: str,
        capability: str,
        *,
        script: str = "",
        engine: str = "",
        invoke: str = "",
        markers: dict | None = None,
        notes: str = "",
        probe: dict[str, str] | None = None,
        verify_command: str = "",
    ) -> dict:
        """Register a verified remote GPU service on an SSH host.

        Prefer a structured probe::

            {"kind": "path_exists", "path": "/opt/service/run.sh"}
            {"kind": "executable_exists", "binary": "service-cli"}

        Paths are probed literally (quoted; no remote ``~``/``$VAR``
        expansion), so pass absolute paths. Providing both ``probe`` and the
        legacy ``verify_command`` is an error. With neither, ``script`` becomes
        a ``path_exists`` probe; ``verify_command`` — accepted only as exactly
        ``test -e <path>`` or ``which <binary>`` — takes precedence over
        ``script`` for verification. The registry is updated only after the
        probe exits 0.
        """
        return self._call(
            "register_remote_capability",
            [
                {
                    "alias": alias,
                    "capability": capability,
                    "script": script,
                    "engine": engine,
                    "invoke": invoke,
                    "markers": markers or {},
                    "notes": notes,
                    "probe": probe,
                    "verify_command": verify_command,
                }
            ],
        )

    def fold(
        self,
        sequence: str,
        *,
        name: str = "protein",
        gpu: int = 0,
        cycle: int = 10,
        step: int = 40,
    ) -> dict:
        """Predict a REAL 3D structure for a protein sequence on the configured
        remote GPU host (8×A100), using Protenix (AlphaFold3-class) inference.

        This is the correct way to build a structural model — do NOT hand-write
        a synthetic backbone or a geometric spiral. Blocks ~1-2 min while the
        remote GPU folds, then returns:
          {ok, pdb, plddt_csv, confidence, mean_plddt, ptm, length, engine,
           host, remote_dir}
        Write `result["pdb"]` to a `.pdb` file with host.write_file(...) so it
        renders in the 3D viewer, and plot per-residue pLDDT from
        `result["plddt_csv"]` (columns: chain,resid,resname,plddt).

        Note: runs single-sequence (no MSA) for speed, so pLDDT is a genuine but
        conservative estimate — say so in the report rather than overclaiming."""
        return self._call(
            "fold",
            [
                {
                    "sequence": sequence,
                    "name": name,
                    "gpu": gpu,
                    "cycle": cycle,
                    "step": step,
                }
            ],
        )

    def score_mutations(
        self,
        sequence: str,
        *,
        name: str = "protein",
        positions: list | None = None,
        gpu: int = 0,
    ) -> dict:
        """Score single-substitution variant effects with a REAL model (ESM
        masked-marginal) on the remote GPU host. Returns
        {ok, scores_csv, summary, mean_score, top5, model, host}; the CSV has
        columns position,wt,mut,mutation,esm_score (higher = more favorable).
        `positions` optionally limits scoring to a list of 1-based positions.

        This is the ONLY sanctioned way to obtain mutation scores. If it returns
        an {error} (no scoring service configured, or the host is unreachable),
        STOP and report that honestly — do NOT fabricate scores with np.random,
        a BLOSUM proxy dressed up as ESM, or a fake 'method-comparison' figure."""
        return self._call(
            "score_mutations",
            [{"sequence": sequence, "name": name, "positions": positions, "gpu": gpu}],
        )

    def request_network_access(self, domain: str, *, reason: str | None = None) -> dict:
        """Ask the user to widen the outbound domain allowlist (report §5.1).

        When OPENAI4S_EGRESS=allowlist, host.web_fetch / host.bash to a domain
        outside the science / package-index / data-repo allowlist return a proxy
        403. Call this to request approval for `domain`; it routes through the
        permission broker, so the USER decides — you cannot widen it yourself.
        On approval the domain is added to the allowlist and the fetch/bash can be
        retried. Pass a short `reason` so the approval prompt has context."""
        return self._call(
            "request_network_access", [{"domain": domain, "reason": reason}]
        )

    def todo_write(self, todos: list[dict]) -> dict:
        """Set the session task list. Each todo = {content, status, priority, id}
        with status ∈ pending|in_progress|completed|cancelled."""
        return self._call("todo_write", [{"todos": todos}])

    def todo_read(self) -> dict:
        """Read the current session task list."""
        return self._call("todo_read", [])

    def plan_update(self, step_id: str, status: str, note: str | None = None) -> dict:
        """Tick a step of the APPROVED plan on the review card, live. Call this
        during auto-execution: `host.plan_update("s1", "in_progress")` when you
        start a step and `host.plan_update("s1", "completed")` once its
        deliverables exist. status ∈ pending|in_progress|completed|failed|skipped.
        """
        return self._call(
            "plan_update", [{"step_id": step_id, "status": status, "note": note}]
        )

    def plan_read(self) -> dict:
        """Read the current session's approved plan (steps + live status)."""
        return self._call("plan_read", [])

    def remember(self, content: str, *, block: str = "general") -> dict:
        """Persist a durable fact the daemon re-injects into future sessions
        (takes effect when Memory is enabled in Customize → Memory)."""
        return self._call("remember", [{"content": content, "block": block}])


def build_host(host_call: Callable[[str, list], Any], mode: str = "repl") -> _Host:
    """Assemble the host.* facade for a kernel.

    capability gate = splice trimming, not a runtime if-check. The `repl`
    (control-plane) kernel is spliced with the full OPENAI4S surface; the analysis
    kernel (`python`/`R`) gets only the ANALYSIS subset. We model openai4s's
    string-concatenation splice by making the control-plane symbols genuinely
    absent on the analysis host — `host.frames` / `host.query` / `host.delegate`
    etc. raise a real AttributeError and `hasattr(host,...)` is False, never a
    runtime "not allowed" error. Data reaches the analysis kernel via
    ./handoff/*.json instead of host.query/host.frames.
    """
    if mode == "repl":
        return _Host(host_call)
    if mode in ("python", "analysis", "r", "R"):
        return _Host(host_call, denied=_ANALYSIS_DENY)
    raise ValueError(f"build_host: unknown kernel mode {mode!r}")
