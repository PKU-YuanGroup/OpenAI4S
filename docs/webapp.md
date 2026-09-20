# The web app

`openai4s serve` starts a pure-stdlib scientific workbench at
`http://127.0.0.1:8760/`: `http.server`, a hand-rolled WebSocket, and the
committed Vite workbench (`openai4s/server/webui/dist/`). The source lives in
`frontend/` (Preact 10 + `@preact/signals` + TypeScript). `npm run dev` serves
`http://127.0.0.1:5173/static/dist/` and proxies `/api`, `/ws`, and `/static`
to the daemon. `npm run build` writes `dist/`; that tree lands in the same PR
as the source. `OPENAI4S_WEBUI=legacy` serves the frozen `webui/index.html` +
`app.js` hatch. Satellite pages (login, replay, share, Ketcher) stay classic
scripts.

## Available now

- **Projects and sessions** — folder/date grouping, deep links, session search,
  command palette, rename/delete, and background turns that survive a closed
  tab.
- **Live turns** — prose, semantic steps, permission pauses, plans, Cell output,
  and artifacts stream over WebSocket. Reopening an in-flight session replays
  the bounded current-turn buffer; completed history reloads over REST.
- **Versioned artifacts** — file writes become immutable versions with
  provenance, environment snapshots, lineage, annotations, priority, edit,
  rename, restore, and artifact/project ZIP download. The current viewers cover
  images, CSV/TSV tables, Markdown/text, HTML/PDF previews, and 3D molecular
  structures through vendored 3Dmol. Restore verifies a trusted immutable
  snapshot and appends a fresh version plus source→restored lineage; it never
  moves the Artifact head back onto an old row. By default, completion links
  name the Artifact's current head under `/api/v1/artifacts/{artifact_id}`
  (links stored by 0.2.0 with the un-versioned `/api/artifacts/{id}` form are
  rewritten to that route when the workbench renders them). With
  `OPENAI4S_STAGE1_TRUSTED_DELIVERY=1`, completion links name the exact
  immutable version under `/api/v1/artifacts/versions/{version_id}`. Clicking, reloading,
  and reopening therefore resolves the same checksummed bytes even after a
  newer head exists. A repeated same-checksum capture does not fabricate a new
  version; its new producing Cell and lineage remain in a separate durable
  local capture observation. The scoped lineage view projects the latest
  version's path-free producer frame, so delegated code/native outputs show the
  real child frame without fabricating a root Notebook Cell or view-code link.
- **Interactive HTML previews on loopback** — the default Workbench requests
  a signed grant before navigating a report's frame anywhere, then runs the
  report on the other loopback hostname at the daemon's port; a granted URL
  is reused per Artifact version until shortly before it expires. The grant
  is bound to that origin, the minting app origin, a nonempty frame and an
  expiry; sibling resources remain scoped to the frame, and the static
  `/preview/` route resolves them by the same frame rule. Unsupported
  deployments and refused grants load the static preview instead (the exact
  version's own `/preview/<version-id>` for a historical link). Ordinary
  app-origin Artifact links remain script-free.
  Ketcher stays on the authenticated app origin. Preview grants are temporary
  bearer URLs, with [documented self-navigation disclosure limits](security.md#executable-artifact-previews-use-a-scoped-alternate-origin).
- **Recoverable completion delivery (opt-in)** — Stage 1 freezes and verifies
  each linked Artifact before the final assistant message and delivery manifest
  commit together. The link-bearing WebSocket event is emitted only afterwards
  and carries a stable `delivery_id`. If that event is lost, reopening reads the
  already committed message through REST; Stage 1 records a queryable
  `committed` durable message-and-manifest fact, while `published` is the
  best-effort emit marker. The ledger does not re-emit the socket event
  automatically. The ordinary bounded WS sequence buffer may
  replay it while the turn is live; after terminal/restart, REST is
  authoritative. A failed snapshot, checksum, scope, relation, or audit check fails
  closed and publishes no success link. Capture observations are local-only in
  Stage 1; Session packages, share snapshots, and Artifact ZIPs do not yet carry
  a portable observation ledger. The UI's metadata export only mirrors the
  current local lineage response.
- **Read-only Notebook by default** — stable Cell IDs project Python/R source,
  stdout/stderr, errors, figures, files, and retry revisions. Failed older
  revisions remain collapsed and read-only; a running Cell is updated in place.
  While the model writes a Python/R fence, one transient draft block is updated
  in place and is replaced by the immutable Cell only when execution starts.
- **Explicit developer REPL** — `OPENAI4S_NOTEBOOK_REPL=1` enables multiline
  Python/R input. Shift+Enter appends a new Cell; it never edits an executed
  Cell. User Cells and Agent turns share the same per-session FIFO execution
  coordinator.
- **Exact ownership and cancellation** — runtime events expose the active
  execution owner and queue positions. Cancel/interrupt requests send the exact
  `execution_id` plus `owner.kind` and `owner.id`. The Notebook Stop control
  selects only an active or queued `user_repl` ticket and fails closed when none
  exists, so it does not fall back to interrupting the Agent. Composer/session
  cancel targets the currently displayed exact owner.
- **Action Timeline workbench surface** — the right dock has safe cards for
  native tools (including delegation calls), Python/R Cells, domain mutations,
  and finalization, plus a Recovery card with restore/retry/fresh-restart action
  availability and a Branch panel with checkpoint fork/activate/revert/undo
  controls. Branch activation is an explicit FIFO lifecycle mutation: it stops
  the old runtime, atomically publishes the selected checkpoint side-state, and
  reports `Active`, `Partial`, or `Failed` recovery instead of pretending that
  arbitrary memory survived. Context, child-agent, and Sandbox containers remain separate. Permission
  waits still use their existing interactive prompt rather than a persisted
  Timeline card. The frontend starts
  from the latest 500 actions, can explicitly load earlier 500-action pages,
  keeps at most 2,000 actions while retaining the latest state, and never renders
  raw arguments or provider wire state. Canonical token usage is shown; cost is
  shown only when the deployment supplied explicit price metadata when the
  action was recorded, otherwise it remains unknown.
- **Portable Session packages** — a session menu export produces one
  deterministic, versioned ZIP with hashes for the ledger, Notebook, branches,
  workspace CAS, Artifact versions, environment/bootstrap references, lineage,
  plans/review/memory, and non-secret policy state. Import validates the ZIP as
  untrusted input, remaps identities into a new project/root, downgrades
  permissions/capabilities, and always opens `Ended · view only` in a durable
  quarantine. Conversation, Notebook and files remain readable, while every
  live mutation returns 423 until the user explicitly confirms `Restart fresh`;
  package code, hooks and Kernel generations are never replayed. Exact-version
  completion deliveries travel with the package, but their source ids and URLs
  do not: import verifies the restored snapshots, builds local manifests/URLs,
  and atomically rebinds each local message. A missing or inconsistent
  message/ledger relation rejects the package rather than leaving a plausible
  link that cannot reopen. Stage 2 also carries a closed, sanitized Auto Mode
  audit graph. All run, candidate, audit, finding, repair, and decision
  identities are remapped per owner; the effective selection is forced to
  `off`/`user`, Verified claims become Unverified, and the original claimed
  status/terminal reason remain diagnostic provenance across repeated exports.
  Imported wall-clock timestamps record the import boundary rather than
  trusting source clocks; event cursor order preserves chronology. Raw prompts,
  hidden rationale, permission payloads, and reusable authorization never cross
  the package boundary.
- **Customize and research UX** — model profiles, Skills/Specialists,
  connectors (catalog, enable/disable, probe, and a secret-preserving launch
  configuration editor), compute, network, memory, permission rules, plan/explore modes,
  voice dictation, uploads/paste/drag-drop, annotations, and bilingual 中文/EN. The
  connector list keeps launch commands in the editor instead of mixing local
  filesystem paths into descriptions. In-tree Python connectors persist a
  portable runtime token that resolves against the current installation when
  spawned, so moving the data directory does not retain the old server's
  interpreter path.
- **Standard environment readiness (opt-in)** — with the Stage 1 flag enabled,
  persistent dashboard and conversation banners show when the `standard`
  Python/R pair is missing or cannot be verified. Customize → Compute lists all
  missing environments and packages and offers copy-only `env plan`/`env apply`
  repair commands; the browser never installs them. A normal task may still use
  native control tools or structured finalization. If it routes a Code Cell,
  that Cell is refused before a pending environment switch, identity/attempt,
  or runtime exists; the terminal event opens the Compute repair card. Direct
  Notebook Cells use the same boundary. Approved/resumed scientific plans are
  refused before their status transition until the check is ready.
- **Web sharing (off by default)** — the session menu can publish a read-only
  snapshot to `https://<share-id>.<domain>/` through a relay you run, without
  binding a public port. The recipient views the conversation/Notebook/artifacts,
  downloads a portable bundle, and imports it into their own local install to run
  or continue (quarantined until an explicit fresh restart). See
  [webshare.md](webshare.md).

History reads have separate message, step and run-state outcomes. Failed or malformed reads show a short error and a GET-only retry control; they do not become an empty-session welcome screen. Same-session reloads retain confirmed content. WebSocket gaps clear only after all required reads and message reconciliation complete; anonymous live text stays visible until a stable terminal read can align it safely.

Completed Notebook outputs use confirmed immutable Artifact versions for figures, tables and downloads. Missing historical bindings show an unconfirmed state; unavailable versions show a read failure with a retry for that version. Opening the latest version is a separate explicit action. Fixed and latest tabs can coexist, with fixed content, environment and lineage retaining their version when a new head arrives.

JSON tables use the union of object fields in first-seen order, including keys after the 5000-row display limit; at most 100 columns are shown. Mixed arrays retain their complete original JSON instead of dropping rows. Raw text over 300000 characters starts with a marked preview; expanding uses the already fetched text and the download retains the full version.

Customize Diagnostics opens with a passive status read. Explicit checks show each item's status, detail and remedy, with known model/network/compute settings links. Facts are collapsed and limited to 20 keys and 500 characters per item. Results retain their receipt time across settings tabs; a successful configuration save marks them for rechecking. A failed check keeps prior results visibly marked as previous. Suggestions are never executed automatically.

## Notebook lifecycle and truthfulness

Python and R are lazy, independent persistent slots. A metadata-only or
tool-only turn does not start either kernel. The Notebook shows generation,
branch/revision placeholders, owner/queue, and states such as
`Live / Busy / Ended · view only / Restoring / Partial / Failed` when the
corresponding projections are available.

Stopping/restarting a kernel preserves messages, Cell history, workspace files,
and Artifact versions, but not arbitrary in-memory objects. A daemon restart
must not be described as namespace recovery unless the verified recovery
pipeline actually rebuilds and validates that state.

## Session-domain feature status

The Gateway now exposes the session-domain read/write adapters, while several
product affordances remain intentionally partial:

| Feature | Status |
|---|---|
| Action Ledger and safe Timeline projection | Backend, redacted max-500 latest/older/newer REST windows, UI cards, and an explicit “load earlier actions” control are implemented. Completed recent history reloads from REST; there is no separate durable per-action WS backlog. |
| Durable approval after daemon restart | Pending cards reload directly from SQLite without starting a kernel/runtime. A live decision resumes its exact blocked call; a post-restart approval never replays stored arguments, records the old action as unexecuted, and exposes an explicit “Continue and replan” control. Restart-only `once` grants are exact and expire after 15 minutes. |
| Checkpoint / branch / revert preview | Content-addressed snapshots and public checkpoint/fork/activate/preview/apply/undo routes are implemented. Durable Cells and user messages best-effort capture exact cursor checkpoints; only records with a proven mapping advertise Fork, while old history returns 409. The UI exposes Cell Fork and collapses internal checkpoints. Activation restores workspace, Artifact heads, environment, capability/permission state and the checkpoint's full plan/review/memory snapshot, then rebuilds a branch-bound runtime. Legacy checkpoints without that sidecar report `Partial` and preserve live structured state. Revert/Undo project provider history, chat and Notebook from the same append-only cursors. |
| Recovery Journal and verified recovery pipeline | Status/actions and `restore`/`retry`/`restart_fresh` mutations are implemented for the active branch. Candidate workers publish only after bootstrap, CAS/Artifact validation, replay-safety and state validation. Bootstrap v2 captures the actual worker's complete package set, locale, interpreter prefix and SDK/provenance/Host protocol versions. Skill-load diagnostics never leak into ordinary Cell output, but they share the untrusted Cell interpreter and therefore cannot authorize durable sidecar replay; observing one marks that generation unrecoverable. Arbitrary historical namespaces without a verified recipe still end Partial. |
| Python/R `.ipynb` export | Deterministic language export/ZIP route and a stable bundle ZIP download in the Notebook header and provenance execution view are implemented. Separate Python/R single-notebook selectors remain API-only. |
| Standalone Jupyter adapter | Optional `openai4s-python` / `openai4s-r` KernelSpecs and a lazy `ipykernel` wire bridge are available outside the daemon. They use independent namespaces and do not attach to Web-session Host RPC, artifacts, ledger, queue, or recovery. |
| Scientific renderer registry | Safe catalog and version-bound descriptor routes are implemented and drive dedicated 2D chemistry, genome, sequence/MSA, and LaTeX UI components. Descriptors stay bound to immutable Artifact versions and provenance. |
| Variable Inspector | Manual Python/R namespace refresh is implemented through a dedicated idle-only protocol request. It never starts a worker, never runs a Cell, avoids custom repr/active bindings/promises, and fails closed while Busy/Restoring/Ended. Fingerprints are bounded samples, not namespace snapshots. |
| Local model discovery | Customize → Models scans a fixed literal-loopback catalogue with proxies and redirects disabled. Results are suggestions only; a user must explicitly add a profile. Unknown local model capabilities stay conservative until explicitly configured. |
| Context composition / security panels | Safe session-specific REST projections and frontend containers are implemented. Sandbox status aggregates the Python/R workers that actually started and takes the weaker claim when they differ; it remains `not_started` only until neither language has run a self-test. |
| Delegation durability and policy | A session-wide persisted tree owns the spawn budget, child progress/results, cancellation propagation, and turn-boundary steering inbox. Child model/steps/permission/capability restrictions are enforced at both native-tool catalog and Host RPC boundaries and may not widen their parent's ceiling. Daemon restart preserves the tree but truthfully marks unfinished children `stopped: daemon_restart`; it does not claim process continuation. |
| Session export/import | Deterministic hashed packages and the dashboard/session-menu UI are implemented. Packages preserve branch-owned conversation, complete canonical provider groups, Revert projection metadata, Notebook/Artifact/lineage state, evidence-review history and checkpoint plan/review/memory snapshots. Import rejects traversal, duplicate/symlink entries, tampering, compression/size/secret violations, never overwrites an existing session, never starts a Kernel, and remains durably quarantined until a confirmed fresh restart. |

The frontend deliberately treats absent routes as unavailable: controls are
disabled or show an empty state rather than mutating history or pretending a
backend action succeeded.

## Demo session (seeded on first boot)

On first boot the app seeds a NIF3/DUF34 protein-family analysis that calls the
real UniProt and RCSB PDB APIs plus a bundled MCP connector, running six
deterministic Notebook Cells without an LLM key:

1. UniProt sequence retrieval.
2. A bundled MCP connector call.
3. A Kyte-Doolittle hydropathy plot.
4. `family_biochemistry.csv`.
5. An RCSB search and `nif3_structure.pdb` download.
6. `nif3_report.md`.

Network-unavailable steps are skipped and reported; demo data is never
fabricated.

### Conditional text editing / 文本条件编辑

The text editor reads a fixed version and verifies its SHA-256 before enabling
Save. Each write carries that baseline version; a concurrent update produces a
conflict instead of overwriting newer content. A lost or malformed save response
keeps the draft and checks version facts using reads only. Even matching current
bytes do not identify which request wrote them, and the UI never resends an
unconfirmed save automatically. Historical version tabs remain read-only.

Drafts live only in the current page's memory, keyed by session, artifact and
baseline version: at most 10 drafts and 8 MiB of original plus edited UTF-8 text.
Oversized files remain read-only. Tab/session changes, real-time refreshes and
failed saves retain drafts. Files → Retained drafts remains available after source
file/session deletion, with selectable text and explicit discard controls.
Closing an editor retains its draft; discard-and-close frees a slot. The page
asks before leaving with unsaved or unconfirmed work. Cancelling a browser reload
preserves memory; accepting it or terminating the browser destroys that memory,
so save or copy first. Nothing is written to browser persistent storage.

文本编辑器先读取固定版本并核对 SHA-256，才允许保存。每次保存携带原始版本；
并发更新会返回冲突，避免覆盖新内容。保存响应丢失或格式错误时保留草稿，只读核对
版本事实；即使当前内容相同，也不能确认是哪次请求写入，因此不会自动重发保存。
历史固定版本标签始终只读。

草稿仅保留在当前页面内存，按会话、产物和基线版本隔离，最多 10 份，原文及修改
文本的 UTF-8 总量最多 8 MiB；超大文件只读。切标签、切会话、实时刷新和保存失败
均保留草稿。Files 中的「保留的草稿」即使在源文件或会话删除后仍可访问，支持选择
复制及明确放弃。关闭编辑器保留草稿，「放弃并关闭」释放名额。有未保存或结果未知
的修改时离开页面会提示；取消浏览器刷新保留内存，明确继续刷新或终止浏览器会清空
内存，须先保存或复制。草稿不写入浏览器持久化存储。

Ship the backend, frontend source and committed Vite dist together, and roll
back that same set together; no schema or migration change is involved.
后端、前端源码与已提交 Vite dist 必须配套交付和整套回退；不涉及 schema 或迁移修改。


### Files filtering and pages / 文件筛选与分页

Files combines filename, content-type and Uploaded/Generated filters. The cards,
displayed count, empty message and Load more all describe the same result. A
session starts at 50 files and adds 50 per click; changing any filter or the
session/scope starts a new first page. Refresh preserves the number of requested
slots, including a partially filled final page. Hidden files stay excluded and
distinct artifact IDs remain distinct even when their names match. Project scope
keeps the server's artifact-index order and pagination, with hidden rows excluded
before the page limit. Late responses and
artifact events from a previous session cannot replace the current cards.

Files 可组合文件名、内容类型及上传/生成来源筛选。卡片、显示数量、空状态和
「加载更多」基于同一结果。会话首次显示 50 项，每次再加载 50 项；改变筛选、
会话或范围会重置第一页。刷新保留已请求的容量，包括尚未填满的最后一页。
隐藏文件不显示，同名但不同 ID 的产物不合并；项目范围沿用服务端索引顺序，并在分页前排除隐藏记录。
上一会话的迟到响应和产物事件不能覆盖当前卡片。
