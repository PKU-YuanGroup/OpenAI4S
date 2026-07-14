# `openai4s` package

[中文](./README_zh.md)

**Status: Implemented core, with explicitly Partial extension surfaces.** This is the top-level Python package for OpenAI4S. Its standard-library control plane composes the provider-neutral outer agent loop, native JSON control tools, persistent scientific kernels, Host RPC services, storage, security, and the Web/CLI adapters.

## Architectural position

OpenAI4S has two nested loops. The **outer loop** in [`agent/`](./agent/) accepts at most one routed action per model step: an ordered native-tool batch, an Engine-owned `finalize_response`, or one complete Python/R cell. The **inner loop** in [`kernel/`](./kernel/) keeps language workers persistent and services synchronous `host.*` calls while a Python cell is still running. [`host_dispatch.py`](./host_dispatch.py) is the compatibility/composition boundary between those planes; focused behavior lives under [`host/`](./host/).

Control-only work can finish through the Engine-owned finalizer. `host.submit_output(...)` is the only completion that can fire from inside a Python Cell; a later sole valid `finalize_response` can still close the Engine after earlier Cells. Ordinary prose, native-tool results, R cells, cancellation, and turn exhaustion are not themselves completion signals.

## Files directly in this directory

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Defines the package identity and version. It intentionally avoids booting services on import. |
| [`__main__.py`](./__main__.py) | Implements `python -m openai4s` by forwarding to the CLI entry point. |
| [`artifact_restore.py`](./artifact_restore.py) | Shared append-only Artifact restore service used by native and Web paths: verifies a historical snapshot, restores bytes inside the workspace, and records a fresh version without rewriting history. |
| [`bash_capability.py`](./bash_capability.py) | Defines the language-neutral version marker and command digest used to bind short-lived, one-shot shell capabilities. |
| [`capabilities.py`](./capabilities.py) | Resolves scoped capability enablement and specialist-profile state through repository ports. |
| [`config.py`](./config.py) | Provides zero-dependency `.env` loading plus the `LLMConfig`, `SecurityConfig`, and global `Config` dataclasses and layered environment resolution. |
| [`egress.py`](./egress.py) | Implements the Host-owned outbound-domain allowlist used at Web/shell policy boundaries. It complements, but does not replace, the OS sandbox. |
| [`host_dispatch.py`](./host_dispatch.py) | Compatibility/composition facade for kernel `host_call` RPC. It applies permission, approval, audit, replay, screening, and step-event policy before routing to focused Host services. |
| [`jobs.py`](./jobs.py) | Manages bounded, process-local background compute jobs and output buffers. Job working files may persist, but the registry itself is in memory. |
| [`mcp_client.py`](./mcp_client.py) | Pure-stdlib MCP stdio JSON-RPC client and process-wide connection manager for tools, resources, and prompts. Server-initiated sampling is out of scope. |
| [`onboarding.py`](./onboarding.py) | Testable first-run model/provider configuration service used by the headless CLI. |
| [`permissions.py`](./permissions.py) | Process-wide permission broker for allow/deny/ask rules, durable approval requests, cancellation, timeouts, and unattended fail-closed behavior. |
| [`pkgscan.py`](./pkgscan.py) | Scans Python, conda, and R environments for normalized package availability without importing those packages into the core. |
| [`prompts.py`](./prompts.py) | Stores narrowly scoped micro-prompts for compaction, review gates, provenance, Skill retrieval, extraction, editing, and security. |
| [`replay.py`](./replay.py) | Records successful `host.*` results into an offline replay tape and detects call-order drift when exported notebooks replay it. |
| [`review.py`](./review.py) | Runs a bounded, tool-free review of completed-turn evidence and normalizes the JSON verdict. It cannot mutate the workspace. |
| [`store.py`](./store.py) | Compatibility facade owning one SQLite connection, schema/migrations, guarded read queries, and focused storage repositories sharing the same lock. |
| [`webtools.py`](./webtools.py) | Implements Host-side Web search/fetch, content conversion, network switches, SSRF checks, and egress enforcement using stdlib-first transports. |

## Direct subdirectories

| Directory | Place in the architecture |
| --- | --- |
| [`adapters/`](./adapters/) | Optional ecosystem adapters kept outside the stdlib runtime core. |
| [`agent/`](./agent/) | Provider-neutral outer loop, action routing, finalization, compaction, delegation, and local runtime composition. |
| [`cli/`](./cli/) | Command-line lifecycle and one-shot task entry points. |
| [`compute/`](./compute/) | Host-side BYOC/remote-compute registry and job orchestration; general remote compute remains a Prototype surface. |
| [`execution/`](./execution/) | Shared scientific-cell admission, cancellation, dependency projection, result values, and timeout recovery. |
| [`host/`](./host/) | Focused services behind the `HostDispatcher` composition facade. |
| [`kernel/`](./kernel/) | Persistent Python/R workers, language-neutral manager protocol, environment selection, sandbox integration, and in-cell Host RPC. |
| [`llm/`](./llm/) | Provider-neutral LLM client, capabilities, normalized messages/tools, stdlib transport, and wire adapters. |
| [`mcp_servers/`](./mcp_servers/) | Bundled pure-stdlib example MCP server used for demonstration and tests. |
| [`sdk/`](./sdk/) | Compatible `host` facade injected into Python cells and the remote-compute namespace. |
| [`security/`](./security/) | Sandbox, environment isolation, code/content screening, injection checks, and related policy helpers. |
| [`server/`](./server/) | Stdlib HTTP/WebSocket workbench, session services, projections, recovery, and static UI. Several specialized UI/recovery workflows remain Partial. |
| [`skills_loader/`](./skills_loader/) | Skill discovery, progressive disclosure, sidecar validation, versioned installation, and rollback. |
| [`storage/`](./storage/) | Focused SQLite repositories used through `Store`. |
| [`tools/`](./tools/) | Class-based provider-native control tools, schemas, registry, dynamic-tool lifecycle, and compatibility fenced-call support. |

## Change rules

- Keep the core importable with the Python standard library; guard optional science dependencies at each use site.
- Add domain behavior to its focused service/repository/tool rather than rewriting `host_dispatch.py` or `store.py` wholesale.
- Preserve the kernel protocol's single frame reader, ID-routed response, transaction lock, and generation checks.
- Treat security and persistence labels literally: best-effort projections and Partial surfaces must not be documented as guarantees.
