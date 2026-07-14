# Native control tools

[中文](./README_zh.md)

**Status: Control catalogue Implemented; target services retain their own Implemented/Partial/Prototype status.** This package declares provider-native JSON tools for orchestration and permission control. It does not turn shell execution, scientific computation, or `submit_output` into native tools.

## Architectural position

When a model reply contains native calls, the outer loop routes the ordered tool batch before considering fenced code. Each concrete [`Tool`](./base.py) declares its JSON schema, approval behavior, side-effect class, resource keys, output policy, and focused `execute()` behavior. A model-originated call enters through `Tool.invoke()` and [`HostDispatcher`](../host_dispatch.py), where permissions, approvals, auditing, injection screening, and activity events apply before a protected adapter calls `execute()`.

[`registry.py`](./registry.py) is the only place that instantiates built-in tool classes. [`catalog.py`](./catalog.py) builds a per-session progressively disclosed view and adds isolated dynamic proxies without mutating the global built-in registry. Provider schemas are generation hints; [`schema.py`](./schema.py) enforces the supported contract again before dispatch.

## Files directly in this directory

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Public compatibility facade re-exporting tool classes, registry helpers, native specs, schema helpers, and batch limits. |
| [`artifacts.py`](./artifacts.py) | Declares Artifact listing, existing-file registration, exact metadata/version lookup, and approval-gated historical-version restore tools. |
| [`background.py`](./background.py) | Declares submit/list/peek/interrupt controls for independent background Python-cell workers; it is not a shell runtime. |
| [`base.py`](./base.py) | Defines the immutable class-based `Tool` contract, metadata, Host invocation boundary, schema validation, approval target, resource keys, and provider-strict compatibility. |
| [`capabilities.py`](./capabilities.py) | Declares `search_capabilities`, which searches and monotonically activates hidden tool groups for the current session. |
| [`catalog.py`](./catalog.py) | Composes built-ins and effective dynamic proxies into a session catalogue, classifies progressive-disclosure groups, and produces the active native specs/prompt metadata. |
| [`content_search.py`](./content_search.py) | Declares bounded regex content search within the confined workspace. |
| [`contexts.py`](./contexts.py) | Defines narrow workspace, environment, and general control runtime protocols used by concrete tools after Host policy checks. |
| [`data.py`](./data.py) | Declares guarded read-only Store schema/query, frame browsing, and bounded Artifact-lineage traversal tools. |
| [`delegation.py`](./delegation.py) | Declares sub-agent start, direct-child list/collect, exact stop, and live steering controls. |
| [`dynamic.py`](./dynamic.py) | Validates session-authored Python tool source/manifests and runs every smoke test/invocation in a fresh `python -I -S` worker with a strict non-secret environment and enforced OS sandbox; resolves session/project/global versions through trusted proxies. |
| [`dynamic_control.py`](./dynamic_control.py) | Declares human-governed define, list, promote, version-list, activate, and rollback operations for Dynamic Tools. |
| [`dynamic_scopes.py`](./dynamic_scopes.py) | Stores content-addressed project/global Dynamic Tool manifests and append-only activation history without compiling or executing model-authored code. |
| [`edit.py`](./edit.py) | Declares exact-string workspace editing with a static precheck and retains the legacy `edit_file` compatibility lookup. |
| [`env.py`](./env.py) | Compatibility facade for environment list/use/create tool classes and instances. |
| [`env_create.py`](./env_create.py) | Declares package installation through the kernel preinstall service. |
| [`env_list.py`](./env_list.py) | Declares prebuilt-environment discovery and optional package-coverage comparison. |
| [`env_use.py`](./env_use.py) | Declares a queued switch to a named Python or R environment for the next scientific cell. |
| [`fs.py`](./fs.py) | Compatibility facade for directory listing and text file read/write tools. |
| [`glob_files.py`](./glob_files.py) | Declares workspace globbing while filtering credential-shaped basenames. |
| [`list_directory.py`](./list_directory.py) | Declares confined listing of one workspace directory. |
| [`mcp.py`](./mcp.py) | Declares MCP server/tool/resource/prompt discovery plus tool calls and resource/prompt reads; untrusted external output is screened at the Host boundary. |
| [`native.py`](./native.py) | Converts declared tools to portable provider-neutral `ToolSpec` metadata and validates cross-provider function names. |
| [`network_access.py`](./network_access.py) | Declares a human-approved request to widen the Host-owned outbound-domain policy for one domain. |
| [`progress.py`](./progress.py) | Declares todo read/write, approved-plan read/step update, and constrained review-status controls. |
| [`read_text_file.py`](./read_text_file.py) | Declares bounded UTF-8 line-window reads inside the workspace, including the binary-file response contract. |
| [`registry.py`](./registry.py) | Creates the ordered built-in instances; resolves tools; parses legacy fenced tool blocks; validates, executes, limits, formats, and finalizes ordered tool batches. |
| [`remote_capabilities.py`](./remote_capabilities.py) | Declares remote GPU capability status and approval-gated registration after a structured probe. |
| [`remote_compute.py`](./remote_compute.py) | Declares provider-neutral remote job submit/status/result/cancel/close lifecycle controls. The native surface is implemented, while general remote compute remains a Prototype subsystem. |
| [`schema.py`](./schema.py) | Implements the dependency-free JSON Schema subset used for definition validation, argument enforcement, normalized object schemas, and provider-strict checks. |
| [`science.py`](./science.py) | Declares normalized catalogue/search access to supported public scientific databases. |
| [`search.py`](./search.py) | Compatibility facade for glob and content-search tool classes and instances. |
| [`session.py`](./session.py) | Declares session status, immutable checkpoint creation, exact view-only branch fork, non-mutating revert preview, and pending-approval inspection. Applying a revert is not exposed here. |
| [`skills.py`](./skills.py) | Declares progressive Skill search/load plus status/history and approval-gated version rollback controls. |
| [`taxonomy.py`](./taxonomy.py) | Defines stable side-effect classes and canonical resource-key/workspace-target normalization used by audit and conflict scheduling. |
| [`web.py`](./web.py) | Compatibility facade for Web search/fetch tool classes and instances. |
| [`web_fetch.py`](./web_fetch.py) | Declares normalized single-URL fetch and resource identity while preserving Host soft-fail behavior. |
| [`web_search.py`](./web_search.py) | Declares normalized live Web search and preserves Host soft-fail behavior. |
| [`write_file.py`](./write_file.py) | Declares UTF-8 create/overwrite for one confined workspace file and marks the write so the Web control-tool boundary can capture an Artifact. |

## Direct subdirectories

None.

## Adding or changing a tool

- Put schema, side-effect declarations, permission target, resource keys, and focused behavior on a named `Tool` subclass; instantiate it only through `registry.py:TOOL_TYPES`.
- Do not call `execute()` directly for model-originated input. Enter through `invoke()`/`HostDispatcher` so the security and audit envelope cannot be skipped.
- Keep schemas portable across supported providers and enforce them again locally. Mark `writes_files`, network use, dangerous operations, and untrusted output accurately.
- Keep scientific algorithms in code/Skills and service behavior in focused Host modules. Native tools should remain a small orchestration control plane.
- Dynamic Tools must fail closed when an enforced OS sandbox is unavailable; static AST restrictions alone are not an isolation boundary.
