# `openai4s` package

[中文说明](README_zh.md)

This is the top-level Python package. The core is implemented, and the extension surfaces that are still Partial are marked as such where they are described. The outer agent loop, the native JSON control tools, the persistent scientific kernels, the Host RPC services, storage, security, and the Web/CLI adapters all hang off this directory, and the control plane that composes them is standard-library only.

## Where this fits

OpenAI4S has two nested loops. The outer loop in [`agent/`](./agent/) accepts at most one routed action per model step: an ordered native-tool batch, an Engine-owned `finalize_response`, or one complete Python/R cell. The inner loop in [`kernel/`](./kernel/) keeps the language workers alive and answers synchronous `host.*` calls while a Python cell is still running. [`host_dispatch.py`](./host_dispatch.py) is the compatibility and composition boundary between those two planes; the behavior behind it lives in the focused services under [`host/`](./host/).

Control-only work can finish through the Engine-owned finalizer. From inside a Python Cell, `host.submit_output(...)` is the only thing that completes a task, and a later sole valid `finalize_response` can still close the Engine after earlier Cells have run. Ordinary prose, native-tool results, R cells, cancellation, and turn exhaustion are not themselves completion signals.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](./__init__.py) | Names the package and holds its version. Importing it starts nothing. |
| [`__main__.py`](./__main__.py) | Makes `python -m openai4s` work by handing off to the CLI entry point. |
| [`artifact_restore.py`](./artifact_restore.py) | The one Artifact restore path, shared by the native and Web routes. It verifies the historical snapshot before it copies those bytes back inside the workspace. What it records is a fresh version. History is never rewritten. |
| [`bash_capability.py`](./bash_capability.py) | Holds the language-neutral version marker and command digest that bind a short-lived, one-shot shell capability. |
| [`capabilities.py`](./capabilities.py) | Resolves whether a scoped capability or specialist profile is enabled, going through repository ports. |
| [`config.py`](./config.py) | Loads `.env` with no dependencies and defines the `LLMConfig`, `SecurityConfig`, and global `Config` dataclasses. Only the LLM credential, base URL, and model id resolve through a layered chain: the per-provider variable, then the generic `OPENAI4S_LLM_*` one, then the provider's built-in default — and for the key, the provider's own conventional variable (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, …) as a last resort. Every other field follows its own default: ports and turn budgets read one variable and fall back to a literal, `data_dir` and `skills_dir` fall back to computed paths (`~/.openai4s` and the repo's `skills/`), and `egress_allowlist` reads no variable at all because it is copied from `egress.EGRESS_GROUPS`. |
| [`egress.py`](./egress.py) | The Host-owned allowlist of outbound domains. The Web and shell policy boundaries consult it, but it is opt-in: unless `OPENAI4S_EGRESS` is set to an enforcing value (`allowlist`, `on`, `1`, `enforce`, …), the mode is `off` and outbound calls fail open with no allowlist check. When it is on, it complements the OS sandbox rather than replacing it. |
| [`host_dispatch.py`](./host_dispatch.py) | The compatibility and composition facade for the kernel's `host_call` RPC. A call passes permission, approval, audit, replay, screening, and step-event policy here before it reaches a focused Host service. |
| [`jobs.py`](./jobs.py) | Runs background compute jobs process-locally and keeps their output buffers bounded. The working files a job leaves behind may persist, but the registry itself only lives in memory. |
| [`mcp_client.py`](./mcp_client.py) | A pure-stdlib MCP stdio JSON-RPC client, plus the process-wide manager that keeps one connection per connector for tools, resources, and prompts. Server-initiated sampling is out of scope. |
| [`onboarding.py`](./onboarding.py) | The first-run model and provider configuration used by the headless CLI, kept as a small service so it can be tested. |
| [`permissions.py`](./permissions.py) | The process-wide permission broker. It resolves allow/deny/ask rules; when a user has to answer, it persists a durable approval request and blocks the turn, and it handles cancellation and timeouts. Unattended execution fails closed by default, and only by default: an operator who sets `OPENAI4S_UNATTENDED_APPROVAL=allow` opts into fail-open, and every unanswerable prompt is then allowed. |
| [`pkgscan.py`](./pkgscan.py) | Scans Python, conda, and R environments for normalized package availability, without importing any of those packages into the core. |
| [`prompts.py`](./prompts.py) | The small single-purpose prompts the core sends on its own: compaction, review gates, provenance, Skill retrieval, extraction, editing, and security. |
| [`replay.py`](./replay.py) | Records successful `host.*` results into an offline replay tape — internal plumbing calls such as provenance edges and credential reads are deliberately excluded — and detects call-order drift when an exported notebook replays that tape. |
| [`review.py`](./review.py) | Runs one bounded, tool-free review over the evidence of a completed turn and normalizes the JSON verdict. The reviewer cannot mutate the workspace. |
| [`store.py`](./store.py) | The compatibility facade over persistence. One SQLite connection lives here, and so do the schema, the migrations, and the guarded read queries. The focused storage repositories are handed that same connection and the same lock. |
| [`webtools.py`](./webtools.py) | Host-side web search and fetch. Transports are stdlib-first. Content conversion happens here, and this is also where the network switches, the SSRF checks, and egress enforcement bite. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`adapters/`](./adapters/) | Optional ecosystem adapters kept outside the stdlib runtime core. |
| [`agent/`](./agent/) | The provider-neutral outer loop. It routes actions and finalizes them, compacts context past a token threshold, fans work out to sub-agents, and composes the local runtime. |
| [`cli/`](./cli/) | Command-line lifecycle and one-shot task entry points. |
| [`compute/`](./compute/) | Host-side registry and job orchestration for BYOC/remote compute. General remote compute remains a Prototype surface. |
| [`execution/`](./execution/) | What a scientific cell goes through outside the kernel: admission, cancellation, dependency projection, result values, and timeout recovery. |
| [`host/`](./host/) | Focused services behind the `HostDispatcher` composition facade. |
| [`kernel/`](./kernel/) | Home of the persistent Python and R workers. The language-neutral manager protocol lives here too, along with environment selection, sandbox integration, and the in-cell Host RPC. |
| [`llm/`](./llm/) | The provider-neutral LLM client. Capabilities, normalized messages and tools, and a stdlib transport sit above one wire adapter per provider. |
| [`mcp_servers/`](./mcp_servers/) | Bundled pure-stdlib example MCP server used for demonstration and tests. |
| [`sdk/`](./sdk/) | Compatible `host` facade injected into Python cells and the remote-compute namespace. |
| [`security/`](./security/) | Sandboxing and child-environment isolation. It also screens code and content, checks for injection, and carries the policy helpers those layers lean on. Each layer is independent, and several can fail open. |
| [`server/`](./server/) | The stdlib HTTP/WebSocket workbench: session services, projections, recovery, and the static UI. Several specialized UI and recovery workflows remain Partial. |
| [`share/`](./share/) | Web sharing transport: the tunnel wire protocol, a stdlib WSS client, the daemon's outbound `TunnelClient`, the stateless public relay, and the SSRF-hardened bundle fetch. The snapshot itself is built server-side in `server/share_projection.py`. |
| [`skills_loader/`](./skills_loader/) | Finds Skills and discloses them progressively: name and summary first, the body only on load. It also validates sidecars, installs versions, and rolls them back. |
| [`telemetry/`](./telemetry/) | Opt-in anonymous telemetry, off by default. Counts and enumerations only, zero free text — and the enforcement is over **values**: `{"error_type": "ValueError"}` and `{"error_type": "FileNotFoundError: /home/y/unpublished/cohort.csv"}` pass the same key check. There is deliberately no domain that can hold free text, so adding such a field requires adding a domain class. |
| [`platform_support.py`](./platform_support.py) | Which platforms a kernel may start on, declared once. Windows is **refused** at the spawn path rather than warned about at onboarding — a program that warns and proceeds has made a different promise from one that refuses, and a half-working kernel is the worse outcome for a product whose claim is trustworthy results. |
| [`storage/`](./storage/) | Focused SQLite repositories used through `Store`. |
| [`benchmark/`](./benchmark/) | The runner for the versioned science-workflow benchmark whose manifests live in [`workflows/`](../workflows/README.md). Every step drives production code — the real Store, kernel manager, host dispatcher and compute manager — and only what cannot run offline is injected: the model, the network, and a package manager. A declared outcome is part of the contract, so a case expecting `failure` fails on a clean run. |
| [`tools/`](./tools/) | Class-based provider-native control tools. Each one carries its own schema. Around them sit the registry, the dynamic-tool lifecycle, and compatibility support for fenced calls. |

## Change rules

- Keep the core importable with nothing but the Python standard library; guard optional science dependencies at each use site.
- Put new domain behavior in its focused service, repository, or tool. Do not rewrite `host_dispatch.py` or `store.py` wholesale.
- The kernel protocol has invariants: one frame reader, ID-routed responses, the transaction lock, and the generation checks. Leave them intact.
- Read the security and persistence labels literally. A best-effort projection or a Partial surface must never be documented as a guarantee.

## Trust Foundation modules

- [`observability.py`](observability.py) — correlation IDs and structured, shape-redacted logs.
- [`doctor.py`](doctor.py) — one command answering whether this installation can do the work: model, runtime, isolation, disk, connectors, remote compute. Runs without the daemon, because the situation that motivates it is usually one where the daemon will not start.
- [`diagnostics.py`](diagnostics.py) — the redacted support bundle and bounded log retention.
- [`evidence.py`](evidence.py) — stdlib-only verification of an exported package, for a recipient who does not trust this host yet.
