---
title: Implementation status
description: Code-verified maturity labels for OpenAI4S core, Web, recovery, rendering, Skills, Jupyter, security, and remote compute.
canonical: true
last_verified: 2026-07-14
verification: code-and-tests
status: current
audience: [contributors, operators, users]
verified_commit: a92e736
owner: OpenAI4S maintainers
---

# Implementation status

This page describes the source tree as implemented, not the intended architecture. A label applies only to the stated scope; **Implemented** does not mean Internet-safe, multi-tenant, scientifically validated for every dataset, or live-tested against every external provider.

## Labels

| Label | Meaning |
|---|---|
| **Implemented** | Wired into the relevant product path and covered by offline contract tests for the stated scope |
| **Partial** | A useful end-to-end path exists, but important formats, lifecycle cases, product controls, or guarantees remain limited |
| **Prototype** | Executable early integration that requires expert supervision and deployment-specific validation; not a production support claim |
| **Planned** | Named in the public architecture but not available as a working end-to-end product path |
| **Historical** | Retained for compatibility or reference and not the preferred current surface |

## Core runtime

| Area | Status | Implemented scope and boundary |
|---|---|---|
| Provider-neutral outer loop | **Implemented** | Routes one ordered native-tool batch, a sole Engine-owned `finalize_response`, one complete Python/R Cell, or no action. Native calls take priority and terminal state is ledger-backed. |
| Native JSON control tools | **Implemented** | Named `Tool` classes, provider-wire normalization, permission/resource metadata, ordered results, bounded parallel read-only waves, and one registry composition root are wired. |
| Python Code-as-Action kernel | **Implemented** | Lazy persistent subprocess, stdout/stderr capture, accurate Cell line mapping, resource accounting, synchronous mid-Cell Host RPC, cancellation, and generation identity. |
| R analysis kernel | **Implemented** | Lazy independent persistent R worker using the shared frame/result protocol and fd-separated I/O. R is analysis-only and intentionally has no mid-Cell Host RPC or `host.submit_output`. |
| Kernel execution coordination | **Implemented** | Web Agent, optional user REPL, lifecycle, branch, and recovery mutations share exact FIFO ownership/leases; cancellation is scoped to an execution owner. |
| Action Ledger and completion | **Implemented** | Provider declarations, canonical tool results, attempts, terminal events, structured completion, and deterministic Web fallback messages are durable. Plain prose and max-turn exhaustion are not silently treated as completion. |
| Context compaction and raw archive | **Implemented** | Token-threshold compaction preserves atomic tool groups and archives compacted raw slices to the data directory. |
| Host capability envelope | **Implemented** | Permissions, audit/replay, activity events, file policy, untrusted-result screening, and service routing are shared by native tools and Python Host RPC where applicable. |
| Object-level provenance | **Partial** | Python worker tagging covers supported file reads, selected JSON/scalar/indexing operations, and Artifact write edges. It is Python-only and instrumentation cannot capture every third-party transformation, native object, copy, or manual file path. Cell/file/Artifact provenance remains available when object tags are absent. |
| Standalone one-shot CLI | **Implemented** | `openai4s run` composes the shared engine and lazy kernel without the Web daemon. It does not provide persistent Web-session lifecycle. |

## Web workbench and persistence

| Area | Status | Implemented scope and boundary |
|---|---|---|
| Gateway and static Web UI | **Implemented** | Standard-library HTTP/WebSocket Gateway, projects/sessions, streaming turns, permissions, plans, artifacts, Notebook trace, Customize surfaces, and safe session projections run as a local/trusted-host single-user workbench. This is not a public multi-user server. |
| HTTP/WebSocket contract | **Implemented** | The current routes and event shapes are documented manually in [Web API](../webapp-api.md) and exercised by contract tests. There is no generated OpenAPI schema, and some historical response shapes are intentionally non-uniform. |
| Action Timeline | **Implemented** | Redacted paginated REST projection and UI cards for native calls, Cells, mutations, delegation, and finalization are wired. Permission waits retain their separate interactive card rather than becoming raw Timeline arguments. |
| Versioned Artifacts | **Implemented** | Cell/control-tool file capture, append-oriented version rows, best-effort immutable snapshot binding, append-only restore, metadata, annotations, ZIP downloads, environment snapshots, and version-bound renderer descriptors are wired. A version is restore-grade immutable only when its snapshot copy and binding succeeded; object-level lineage completeness remains Partial. |
| Live Notebook trace | **Implemented** | Immutable Python/R Cell source, output, errors, figures, files, retry revisions, runtime segments, and exact ownership are projected. Direct protocol-only submission Cells are hidden from the live/read-only Notebook but remain in audit records. |
| Notebook developer REPL | **Implemented** | Explicit `OPENAI4S_NOTEBOOK_REPL=1` enables multiline Python/R input through the same FIFO queue. It is off by default and user Cells bypass the agent code classifier. |
| Branches, checkpoints, Revert/Undo | **Partial** | Content-addressed workspace snapshots, branch fork/activate, preview/apply/undo, Artifact/policy/environment and structured plan/review/memory state are wired. Cursor checkpoints are best-effort; legacy checkpoints can lack side-state; arbitrary in-memory variables are not checkpoint snapshots; a dedicated assistant-message fork affordance is absent. |
| Kernel Recovery Journal | **Partial** | Status/actions, build-first candidate workers, bootstrap manifests, frozen Python Skill sidecars, CAS/Artifact checks, replay-safety checks, and atomic publish are implemented. Without an explicit verified recipe/symbol coverage, prior namespace state remains Partial rather than guessed. |
| Portable Session export/import | **Implemented** | Deterministic hashed package, path/size/secret validation, identity remapping, downgraded authority, and ended/view-only quarantine are wired. Import never resumes a live namespace and is not an instance backup. |
| Variable Inspector | **Implemented** | Manual bounded idle-only Python/R inspection avoids custom repr/active bindings and never starts a worker. Fingerprints are samples, not namespace serialization. |
| Scientific renderer registry | **Partial** | Version-bound safe descriptors and UI paths exist for 3D molecules, 2D chemistry, genome records, sequence/MSA, tables, images, PDF, sandboxed HTML, LaTeX, Markdown, and text. Several advertised extensions/capabilities exceed the bounded local parsers (for example binary columnar tables and full chemistry/genome tooling), so catalog presence is not full format support. |
| Python/R Notebook export | **Implemented** | Deterministic per-language `.ipynb` and a stable two-language ZIP bundle are available; the UI links the bundle while language-specific selection remains API-level. |
| Local model discovery | **Implemented** | Probes a fixed loopback-only catalog with proxies/redirects disabled and returns suggestions. Unknown models remain conservative until explicitly configured. |

## Skills and extensions

| Area | Status | Implemented scope and boundary |
|---|---|---|
| Skill loader and progressive disclosure | **Implemented** | Bundled/user roots, frontmatter, enablement, search/load, compile-checked Python sidecars, origin separation, and Store-generation-safe capability lookup are wired. |
| Bundled Skill catalog | **Partial** | The current tree contains **32 bundled Skill directories**. Catalog loading is implemented; many scientific Skills require external models, packages, data services, GPUs, or SSH and are not live-validated by the default offline suite. Count the source tree for each release rather than copying older “24” or “28” claims. |
| User Skill lifecycle | **Implemented** | User-space confinement, bundled-name precedence, immutable versions, `draft`/`personal` and Web `user` origins, project overlays, and rollback are wired. User content remains executable extension code. |
| Dynamic control tools | **Partial** | Session/project/global manifests, schema/policy checks, persistence, hash binding, and rollback exist. Model-authored implementations are tested and invoked in fresh isolated Python processes under an enforced OS sandbox, and definition fails closed when that sandbox is unavailable. This remains a scoped dynamic-tool system, not a general plugin ABI or hot-unload mechanism. |
| MCP client | **Partial** | Reusable stdio JSON-RPC client and bundled example server support tools/resources/prompts. Sampling, server-initiated requests, and a general third-party connector security guarantee are outside the current client. |

## Security and operations

| Area | Status | Implemented scope and boundary |
|---|---|---|
| OS kernel sandbox adapter | **Implemented** | Seatbelt/bubblewrap detection, real write/network self-test, private temp, targeted secret read denials, status reporting, `auto` degradation, and `enforce` failure are wired. Availability and containment strength remain OS/deployment dependent. |
| Child environment allowlist | **Implemented** | Python/R children and descendants receive an allowlisted environment rather than daemon secrets. It is a spawn boundary, not protection against secrets deliberately placed in allowed channels. |
| Durable permission broker | **Implemented** | Scoped rules, pending decisions, reconnect/restart semantics, exact expiring continuation grants, default unattended denial, and audit markers are wired. Policy can still be widened by operator-approved broad rules. |
| Code/content/biosecurity screening | **Partial** | Agent Cell classifier and injection annotation are wired; CLI also calls the trajectory screener. Classifier/scanner/model exceptions have fail-open paths, injection is annotation-only, `ESCALATE` is advisory, and Web currently lacks trajectory-screen invocation. |
| Workbench authentication | **Partial** | Loopback deployment and optional process token/origin checks are implemented for a trusted operator. There are no user identities, roles, tenant isolation, TLS termination, or public-service hardening. |
| Backup and disaster recovery | **Partial** | Durable application state and Session portability exist, but there is no built-in whole-instance backup scheduler, cross-file hot snapshot, down-migration, or automatic disaster-recovery orchestrator. Operators must take stopped whole-data-directory backups. |

## External platforms

| Area | Status | Implemented scope and boundary |
|---|---|---|
| General `host.compute` | **Prototype** | `ssh:<alias>` and discovered `byoc:<id>` routing, SDK handles, NVIDIA provider code, and result methods exist. Submission is approval-gated; result/cancel/close do not request a second approval, and legacy direct SSH/SCP helpers bypass the Tool permission gate. Job/sandbox state is process-local, no manager background poller is implemented, generic SSH staging/exit/output harvest is incomplete, and live external tests are opt-in. See [Remote compute](../compute.md). |
| `host.fold` / `host.score_mutations` | **Partial** | Registered SSH wrappers return structured real results or explicit no-fabrication errors. Provisioning, model/weight attestation, scientific validation, ongoing health, and remote retention remain deployment responsibilities. |
| Remote capability provisioner | **Prototype** | LLM specialist can inspect/provision through approved shell actions and register a path/executable only after a probe. It is not a deterministic installer or scientific verifier. |
| Local compute-job API | **Prototype** | Host-side process launch, in-memory listing/output state, and cancellation exist for the trusted local UI. Jobs use a confined shared root by default or a caller-selected relative subdirectory; they do not automatically receive job-ID directories. Job metadata is not a durable registry; only command-created working files may remain. This surface is outside the worker sandbox and must not be exposed to untrusted users. |
| Model endpoint provider execution | **Partial** | Endpoint/configuration records and discovery surfaces exist; the fully scoped inference execution path described by the provider architecture is not uniformly wired. |
| SLURM, Kubernetes, Modal, and laboratory providers | **Planned** | Named public extension categories do not have working built-in end-to-end providers in the current tree. “Planned” carries no release-date commitment. |

## Jupyter compatibility

| Area | Status | Implemented scope and boundary |
|---|---|---|
| KernelSpec export/install | **Implemented** | Pure-stdlib description, export, user/prefix install, replacement checks, and Python/R specs are wired. |
| Standalone Jupyter wire bridge | **Partial** | Optional `ipykernel>=7,<8` bridge supports persistent standalone Python/R execution, text streams, structured errors, interrupt, and shutdown over the existing worker protocol. It has an independent namespace and no Web-session Host RPC, Artifacts, Ledger, permissions, queue, or recovery. Rich display/comms, debugger, completion, inspection, history, stdin, and arbitrary user expressions are absent. |
| Attach Jupyter to a live Web session | **Planned** | No supported adapter attaches an external Jupyter frontend to an existing Workbench namespace or Host RPC context. |

## Compatibility surfaces

| Area | Status | Boundary |
|---|---|---|
| Fenced legacy `tool` blocks | **Historical** | Parser remains for saved prompts/older clients but native provider JSON tools are the advertised control plane. |
| Minimal `server/daemon.py` UI | **Historical** | A smaller compatibility server remains in the tree; `openai4s serve` composes the full Gateway workbench. |
| Compatibility facades | **Implemented** | `gateway.py`, `host_dispatch.py`, `store.py`, `sdk/host.py`, and selected imports preserve public contracts while behavior is extracted into focused services. They are composition boundaries, not new feature homes. |

## Verification policy

The default `uv run pytest` suite is offline and excludes markers requiring external networks, live models, GPUs, SSH, Docker, browsers, or laboratory hardware. Passing it verifies deterministic contracts, not the external environment. Kernel, Gateway/WebSocket, browser, OS sandbox, remote compute, and scientific-model changes need the targeted real-runtime checks described in [Release validation](../release-validation.md) and [Operations](../operations/).

When implementation and prose disagree, code plus executable tests win. Update this page in the same change that wires, removes, or materially narrows a surface.
