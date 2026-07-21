# Web App API Contract (as implemented)

This document records the **actual** HTTP/WebSocket contract between
`openai4s/server/gateway.py` (backend) and `openai4s/server/webui/app.js`
(frontend), including known warts and gaps. It is descriptive, not
aspirational: every claim below maps to the Gateway/frontend or to a focused
service they compose (notably the execution coordinator, session-domain,
workbench-state, and permission services). If you change that public surface,
update this document.

Scope note: this covers the **gateway** started by `openai4s serve` /
`./start.sh`. The minimal `openai4s/server/daemon.py` single-page UI and its
`/run` endpoint are a separate, smaller surface and are not documented here.

## 1. Transport and general behavior

- Server: stdlib `http.server.BaseHTTPRequestHandler`, `HTTP/1.1`
  (`protocol_version = "HTTP/1.1"`), hand-rolled WebSocket upgrade on
  `/api/v1/ws`. Default bind `127.0.0.1:8760`.
- **REST lives under `/api/v1/*` — contract v1.** The handler strips the
  `/api/v1` prefix (`_API_ROOT`) and matches the remainder (`sub`) with a long
  `if`/`re.fullmatch` chain in `Handler._api` — there is no route table or
  OpenAPI spec yet.
- **There is no un-versioned surface and no legacy alias.** Any path under
  `/api/` that is not `/api/v1/` returns `404` with a JSON body naming
  `api_root`. That is deliberate: falling through to the SPA shell would answer
  `200 text/html` to an API call, which a client reads as success and then
  fails to parse — a worse failure than a clear one. The un-versioned `/api/*`
  surface was removed outright rather than aliased, because it had no external
  consumers at the time of the cut.
- The frontend builds every request from a single `API` constant in `app.js`,
  so a future version bump is one line there plus `_API_ROOT` in the gateway.
- **Every error response is `{"error": <message>, "code": <stable>, "status":
  <int>, "request_id": <id|null>}`**, plus any route-specific diagnostic fields.
  `code` is the machine-readable contract; `error` remains the human message and
  is unchanged, so the enrichment is additive. Match on `code`, never on prose —
  the message wording is not an interface and will be improved.
  Status is too coarse to branch on alone: four distinct 400s
  (`malformed_json`, `invalid_body_type`, `invalid_cursor`, `invalid_limit`)
  need telling apart, and a client retrying `invalid_cursor` the way it retries
  a transient failure would loop on a request that can never succeed.
  `request_id` matches the `X-Request-Id` response header and the correlation id
  in the structured log line, so one id ties a user report to a server event.
- **Success bodies are not wrapped in a `{data: …}` envelope.** Considered and
  declined: it would churn every route and every consumer to relocate
  information that is already unambiguous, and a half-finished reshape presents
  as a silently broken screen rather than a failing test. What the contract
  needs from the success side is a documented, stable shape per route, which the
  route/event inventory test enforces.
- **WebSocket events carry a monotonic `seq` per root frame.** A client resumes
  with `{"type":"view_session","root_frame_id":…,"since_seq":N,"epoch":E}` and
  receives only events after `N`; `replay_begin` reports `from_seq`/`to_seq`,
  this daemon run's `epoch`, and
  `gap: true` when the capped buffer no longer reaches back to `N+1`, so a
  client that was away too long can refetch state instead of resuming into a
  hole it cannot detect. `since_seq` absent or `0` replays the whole buffer.
- A cursor is only meaningful inside the daemon run that issued it. The
  sequence counter lives in the process, so a restart puts it back to zero
  while the client still holds a cursor from the previous run — which used to
  produce no replay frames at all and left the client believing it was caught
  up on a stream it had entirely missed. The server now declares `gap: true`
  in that case, detecting it either from a mismatched `epoch` or, for a client
  that sends none, from its own counter sitting below the presented cursor.
  The client stores the `epoch`, drops every cursor when it changes, and
  refetches the session on `gap: true`. A cursor the server cannot place
  replays *nothing* — the client is about to refetch, so replaying the buffer
  from the start would render events that are immediately discarded, and a
  fabricated cursor must never wrap around into a full replay.
  The counter does not reset between turns — a per-turn counter would make a
  stale cursor look already-satisfied and skip the new turn's first events.
  Only `broadcast` events are sequenced; point-to-point snapshots delivered on
  subscribe (`execution_queue`, pending approval cards) and the replay control
  frames deliberately carry no `seq`.
- The frontend is a single-page app served from the working tree
  (`/`, `/index.html`, `/static/*`). Any unknown non-API `GET` serves the SPA
  shell (`index.html`) to support deep links. Unknown non-GET, non-API paths
  return `404 {"error": "not found"}`.
- All JSON responses are `application/json; charset=utf-8` with
  `Cache-Control: no-cache` and an explicit `Content-Length`.
- Request bodies are JSON except the explicitly documented Session-package
  import route, which consumes raw ZIP bytes. `Handler._body()` accepts an
  empty body, but an unparsable one is rejected with `400 malformed_json`, and
  a body that parses to something other than an object with
  `400 invalid_body_type`. Neither is silently coerced to `{}`.
- Query strings are parsed with `parse_qs` (every value is a list;
  handlers read `q.get("x", [default])[0]`).

### Authentication and CSRF

- **CSRF/origin guard:** every mutating request (`POST`/`PUT`/`PATCH`/`DELETE`)
  to `/api/v1/*` whose `Origin` header is present and whose netloc differs from
  the `Host` header is rejected with `403 {"error": "cross-origin request
  refused"}`. Requests without an `Origin` header (curl, same-origin fetches)
  pass.
- **Token gate** (only active when bound to a non-loopback address or
  `OPENAI4S_REQUIRE_TOKEN=1`): all paths except `/health` require either the
  `os_token` cookie or `?token=<hex>`. A `GET` carrying a valid `?token=`
  responds `303 Location: /` and sets the cookie; a valid non-GET proceeds.
  Anything else gets `401 {"error": "unauthorized — append ?token=… to the
  URL"}`. On the default loopback bind there is **no authentication at all**.

### Error envelope

- The backend error shape is always **`{"error": "<message>"}`** with an HTTP
  status code: raised `GatewayError(code, message)` → `{"error": message}`
  with that code; any unhandled exception → `500 {"error": str(e)}`; the
  `_api` catch-all → `404 {"error": "not found", "path": sub, "method": …}`.
- The frontend `api()` helper reads `j.error || j.detail`, so the Gateway's
  error text is shown. `detail` remains accepted for compatibility with
  external adapters.
- Some handlers return errors **inside a 200 body** instead of an error
  status: `POST /api/connectors/{id}/call` returns `{"error": str(e)}` with
  HTTP 200 on exception, and `POST
  /api/artifacts/{aid}/versions/{vid}/restore` maps a soft
  `{"error": …}` result to 404 but other handlers pass soft errors through as
  200. Do not assume "2xx ⇒ no `error` key".

### JSON routes vs raw-bytes routes

Most routes return JSON. The exceptions return **raw bytes** with a guessed
or stored `Content-Type`:

| Route | Body | Notes |
| --- | --- | --- |
| `GET /` , `GET /index.html`, unknown non-API GET | HTML | SPA shell from `webui/index.html`. |
| `GET /static/<rel>` | file bytes | Path-traversal-guarded; 404/403 as JSON. |
| `GET /api/artifacts/{ident}` | artifact bytes | `ident` may be a **version_id, artifact_id, or filename** (in that resolution order: `store.resolve_artifact_path` tries `artifact_versions.version_id` first, then `artifacts.artifact_id` → its latest version; the handler falls back to a filename lookup). `Content-Type` comes from stored metadata, else guessed from the filename. |
| `GET /api/frames/{fid}/artifacts.zip` | ZIP bytes | Current Artifact versions for one session. |
| `GET /api/projects/{pid}/artifacts.zip` | ZIP bytes | Current Artifact versions across one project. |
| `GET /api/frames/{fid}/notebook/export?language=` | `.ipynb` or ZIP bytes | `python`/`r` returns one Notebook; omitted/`bundle` returns both plus a manifest. |
| `GET /api/frames/{fid}/session/export` | Session ZIP bytes | Deterministic `application/vnd.openai4s.session+zip`; carries schema and SHA-256 headers. |
| `GET /preview/{ident}` | artifact bytes | Same resolution, but `Content-Type` is **forced** to `text/html; charset=utf-8` (sandboxed iframe preview). Not under `/api`. |
| `GET /ketcher` | HTML | Static placeholder page. |

**Wart:** when a raw-bytes route fails (artifact missing) it responds with a
*JSON* body `404 {"error": "artifact not found"}` — a consumer streaming the
response to disk gets a JSON document.

Note the overlap on `GET /api/artifacts/…`: the specific matchers
(`/lineage`, `/environment`, `/versions`, …) are tried first; the final
`re.fullmatch(r"/artifacts/(.+)")` + GET catch-all serves bytes, and because
it matches `.+` (slashes included) it also catches any otherwise-unmatched
GET under `/api/v1/artifacts/`.

## 2. REST routes

All paths below are under `/api` unless stated otherwise. "→" describes the
success response body. Serializer shapes are in §4.

### Identity / config / meta

| Method & path | Behavior |
| --- | --- |
| `GET /health` (not under `/api`) | Minimal public projection `{"status":"ok","model"}`. Exempt from the token gate and deliberately omits host filesystem paths. |
| `GET /me` | Hardcoded local identity: `{"user_id":"local-dev","email":null,"provider","has_api_key","shared_api_key":false,"auth_mode":"none"}`. |
| `GET /auth/status` | `{"authenticated":true,"auth_mode":"none"}` (always). |
| `GET /csrf` | `{"csrf_token":"local"}` (a stub; the real CSRF defense is the Origin check). |
| `GET|POST|PUT|PATCH /config/llm` | GET → `{provider,model,base_url,has_api_key}`. Write → persists `provider`/`model`/`base_url`; `api_key` only overwrites when non-empty; `clear_api_key:true` empties it → `{"ok":true,"has_api_key"}`. The raw key is never returned. |
| `GET /search?q=` | `{sessions:[{id,project_id,name,task_summary}], artifacts:[{id,filename,content_type,root_frame_id,project_id}]}`; empty `q` → empty lists. |
| `GET /` (i.e. `/api` or `/api/v1/`) | `{"service":"openai4s","ok":true}`. |

### Models and model profiles

| Method & path | Behavior |
| --- | --- |
| `GET /models` | `{"models":{"default":[{id,name,description}…]},"default_model_id"}` — the live model first, then the saved profiles' models, deduped. Built-in provider defaults are not listed: an endpoint the user never configured must not be selectable. A profile that leaves `model` blank is resolved through its protocol's default. |
| `GET /models/default` | `{"default_model_id"}`. |
| `POST /models/default` (any non-GET) | Body `{model_id}` → persists as `llm_model` setting → `{"default_model_id"}`. |
| `GET /model-endpoints/discover?force=1` | Explicitly probes the fixed loopback catalogue for Ollama, LM Studio, vLLM, and llama.cpp, with environment proxies disabled. Returns sanitized profile suggestions plus `mutated_settings:false`; it never accepts a caller-supplied URL and never creates or activates a profile. `force=1` bypasses the short in-process cache. A discovered endpoint is keyless, but vendor capabilities are not inferred: until an explicit override exists it uses conservative Code-as-Action (no inherited vision/tool/schema claim). |
| `GET /model-profiles` | Returns only user-saved profiles as `{"profiles":[masked…],"active_id","protocols":["chatgpt","claude","ark"]}`; no default endpoints are seeded. A one-time migration removes entries matching the preset identities generated by older releases. Profiles are **masked**: `{id,name,provider,base_url,model,has_api_key}` — the API key is never echoed. |
| `POST /model-profiles` | Body `{name,provider,base_url?,model?,api_key?}` where `provider` selects the `chatgpt` (OpenAI-compatible), `claude` (Anthropic-compatible), or `ark` protocol; missing `name` or an unsupported protocol → `400 {"error":…}`; success → `201` masked profile. |
| `POST /model-profiles/{id}/activate` | Copies the profile's fields into the live `llm_*` settings, moves it to the front of the list → `{"ok":true,"active_id","has_api_key"}`; unknown id → 404. |
| `PUT|PATCH /model-profiles/{id}` | Partial edit; `api_key` only overwrites when non-empty; `clear_api_key:true` clears. Editing the active profile also syncs the live settings → masked profile; unknown id → 404. |
| `DELETE /model-profiles/{id}` | Removes it (clears `active_model_profile` if it was active) → `{"ok":true}`. Deleting a nonexistent id still returns `{"ok":true}`. |

### Projects, notes, folders

| Method & path | Behavior |
| --- | --- |
| `GET /projects` | `{"projects":[project…],"total":n}`. **No pagination:** the frontend sends `?limit=100&offset=0` but the handler ignores both parameters and always returns *all* projects; `total` is just `len(projects)`. Do not document or rely on offset semantics — they do not exist. |
| `POST /projects` | Body `{name?,description?,context?}` → project JSON (with `conversation_count: 0`). |
| `GET /projects/{pid}` | Project JSON, or `{}` when not found (**not** a 404). |
| `GET /projects/{pid}/action-timeline?limit=` | Bounded cross-session safe Timeline projection with session labels. |
| `GET /projects/{pid}/lineage?limit=` | Project-wide Artifact/version lineage graph with bounded nodes/edges. |
| `PUT|PATCH /projects/{pid}` | Updates `name`/`description`/`context` → project JSON. |
| `DELETE /projects/{pid}` | Deletes project + frames, unlinks artifact files and session workspaces → `{"ok":true,"freed_files","freed_sessions"}`. |
| `GET /projects/{pid}/notes` | `{"notes":[note…]}`. |
| `POST /projects/{pid}/notes` | Body `{content}` → note JSON. |
| `DELETE /notes/{note_id}` | `{"ok":true}`. |
| `GET /projects/{pid}/folders` | `{"folders":[…]}`. |
| `POST /projects/{pid}/folders` | Body `{name}` → folder row. |
| `PUT|PATCH /folders/{fid}` | Rename → `{"ok":true}`. |
| `DELETE /folders/{fid}` | `{"ok":true}`. |
| `POST|PUT|PATCH /frames/{fid}/folder` | Body `{folder_id}` (or null) → `{"ok":true}`. |

### Frames (sessions) and turns

| Method & path | Behavior |
| --- | --- |
| `GET /frames?project_id=&limit=&cursor=` | `{"frames":[…],"next_cursor":…,"has_more":bool}`. Keyset pagination, newest first; `limit` 1–200 (default 100). `cursor` is opaque — parsing it would couple a client to the sort key. An unreadable cursor is a `400`, never a silent restart, which would loop a client on page one. `has_more` is observed by collecting one row beyond the page, not inferred from the page being full: hidden abandoned sessions are filtered *after* the read, so a full-looking page is not evidence of a next one. |
| `POST /frames` | Body `{project_id?,model?}` → frame JSON for a new root frame. |
| `GET /frames/{fid}` | Frame JSON, or `{}` when not found. |
| `PATCH /frames/{fid}` | Updates `name`/`task_summary`, broadcasts `frame_update` → frame JSON. |
| `DELETE /frames/{fid}` | `{"ok":true}`. |
| `GET /frames/{fid}/messages?from=&limit=&branch_id=` | Branch-projected `{"messages":[{message_id,role,content,created_at,fork_checkpoint_id}…]}`. Omitted `branch_id` selects the durable active branch; its inherited prefix and post-Revert continuation are included, while sibling/abandoned rows remain only in the audit source. `from` (default 0) and `limit` (default 300) are real slice parameters. |
| `GET /frames/{fid}/steps` | `{"steps":[…]}` (persisted semantic steps). |
| `POST /frames/{fid}/message` | Starts a turn. Body `{request}` (or `{input_data:{request}}`), optional `model`, `plan`, `explore`, `annotation_ids`. With `wait:false` → `202 {"status":"accepted","frame_id","job_id","execution_id","owner":{"kind","id"},"queue_position"}`; default (`wait` omitted/true) blocks for the turn result. A valid sole `finalize_response` is an Engine completion (even if an earlier step ran a Cell); `host.submit_output(...)` is the only completion emitted from inside a Python Cell. Ordinary prose/results and max-turn exhaustion are not success. |
| `GET /frames/{fid}/execution` | Authoritative FIFO snapshot: `{root_frame_id,owner,queue,queued_count,active_count,closed,close_reason}`. Owner/queue entries include `execution_id`, `{kind,id}` owner, status, position, branch/language/generation and resource keys when known. |
| `POST /frames/{fid}/cancel` | Scoped cancellation. Body `{execution_id,owner:{kind,id}}` (or `owner_kind` + `owner_id`) and optional `reason` → `{ok,execution_id,owner,scope,…}`. Missing identity returns HTTP 400 with `error`; stale/mismatched identity returns `ok:false`. A queued cancellation does not affect the active owner. |
| `GET /frames/{fid}/status` | `{"frame_id","running",kernel:{…kernel status…}}`. |
| `POST /frames/{fid}/feedback` | Body `{key,rating}` → `{"ok":true}`. |
| `GET /frames/{fid}/feedback` | `{"feedback":[…]}`. |
| `GET /frames/{fid}/session/export` | Raw deterministic Session-package ZIP with `X-Content-SHA256` and `X-OpenAI4S-Session-Schema`. It contains branch-owned messages, complete sanitized provider groups/wire state, Notebook and Artifact/lineage records, Revert cursors, evidence reviews and checkpoint plan/review/memory snapshots; secret material is rejected. |
| `POST /sessions/import` | Raw Session ZIP body (not JSON, maximum archive 128 MiB) → HTTP 201 with new `{project_id,root_frame_id,active_branch_id,kernel_state:"ended",view_only:true,trust_state:"quarantined",explicit_recovery_required:true,…}`. The entire archive is preflighted as untrusted input, all identities are remapped, permissions are downgraded, review automation is disabled, and no Kernel/hook/package code starts. The quarantine is durable: frame-scoped mutations return HTTP 423 until the user calls `POST /frames/{fid}/recovery/actions/restart_fresh` with `{"confirm":true}`; read/export/delete remain available. |

### Plan mode

| Method & path | Behavior |
| --- | --- |
| `GET /frames/{fid}/plan` | `{"frame_id","plan_id","status","plan"}` (nulls when no plan). |
| `POST /frames/{fid}/plan/approve` | `202 {"status":"accepted","frame_id","job_id"}` — auto-execution runs in the background. |
| `POST /frames/{fid}/plan/revise` | Body `{changes}` (or `{feedback}`); empty → `400 {"error":"changes required"}`; else `202` accepted. |
| `POST /frames/{fid}/plan/discard` | Result of `runner.discard_plan` (synchronous). |

### Permissions

| Method & path | Behavior |
| --- | --- |
| `POST /frames/{fid}/decision` | Answers a pending `await_permission` prompt. Body `{decision_id,allow,scope?("once"),pattern?,message?}`. A live decision returns `{ok,decision_id,allow,scope,resolution_context:"live_thread",requires_continue:false,original_action_executed:null}` and wakes the exact blocked call. After daemon restart it returns `resolution_context:"after_restart"`, `original_action_executed:false`, and `requires_continue:true` for an approval; no stored arguments are replayed. A restart `once` approval also returns its exact-grant `continuation_expires_at` and `continuation_authorization`; broader scopes persist a standing rule. Unknown, cross-frame, conflicting, or expired decisions return `ok:false` with `error`. |
| `GET /frames/{fid}/permissions` | `{"root_frame_id","project_id","rules":[…]}` — rules effective for that conversation. |
| `POST /permissions` | Upsert a rule. Body `{scope("global"),scope_id?,frame_id?,tool("*"),pattern("*"),decision("ask")}`; when `scope_id` is omitted but `frame_id` given, the scope id is derived from the frame → `{"ok":true,"rule_id"}`. |
| `POST /permissions/reset` | Re-seeds defaults → `{"ok":true,"rules":[…]}`. |
| `DELETE /permissions/{rule_id}` | `{"ok":true}`. |

### Image annotations (figure review)

| Method & path | Behavior |
| --- | --- |
| `GET /frames/{fid}/annotations?artifact_id=` | `{"annotations":[annotation…]}`. |
| `POST /frames/{fid}/annotations` | Body `{artifact_id,body` (or `text`)`,artifact_name?,x?,y?}` (`x`/`y` are 0–1 fractions; `rel_x`/`rel_y` accepted as aliases). Missing artifact_id/body → 400 → else `201 {"annotation":…}`. |
| `PATCH|POST|PUT /annotations/{aid}` | Body `{body?,status?}` → `{"annotation":…}` or `404 {"annotation":null}`. |
| `DELETE /annotations/{aid}` | `{"ok":true}`. |

### Kernel / notebook (per-session)

Kernel status and execution-log reads are lazy: they never start Python or R.
The first Agent/user Cell starts only the selected language; a native-tool or
`FinalizeAction`-only turn can complete with no kernel process.

| Method & path | Behavior |
| --- | --- |
| `GET /frames/{fid}/execution-log` | `{"kernels":[id…],"entries":[cell…]}`; entries include stable `producing_cell_id`, `cell_index`, session-monotonic `state_revision`, attempt-derived `generation_id` (nullable for legacy rows or when no worker was acquired), `kernel_id`, `language`, `origin`, source/output/error, files/figures, usage, and immutable retry metadata when recorded. |
| `POST /frames/{fid}/kernel/execute` | Body `{code,language?,execution_id?,wait?}` where language is `python` (default) or `r`; the shipped UI supplies a portable execution ID. Default/`wait:false` returns HTTP 202 `{status:"accepted",job_id,execution_id,owner,queue_position}` immediately, so a queued cell remains addressable. `wait:true` blocks for the completed FIFO-owned Cell result. Execution always appends and never edits history. |
| `POST /frames/{fid}/kernel/restart` | → `{"ok":true,"status":"restarted","generation","generation_id","frame_id"}` + `kernel_status` WS event. |
| `POST /frames/{fid}/kernel/stop` | → `{"ok":true,"state":"stopped"|"none","frame_id"}`. |
| `POST /frames/{fid}/kernel/start` | → `{"ok":true,"state":"running","generation","frame_id",…}`. |
| `POST /frames/{fid}/kernel/interrupt` | Exact ticket stop. Body `{execution_id,owner:{kind,id}}` (or owner aliases) identifies one ticket: a queued ticket is cancelled without touching the active writer; an active ticket requests a signal only for its frozen lease. The result's `interrupted` flag says whether a lease was actually signalled. Missing identity returns HTTP 400; stale/wrong-owner requests return `ok:false`. The shipped Notebook Stop control selects only `user_repl` tickets. |
| `GET /frames/{fid}/kernel` | Kernel status: `{frame_id,state("none"|"running"|"stopped"|"ended"),alive,generation,generation_id,generation_ordinal,last_activity_at,ended_reason,turn_running,cell_count,manual_stop,repl_enabled,env:{name,language,python_version,pending,kernel_id}}`. `repl_enabled` mirrors `OPENAI4S_NOTEBOOK_REPL`. |
| `POST /frames/{fid}/kernel/install` | Body `{packages:[…]}` or `{package}` (+`restart`, default true) → pip-install report (`{ok,installed,…,restarted}`). |
| `GET /frames/{fid}/environments` | `{"environments":[…],"current","default","pending"}`. |
| `POST /frames/{fid}/kernel/env` | Body `{env}` (or `{name}`) — switches the kernel to a prebuilt env (restart) → `{"ok":true,"state","env","generation","language","python_version","frame_id"}`. |

**Notebook REPL gate:** the Notebook is a **read-only execution trace** by
default. The mutating `kernel/*` routes — `execute`, `env`, `restart`, `stop`,
`start`, `interrupt` — return `403 {"error":…}` unless
`OPENAI4S_NOTEBOOK_REPL` is set. `kernel/install` is intentionally not gated:
it backs Customize → Compute rather than arbitrary Notebook execution. The
read-only `GET /frames/{fid}/kernel` and `GET /frames/{fid}/execution-log` stay available.
`GET /frames/{fid}/kernel` reports the current state in `repl_enabled`. When
enabled, the shipped UI provides multiline Python/R input and Shift+Enter;
every submission appends a Cell through the same FIFO coordinator as Agent and
lifecycle work.

**`kernel_id` runtime segment:** the `kernel_id` returned by the kernel and
execution-log routes now carries the runtime segment — `python` for the
default env, `python — struct` / `python — phylo` etc. when the agent has
switched conda env — so per-cell rows label which environment they ran under.
`state_revision` currently reuses the durable session Cell ordinal. It is a
state-change cursor used for stale/read-only UI labeling, not serialized
variable state and not evidence that an older in-memory namespace is
recoverable. `generation_id` is the UUID bound to the execution attempt rather
than a value reconstructed from this display label.

### Scientific session workbench

These routes are thin Gateway adapters over `SessionDomainService` and
`SessionWorkbenchStateService`:

| Method & path | Behavior |
|---|---|
| `GET /frames/{fid}/action-timeline?branch_id=&before_ordinal=&after_ordinal=&limit=` | Researcher-facing Action Ledger projection. `limit` defaults to 500 and must be 1–500. Without a cursor it returns the latest window; `before_ordinal` moves older and `after_ordinal` moves newer. Cursors must be non-negative and mutually exclusive (invalid values → 400). Fields are bounded/redacted and raw arguments/provider wire state are omitted. Canonical usage is included; `cost` is non-null only when explicit deployment price metadata was recorded. Response metadata includes `count`, `total_count`, `truncated`, `has_earlier`, `has_more`, `first_ordinal`, and `last_ordinal`. |
| `GET /frames/{fid}/execution-queue` | Alias of the authoritative execution snapshot (`/execution`). |
| `GET /frames/{fid}/context` | Safe token-composition projection: totals/limit, message count, handoff/compaction state, and text/image/tool/wire token layers; no message content. |
| `GET /frames/{fid}/security` | Aggregate sandbox self-test projection plus per-language `sandbox.runtimes[]`, durable-permission pending count, and Notebook interactive flag. Python-only and R-only sessions report the worker that actually ran; before either worker starts, state is truthfully `not_started`, not inferred. |
| `GET /frames/{fid}/delegations` | Safe durable child-agent tree, shared spawn budget, progress/terminal status, enforced override summary, and steering delivery counters. Result/output bodies and steering text are excluded from the browser projection. |
| `GET /frames/{fid}/branches` | Branch tree plus checkpoints and capability descriptors. A GET does not create the initial branch/checkpoint. |
| `GET|POST /frames/{fid}/checkpoints` | List or create immutable checkpoints. `/branches/checkpoints` is an alias. POST accepts `branch_id`, `reason`, `expected_head`. |
| `POST /frames/{fid}/branches/fork` | Body must select exactly one of `from_checkpoint_id`, `from_cell_id`, or `from_message_id`; optional `name`. Cell/message sources resolve only through an exact boundary checkpoint in this root session. Old history without one returns 409. The new branch has an independent workspace and remains inactive/view-only. |
| `POST /frames/{fid}/branches/{branch_id}/activate` | Exact FIFO lifecycle mutation. Stops the old branch runtime, atomically selects the requested branch/checkpoint side-state, and returns `status: active|partial|failed` plus per-dimension apply/recovery details. It never mutates the old branch history. |
| `POST /frames/{fid}/revert/preview` | Body `{target_checkpoint_id,branch_id?}` → `{preview}` including workspace/message/action/Notebook/artifact/env/permission differences and conflicts. `/branches/revert-preview` is an alias. |
| `POST /frames/{fid}/revert/apply` | Conflict-checked append-only revert, invalidates live kernels, returns 409 when it cannot safely apply. `/branches/revert` is an alias. |
| `POST /frames/{fid}/revert/undo` | Body `{revert_checkpoint_id,branch_id?}` — reverts to the recorded pre-revert checkpoint. |
| `GET /frames/{fid}/revert/operations` | Durable revert operation history. |
| `GET /frames/{fid}/recovery` | Safe Recovery Journal status projection. |
| `GET /frames/{fid}/recovery/actions` | Describes enabled/disabled reasons for `restore`, `retry`, and `restart_fresh` on the current root branch. |
| `POST /frames/{fid}/recovery/actions/{restore\|retry\|restart_fresh}` | Runs the advertised verified-recovery action under an exact recovery execution ticket. `restart_fresh` requires `{"confirm":true}` and never claims namespace restoration. |
| `GET /frames/{fid}/kernel/variables?language=python|r` | Bounded idle-only Variable Inspector projection. It never starts a stopped language worker and returns explicit Busy/Restoring/Ended/Not Started states. |
| `GET /frames/{fid}/notebook/export?language=` | Raw deterministic `.ipynb` for `python`/`r`; omitted or `bundle` returns a stable ZIP containing both plus a manifest. Includes `Content-Disposition` and `X-Content-SHA256`. |
| `GET /frames/{fid}/session/export` | Raw deterministic, manifest-hashed Session package. |
| `GET /renderers` | Safe scientific renderer descriptor catalog. |
| `GET /artifacts/{aid}/renderer?version=&root_frame_id=` | Selects a version-bound renderer descriptor plus immutable checksum/size/provenance metadata; it never executes Artifact content. |

The Timeline UI requests the latest 500 records first. When `has_earlier` is
true it exposes an explicit control that requests
`before_ordinal=<first_ordinal>&limit=500`, merges by durable group identity,
and keeps a maximum of 2,000 records without dropping the latest window.

The Notebook header and provenance execution view link the bundle form of the
Notebook export route. Language-specific Python/R files remain directly
available through the query parameter.

### Artifacts

| Method & path | Behavior |
| --- | --- |
| `GET /frames/{fid}/artifacts` | **Bare array** of artifact JSON. |
| `GET /projects/{pid}/artifacts` | **Bare array** — every artifact across the project's conversations. |
| `GET /frames/{fid}/artifacts.zip` | Raw ZIP of the session's current Artifact versions. |
| `GET /projects/{pid}/artifacts.zip` | Raw ZIP of current Artifact versions across the project. |
| `GET /artifacts/{aid}/lineage` | `{"artifact_id","filename","interactions":[{kind:"cell",…}|{kind:"save",at}],"dependency_mappings":{"inputs":[…]}}`. Unknown artifact → the same shape with nulls/empties, HTTP 200 (**not** 404). |
| `GET /artifacts/{aid}/environment?version=` | Env snapshot captured for the producing run, `{"source":"captured",…}`; falls back to a live freeze `{"source":"live",…}` when none was recorded. |
| `POST|PUT|PATCH /artifacts/{aid}/priority` | Body `{priority:int}` → `{"ok":true,"artifact":…|null}`. |
| `GET /artifacts/{aid}/versions` | `{"versions":[{version_id,ordinal,is_latest,size_bytes,content_type,checksum?,producing_cell_id?,created_at}…]}`. |
| `POST /artifacts/{aid}/versions/{vid}/restore` | Reverts the live file + latest pointer → `{"ok":true,"artifact":…}` or `404 {"error":…}`; broadcasts a *bare* `artifact_created` (see §3). |
| `POST|PUT|PATCH /artifacts/{aid}/edit` | Body `{content}` (text). Non-text artifact → `415`; unknown → `404` (both via `GatewayError`) → `{"ok":true,"artifact_id","version_id","size_bytes"}`. |
| `POST|PUT|PATCH /artifacts/{aid}/rename` | Body `{filename}`; missing → `400`; unknown → `404` → `{"ok":true,"artifact_id","filename"}`. |
| `DELETE /artifacts/{aid}` | Deletes rows + snapshot files → `{"ok":true}`; broadcasts a *bare* `artifact_created`. |
| `GET /artifacts/{ident}` | **Raw bytes** (see §1). |
| `POST /uploads` | **Base64 JSON upload — not multipart.** Body `{filename?,content_base64` (or `content`)`,frame_id?,project_id?}`. Invalid base64 does not error (wart, two-tier): decoding uses `base64.b64decode` without `validate=True`, so **non-alphabet characters are silently discarded** before decoding; only when the result still has a bad length/padding (`binascii.Error`/`ValueError`) does it fall back to storing the raw string's UTF-8 bytes as-is. File lands in the session workspace (or `data_dir/uploads` without `frame_id`), is registered as a versioned artifact (`is_user_upload`), re-upload of the same name in the same frame creates a new version → `{"artifact_id","id","filename"}`. |

### Skills / agents / specialists / connectors

| Method & path | Behavior |
| --- | --- |
| `GET /skills/catalog` | `{"skills":[{…,enabled}…]}`. |
| `PUT|PATCH /skills/catalog/{name}/enabled` | Body `{enabled}` → `{"ok":true}`. Skill enablement is persisted through scoped capability state and is enforced by discovery/prompt/runtime loading. |
| `POST /skills` | Create a Web-authored `user` Skill under `<data_dir>/user-skills`: `{name,description?,body|content}`. Bundled-name collisions and unsafe paths are rejected. |
| `POST /skills/import` | Accepts a raw `SKILL.md` in `content` (frontmatter parsed) or explicit fields, then writes a normalized `user` document; imported frontmatter cannot claim bundled trust. |
| `GET|PUT|PATCH|DELETE /skills/{name}` | Read / update / delete a user Skill (URL-encoded name). Bundled `openai4s` Skills remain non-editable/non-deletable. |
| `GET /skills/{name}/versions` | Personal immutable version/event history plus safe active manifest; never returns stored source bytes. |
| `POST /skills/{name}/rollback` | Body `{version_id}` atomically activates a retained personal version. |
| `GET /projects/{project_id}/skills/catalog` | Project-owned Skill overlays only; personal fallbacks and bundled entries are omitted. |
| `GET /projects/{project_id}/skills/{name}/versions` | Exact project-scoped immutable history. Unknown projects fail closed. |
| `POST /projects/{project_id}/skills/{name}/rollback` | Body `{version_id}` activates a retained version in that project only. |
| `GET /agents` | Bare array of built-in agent descriptors (with `enabled`). |
| `PUT|PATCH /agents/{name}/enabled` | `{"ok":true}`. This legacy built-in-agent roster toggle remains process-local; persisted Specialist capability policy is enforced in delegation separately. |
| `GET /agents/{name}` | Agent descriptor or `404 {"error":"unknown agent"}`. |
| `GET /specialists` | `{"builtin":[…],"specialists":[…]}`. |
| `POST /specialists` | Upsert by `name` (400 when missing) → agent row. |
| `GET|PUT|PATCH|DELETE /specialists/{name}` | CRUD; GET 404s with `{"error":"not found"}`. |
| `GET /connectors` | `{"connectors":[…]}` (MCP servers). |
| `POST /connectors` | `{name,command}` required (400) → connector row. |
| `GET /connectors/directory` | `{"directory":[…]}` — the curated install list. |
| `PUT|PATCH /connectors/{id}/enabled` | `{"ok":true}`. |
| `POST /connectors/{id}/probe` | Spawns the server, lists tools; unknown id → 404. |
| `POST /connectors/{id}/call` | Body `{tool,args}` → tool result; **exceptions are returned as `{"error":…}` with HTTP 200**. |
| `DELETE /connectors/{id}` | Disconnect + delete → `{"ok":true}`. |

### Session sharing (`shares`)

Read-only session sharing over an outbound relay tunnel. The full protocol,
trust model, and operator controls are in [`webshare.md`](webshare.md); this is
the route index so the surface is discoverable from one place.

| Method & path | Behavior |
| --- | --- |
| `GET /shares` | List this daemon's read-only session shares (`shares.list_all()`). |
| `POST /frames/{id}/shares` | Create a share for a session (a `frames`-family route). `403` when sharing is disabled or the relay is unconfigured. |
| `PUT /shares/{id}` | Publish or update a share (optional TTL); ensures the tunnel. Unknown id → `404`. |
| `DELETE /shares/{id}` | Revoke a share and unregister it from the relay (`shares.revoke()`). |

### Compute / environments / kernel packages

| Method & path | Behavior |
| --- | --- |
| `GET /compute/gpu` | Local GPU detection report. |
| `GET /compute/ssh-aliases` | `{"aliases":[…]}` from `~/.ssh/config`. |
| `GET /compute/remote` | Registered remote-host info. |
| `POST /compute/remote` | Body `{alias,label?}`; alias must exist in `~/.ssh/config` (400 otherwise); probes GPUs over SSH → `{"ok":true,"alias",…,"info"}`. |
| `DELETE /compute/remote/{alias}` | `{"ok":bool}`. |
| `GET /compute/providers` | `{"providers":[…]}`. |
| `GET /compute/local/hostinfo` | Host info snapshot. |
| `GET /compute/jobs` | `{"jobs":[…]}`. |
| `POST /compute/jobs` | Body `{command|code,kind("bash"),cwd?}` → job row. **Local code-exec endpoint** — protected only by the Origin check + loopback bind. |
| `POST /compute/jobs/{id}/cancel` | Cancel result. |
| `GET /compute/jobs/{id}` | Job row. |
| `GET /environments/status` | `{"environments":[{language,status,python_version,package_count,packages,preinstall}]}`. |
| `GET /environments` | Same shape as `GET /frames/{fid}/environments`, without a session. |
| `GET /kernel/packages` | `{"packages":[…],"preinstall":{…}}`. |
| `GET /kernel/environment` | Full env freeze for Provenance → Environment. |
| `POST /kernel/install` | Body `{packages}` or `{package}` → install report (no kernel restart). |

### Memory / network / web-search config

| Method & path | Behavior |
| --- | --- |
| `GET /memory/enabled` | `{"enabled":bool,"override":null}`. |
| `PUT|PATCH|POST /memory/enabled` | Body `{enabled}` → `{"enabled"}`. |
| `GET /memory?project_id=` | `{"enabled","memories":[…]}` (`project_id` defaults to `all`). |
| `POST /memory` | Body `{content,block?("general"),project_id?}` → memory row. |
| `GET /memory/categories?project_id=` | `{"categories":[…]}`. |
| `GET /memory/context?project_id=` | `{"context":"- …\n- …"}`. |
| `DELETE /memory/{id}` | `{"ok":true}`. |
| `GET|PUT|PATCH|POST /network/status` | Write toggles `OPENAI4S_ALLOW_NETWORK` (process env + setting); always returns `{"enabled":bool}`. |
| `GET /preferences/builtin-allowlist` | `{"enabled","egress_mode","granted":[domains],"groups"}`. |
| `GET|PUT|PATCH|POST /search/config` | Tavily key config; write accepts `{api_key}` or `{clear_api_key}`; always returns `{"endpoint":"https://api.tavily.com/search","api_key_configured":bool}` — the key itself is never echoed. |

## 3. WebSocket contract (`/api/v1/ws`)

Standard RFC-6455 upgrade (hand-rolled: `Sec-WebSocket-Accept` computed, no
extensions/subprotocols). Messages both ways are JSON text frames. Protocol
`ping` frames (opcode 0x9) are answered with `pong` frames; a JSON
`{"type":"ping"}` is answered with `{"type":"pong"}` (the frontend sends the
JSON form every 25 s).

### Client → server messages

| Message | Effect |
| --- | --- |
| `{"type":"ping"}` | → `{"type":"pong"}`. |
| `{"type":"view_session","root_frame_id":fid}` | Subscribes this connection to `fid`'s events. If a turn is in flight, the buffered current-turn events are replayed (`replay_begin` … events … `replay_end`); any pending `await_permission` prompts are re-sent from durable storage even when no session runtime has been rebuilt after restart. `frame_id` is accepted as an alias. |
| `{"type":"unview_session","root_frame_id":fid}` | Unsubscribes. |
| `{"type":"cancel_execution","root_frame_id":fid,"execution_id", "owner":kind,"owner_id":id}` | Requests exact-ticket cancellation and receives `execution_cancel_result`. `cancel` is accepted as a compatibility type, but missing/stale/mismatched identity fails closed. |

Events are only delivered to connections subscribed to the event's
`root_frame_id` (broadcasts with `root_frame_id=None` go to everyone, but the
gateway does not currently emit any).

### Server → client events

Every event has `type` and (via the hub emitter) a `root_frame_id`; most also
carry a redundant `frame_id`. The frontend keys off `m.root_frame_id ||
m.frame_id`.

| Event `type` | Fields (beyond `root_frame_id`) | Meaning |
| --- | --- | --- |
| `notebook_cell_draft` | `frame_id`, `draft_id`, `revision`, `source`, `status`, `reason` | A Notebook cell the agent is composing, before it runs. Superseded revisions are collapsed in the resume buffer so a reconnect renders only the newest. Emitted by `server/agent_run.py`. |
| `recovery_state` | `branch_id`, `recovery_id`, `state`, `status`, `message` | A kernel-recovery attempt changing state. Emitted by `server/recovery_execution.py`. |
| `recovery_log` | `branch_id`, `recovery_id`, plus the journal entry's own fields | One line of a recovery's journal, as it happens. Emitted by `server/recovery_control.py`. |
| `branch_activated` | `branch_id`, `checkpoint_id`, `ok` | A branch became the session's active one, and its runtime state was reconstructed. Emitted by `server/session_domain.py`. |
| `cursor_checkpoint_failed` | `branch_id`, `source_kind`, `source_id`, `reason`, `ok: false` | A cell or message completed but its cursor checkpoint could not be captured — so forking from that point will 409 rather than reconstruct state it does not have. Emitted by `server/session_domain.py`. |
| `delegation_child_event` | `event`, `at`, `child` (a snapshot), plus per-event extras | A sub-agent started, progressed, or finished. Carries no `frame_id` of its own; the hub's emitter attaches `root_frame_id`. Emitted by `agent/delegation.py`. |
| `replay_begin` / `replay_end` | — | Bracket the buffered-event replay after `view_session` mid-turn. `replay_begin` carries `from_seq`, `to_seq`, the daemon run's `epoch`, and `gap`. |
| `text_reset` | `frame_id` | Start of a fresh streamed assistant message (clears the live bubble). |
| `text_chunk` | `frame_id`, `block_type` (`"text"` for prose, `"tool"` for code-cell echo/stdout/errors), `chunk`; a code-cell start also carries `cell_index`, canonical `kernel_id`, and `language` | Incremental stream. The frontend uses the start metadata directly so live Notebook grouping matches the persisted execution log without a status-cache race. |
| `notebook_cell_start` | `frame_id`, `producing_cell_id`, `cell_index`, `state_revision`, `generation_id`, `kernel_id`, `language`, `origin`, `source`, `status` | Starts/upserts one immutable Cell identity using the exact attempt-bound runtime generation. |
| `notebook_cell_chunk` | `frame_id`, `producing_cell_id`, `stream`, `chunk` | Appends output to that exact live Cell. Unknown/replayed fields are tolerated. |
| `notebook_cell_finished` | start identity (including the unchanged `state_revision` and `generation_id`) plus complete source/output/error, figures/files and usage | Replaces the live projection with the authoritative finished revision. |
| `step` | `frame_id`, `step_id`, `kind`, `title`, `input`, `status:"running"` | A semantic step began (host call, artifact save, …). |
| `step_update` | `frame_id`, `step_id`, `status`, `output`, `summary` | Step finished/patched. Artifact-save steps emit `step`+`step_update` back-to-back. |
| `plan_ready` | `frame_id`, `plan_id`, `status`, `plan`, `artifact_id` | A plan-mode turn produced a structured plan. |
| `plan_progress` | `frame_id`, `plan_id`, `step_id`, `status`, `note` | A plan step ticked during auto-execution. |
| `await_permission` | `frame_id`, `decision_id`, `tool`, `kind`, `title`, `input`, `target`, `suggested_patterns`, `scopes`, `sub_agent` | A tool call is blocked awaiting user approval (answer via `POST /api/frames/{fid}/decision`). Emitted from `openai4s/permissions.py`. |
| `permission_resolved` | `frame_id`, `decision_id`, `allow`, `scope`, and after restart: `resolution_context`, `requires_continue`, `original_action_executed`, `continuation_expires_at`, `continuation_authorization` | The pending prompt was answered / timed out. An after-restart event explicitly says the old operation did not execute and whether the user must start a fresh continuation. |
| `frame_update` | `frame_id`, `status`, `task_summary` (only with `status:"titled"`) | Turn/session lifecycle. Emitted statuses: `processing`, `completed`, `failed`, `cancelled`, `success` (REPL cell), `updated` (rename/PATCH), and `titled` — the background auto-title thread's upgrade of the placeholder session title, which carries an extra `task_summary` field (the new title) that no other status has. The frontend treats `completed|failed|cancelled|success|done` as terminal — note `done` is in the frontend's terminal set but is **never emitted** by the gateway as a `frame_update` status (it is only the *stored* frame status for a completed turn). |
| `kernel_status` | `frame_id`, `status` ∈ `restarted|stopped|started|env_changed|packages_installed|ended`, plus per-status extras (`generation`, `env`, `installed`, `ok`, `state`, `ended_reason`, `requires_kernel_recovery`) | Kernel lifecycle changes. A successful branch revert emits `ended` after invalidating both language slots. |
| `execution_state` | `frame_id`, `execution_id`, `owner:{kind,id}`, `status` (`queued|running|finalizing|completed|failed|cancelled`), `queue_position`, `reason` | One exact ticket changed state. |
| `execution_queue` | authoritative snapshot fields from `GET /frames/{fid}/execution` | Queue/position projection; also sent immediately after `view_session`. |
| `execution_owner` | `execution_id`, `owner`, previous identity, `reason` | Active writer changed. |
| `execution_cancel_result` | scoped cancellation result | Direct reply to a WS cancellation request. |
| `checkpoint_created` | `branch_id`, `checkpoint_id`, `reason` | An immutable checkpoint committed. |
| `branch_created` | `branch_id`, `from_checkpoint_id` | A checkpoint-backed branch committed. |
| `branch_revert_conflict` | `branch_id`, `operation_id`, `target_checkpoint_id`, `reason` | Revert was recorded but not applied because the conflict check failed. |
| `branch_reverted` | `branch_id`, `operation_id`, `target_checkpoint_id`, `checkpoint_id`, `undo_checkpoint_id`, `ok`, `requires_kernel_recovery` | Revert committed append-only state; clients must refresh branch/recovery projections. Full previews/checkpoint records stay in the direct REST result and never enter WebSocket. |
| `branch_projection_restored` | `frame_id`, `branch_id`, `checkpoint_id` | The branch-scoped projection was rebuilt (for example after a Revert); clients holding a stale message/Notebook view must refetch it rather than patch. |
| `branch_activation_state` | `frame_id`, `root_frame_id`, `branch_id`, `checkpoint_id`, `status`/`state` | Activation of a branch runtime progressed. `status` and `state` carry the same value — a compatibility duplication kept because both spellings are already consumed. |
| `artifact_created` | **non-uniform — see below** | An artifact was produced, edited, renamed, uploaded, restored, or deleted. |
| `pong` | — | Reply to JSON ping. |

### `artifact_created` payload non-uniformity (wart, load-bearing)

The gateway emits **four different shapes** under the same event type:

1. **Auto-capture** (a cell wrote a file) — the richest form:
   `{"type":"artifact_created","artifact":{"id","artifact_id","version_id",
   "filename","content_type","size_bytes","project_id","root_frame_id"}}`.
   Note the duplicated `id`/`artifact_id`.
2. **Edit / rename / upload** — a *partial* `artifact` object: edit has
   `{id,filename,version_id,root_frame_id}`; rename has
   `{id,filename,root_frame_id}` (**no** `version_id`); upload has
   `{id,filename,content_type,root_frame_id}` (**no** `version_id`).
3. **Plan artifact** (`plan_*.json`) — a *flat* event with **no nested
   `artifact` key at all**: `{"type":"artifact_created","frame_id",
   "artifact_id","filename"}`.
4. **Delete / version-restore** — a bare refresh signal:
   `{"type":"artifact_created","root_frame_id"}` with **no artifact info
   whatsoever**.

The event can also be **absent entirely**: the edit/rename/upload/delete/
restore broadcasts only fire when the artifact has a `root_frame_id` (for
uploads, only when `frame_id` was supplied in the request) — an upload
without `frame_id` stores the file but emits no `artifact_created` at all.

Consumers must treat every field as optional. The frontend does exactly this
(`const art = m.artifact || {}; const aid = art.id || art.artifact_id;`):
when `version_id` is present it is used as an image-cache-bust key, otherwise
the event just triggers an artifact-list reload. **Do not** rely on
`artifact_created.artifact.id` being present or stable across emit sites.

## 4. JSON serializers (shared shapes)

Defined at module level in `gateway.py` so tests can import them. All
timestamps are ISO-8601 strings (or null).

- **Frame** (`_frame_json`): `{id, root_frame_id, parent_frame_id, project_id,
  name, task_summary, model, status, folder_id,
  conversation_type:"agent", message_count, input_tokens, output_tokens,
  created_at, updated_at}`. List rows additionally get `running` and
  `kernel_alive`.
- **Project** (`_project_json`): `{project_id, id, name, description, context,
  conversation_count, last_active_at, created_at, updated_at, is_example}`
  (`project_id`/`id` duplicated).
- **Artifact** (`_artifact_json`): `{id, artifact_id, filename, content_type,
  size_bytes, version_id` (= latest version, the UI cache-bust key)`,
  checksum, project_id, root_frame_id, priority, created_at,
  is_user_upload}` (`id`/`artifact_id` duplicated).
- **Note** (`_note_json`): `{note_id, id, content, created_at, updated_at}`.
- **Annotation** (`_annotation_json`): `{id, annotation_id, root_frame_id,
  artifact_id, artifact_name, x, y` (0–1 fractions)`, number, body,
  status("open"|"sent"), created_at, updated_at}`.

The duplicated-key pattern (`id` + a typed id) is deliberate frontend
compatibility; keep both when touching these serializers.

## 5. Known gaps and sharp edges (summary)

- `GET /api/projects` accepts but **ignores** `limit`/`offset`; there is no
  project pagination. Real bounded reads exist for `from`/`limit` on messages,
  `limit` on frames, and the Timeline's `before_ordinal`/`after_ordinal` +
  `limit` windows (§2).
- `artifact_created` has four payload shapes; every field is optional (§3).
- Uploads are JSON/base64, not multipart; non-alphabet characters in the
  base64 are silently discarded, and input that still fails to decode is
  silently stored as raw UTF-8 text (§2).
- Missing resources are inconsistently signaled: some routes 404 with
  `{error}`, others return `{}` (frame/project GET), `{"ok":true}`
  (idempotent deletes), a nulls-filled 200 (`/artifacts/{aid}/lineage`), or a
  200 body containing `{error}` (`/connectors/{id}/call`).
- Malformed JSON request bodies are rejected with `400 malformed_json`.
- Raw-bytes artifact routes return JSON bodies on 404.
- Skill enable-disable state is durable; the legacy built-in-agent roster
  toggle is still process-local. Specialist runtime policy has separate
  persistent capability state.
- On the default loopback bind there is no auth; the CSRF Origin check and
  loopback bind remain the HTTP boundary. Kernel execution additionally uses
  environment scrubbing, permission/audit layers, and the configured OS
  sandbox; local `/compute/jobs` is still a privileged surface.
- The WS replay buffer covers only the **current in-flight turn**; a client
  connecting after a turn ends must reload state over REST (the frontend
  does).
- Structured `notebook_cell_*` events are live projections; reconnect safety
  still relies on the compatibility `text_chunk` stream and authoritative
  `/execution-log` reload rather than a durable per-Cell WS backlog.
- Workbench read/write routes are public, but no mutating endpoint runs the
  verified recovery pipeline. Fork-from-cell, visible checkpoint-fork/undo/
  branch-navigation controls and most specialized renderer UI components are
  also still absent (§2).
