# Web App API Contract (as implemented)

This document records the **actual** HTTP/WebSocket contract between
`openai4s/server/gateway.py` (backend) and `openai4s/server/webui/app.js`
(frontend), including known warts and gaps. It is descriptive, not
aspirational: every claim below maps to the Gateway/frontend or to a focused
service they compose (notably the execution coordinator, session-domain,
workbench-state, and permission services). If you change that public surface,
update this document.

Scope note: this covers the **gateway** started by `openai4s serve` /
`./start.sh`, which is the only HTTP surface the package serves. A second,
minimal server (`openai4s/server/daemon.py`, `POST /run`) was removed rather
than documented: nothing imported it and it had none of the gateway's Host,
Origin, token or header defences.

### Auto Mode API status at Stage 0

The new Auto Mode API and state vocabulary are **not implemented in this
contract version**. The only planned route names are explicitly versioned as
`GET/PATCH /api/v1/frames/{fid}/auto-mode` and
`GET /api/v1/frames/{fid}/auto-audits?subject_kind=&before=&limit=`; neither
exists yet, and there will be no unversioned or alternate alias. There is
likewise no durable or WebSocket event named `auto_run_started`,
`candidate_ready`, `auto_audit_started`, `auto_audit_completed`,
`repair_started`, `repair_completed`, or `auto_run_terminal`, and `candidate`,
`verified`, `completed_with_issues`, `review_unavailable`, and
`blocked_by_guardian` are not currently emitted or stored frame statuses.

`auto_audit_started` and `auto_audit_completed` are the sole planned wire and
storage event names for both audit kinds. Their required `subject_kind` is
`result_review` or `permission_review`; phrases such as “review started” and
“Guardian completed” are domain descriptions, not additional event types or
aliases. One durable event id names one committed transition, so an adapter
must never duplicate it under a second name. The existing `permission_resolved`
event remains canonical; later stages add `resolution_actor` and `audit_id` to
that event after the corresponding durable transaction rather than inventing a
second permission-resolution event.

The planned audit envelope also requires an orthogonal
`subject_entity_kind`: `result_review` pairs only with
`candidate_evidence_snapshot`, and `permission_review` pairs only with
`approval_action`. Entity values must not be placed in `subject_kind` and do
not create event aliases. A started/completed pair shares one `audit_id` and
one `audit_request_digest`, which binds only the immutable request and subject.
`auto_audit_completed` additionally carries the canonical assessment and a
separate `assessment_digest` binding the request digest, subject fields,
attempt, verdict/decision, findings, risk, authorization, outcome, rationale,
failure, durability, and retry state. Request and assessment digests are not
interchangeable; the server must fail closed on reuse, substitution, or an
assessment hash mismatch.

The existing per-frame `auto_review` preference still starts the legacy,
single-call evidence Reviewer after the answer has already been finalized. Its
ordinary `step`/`step_update` records do not promote or veto `frame_update`, and
reopen/share/export must not reinterpret such a step as a new Auto Mode
terminal fact. Stage 0's default-off config values are inert reservations, not
API capability. The future truth and recovery rules are frozen in the
[Auto Mode product contract](auto-mode.md).

A future `blocked_by_guardian` projection requires a durable Guardian assessment
of a deterministic `ask`. Sandbox, egress, secret/credential, biosecurity,
cost, deterministic hard-deny, action-digest mismatch, and permission-audit
persistence controls remain prior to Guardian; the API/UI must not claim
Guardian made those decisions. Projection follows the committed reason:
`policy_requires_explicit_setup`, `budget_exhausted`,
`safe_rollback_unavailable`, `outcome_unknown`, `loop_detected`, or the
hard/integrity `safety_boundary`, with the user truth frozen in the Auto Mode
contract.

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
  The enrichment never overwrites a field the route itself set, so on the few
  routes that return a domain result under an error code — `POST
  /frames/<id>/recovery/actions/<id>` answers a failed action with its whole
  result and `409` — `status` stays that route's own value. Read the HTTP
  status line, or `code`, when you need the transport status specifically;
  those are always present, whereas a clobbered domain field has no second
  copy.
  Status is too coarse to branch on alone: four distinct 400s
  (`malformed_json`, `invalid_body_type`, `invalid_cursor`, `invalid_limit`)
  need telling apart, and a client retrying `invalid_cursor` the way it retries
  a transient failure would loop on a request that can never succeed.
  `request_id` matches the `X-Request-Id` response header and the correlation id
  in the structured log line, so one id ties a user report to a server event.
  A **background job that fails** carries the same field on its *success-path*
  body (`200 {"status":"failed", …}` from a waited turn, plan or cell), holding
  the id of the request that started it. It used to be absent there, and worse,
  the log lines from inside those job threads carried an empty id: a new thread
  starts with an empty `contextvars` context, so the id was lost at exactly the
  boundary where the slow, failure-prone work begins. The field is **omitted,
  never null**, when a job was built outside any request — a daemon-lifetime
  sweep or a recovery pass — because `null` would read as "this request had no
  id" rather than "there was no request".
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
- **Token gate — on by default, including on loopback.** All paths except
  `/health` and `/api/v1/auth/status` require a credential, in either spelling:
  the `os_token` cookie, `Authorization: Bearer <token>` (what a generic client
  or `curl -H` reaches for), or `X-OpenAI4S-Token` (for when something upstream
  already owns `Authorization`). Neither header is preferred; the scheme is
  compared caselessly per RFC 7235 and the value in constant time.
- **`?token=` bootstraps the root page only.** A `GET` for `/` or
  `/index.html` responds `303` with the cookie set, redirecting to the same
  page with only the token stripped (any other query parameters survive).
  Everywhere else it is refused — deep links included. They used to bootstrap,
  because the rule was "any path that is not `/api/v1/*` or `/static/*`", and
  `/preview/<id>` is neither: a link carrying a token there set the cookie and
  then served the artifact bytes. A URL carrying a credential is a shareable
  credential: pasted into chat, logged by proxies, kept in history, leaked by
  `Referer`. On the root page the link buys an empty SPA shell; on a data path
  the response *is* the payload.
- **A `token` query parameter on a mutating request is refused outright** with
  `401`, even when the request also carries a valid cookie or header. Ignoring
  it meant the leaked URL worked, so the caller never discovered they were
  shipping a secret in a URL. Send `Authorization: Bearer <token>` or
  `X-OpenAI4S-Token` instead.
- The token is minted once under the data dir (`access-token`, owner-only) and
  survives restarts; it used to be per-boot, which invalidated every cookie
  already issued. The CLI reads the same file, or `OPENAI4S_TOKEN` when the
  daemon runs under another account.
- `OPENAI4S_REQUIRE_TOKEN=0` disables the gate **on loopback only**, until the
  version named by `gateway.LEGACY_TOKEN_OPT_OUT_REMOVED_IN`. It is the same variable that used to opt *in*, with its sense
  reversed. Off loopback it is ignored: a bind anything can route to has no
  configuration under which it should answer without a credential.
- `GET /api/v1/auth/status` is reachable unauthenticated so a client can
  discover it needs a credential, and answers
  `{authenticated, auth_mode: "token"|"none", token_header}` — a mode string
  only, never any part of the token. It previously reported `"none"`
  unconditionally, so a daemon running with the gate on told every caller there
  was no gate.

### Error envelope

- The backend error shape is the enriched envelope described in §2 —
  `{"error", "code", "status", "request_id"}` plus route-specific diagnostic
  fields. The unenriched `{"error": "<message>"}` shape this section used to
  describe has not been the wire format since the envelope landed; the two
  sections contradicted each other, and the frozen artifacts agreed with the
  wrong one because the contract capture observed bodies before enrichment ran.
  The sources are unchanged: raised `GatewayError(code, message)`; any
  unhandled exception → `500`; the `_api` catch-all → `404` carrying `path`
  and `method`.
- The frontend `api()` helper reads `j.error || j.detail`, so the Gateway's
  error text is shown. `detail` remains accepted for compatibility with
  external adapters.
- An **unhandled** exception never puts its own text on the wire. It is
  projected through `errors.public_exception` into `{"error": "internal
  error", "code": "internal_error", "status", "request_id"}`, and the original
  goes to the redacted `unhandled_exception` diagnostic that
  `diagnostics.build_bundle` collects. A `GatewayError`'s message is
  author-written and is passed through unchanged. Quote `request_id` in a
  support report: it is this daemon's own correlation id, never an upstream
  provider's.
- Some handlers still return errors **inside a 200 body** instead of an error
  status: `POST /api/artifacts/{aid}/versions/{vid}/restore` maps a soft
  `{"error": …}` result to 404 but other handlers pass soft errors through as
  200. Do not assume "2xx ⇒ no `error` key". `POST /api/connectors/{id}/call`
  used to be in this list and now answers `502 connector_failed`: `api()` in
  the web client only rejects on a non-2xx, so a connector that never ran was
  reported to the user as one that did.

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
| `GET /api/frames/{fid}/notebook/export?language=` | `.ipynb`, ZIP or Markdown bytes | `python`/`r` returns one Notebook; omitted/`bundle` returns both plus a manifest; `markdown` returns one `.md` with both languages in execution order. |
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
| `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` | Team mode (M1). HttpOnly `SameSite=Lax` cookie; only the token's sha256 is stored. Login is rate-limited per username+IP and the bucket is charged *before* the password hash, so the limit also bounds the hashing an attacker can provoke. Wrong password, unknown user and disabled account are one sentence — the difference is the attacker's question. |
| `GET /auth/me/llm-key` | Whether this user has a key of their own, per provider: `{keys: [{provider, configured, created_at, updated_at}]}`. Never the value — a credential a screen can display is one a screenshot leaks — and never the reference either, which names a keychain slot. |
| `PUT /auth/me/llm-key` | Body `{provider, api_key}` (M4-1, decision D7's second half). The key goes to the `SecretBroker`; the database keeps only a reference. A broker that cannot store it answers `503 secret_store` and **writes no row**: a row pointing at a slot that was never filled would make the next turn refuse with "configured but unreadable" for a key that was never accepted. The override is per provider, so a user with their own Anthropic account and no OpenAI key runs on theirs for one and the group's for the other. |
| `DELETE /auth/me/llm-key` | Body `{provider}` → `{ok, removed, provider}`. `removed:false` when there was nothing to clear, which is not an error: the intent — "do not use my key" — is satisfied either way. Disabling an account clears every key it had, because a credential that outlives its account is one nobody is watching. |
| `GET /csrf` | `{"csrf_token":"local"}` (a stub; the real CSRF defense is the Origin check). |
| `GET|POST|PUT|PATCH /config/llm` | GET → `{provider,model,base_url,has_api_key}`. Write → persists `provider`/`model`/`base_url`; `api_key` only overwrites when non-empty; `clear_api_key:true` empties it. Changing provider without a replacement key also clears the old provider-bound credential so it cannot be reinterpreted or sent to the new provider → `{"ok":true,"has_api_key"}`. The raw key is never returned. |
| `GET /search?q=` | `{sessions:[{id,project_id,name,task_summary}], artifacts:[{id,filename,content_type,root_frame_id,project_id}], datapro:[{query,dataset_type,json_pointer,content,artifact_id,root_frame_id,project_id}]}`; empty `q` → empty lists. `datapro` searches every recursively indexed key and scalar in successful DataPro content; each hit is a logical result occurrence, so equal records at different JSON pointers remain distinct. |
| `GET /` (i.e. `/api` or `/api/v1/`) | `{"service":"openai4s","ok":true}`. |

### Models and model profiles

**Readiness is local; reachability is asked for.** Every profile carries a
`readiness` object — `ready` / `needs_key` / `needs_model` / `unsupported` —
derived entirely from stored state, so listing profiles costs no network at
all. `checked_endpoint` is always `false` there, and `ready` means *the
configuration is complete*, not that anyone answered; the detail line says so,
because a user who read it as "verified" would be reading a stronger claim than
the data supports.

`POST /model-profiles/{id}/probe` is the only thing that contacts a provider.
POST rather than GET because it spends the user's own quota and rate limit, and
a GET invites a prefetch or a refresh loop to spend it for them. It refuses
without contacting anything when readiness is not `ready` — a keyless profile's
401 reads like an endpoint fault rather than the missing credential it is. A
failure reports the provider's own message, redacted, because a rewritten one
loses the detail that distinguishes a bad key from a bad model name.

`gemini` and `openai_responses` are selectable protocols. Both were dispatchable
by the LLM layer and absent from the profile menu, so a user holding a Gemini
key had no way to say so.


**A session binds `profile_id + revision`.** A frame used to store a model
*string*, which answers "which model name" and not "which configuration" — and
the two come apart in exactly the case that matters, because two profiles can
name the same model against different providers or endpoints, and editing a
profile rewrote it in place. A replayed session therefore reported whatever its
profile says today.

- Each profile carries `revision` and an append-only `revisions[]`. A new
  revision is minted only when `(provider, base_url, model)` changes. A rename
  does not, and neither does a key rotation — the credential reference is
  derived from `(scope, profile_id)`, so revisions must share the profile id or
  rotating a key would strand earlier revisions on an unreadable secret and
  deleting any revision would destroy the key the others point at.
- Binding happens on **send only**. Reading a session never binds it, so an
  unbound legacy session stays fully readable — history, artifacts, Notebook.
- `409 model_revision_unavailable` — the session is pinned to a revision that
  no longer exists. Resolving to the nearest one would be the silent
  follow-latest behaviour this replaces, wearing a number.
- `409 model_revision_ambiguous` — a legacy session whose recorded model
  matches more than one profile. Backfill happens only on a **unique** match;
  an ambiguous one stays unbound and asks, because picking either would be a
  guess presented as a fact.
- An install with no profiles at all (driven by `.env`) binds nothing and runs.
  An absent profile is an absent binding, not an error.

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

### Projects, notes, folders, example session

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
| `GET /example/session` | `{seeded,frame_id,project_id,started,running,seeds_at_startup,error}` — state of the bundled example analysis. `started` is always `false` on a GET. |
| `POST /example/session` | Body **must** be `{"confirm": true}`; anything else is `400 confirmation_required` and seeds nothing. Idempotent: already seeded → `{"seeded": true, "started": false}`; already running → `{"started": false, "running": true}`, which is distinguishable from a refusal. Seeding happens on a background thread, so this returns immediately and the client polls the `GET`. |

### Frames (sessions) and turns

**`@file` references are version-pinned.** `@name#v-<version_id>` sends the
frozen bytes of that exact version; it used to read the artifact's *live path*,
so the same reference meant different bytes once a later cell overwrote the
file. A same-project reference belonging to another session is **materialised**
into this one (D3) when the turn is sent — not when the reference is typed, so
an inserted-then-deleted reference leaves no Artifact and no lineage edge
behind. Cross-project is refused with the same answer as absent.

The bare `@name` spelling still works for one minor release. It resolves inside
the calling session only, through the artifact's latest *version* rather than
its live path, and says in the injected block that it is unpinned.


| Method & path | Behavior |
| --- | --- |
| `GET /frames?project_id=&limit=&cursor=` | `{"frames":[…],"next_cursor":…,"has_more":bool}`. Keyset pagination, newest first; `limit` 1–200 (default 100). `cursor` is opaque — parsing it would couple a client to the sort key. An unreadable cursor is a `400`, never a silent restart, which would loop a client on page one. `has_more` is observed by collecting one row beyond the page, not inferred from the page being full: hidden abandoned sessions are filtered *after* the read, so a full-looking page is not evidence of a next one. |
| `POST /frames` | Body `{project_id?,model?}` → frame JSON for a new root frame. |
| `GET /frames/{fid}` | Frame JSON, or `{}` when not found. |
| `PATCH /frames/{fid}` | Updates `name`/`task_summary`, broadcasts `frame_update` → frame JSON. |
| `DELETE /frames/{fid}` | `{"ok":true}`. |
| `GET /frames/{fid}/messages?from=&limit=&branch_id=` | Branch-projected `{"messages":[{message_id,role,content,created_at,fork_checkpoint_id,artifact_refs,failure?}…]}`. `failure` is present only on a message that recorded one, and carries `{request_id,code,output_committed?}` — an allowlisted projection of the row's metadata, never the exception. It exists because reopening otherwise lost both the support id and the retry veto: the socket event is gone once the tab closes and the stored row is a sentence. `output_committed` appears only when true; absent is "no claim". Omitted `branch_id` selects the durable active branch; its inherited prefix and post-Revert continuation are included, while sibling/abandoned rows remain only in the audit source. `from` (default 0) and `limit` (default 300) are real slice parameters. **Latest-first paging:** `?newest_first=1` returns the newest page and adds `next_before_seq` + `has_earlier`; `?before_seq=<seq>` walks backwards. Without either, the response is exactly what it always was — oldest-first from `from`, and no cursor keys. It mattered because a 640-message session returned messages 0–299: the *oldest* page, with the newest 340 absent. The cursor is a `seq` bound rather than an offset, because newest-first plus OFFSET shifts on every arriving message. `has_earlier` is observed, not inferred from a short page — the branch projection can hide rows, which a client cannot tell from the end of history. `before_seq` that is not an integer is `400 invalid_cursor`. |
| `GET /frames/{fid}/steps` | `{"steps":[…]}` (persisted semantic steps). |
| `POST /frames/{fid}/message` | Starts a turn. Body `{request}` (or `{input_data:{request}}`), optional `model`, `plan`, `explore`, `annotation_ids`. With `wait:false` → `202 {"status":"accepted","frame_id","job_id","execution_id","owner":{"kind","id"},"queue_position","request_id"}`; `request_id` is the local id this turn will be named by everywhere else — the failure `frame_update`, the job result, the persisted assistant message and `GET /frames/{fid}/messages` all carry the same one, and a `wait:false` client has no other synchronous chance to learn it; default (`wait` omitted/true) blocks for the turn result.

When `annotation_ids` are sent, **both** branches additionally carry `annotations` and `annotation_reservation_id`. Admission is exactly-once: the pins named are claimed atomically, only what was actually claimed is quoted into the prompt, and `annotations` says what became of them — `sent` (consumed), `pending` (the turn was accepted but the consume did not confirm; they are still reserved, so neither retry them nor treat them as gone) or `none` (this request claimed nothing, because a concurrent turn won the race, and they are still the user's to send). The fields are **absent** when no pins were sent: an absent field and a field saying "none" are different claims. `annotation_reservation_id` is what `GET /frames/{fid}/admissions/{reservation_id}` is asked about after a lost response — a dropped connection, a closed tab, a reload — and it is scoped to the frame, so it is a value a client holds rather than a capability. A synchronous refusal (413/409/429, or a worker that could not be started) releases the reservation and leaves the pins `open`; a failure with **no** response is not a refusal and must not be treated as one. A valid sole `finalize_response` is an Engine completion (even if an earlier step ran a Cell); `host.submit_output(...)` is the only completion emitted from inside a Python Cell. Ordinary prose/results and max-turn exhaustion are not success. |
| `GET /frames/{fid}/execution` | Authoritative FIFO snapshot: `{root_frame_id,owner,queue,queued_count,active_count,closed,close_reason}`. Owner/queue entries include `execution_id`, `{kind,id}` owner, status, position, branch/language/generation and resource keys when known. |
| `POST /frames/{fid}/cancel` | Scoped cancellation. Body `{execution_id,owner:{kind,id}}` (or `owner_kind` + `owner_id`) and optional `reason` → `{ok,execution_id,owner,scope,…}`. Missing identity returns HTTP 400 with `error`; stale/mismatched identity returns `ok:false`. A queued cancellation does not affect the active owner. |
| `GET /frames/{fid}/status` | `{"frame_id","running","status",kernel:{…kernel status…}}`. `status` is the **frame's** stored status, which `running` cannot express: `running:false` is equally true of a session that completed, one that was cancelled and one that failed, so a client reopening a session could not tell which and could not restore a failure that had already ended. |
| `POST /frames/{fid}/feedback` | Body `{key,rating}` → `{"ok":true}`. |
| `GET /frames/{fid}/feedback` | `{"feedback":[…]}`. |
| `GET /frames/{fid}/session/export` | Raw deterministic Session-package ZIP with `X-Content-SHA256` and `X-OpenAI4S-Session-Schema`. It contains branch-owned messages, complete sanitized provider groups/wire state, Notebook and Artifact/lineage records, Revert cursors, evidence reviews and checkpoint plan/review/memory snapshots; secret material is rejected. |
| `POST /sessions/verify` | Raw Session ZIP body (same limit as import) → HTTP 200 with the `verify_package` report: `{ok, format, schema_version, archive_sha256, files_verified, problems, verifies}`. Reads only the archive — no daemon state, no network, nothing admitted to the database — so a recipient can check what they were handed before deciding to import it. The frontend calls this *before* `/sessions/import` and refuses the import when it fails. `verifies` states plainly what the check does not establish: internal consistency is not authorship, which would need a signature. |
| `POST /sessions/import` | Raw Session ZIP body (not JSON, maximum archive 128 MiB) → HTTP 201 with new `{project_id,root_frame_id,active_branch_id,kernel_state:"ended",view_only:true,trust_state:"quarantined",explicit_recovery_required:true,…}`. The entire archive is preflighted as untrusted input, all identities are remapped, permissions are downgraded, review automation is disabled, and no Kernel/hook/package code starts. The quarantine is durable: frame-scoped mutations return HTTP 423 until the user calls `POST /frames/{fid}/recovery/actions/restart_fresh` with `{"confirm":true}`; read/export/delete remain available. |

### Plan mode

| Method & path | Behavior |
| --- | --- |
| `GET /frames/{fid}/plan` | `{"frame_id","plan_id","status","plan"}` (nulls when no plan). |
| `POST /frames/{fid}/plan/approve` | `202 {"status":"accepted","frame_id","job_id","request_id","execution_id"}` — auto-execution runs in the background; `request_id` correlates with the failure surfaces exactly as the message route's does. `execution_id` is a real coordinator execution taken at submit, so the 202, the job poll, the socket and any failure all name the same one; a plan turn is queued behind the running turn and holds the session until it has written its own outcome, exactly like a message turn. The approve/resume routes claim the plan row before answering, and the background turn always settles that claim — `failed` when it faults, `paused` when it is cancelled, including a cancellation that arrives while it is still queued. |
| `POST /frames/{fid}/plan/resume` | `202 {"status":"accepted","frame_id","job_id","request_id","execution_id"}` — runs only the plan's **unfinished** steps. `409 plan_not_paused` when the plan is any other status, refused synchronously: the `paused` → `executing` transition is a compare-and-swap performed *before* the 202, so of two concurrent resumes exactly one is accepted and the other is refused with the status it lost to, instead of both being handed a job that runs the same steps. A step counts as settled when it is `completed` or `failed`: `failed` is a decision the agent made and moved on from, while `in_progress` was interrupted with no record of how far it got, so it is re-run. The resume seed names the settled steps and instructs the agent not to redo them. A paused plan with nothing unfinished is marked `completed` without running a turn. |
| `POST /frames/{fid}/plan/revise` | Body `{changes}` (or `{feedback}`); empty → `400 {"error":"changes required"}`; else `202 {"status":"accepted","frame_id","job_id","request_id","execution_id"}`. |
| `POST /frames/{fid}/plan/discard` | Result of `runner.discard_plan` (synchronous). |

### Cluster batch jobs (orchestration)

Long unattended work: submitted here, executed by whatever resource plane the daemon has (this machine by default, a scheduler when `cluster.toml` configures one). The API is deliberately backend-neutral — a response names an `allocation_id`, never the scheduler's job id, and a `profile` name, never a queue (INV-2, decision D5).

Nothing here submits synchronously. A request writes a durable row; the reconciler loop does the talking on its next pass. That is what makes a cancel survive a daemon restart, and what keeps one submission from being attempted twice by a request thread that goes away mid-flight.

| Method & path | Behavior |
| --- | --- |
| `POST /orchestration/jobs` | Body `{command: [...], profile?, backend?, workdir?, environment?, project_id?}`. `202 {id, phase, ...}` — accepted, not started; `201` would promise a resource that has not been granted. `command` **must** be a list: a string is refused with `400 invalid_command`, because splitting a command line is where quoting bugs become injection. Unknown profile → `400 unknown_profile`; unknown backend → `400 unknown_backend` with the available ones. In team mode the `local` backend is **admin only** (`403 admin_only`, with the refused `backend` named) — see below. |
| `GET /orchestration/jobs` | `{jobs: [...]}`, filtered to the caller's own unless they are an admin. `project_id`, `limit` and `all=0` (hide terminal) narrow it. |
| `GET /orchestration/jobs/{id}` | One job plus its `allocation` (the live attempt) and `allocations` (every epoch). Someone else's job is `404`, not `403` — which jobs exist is itself information about what a colleague is working on. |
| `POST /orchestration/jobs/{id}/cancel` | Records the desire to stop and returns `{ok, reason}`; the reconciler runs the cancel barrier. `409 already_final` when the job has already ended. An admin cancelling another user's job is recorded as `ADMIN_CANCELLED` rather than `USER_CANCELLED`. |
| `GET /orchestration/jobs/{id}/logs` | `{allocation_id, stdout, stderr}` — the tail (64 KiB) of what the job wrote. |
| `GET /orchestration/profiles` | `{cluster, configured, profiles: [{name, cpus, memory_mb, gpus, walltime_s, nodes}]}`. The queue and service-class each profile maps to are **not** in this payload: they live in `cluster.toml` and nowhere else. |

**Which backends a member may name.** Privilege here is a property of the *backend*, not of the route. A scheduler backend hands the work to the site's scheduler, which runs it under the submitting user's own account and accounting — so a member submitting to a cluster is submitting as themselves, and keeps it. The built-in `local` backend runs `Popen(argv)` as the **daemon's** uid, outside the kernel sandbox, with the daemon's filesystem access: in team mode that is every tenant's sessions, the access-token file and the group LLM credential. It is therefore admin only, by the same rule that makes `/compute/jobs` admin only.

The check runs *after* the backend name is validated and *before* anything is written, so a misspelled backend still reads as `400 unknown_backend` rather than a permission problem, and a refused submission leaves no workload row and no audit entry behind.

A single-user daemon is unaffected (INV-1): without team mode there is no member/operator distinction, because the person running the daemon is the operator.

Note what this does **not** do: it does not put `LocalBackend` inside the kernel sandbox. That sandbox degrades visibly rather than failing closed on hosts that cannot give it namespaces, and a privilege boundary that is sometimes absent is worse than a refusal. Member-submitted local work wants a separate, fail-closed `local-sandboxed` backend designed as one.

### Where a session runs (cluster sessions)

A session's kernel is on the daemon by default. Asking for it to be on a granted resource instead is these three routes, and the answer they exist for is `readiness`: a cluster session is not one boolean but four conditions (INV-5), so the payload names the one that is outstanding. "Queued for a node", "waiting for the worker to dial in" and "starting the kernel" are three different waits with three different expected durations, and one spinner for all of them is how a user concludes the product is broken.

| Method & path | Behavior |
| --- | --- |
| `GET /sessions/{id}/compute` | `{session_id, location}` — `location:"local"` with `workload:null` when this session runs on the daemon, which is the default and is not an error. On a cluster session: `readiness:{ready, blocked_on, allocation_granted, worker_registered, workspace_ready, kernel_ready}`, `workload:{id, profile, phase, desired_state, reason, execution_epoch}`, `allocation:{allocation_id, epoch, phase, reason}`, `lease:{idle_ttl_s, max_lifetime_s, last_active_at, created_at, released_at}`, and `state_lost_epochs` — the epochs whose kernel memory was lost to a node failure. Someone else's session is `404`, not `403`. |
| `POST /sessions/{id}/compute` | Body `{profile}`. `201` with the same status payload. A profile the operator has not configured is `400 unknown_profile` rather than a guess: guessing is how a session lands on resources its owner never chose. A daemon with no worker listener answers `409 not_configured` and says which variable turns one on — the feature is off by default because a listener on every laptop that will never run a cluster job is an attack surface, not a convenience. |
| `POST /sessions/{id}/compute/release` | Records the desire to stop and ends the lease; the reconciler runs the cancel barrier. `{ok, session_id}`. |

`state_lost_epochs` is what the UI turns into a banner. When a node dies the kernel's memory dies with it — variables, imports, the seed somebody set three cells ago — and the session continues on a new epoch. Saying so is mandatory (INV-11): the results afterwards look exactly like results from the session that was lost.

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
| `GET /frames/{fid}/admissions/{reservation_id}` | `{"reservation_id","state","annotations":[id…],"request_id","job_id"}`, or `404` when this session has no such admission. What a client asks after its 202 was lost. Scoped to the frame: a reservation id travels in a response, so it is a value a client holds and not a capability. |
| `POST /frames/{fid}/annotations` | Body `{artifact_id,body` (or `text`)`,artifact_name?,x?,y?}` (`x`/`y` are 0–1 fractions; `rel_x`/`rel_y` accepted as aliases). Missing artifact_id/body → 400 → else `201 {"annotation":…}`. The server binds the pin to the artifact's **current version id + checksum**; a client may not supply them. On send, that exact version's bytes are read (its immutable snapshot, else the live path verified against the checksum) — a file overwritten after the pin is refused as `version_changed`, never substituted. |
| `PATCH\|POST\|PUT /annotations/{aid}` | Body `{body?,status?}` → `{"annotation":…}`, `404`, `400 invalid_status`, or `409 annotation_reserved`. `status` is a whitelist (`open`/`sent`/`resolved`/`dismissed`); `reserved` is not publicly writable, because that state is entered only together with its holder and a row holding nothing is released by nothing. A pin currently held by an in-flight turn refuses with 409 — the check and the write are one statement, so a reservation taken concurrently is respected rather than raced. |
| `DELETE /annotations/{aid}` | `{"ok":true}`, or `409 annotation_reserved` while a turn holds the pin. Same single-statement guard as PATCH. |

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
| `GET /frames/{fid}/notebook/export?language=` | Raw deterministic `.ipynb` for `python`/`r`; omitted or `bundle` returns a stable ZIP containing both plus a manifest. `markdown` returns a `text/markdown` rendering of the branch — both languages in execution order, because the interleaving is the record the split forms lose — with every cell's index, language and state revision in a citable heading, and failed cells kept and labelled. Anything else is 400. Includes `Content-Disposition` and `X-Content-SHA256`. |
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
| `POST /uploads` | **Base64 JSON upload — not multipart.** Body `{filename?, content_base64` (or `content`, or `content_text`)`, frame_id?, project_id?}`. Supply **exactly one** content field; two is a `400`, because which one is authoritative cannot be guessed. `content_base64`/`content` are strict base64 — whitespace is stripped (line wrapping is transport formatting) and anything else outside the alphabet is a `400`. `content_text` uploads text as UTF-8. A rejected upload writes nothing. This used to decode without `validate=True`, silently discarding stray characters so a corrupted payload decoded to different bytes with no error, and to fall back to storing the raw string's UTF-8 bytes — so a `.npy` that lost one character became an artifact containing base64 text, versioned and checksummed. File lands in the session workspace (or `data_dir/uploads` without `frame_id`), is registered as a versioned artifact (`is_user_upload`), re-upload of the same name in the same frame creates a new version → `{"artifact_id","id","filename"}`. |

### Skills / agents / specialists / connectors

**Customize skill failures are real failures.** `POST /skills`,
`PUT|PATCH /skills/{name}`, `GET /skills/{name}`, `DELETE /skills/{name}` and
`POST /skills/import` used to answer `200` with `{"error": …}` in the body. The
service returns soft dictionaries by design (`server/skills.py`) and still
does; what changed is that the gateway now projects them to a status. Each
failure carries a stable `code` — the status is derived from the code, never
from the message:

| `code` | Status | When |
| --- | --- | --- |
| `skill_name_required` | `400` | empty name, including an import whose frontmatter has none |
| `skill_name_unsafe` | `400` | the name resolves outside the user skills directory (symlink or traversal). Not `403`: nothing was denied by policy, the name is unusable |
| `skill_name_conflict` | `409` | collides with a bundled skill, which discovery would shadow anyway |
| `skill_not_found` | `404` | no such user skill |
| `skill_read_only` | `403` | a bundled skill cannot be edited or deleted. Not `404` — it plainly exists, and saying otherwise is a lie the user can disprove |
| `skill_no_version_history` | `404` | version history requested for a skill with none |
| `skill_version_storage_unavailable` | `503` | the version store is absent — a missing dependency, not a bad request |
| `skill_write_failed` | `500` | the write itself failed (`OSError`, permissions) |

Why it mattered beyond tidiness: `api()` in the web client throws only on a
non-2xx, and the Customize editor's save handler does not inspect the body — so
a rejected save closed the modal and told the user "saved" while nothing was
written. These bodies also never reached `public_failure`, so they carried no
`request_id`.

The four sibling routes that already answered a real status —
`GET /skills/{name}/versions` and `POST /skills/{name}/rollback`, plus their
`/projects/{pid}/…` twins — keep the statuses they had (`404` and `409`
respectively, chosen per route) and now carry the specific `code` too. Their
statuses are deliberately **not** re-derived from the code table: that would
change published behaviour for no stated benefit. Branch on `code`.

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
| `POST /connectors/{id}/call` | Body `{tool,args}` → tool result; a failing call answers `502` with `code: "connector_failed"` (the MCP server's own message is not echoed — it quotes the argv and env it was launched with). |
| `DELETE /connectors/{id}` | Disconnect + delete → `{"ok":true}`. |

Doubao Search is the primary managed web-search product in Customize →
Network. It shares the brokered Agent Plan credential with Ark and DataPro,
but its dedicated test route is deliberately not the generic multi-engine
search path: a Tavily or keyless result must never make Doubao appear healthy.

| Method + path | Response / semantics |
| --- | --- |
| `GET /doubao-search/config` | Returns `{key_configured,ark_key_reused,provider:"doubao-search",primary:true}`; credential state is boolean and the provider label is non-secret metadata. It never returns the key, an Authorization value, or the fixed upstream endpoint. Configuration is not an authentication verdict. |
| `POST /doubao-search/config` | Body `{agent_plan_key}`. Stores the same SecretBroker credential used by DataPro and mirrors it to the active Ark credential/profile when Ark is selected. The response contains booleans and product metadata only. |
| `POST /doubao-search/search` | Body `{query,num_results?}` (`query` 1–100 characters; `num_results` 1–50). Calls Doubao Search directly with no fallback and returns normalized `{query,count,results:[{title,url,snippet}],source:"doubao",available,message}`. `available:true` requires the real response source to be Doubao and at least one usable result with a non-empty URL; an empty response remains unavailable. Reflected credential text is redacted before projection. |

DataPro is the one managed Streamable HTTP connector and has a narrower
product route. It is not editable or deletable through the generic connector
API, and its generic probe is refused because `initialize` plus `tools/list`
does not establish authentication:

| Method + path | Response / semantics |
| --- | --- |
| `GET /datapro/config` | Credential, connector, and bundled-Skill booleans only. No key, broker reference, endpoint header, or header value is returned. An active Ark model key is reported as reused only while the active provider is Ark. |
| `POST|PUT|PATCH /datapro/config` | Body `{agent_plan_key}`. Stores it through SecretBroker, enables the connector, drops the cached MCP session when the effective credential changes, and mirrors it to the active Ark credential/profile when Ark is selected. The response contains booleans only. |
| `POST /datapro/search` | Body `{query,frame_id?}`. Makes a real `dataPro_search({query})` tool call through the managed connector, fully indexes the redacted result envelope returned by that call (including `structuredContent`, content blocks, text, and future fields), saves a JSON Artifact, and returns `{structuredContent,content,is_error,code,available,message,index,artifact}`. A successful `index` receipt includes `complete:true`, logical `entry_count`, equal `source_leaf_count`/`indexed_leaf_count`, and completeness digests. Only a real integer `structuredContent.code` equal to `0` **and** a complete index transaction let the UI report “专业数据集可用”; `4011` maps to `Key 无效、额度不足，或者专业数据集 Harness 未开启。`. Reflected credential text is redacted before the response, index, or Artifact is written. |

The completeness receipt covers all redacted content in the result envelope
returned by this specific DataPro call, including content blocks, text,
unrecognized future keys, and nested values; it
does not claim that one query has enumerated or indexed DataPro's entire remote
corpus. The same ingestion boundary is used by the dedicated product route,
the managed connector call, and `host.mcp.call` from the bundled Skill. A
failed or non-integer/nonzero tool code creates no successful index batch, and
an index transaction failure prevents an availability success from being
projected.

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
| `POST /compute/jobs` | Body `{command|code,kind("bash"),cwd?,deadline_s?}` → job row. `deadline_s` defaults to one hour and is refused above 24 h with `job_bad_deadline`; there is no unbounded run. **Local code-exec endpoint** — protected only by the Origin check + loopback bind. |
| `POST /compute/jobs/{id}/cancel` | Cancel result. |
| `GET /compute/jobs/{id}` | Job row, plus `output`. |

A job row carries `status` from `queued|running|done|failed|cancelled|timeout|abandoned`.
The last two are distinct on purpose: `timeout` is the daemon stopping a job that
outlived its deadline, and `abandoned` is a job the previous daemon was running
when it died — read from its receipt on the next boot, never revived, and never
reported as `failed` (which would blame the job's own command) or `cancelled`
(which would claim somebody meant to stop it).

Output is bounded in bytes as it is read, not trimmed afterwards, so every row
carries `seen_bytes`, `retained_bytes`, `dropped_bytes` and `truncated`. What is
kept is the tail; `output` is prefixed with a notice when anything was dropped.
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
| `PATCH /memory/{id}?project_id=` | Body `{content?,block?}` → the edited row. `project_id` is required and is not defaulted, exactly as for the DELETE: an id is not authority over a project, and a cross-scope edit answers 404 rather than succeeding. Refuses empty or over-long content before the write (`memory_empty`, `memory_too_long`), and refuses a request that changes nothing (`memory_no_change`). Sets `updated_at`, which is what retention measures — an edit is a touch, so a corrected memory does not expire on the clock of the one it replaced. |
| `DELETE /memory/{id}` | `{"ok":true}`. |

`POST /memory` refuses on two distinct codes when a scope is full.
`memory_scope_full` counts memories still inside the retention window;
`memory_scope_full_expired` is the ceiling on rows *stored*, live or not. They
are separate because the remedies are: one asks the user to delete a memory they
are still using, the other rows that are not being injected at all.
| `GET|PUT|PATCH|POST /network/status` | Write toggles `OPENAI4S_ALLOW_NETWORK` (process env + setting); always returns `{"enabled":bool}`. |
| `GET /preferences/builtin-allowlist` | `{"enabled","egress_mode","granted":[domains],"groups"}`. |
| `GET|PUT|PATCH|POST /search/config` | Backup Tavily key config; write accepts `{api_key}` or `{clear_api_key}`; always returns `{"endpoint":"https://api.tavily.com/search","api_key_configured":bool}` — the key itself is never echoed. The dedicated Doubao Search route never falls back to Tavily. |
| `GET /telemetry/consent` | `{"enabled":bool,"env_locked":bool}`. Opt-in anonymous telemetry, off by default; `env_locked` is true when `OPENAI4S_TELEMETRY` vetoes it, so the UI can disable a toggle that would otherwise do nothing. |
| `PUT|PATCH|POST /telemetry/consent` | Body `{enabled}`. Granting records consent and mints the anonymous install id; revoking deletes both. Neither ever transmits — see `openai4s/telemetry/`. Returns the same shape as the GET. |

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
| `frame_update` | `frame_id`, `status`, `request_id`, `code` + `output_committed?` (terminal turn events), `task_summary` (only with `status:"titled"`) | Turn/session lifecycle. Emitted statuses: `processing`, `completed`, `failed`, `cancelled`, `success` (REPL cell), `updated` (rename/PATCH), and `titled` — the background auto-title thread's upgrade of the placeholder session title, which carries an extra `task_summary` field (the new title) that no other status has. Every turn event — `processing`, both terminal forms, and the outer handler's `text_reset`/`text_chunk` — also carries `execution_id`, and that is the field a client filters on. A request id cannot separate two turns: clients may reuse `X-Request-Id`, and the ordering that matters (`processing(A)`, `processing(B)`, `failed(A)` — A unwinding after B was promoted out of the queue) then looks like B's own terminal event. A terminal whose `execution_id` differs from the running turn's must not close it. When one side names no execution the pair falls back to `request_id`, and when neither names anything the event is treated as current: that is the pre-identity contract, and anything stricter strands every turn against an older daemon. The `processing` event carries `request_id` too, and it is the one a queued follow-up depends on: that turn's 202 resolved while an earlier turn still owned the screen, so `processing` — "your turn is running now" — is the first moment its id is current. Under an HTTP job it is the same string the 202 returned; a direct call (CLI, recovery replay) mints one rather than emitting an empty field. A terminal turn event also carries `request_id` — the same id the submit 202 returned — and, when the turn failed, a stable `code` (`max_turns` for turn-limit exhaustion; `llm_request_burst`, `llm_rate_limited`, or `llm_upstream_overloaded` for controlled LLM capacity failures; otherwise the projector's, defaulting to `turn_failed`). These local codes never expose the provider's raw error code or message. `output_committed:true` is added only when the failure happened after bytes were streamed or a tool ran: `llm/models.py` calls it the retry veto, because a transparent retry there duplicates visible output or re-fires a side effect however retryable the status looks. It is never emitted as `false` — absent is "no claim", and a `false` would assert a safety the projector cannot know. The frontend treats `completed|failed|cancelled|success|done` as terminal — note `done` is in the frontend's terminal set but is **never emitted** by the gateway as a `frame_update` status (it is only the *stored* frame status for a completed turn). |
| `kernel_status` | `frame_id`, `status` ∈ `restarted|stopped|started|env_changed|packages_installed|ended`, plus per-status extras (`generation`, `env`, `installed`, `ok`, `state`, `ended_reason`, `requires_kernel_recovery`) | Kernel lifecycle changes. A successful branch revert emits `ended` after invalidating both language slots. |
| `execution_state` | `frame_id`, `execution_id`, `owner:{kind,id}`, `status` (`queued|running|finalizing|completed|failed|cancelled`), `queue_position`, `reason` | One exact ticket changed state. |
| `execution_queue` | authoritative snapshot fields from `GET /frames/{fid}/execution` | Queue/position projection; also sent immediately after `view_session`. |
| `execution_owner` | `execution_id`, `owner`, previous identity, `reason` | Active writer changed. |
| `execution_cancel_result` | scoped cancellation result | Direct reply to a WS cancellation request. In team mode a cancellation aimed at a session the caller may not see answers `ok:false` with `reason:"session not found"` — the same sentence an unknown session gets, because which sessions exist is itself protected. |
| `view_denied` | `frame_id`, `reason` | Team mode only: `view_session` named a session this login may not see (another member's, or one with no ownership row). Refused before subscription, so neither the replay buffer, pending `await_permission` prompts, nor the queue snapshot leak. The reason is always `"session not found"`. |
| `checkpoint_created` | `branch_id`, `checkpoint_id`, `reason` | An immutable checkpoint committed. |
| `branch_created` | `branch_id`, `from_checkpoint_id` | A checkpoint-backed branch committed. |
| `branch_revert_conflict` | `branch_id`, `operation_id`, `target_checkpoint_id`, `reason` | Revert was recorded but not applied because the conflict check failed. |
| `branch_reverted` | `branch_id`, `operation_id`, `target_checkpoint_id`, `checkpoint_id`, `undo_checkpoint_id`, `ok`, `requires_kernel_recovery` | Revert committed append-only state; clients must refresh branch/recovery projections. Full previews/checkpoint records stay in the direct REST result and never enter WebSocket. |
| `branch_projection_restored` | `frame_id`, `branch_id`, `checkpoint_id` | The branch-scoped projection was rebuilt (for example after a Revert); clients holding a stale message/Notebook view must refetch it rather than patch. |
| `branch_activation_state` | `frame_id`, `root_frame_id`, `branch_id`, `checkpoint_id`, `status`/`state` | Activation of a branch runtime progressed. `status` and `state` carry the same value — a compatibility duplication kept because both spellings are already consumed. |
| `artifact_created` | **non-uniform — see below** | An artifact was produced, edited, renamed, uploaded, restored, or deleted. |
| `artifact_ref_problems` | `frame_id`, `problems[]` of `{ref, code, message}` (max 8) | One or more `@file` references in the user's message did not resolve. Emitted rather than raised: a user who referenced four files and mistyped one wants an answer about the other three plus a note, not a refusal. Codes: `not_found` (absent, or in another project — deliberately the same answer), `no_frozen_bytes`, `not_text` (a binary artifact, which used to be pasted in as replacement characters), `cross_session_not_allowed`, `materialise_failed`, `too_many_refs`. The previous behaviour was to drop an unresolvable reference silently, so the user asked about a file the model never received. |
| `attachment_problems` | `frame_id`, `problems[]` of `{name, reason, limit?, bytes?}` (max 8) | Pinned figures that exceeded this turn's image budget and were not sent. Reasons: `too_many` (more than 8 images), `too_large` (one image over 4 MiB after the pin markers are drawn), `budget_exhausted` (12 MiB total). None of these limits existed: every pinned figure was attached at full size, so eight pins on a large raster sent ~10 MiB and eighty sent ten times that. The model is told as well as the user — a system note names the missing figures and instructs it to say they were not received, rather than describe a picture it never got. |
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
  status("open"|"sent"), version_id, created_at, updated_at}`. `version_id` is
  the artifact version the pin was taken against (`null` on pins created before
  the binding existed, which fall back to the artifact's latest version).

The duplicated-key pattern (`id` + a typed id) is deliberate frontend
compatibility; keep both when touching these serializers.

## 5. Known gaps and sharp edges (summary)

- `GET /api/projects` accepts but **ignores** `limit`/`offset`; there is no
  project pagination. Real bounded reads exist for `from`/`limit` on messages,
  `limit` on frames, and the Timeline's `before_ordinal`/`after_ordinal` +
  `limit` windows (§2).
- `artifact_created` has four payload shapes; every field is optional (§3).
- Uploads are JSON/base64, not multipart, and are strict: exactly one content
  field, whitespace tolerated, anything else outside the base64 alphabet
  refused with `400` rather than decoded to other bytes or stored as text
  (§2).
- Missing resources are inconsistently signaled: some routes 404 with
  `{error}`, others return `{}` (frame/project GET), `{"ok":true}`
  (idempotent deletes), or a nulls-filled 200 (`/artifacts/{aid}/lineage`).
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
