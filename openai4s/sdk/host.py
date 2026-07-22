"""host.* SDK — worker-side facade.

Runs inside the kernel worker. Every method routes through the injected
`host_call(method, args)` RPC back to the host-side dispatcher: the SDK layer
is thin, all real work is host-side.

v0.1 surface: host.llm, host.artifacts, host.artifact_path, host.delegate,
host.submit_output. Enough to prove the Code-as-Action loop end-to-end.
"""
from __future__ import annotations

from typing import Any, Callable

from openai4s.sdk.bash import BashExecutor
from openai4s.sdk.compute import (
    SessionConcurrencyFull,
    _Compute,
    _compute_call,
    _ComputeInstance,
    _ComputeJob,
    _normalize_provider_params,
    _relativize_local,
)

# Version of the worker-side Host RPC capability contract recorded in every
# Kernel bootstrap manifest.  Bump this only for a compatibility-affecting
# surface change; recovery compares the value observed inside the candidate
# worker with the frozen checkpoint value.
HOST_CAPABILITY_VERSION = "2"

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

    def status(self, name: str, scope: str = "personal") -> dict:
        """Inspect the active version for a personal/current-project Skill."""

        return self._call("skills_status", [{"name": name, "scope": scope}])

    def history(
        self,
        name: str,
        scope: str = "personal",
        limit: int = 50,
    ) -> dict:
        """List immutable manifests and lifecycle events without source bytes."""

        return self._call(
            "skills_history",
            [{"name": name, "scope": scope, "limit": int(limit)}],
        )

    def rollback(self, name: str, version_id: str, scope: str = "personal") -> dict:
        """Request approval, then activate an exact retained writable version."""

        return self._call(
            "skills_rollback",
            [{"name": name, "scope": scope, "version_id": version_id}],
        )


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
        lease = self.issue(name)
        return self.redeem(lease["token"])["value"]

    def issue(
        self,
        name: str,
        *,
        purpose: str = "host credential access",
        ttl_seconds: float = 30.0,
    ) -> dict:
        return self._call(
            "credentials_issue",
            [{"name": name, "purpose": purpose, "ttl_seconds": ttl_seconds}],
        )

    def redeem(self, token: str) -> dict:
        return self._call("credentials_redeem", [token])

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

    def resources(self, server: str, *, cursor: str | None = None) -> dict:
        """Discover URI-addressed resources and an optional nextCursor."""
        return self._call(
            "mcp_resources",
            [{"server": server, "cursor": cursor}],
        )

    def read_resource(self, server: str, uri: str) -> dict:
        """Read one MCP resource as its standard ``contents`` blocks."""
        return self._call(
            "mcp_resource_read",
            [{"server": server, "uri": uri}],
        )

    def prompts(self, server: str, *, cursor: str | None = None) -> dict:
        """Discover reusable prompts and an optional nextCursor."""
        return self._call(
            "mcp_prompts",
            [{"server": server, "cursor": cursor}],
        )

    def get_prompt(
        self,
        server: str,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> dict:
        """Render one MCP prompt into its standard message blocks."""
        return self._call(
            "mcp_prompt_get",
            [{"server": server, "name": name, "arguments": arguments or {}}],
        )

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


class _Science:
    """Structured public database discovery and search.

    Results share a stable ``{id,title,url,type,attributes}`` record shape so a
    code cell can join sources without scraping provider-specific pages.
    """

    def __init__(self, host_call: Callable[[str, list], Any]):
        self._call = host_call

    def list_databases(self, domain: str = "all") -> dict:
        return self._call("science_list_dbs", [{"domain": domain}])

    def search(
        self,
        database: str,
        query: str,
        *,
        limit: int = 10,
        cursor: str | None = None,
        filters: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict:
        return self._call(
            "science_search",
            [
                {
                    "database": database,
                    "query": query,
                    "limit": int(limit),
                    "cursor": cursor,
                    "filters": filters,
                    "timeout": float(timeout),
                }
            ],
        )


# --- remote compute (host.compute) -----------------------------------
#
# Gated on the host advertising a remote-compute provider. Installs
# host.compute = namespace with one constructor (create). Two provider
# families: "ssh:<alias>" runs jobs over an SSH connection; "byoc:<id>"
# provisions a bring-your-own-compute sandbox (e.g. "byoc:nvidia").
# Every method routes through host_call("compute_<op>", [kw])
# back to the host-side dispatcher, which owns the real remote work.


class _Host:
    def __init__(
        self,
        host_call: Callable[[str, list], Any],
        denied: frozenset[str] = frozenset(),
        bash_authorizer: Callable[[str, list], Any] | None = None,
    ):
        # Wrap the raw RPC so every SDK call encodes its args for the wire
        # (snake->camel + drop-None) exactly once, transparently to accessors.
        def _encoded_call(method: str, args: list) -> Any:
            return host_call(method, encode_args(args))

        def _encoded_bash_authorizer(method: str, args: list) -> Any:
            target = bash_authorizer or host_call
            return target(method, encode_args(args))

        # `_denied` is the set of control-plane symbols this kernel was NOT
        # spliced with. Set FIRST so __getattribute__ can consult it
        # while the rest of __init__ runs.
        object.__setattr__(self, "_denied", frozenset(denied))
        self._call = _encoded_call
        self._bash = BashExecutor(
            self._call,
            authorization_call=_encoded_bash_authorizer,
        )
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
        self.science = _Science(self._call)

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
        source: Any = None,
    ) -> dict:
        """Register a workspace file as a versioned artifact. Returns {version_id,...}.

        `input_version_ids` records data lineage edges from those inputs to
        this output.

        `source` records where the data came from when the artifact was derived
        from something retrieved. Pass the `provenance` envelope a
        `host.science.search(...)` result carries: it names the database, the
        exact request, when it was fetched, and the hash of the bytes that came
        back. Without it a saved result answers "what is this" but not "when
        was this true, and was it the same data I am looking at" -- which is
        the difference between a file and evidence.
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
                    "source": source,
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
        steps: int | None = None,
        max_steps: int | None = None,
        max_turns: int | None = None,
        permissions: dict[str, str] | None = None,
        capabilities: list[str] | None = None,
        unrestricted: bool | None = None,
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
                    "steps": steps,
                    "max_steps": max_steps,
                    "max_turns": max_turns,
                    "permissions": permissions,
                    "capabilities": capabilities,
                    "unrestricted": unrestricted,
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

        completion_bullets must be 1-4 completed-action strings. English uses
        past-tense, verb-first wording; CJK verb phrases are accepted without
        English tense morphology. If output_schema is given, `output` is
        validated against it. A validation failure returns {"error":...} so
        the model can retry.
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
    # bash/read/write/edit/glob/grep/list/webfetch/websearch/todo tools. File
    # ops are confined to your working directory (the session workspace).
    def bash(
        self, command: str, *, timeout: float = 120, workdir: str | None = None
    ) -> dict:
        """Run a shell command INSIDE the kernel process. Returns
        {exit_code, stdout, stderr, workdir}. Networking is available.

        The host executes only python/R cells — shell work happens here in the
        worker, whose cwd is the session workspace and whose PATH already
        carries the active prebuilt env's bin/ (so pip/mafft/iqtree resolve).
        The static shell precheck and the egress fence still apply.
        """
        return self._bash.run(command, timeout=timeout, workdir=workdir)

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
        """Ask the user to widen the outbound domain allowlist.

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

    def review_status(self) -> dict:
        """Read evidence-review configuration and recent bounded verdicts."""
        return self._call("review_status", [])

    def remember(self, content: str, *, block: str = "general") -> dict:
        """Persist a durable fact the daemon re-injects into future sessions
        (takes effect when Memory is enabled in Customize → Memory)."""
        return self._call("remember", [{"content": content, "block": block}])


def build_host(
    host_call: Callable[[str, list], Any],
    mode: str = "repl",
    *,
    bash_authorizer: Callable[[str, list], Any] | None = None,
) -> _Host:
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
        return _Host(host_call, bash_authorizer=bash_authorizer)
    if mode in ("python", "analysis", "r", "R"):
        return _Host(
            host_call,
            denied=_ANALYSIS_DENY,
            bash_authorizer=bash_authorizer,
        )
    raise ValueError(f"build_host: unknown kernel mode {mode!r}")
