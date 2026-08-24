# Native control tools

[中文说明](README_zh.md)

The provider-native JSON tools the agent uses to orchestrate work and to ask for permission are declared here. The control catalogue itself is implemented; each service a tool targets keeps its own Implemented, Partial, or Prototype status. Shell execution, scientific computation and `submit_output` are deliberately kept out of this package; none of them is a native tool.

## Where this fits

When a model reply contains native calls, the outer loop runs the ordered tool batch before it looks at any fenced code. Each concrete [`Tool`](./base.py) declares its own JSON schema, approval behavior, side-effect class, resource keys, output policy, and a focused `execute()`. A model-originated call enters through `Tool.invoke()` and [`HostDispatcher`](../host_dispatch.py); permissions, approvals, auditing, injection screening and activity events all apply first, and only then does a protected adapter reach `execute()`.

[`registry.py`](./registry.py) is the only place that instantiates a built-in tool class. [`catalog.py`](./catalog.py) builds the per-session progressively disclosed view and layers isolated dynamic proxies on top of it, leaving the global built-in registry untouched. A provider schema is a hint for generation, nothing more; [`schema.py`](./schema.py) enforces the supported contract again before dispatch.

Workspace-backed file tools share one capability-checked boundary. On POSIX,
they traverse pinned parents with `dir_fd`/openat-style operations and
`O_NOFOLLOW`, consume reads through the same descriptor they validated, reject
multiply linked regular files, and publish staged writes inside that pinned
parent. Native Windows lacks the required capabilities, so these operations
fail closed rather than reverting to pathname `os.replace` or check-then-open.
This protects against concurrent userspace namespace substitution, not a
compromised kernel or arbitrary changes to kernel filesystem semantics.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Public compatibility facade: re-exports the tool classes, registry helpers, native specs, schema helpers, and the batch limits. |
| [`artifacts.py`](./artifacts.py) | Artifact tools: list them, register an existing file, look up exact metadata or an exact version. Restoring a historical version is approval-gated. |
| [`background.py`](./background.py) | Submit, list, peek at and interrupt independent background Python-cell workers. This is job orchestration, not a shell runtime. |
| [`base.py`](./base.py) | The immutable class-based `Tool` contract: metadata, the Host invocation boundary, schema validation, approval target, resource keys, and provider-strict compatibility. |
| [`capabilities.py`](./capabilities.py) | Declares `search_capabilities`, which searches the hidden tool groups and activates the matching ones for the current session. Activation only ever adds. |
| [`catalog.py`](./catalog.py) | Composes the built-ins and the effective dynamic proxies into one session catalogue, sorts tools into progressive-disclosure groups, and produces the active native specs and prompt metadata. |
| [`content_search.py`](./content_search.py) | Bounded regex search over file contents inside the confined workspace. `include` recurses, like the unfiltered default: `Path.glob("*.py")` matches only direct children, so passing the schema's own example made the search *narrower* than no filter at all and said nothing about it. Four bounds can stop it — the hit cap, the candidate-file cap, the directory walk, and a file too large to finish — and whichever bites is named in the reply, because an unqualified empty result reads as "not in this tree". |
| [`contexts.py`](./contexts.py) | The narrow runtime protocols a concrete tool depends on: workspace, environment, and general control. A tool is handed one only after the Host's policy checks have passed. |
| [`data.py`](./data.py) | Read-only access to the Store: guarded schema and query, frame browsing, and bounded traversal of Artifact lineage. |
| [`delegation.py`](./delegation.py) | Start a sub-agent, list and collect direct children, stop one child by exact ID, and steer a running one. |
| [`dynamic.py`](./dynamic.py) | Validates session-authored Python tool source and manifests, then runs every smoke test and every invocation in a fresh `python -I -S` worker with a strict non-secret environment and an enforced OS sandbox. Session, project and global versions resolve through trusted proxies. |
| [`dynamic_control.py`](./dynamic_control.py) | The human-governed lifecycle for Dynamic Tools: define, list, promote, version-list, activate, rollback. |
| [`dynamic_scopes.py`](./dynamic_scopes.py) | Stores the content-addressed project and global Dynamic Tool manifests plus an append-only activation history. It never compiles or executes model-authored code. |
| [`edit.py`](./edit.py) | Exact-string editing in the workspace, with a static precheck that rejects a degenerate edit before approval is even requested. The source is read from one already verified, no-follow descriptor and the rewrite streams into a staged sibling holding one chunk at a time — carrying `len(old_string) - 1` characters across the boundary so a match straddling it is still found — and the count that decides whether the edit is allowed is only known at the end, so a refused or non-unique edit discards the staged file and leaves the original untouched. Publication is an atomic descriptor-relative rename within the pinned parent directory. The legacy `edit_file` compatibility lookup is still here. |
| [`env.py`](./env.py) | Compatibility facade for the environment list/use/create tool classes and instances. |
| [`env_create.py`](./env_create.py) | Installs packages through the kernel preinstall service. |
| [`env_list.py`](./env_list.py) | Finds the prebuilt environments and can compare their package coverage. |
| [`env_use.py`](./env_use.py) | Queues a switch to a named Python or R environment, to take effect on the next scientific cell. |
| [`fs.py`](./fs.py) | Compatibility facade for the directory-listing and text file read/write tools. |
| [`glob_files.py`](./glob_files.py) | Globs the workspace for filenames, filtering credential-shaped paths — basenames and credential directories alike — out of the result. The generator is consumed one path at a time under a `BoundedSelection`, so a directory of a million files does not cost a million retained paths to answer a thousand-path question, and the walk itself stops on an entry or a seconds budget rather than only on what it keeps. `count` is what came back — it used to be the pre-slice total printed beside a sliced list, and it disagreed with `content_search`'s `count` in the same tool family; `total_count`, `dropped` and `truncated` now carry the rest. |
| [`list_directory.py`](./list_directory.py) | Lists one workspace directory, and only inside it — under `glob_files`'s entry cap and the same truncation counters, so the two tools cut at the same place and say so the same way. It had no cap at all, and a `stat()` and a dict per entry. |
| [`mcp.py`](./mcp.py) | MCP discovery for servers, tools, resources and prompts, plus tool calls and resource/prompt reads. What an external server returns is untrusted and gets screened at the Host boundary. |
| [`native.py`](./native.py) | Turns declared tools into portable, provider-neutral `ToolSpec` metadata, and checks that every function name is legal on every supported provider. |
| [`network_access.py`](./network_access.py) | Asks a human to widen the Host-owned outbound-domain policy by exactly one domain. |
| [`progress.py`](./progress.py) | Todo read/write, approved-plan read and step updates, and the constrained review-status control. |
| [`read_text_file.py`](./read_text_file.py) | Bounded UTF-8 line-window reads inside the workspace, including the response contract for a binary file. Two bounds, because `limit` counts lines and says nothing about their length: the file is streamed under a byte budget rather than read whole and sliced, and the retained window has a character ceiling of its own. When either bites, the reply also reports what was scanned against the file's real size, so `total_lines` cannot be read as a total when it is not one. |
| [`registry.py`](./registry.py) | The single place built-in tools are instantiated, in order. It resolves a call to a tool, validates it, executes an ordered batch under the per-turn cap, formats the results, and finalizes them into one bounded observation. The legacy fenced tool-block parser also lives here. |
| [`remote_capabilities.py`](./remote_capabilities.py) | Reports remote GPU capability status. Registering a capability requires a structured probe to succeed first, and then a human approval. |
| [`model_assets.py`](./model_assets.py) | Imports one user-supplied local checkpoint into the confined session workspace under an exact-path approval, streaming its SHA-256 and leaving it explicitly staged—not admitted—until a real inference canary succeeds. |
| [`remote_compute.py`](./remote_compute.py) | Provider-neutral submit/status/result/cancel/close lifecycle for remote jobs. The native surface is implemented, while general remote compute remains a Prototype subsystem. |
| [`schema.py`](./schema.py) | The dependency-free JSON Schema subset used for definition validation, argument enforcement, normalized object schemas, and provider-strict checks. |
| [`science.py`](./science.py) | Normalized catalogue and search access to the supported public scientific databases. |
| [`search.py`](./search.py) | Compatibility facade for the glob and content-search tool classes and instances. |
| [`session.py`](./session.py) | Session status, immutable checkpoint creation, a view-only branch fork from one exact cursor, a non-mutating revert preview, and pending-approval inspection. Applying a revert is not exposed here. |
| [`skills.py`](./skills.py) | Progressive Skill search and load, plus status, history, and an approval-gated version rollback. |
| [`taxonomy.py`](./taxonomy.py) | The stable side-effect classes and the canonical resource-key/workspace-target normalization. Audit events record them, and resource conflict scheduling compares them. |
| [`web.py`](./web.py) | Compatibility facade for the Web search/fetch tool classes and instances. |
| [`web_download.py`](./web_download.py) | Fetches a URL straight into the session workspace for content `web_fetch` cannot represent as text. Two guards meet here: the URL side is `web_fetch`'s (network enabled, egress allowlist per redirect hop, SSRF refusal), and the path side is `write_file`'s (the destination is resolved against the workspace and an escape is refused **before** the request, so a rejected path does not reveal whether the URL was reachable). The byte ceiling is enforced while reading. |
| [`web_fetch.py`](./web_fetch.py) | Normalizes a single-URL fetch and its resource identity, keeping the Host soft-fail behavior intact. |
| [`web_search.py`](./web_search.py) | Normalizes live Web search, and likewise preserves the Host soft-fail behavior. |
| [`write_file.py`](./write_file.py) | Creates or overwrites one UTF-8 file in the confined workspace, and marks the write so the Web control-tool boundary can capture it as an Artifact. It writes a sibling through the already verified parent directory descriptor, then publishes with an atomic descriptor-relative rename: `write_text` truncates first and writes second, so an interrupt in between used to leave a half-written file where a complete one had been. The staged file inherits the target's mode, or an overwrite would silently tighten it. |

## Adding or changing a tool

- Put the schema, side-effect declarations, permission target, resource keys, and the behavior itself on a named `Tool` subclass; instantiate it only through `registry.py:TOOL_TYPES`. Never add a module-level tool singleton: the lowercase names the compatibility facades export are `get_tool(...)` lookups of the registered instance, and a fresh instance of the right class is a tool the registry has never seen, so it carries no permission wiring at all.
- Never call `execute()` directly on model-originated input. Go in through `invoke()`/`HostDispatcher`, or the security and audit envelope is simply skipped.
- Keep schemas portable across the supported providers, and enforce them again locally. Mark `writes_files`, network use, dangerous operations and untrusted output accurately.
- Scientific algorithms belong in code and Skills; service behavior belongs in a focused Host module. Native tools stay a small orchestration control plane.
- A Dynamic Tool must fail closed when no enforced OS sandbox is available. Static AST restrictions on their own are not an isolation boundary.
