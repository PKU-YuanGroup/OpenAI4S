# Tests

[中文](README_zh.md)

This directory is the correctness gate for OpenAI4S. The default pytest suite exercises the provider-neutral agent engine, Host services, persistent Python/R kernel protocol, repositories, security boundaries, tools, Skills, and Web composition with deterministic fakes. It is distinct from the reusable scenario/evaluation layer in [`../harness/`](../harness/).

## Offline contract

- `uv run pytest` must not require a live LLM, API key, network, GPU, SSH host, Docker daemon, browser, or lab system. [`conftest.py`](conftest.py) redirects `~/.openai4s` to a temporary directory and installs a fake provider/key for every test.
- External-resource tests must be explicit opt-ins under markers registered in `pyproject.toml`; [`test_marker_policy.py`](test_marker_policy.py) guards that policy.
- Captured inputs and byte-sensitive samples live under `fixtures/`; tests must not silently rewrite them.
- Network, subprocess, provider, clock, UUID, and filesystem boundaries are mocked or confined unless a separately invoked smoke program clearly says otherwise.
- Run one module with `uv run pytest tests/test_kernel.py`, one case with `uv run pytest tests/test_agent.py::test_max_turns_stop`, or the full gate with `uv run pytest`.

## Support and smoke files

| File | Responsibility |
| --- | --- |
| [`conftest.py`](conftest.py) | Establishes import paths, isolated per-test data directories, fake LLM configuration/key, Store cleanup, and shared pytest fixtures. |
| [`browser_smoke.mjs`](browser_smoke.mjs) | Real-browser smoke driver for the running Gateway UI and streamed interaction paths. It is invoked separately from local pytest and runs automatically in the normal PR CI workflow. |
| [`scientific_renderers_smoke.cjs`](scientific_renderers_smoke.cjs) | Dependency-light Node contract runner for the UMD scientific artifact parsers in the Web UI. |

## Test modules

Every `test_*.py` file is listed below. The description names its primary contract boundary; individual modules often include additional regression cases around failure, restart, redaction, and concurrency.

| File | Primary coverage |
| --- | --- |
| [`test_action_ledger_repository.py`](test_action_ledger_repository.py) | Append-only canonical Action Ledger groups/events, atomic tool groups, execution attempts, migrations, and immutable terminal state. |
| [`test_action_ledger_runtime.py`](test_action_ledger_runtime.py) | Runtime Ledger writing, argument redaction, branch-prefix inheritance, interruption reduction, and restart reconstruction. |
| [`test_action_routing_eval.py`](test_action_routing_eval.py) | Scored offline Tool/Code/Finalize routing fixtures and reviewable failure/confusion reports. |
| [`test_action_timeline_service.py`](test_action_timeline_service.py) | Bounded, redacted public Timeline projections for actions, attempts, permissions, failures, and branch cursors. |
| [`test_actions.py`](test_actions.py) | Parsing and priority of native calls, Python/R fences, incomplete/unsupported fences, first-cell selection, and finalization. |
| [`test_admet_genetic.py`](test_admet_genetic.py) | Discovery and deterministic helper/output contracts for the bundled ADMET genetic Skill. |
| [`test_agent.py`](test_agent.py) | Offline outer loop, Code-as-Action cycles, delegation, compaction, artifact paths, completion rules, and max-turn stop. |
| [`test_agent_control.py`](test_agent_control.py) | Ordered native-tool batches, schema failures, barriers, parallel reads, cancellation, and complete canonical result closure. |
| [`test_agent_engine.py`](test_agent_engine.py) | Pure `AgentEngine` orchestration through fake ports, routing precedence, replay-safe history, cancellation, and completion reasons. |
| [`test_agent_hybrid.py`](test_agent_hybrid.py) | Minimal hybrid `Agent` facade integration and state reset between reused tasks. |
| [`test_agent_profile_repository.py`](test_agent_profile_repository.py) | Named agent-profile CRUD, serialization edges, ordering, commits, and concurrent update semantics. |
| [`test_agent_runtime.py`](test_agent_runtime.py) | Local model/action/cell adapters, dynamic tool catalogues, canonical observations, validation, limits, and submit-only completion. |
| [`test_analysis_skills.py`](test_analysis_skills.py) | Executable bundled analysis Skills: discovery, compilation, data audit, classification/regression metrics, and deterministic bootstrap. |
| [`test_annotation_repository.py`](test_annotation_repository.py) | Image annotation persistence, atomic ordinal allocation, transaction/cascade behavior, and Store facade parity. |
| [`test_artifact_control_tools.py`](test_artifact_control_tools.py) | Native Artifact lifecycle schemas/policies, scoped metadata, verified immutable restore, approval, audit, and rollback safety. |
| [`test_artifact_manager.py`](test_artifact_manager.py) | Workspace Artifact capture/versioning, provisional coalescing, snapshots, provenance merge, protection, restore, and broadcasts. |
| [`test_artifact_mutation_service.py`](test_artifact_mutation_service.py) | Interactive edit/rename/upload mutations, event shape, text eligibility, versioning, and workspace-escape rejection. |
| [`test_artifact_repository.py`](test_artifact_repository.py) | Artifact/version/environment/lineage repositories, exact scope, transaction rollback, snapshot binding, listing, and restore metadata. |
| [`test_artifact_scope.py`](test_artifact_scope.py) | Artifact ownership inheritance and conflict rules across frame, root session, and project scopes. |
| [`test_backend_import_contract.py`](test_backend_import_contract.py) | Declared backend facade imports/exports and compatibility boundaries during modularization. |
| [`test_background_cleanup.py`](test_background_cleanup.py) | Session shutdown interruption/kill of independent background kernels without worker leaks. |
| [`test_bash_authorization.py`](test_bash_authorization.py) | One-shot `host.bash` capabilities bound to command, cwd, generation, challenge, expiry, and path confinement. |
| [`test_capability_state.py`](test_capability_state.py) | Durable Skill/Specialist enablement, scope precedence, loader rebinding, sidecar versions, events, and restart behavior. |
| [`test_catalyst_sar_screening.py`](test_catalyst_sar_screening.py) | Discovery, documentation, safe examples, and helper contracts for the catalyst SAR Skill. |
| [`test_cell_dependencies.py`](test_cell_dependencies.py) | Conservative Python/R static dependency analysis, namespace mutations, uncertainty, and transitive stale projections. |
| [`test_cell_execution_service.py`](test_cell_execution_service.py) | Web Cell transaction ordering, generation identity, bounded live output, logging, capture, interruptions, and protocol-only completion visibility. |
| [`test_cell_watchdog.py`](test_cell_watchdog.py) | Timeout policy, permission-paused budgets, exact cancellation, SIGINT, hard-kill respawn, and bootstrap recovery. |
| [`test_checkpoint_state_snapshots.py`](test_checkpoint_state_snapshots.py) | Immutable checkpoint binding of plan/review/memory state, migrations, revert/undo restore, legacy partial state, and atomicity. |
| [`test_cli_contract.py`](test_cli_contract.py) | Supported CLI entry points, subcommands, options, environment setup choices, help text, and invalid invocation behavior. |
| [`test_compute_nvidia.py`](test_compute_nvidia.py) | Offline NVIDIA BYOC provider discovery, hosted/self-hosted creation, command construction, parameters, and secret scrubbing. |
| [`test_config.py`](test_config.py) | Layered configuration, placeholder-key filtering, provider/generic fallbacks, environment parsing, and Notebook REPL flag. |
| [`test_connector_repository.py`](test_connector_repository.py) | MCP connector CRUD, JSON normalization, ordering, enable/disable, commits, and Host service integration. |
| [`test_context_policy_web.py`](test_context_policy_web.py) | Durable Web Context Policy, compaction history, and large-output Artifact deduplication/linkage. |
| [`test_data_background_tools.py`](test_data_background_tools.py) | Class-based native data/background tools, schemas, policies, Host forwarding, read-only query, submit approval, and exact interrupt. |
| [`test_delegation_persistence.py`](test_delegation_persistence.py) | Restart-safe child state, budgets, stale leases, steering delivery, cascade cancellation, and deletion cleanup. |
| [`test_delegation_policy.py`](test_delegation_policy.py) | Enforced child capability/permission policies, invalid-policy rejection, and prevention of nested policy widening. |
| [`test_delegation_runtime.py`](test_delegation_runtime.py) | Tree-wide delegation budgets, depth limits, canonical child ledgers, cancellation, lineage, concurrency, and live steering. |
| [`test_dynamic_tool_scopes.py`](test_dynamic_tool_scopes.py) | Project/global/session Dynamic Tool resolution, version activation, promotion, rollback, isolation, audit, and tamper rejection. |
| [`test_dynamic_tools.py`](test_dynamic_tools.py) | One-shot isolated Dynamic Tool workers, source gate, secret-free environment, enforced sandbox, schema/TTL/permission checks, and timeouts. |
| [`test_e2e.py`](test_e2e.py) | Offline end-to-end Code-as-Action flow through Skill use and kernel-error observation. |
| [`test_egress.py`](test_egress.py) | Outbound domain allowlist modes, scientific/package domains, suffix matching, lookalike rejection, and URL parsing. |
| [`test_environments.py`](test_environments.py) | Prebuilt environment discovery, default selection, hidden/R-only filtering, executable resolution, and overrides. |
| [`test_execution_coordinator.py`](test_execution_coordinator.py) | Core FIFO per-session writer ownership, independent sessions, exact cancel/interrupt, exception release, and close behavior. |
| [`test_execution_view_service.py`](test_execution_view_service.py) | Execution/Notebook DTO ordering, retries, runtime boundaries, protocol-only cells, dependencies, stale state, and lineage. |
| [`test_frame_repository.py`](test_frame_repository.py) | Project/frame/message/step/cell-log repositories, hierarchy, sequence, browsing, search, metadata, JSON fallback, and commits. |
| [`test_gateway.py`](test_gateway.py) | WebSocket hub resume/coalescing/trim ordering, static startup behavior, and bounded live Notebook/activity state. |
| [`test_gateway_engine.py`](test_gateway_engine.py) | `AgentEngine`-backed Web runner integration: native tools, artifacts, streaming, plan restrictions, environment switching, and cancellation. |
| [`test_gateway_kernel_lifecycle.py`](test_gateway_kernel_lifecycle.py) | Supervised lazy Python/R slots, stop/start races, dead-worker replacement, bootstrap locks, environment replacement, and R soft failure. |
| [`test_gateway_lazy_runtime.py`](test_gateway_lazy_runtime.py) | Tool-only no-kernel turns, lazy first Cell/REPL spawn, session runtime reuse, structured finalization, and durable attempt ordering. |
| [`test_gateway_session_domain_routes.py`](test_gateway_session_domain_routes.py) | Gateway routes for deletion, checkpoint/fork, promotion, branch activation, revert/undo, and shared session-domain composition. |
| [`test_gateway_session_lifecycle.py`](test_gateway_session_lifecycle.py) | Durable generation/attempt IDs, TTL sweeps, startup reconciliation, project deletion admission, cleanup, and race handling. |
| [`test_global_research_views.py`](test_global_research_views.py) | Cross-session project Timeline and lineage read models, scope, ordering, bounded output, and redaction. |
| [`test_governance.py`](test_governance.py) | Repository governance/security automation such as workflow pinning, permissions, release discipline, and source-policy contracts. |
| [`test_harness_characterization.py`](test_harness_characterization.py) | Deterministic r5 production probes, normalized golden comparison, known-bug labelling, and explicit regeneration behavior. |
| [`test_harness_contract.py`](test_harness_contract.py) | Harness schema, scripted provider, faults, normalization, runner invariants, CLI selection/errors, and required PR scenarios. |
| [`test_host_completion_service.py`](test_host_completion_service.py) | `host.submit_output` validation, sole in-Cell completion semantics, artifact/public fields, and duplicate/failure handling. |
| [`test_host_contract.py`](test_host_contract.py) | Worker-to-Host API wire surface, injected facade behavior, soft errors, audit context, and compatibility contracts. |
| [`test_host_credentials_service.py`](test_host_credentials_service.py) | Session-local credential references, provider scoping, secret non-disclosure, lookup, and audit behavior. |
| [`test_host_data_service.py`](test_host_data_service.py) | Store-backed `host.query`, data registration, lineage/version lookup, read-only SQL enforcement, and public result shape. |
| [`test_host_delegation_service.py`](test_host_delegation_service.py) | Host delegation, steering, cancellation, tree policy/budget forwarding, progress events, and soft failures. |
| [`test_host_endpoint_service.py`](test_host_endpoint_service.py) | Managed endpoint create/status/request/close lifecycle, validation, authorization, and secret-safe projection. |
| [`test_host_llm_service.py`](test_host_llm_service.py) | In-kernel `host.llm` model configuration, message/schema validation, usage/audit, cancellation, and error contracts. |
| [`test_host_mcp_service.py`](test_host_mcp_service.py) | MCP connector enablement, tool discovery/call routing, configuration, errors, audit, and disabled-state behavior. |
| [`test_host_progress_service.py`](test_host_progress_service.py) | Session todo and approved-plan progress updates, scope/status validation, persistence, and emitted events. |
| [`test_host_remote_capability_service.py`](test_host_remote_capability_service.py) | Verified remote capability probes, structured inputs, SSH boundary rejection, activity projection, and SDK wire behavior. |
| [`test_host_remote_science_service.py`](test_host_remote_science_service.py) | Remote fold/mutation-scoring orchestration, provider contracts, no-fabrication checks, artifacts, and failure projection. |
| [`test_host_skill_service.py`](test_host_skill_service.py) | Host Skill search/read/execute/version behavior, policy, sidecars, scoped capability state, and audit. |
| [`test_host_workspace_service.py`](test_host_workspace_service.py) | Confined Host workspace read/write/list operations, version capture, path safety, and compatibility shapes. |
| [`test_jupyter_adapter.py`](test_jupyter_adapter.py) | Optional Jupyter KernelSpec discovery and wire adapter framing, execution, timeouts, interrupts, and absence handling. |
| [`test_kernel.py`](test_kernel.py) | Persistent Python worker namespace, stdout/stderr, error attribution, Host mid-cell RPC, locks, background work, provenance, and deadlock resistance. |
| [`test_kernel_generation_storage.py`](test_kernel_generation_storage.py) | Durable kernel-generation/attempt allocation, lease binding, terminal transitions, activity, migrations, and restart state. |
| [`test_kernel_generation_supervisor.py`](test_kernel_generation_supervisor.py) | Persistent UUID generation identity over supervisor ABA counters, replacement fencing, leases, and status projection. |
| [`test_kernel_recovery.py`](test_kernel_recovery.py) | Recovery candidate isolation, exact generation commit/rollback, sidecar/environment replay, failure cleanup, and namespace restoration. |
| [`test_kernel_sandbox.py`](test_kernel_sandbox.py) | Python/R OS sandbox command construction, self-test, auto/enforce/off behavior, workspace confinement, network denial, and secret-free child env. |
| [`test_kernel_supervisor.py`](test_kernel_supervisor.py) | Protocol-neutral worker lifecycle, lazy start, leases, restart, exact interrupt, concurrent callers, and shutdown. |
| [`test_lazy_kernel.py`](test_lazy_kernel.py) | Thread-safe one-shot CLI kernel ownership, lazy startup, context cleanup, and no-spawn tool/finalize paths. |
| [`test_llm_anthropic_tool_calls.py`](test_llm_anthropic_tool_calls.py) | Lossless Anthropic Messages native-tool encode/decode, IDs, ordering, arguments, history, streaming, and malformed input. |
| [`test_llm_capabilities.py`](test_llm_capabilities.py) | Provider capability catalogue, model feature resolution, token/usage normalization, and canonical accounting. |
| [`test_llm_gemini_tool_calls.py`](test_llm_gemini_tool_calls.py) | Lossless Gemini `generateContent` native-tool encode/decode, thought/content parts, history, streaming, and malformed calls. |
| [`test_llm_openai_tool_calls.py`](test_llm_openai_tool_calls.py) | Lossless OpenAI Chat native-tool encode/decode, ordering, argument preservation, history, streaming, and errors. |
| [`test_llm_providers.py`](test_llm_providers.py) | Multi-provider/multimodal stdlib LLM transport, config resolution, payloads, SSE, retries/errors, usage, and image handling. |
| [`test_llm_responses_tool_calls.py`](test_llm_responses_tool_calls.py) | OpenAI Responses native-tool wire assembly/parsing, item ordering, IDs, streaming deltas, usage, and error cases. |
| [`test_local_model_discovery.py`](test_local_model_discovery.py) | Loopback-only model endpoint discovery, redirect denial, bounded probes, deduplication, and non-mutating public results. |
| [`test_marker_policy.py`](test_marker_policy.py) | Registration and explicit opt-in rules for external/network/live-LLM/GPU/SSH/Docker/browser/lab test markers. |
| [`test_mcp_client.py`](test_mcp_client.py) | Offline MCP JSON-RPC framing, stdio lifecycle, request correlation, timeouts/errors, and strict child-environment allowlist. |
| [`test_mcp_control_tools.py`](test_mcp_control_tools.py) | Class-owned MCP native-tool schemas, policy/resources, connector listing, tool listing/calls, and Host forwarding. |
| [`test_memory_repository.py`](test_memory_repository.py) | Long-term memory CRUD, scope, deduplication, search/ranking, invalid data fallback, and Store facade behavior. |
| [`test_metadata_repositories.py`](test_metadata_repositories.py) | Small metadata/settings-like repositories, serialization, upsert/delete, ordering, transactions, and facade parity. |
| [`test_methodology_skills.py`](test_methodology_skills.py) | Discovery/content contracts for the six methodology-only bundled Skills. |
| [`test_mineral_spectra_analysis.py`](test_mineral_spectra_analysis.py) | Offline discovery and deterministic spectrum parsing/matching/report helpers for the mineral analysis Skill. |
| [`test_model_catalog.py`](test_model_catalog.py) | Extensible provider/model catalogues, aliases/defaults, profile composition, capability metadata, and migration. |
| [`test_native_tools.py`](test_native_tools.py) | Provider-neutral native-tool declarations, independent schema copies, naming limits, progressive groups, and exclusion of shell/completion. |
| [`test_notebook_export.py`](test_notebook_export.py) | Separate deterministic Python/R read-only notebook exports, bundle manifest/checksums, and language validation. |
| [`test_onboarding.py`](test_onboarding.py) | Deterministic first-run provider setup, validation, defaults, provider switching, key clearing, and secret-free responses. |
| [`test_orchestration_skills.py`](test_orchestration_skills.py) | End-to-end runtime-orchestration Skills for kernel Host facade, customization, self-awareness, endpoints, compute/env setup, and audit. |
| [`test_permission_repository.py`](test_permission_repository.py) | Durable permission-rule normalization, scope/specificity resolution, absolute deny, defaults, upgrades, atomic seeding, and concurrent upsert. |
| [`test_permissions.py`](test_permissions.py) | Tool-call permission gate precedence across global/project/conversation rules, patterns, secret env denial, CRUD, and fallback. |
| [`test_plan.py`](test_plan.py) | Plan JSON/prose extraction, normalization, public step merge, persistence, review, and auto-execute foundations. |
| [`test_plan_repository.py`](test_plan_repository.py) | `PlanRepository` parity through `Store`, malformed JSON fallback, status merge, updates, and deletion. |
| [`test_plan_service.py`](test_plan_service.py) | Plan draft/finalize/artifact lifecycle, public state, discard, normal-turn execution, guards, revisions, and event failures. |
| [`test_protein_mutation_enhancement_skill.py`](test_protein_mutation_enhancement_skill.py) | Deterministic mutation enumeration, application, scoring/ranking, selection rounds, thresholds, and next-position suggestions. |
| [`test_provenance_paths.py`](test_provenance_paths.py) | Worker filesystem canonicalization and object-provenance read/write identity across cwd changes and real kernel execution. |
| [`test_public_api_contract.py`](test_public_api_contract.py) | Supported public imports, package version, constructors/signatures, `run_task`, Host facade, and server facade compatibility. |
| [`test_r_kernel.py`](test_r_kernel.py) | R worker FD protocol, persistence, output isolation, variable inspection, child environment, interrupts, errors, and deadlock resistance. |
| [`test_recovery_recipe.py`](test_recovery_recipe.py) | Conservative dependency-closed Python/R recovery recipes, external/manual state, exact source hashes, environment, and sidecar bootstrap. |
| [`test_release_gates.py`](test_release_gates.py) | Source secret scanning and release archive verification for required resources, clean dependencies, and safe synthetic fixtures. |
| [`test_remote_capability_probe.py`](test_remote_capability_probe.py) | Structured remote probe validation/quoting, shell rejection before SSH, activity visibility, and SDK forwarding. |
| [`test_remote_compute_control_tools.py`](test_remote_compute_control_tools.py) | Class-based remote compute submit/result/cancel/close schemas, policy/resources, approval boundary, and SDK forwarding. |
| [`test_renderer_registry.py`](test_renderer_registry.py) | Deterministic artifact renderer selection, duplicate-ID rejection, and version/provenance-preserving public descriptors. |
| [`test_retrosynthesis_planning.py`](test_retrosynthesis_planning.py) | Retrosynthesis Skill discovery, route normalization/ranking, solved metadata, example, HTML rendering, and report output. |
| [`test_revert_projection.py`](test_revert_projection.py) | Append-only Revert/Undo projection across restart, branch message isolation, state/policy restoration, and legacy backfill. |
| [`test_review.py`](test_review.py) | Constrained Reviewer JSON extraction/normalization, bounded evidence packets, verdicts, and omission fail-closed behavior. |
| [`test_review_service.py`](test_review_service.py) | Review orchestration, evidence excerpts, model late binding, cancellation, persistence, usage/events, and nonfatal provider failures. |
| [`test_science_connectors.py`](test_science_connectors.py) | Schema-normalized scientific database connectors, catalog coverage, request encoding, pagination, result normalization, and offline HTTP fakes. |
| [`test_science_control_tools.py`](test_science_control_tools.py) | Native and in-kernel science search surfaces, flat schemas, registry behavior, catalog operation, and Host wire encoding. |
| [`test_sdk_compute.py`](test_sdk_compute.py) | Worker-side `host.compute` namespace, legacy exports, provider normalization, path rules, concurrency, instances, transfer, attach, and cleanup. |
| [`test_security.py`](test_security.py) | Defense-in-depth code classification, safety modes, heuristics, LLM classifier invocation, biosecurity/injection checks, and fast paths. |
| [`test_server_agent_run.py`](test_server_agent_run.py) | Web engine adapters for prose/code draft streaming, fence hiding, event usage, cancellation, throttling, and legacy Tool fences. |
| [`test_server_completions.py`](test_server_completions.py) | Localized public progress/completion rendering, artifact deltas, scientific fields, deduplication, bounded fallback, errors, and redaction. |
| [`test_server_execution_coordinator.py`](test_server_execution_coordinator.py) | Web admission/event projections, exact ticket/lease cancellation, REPL/Agent serialization, queued cancellation, and Gateway concurrency. |
| [`test_session_branching.py`](test_session_branching.py) | Checkpoint/fork immutability, revert preview/mutation, external-edit conflicts, append-only undo, and untracked-file preservation. |
| [`test_session_control_tools.py`](test_session_control_tools.py) | Native current-session/capability tools, schemas, resources, exact scope, progressive activation, redaction, and fail-closed mutations. |
| [`test_session_deletion.py`](test_session_deletion.py) | Aggregate session/project cleanup, shared CAS locking/GC, symlink safety, scoped Dynamic Tools, and feedback deletion. |
| [`test_session_domain_service.py`](test_session_domain_service.py) | Store/session-domain composition for snapshots, checkpoints, cursor forks, branches, Timeline, export, renderer, and recovery. |
| [`test_session_package.py`](test_session_package.py) | Deterministic session export/import, checksums, graph validation, secret/path/symlink/size filtering, quarantine, and round trip. |
| [`test_session_recovery.py`](test_session_recovery.py) | TTL parsing, strict idle sweeping blockers, persisted activity, recovery occupancy, startup reconciliation, and sweeper lifecycle. |
| [`test_session_snapshots.py`](test_session_snapshots.py) | Workspace CAS snapshots/restores, exclusions, conflict/delete handling, append-only branches, exact cursor binding, and migrations. |
| [`test_session_title_service.py`](test_session_title_service.py) | Background title prompt/cleanup, late-bound model, placeholders, race protection, failure behavior, persistence, and broadcast. |
| [`test_session_tool_catalog.py`](test_session_tool_catalog.py) | End-to-end class-based session tool composition, gated Dynamic Tool lifecycle, exact proxy behavior, and progressive catalog groups. |
| [`test_settings_repository.py`](test_settings_repository.py) | Settings, model-profile, feedback repositories, JSON fallback, concurrent mutation, Store facade, and permission seed interaction. |
| [`test_skill_customization_service.py`](test_skill_customization_service.py) | Web Customize Skill create/read/update/delete/import, validation, builtin collision, roots, precedence, enablement, and routes. |
| [`test_skill_product_surface.py`](test_skill_product_surface.py) | Versioned personal/project Skill control tools, SDK calls, scoped rollback/history, dispatcher audit, and HTTP routes. |
| [`test_skill_sidecar_recovery.py`](test_skill_sidecar_recovery.py) | Real-worker Skill sidecar capture into generation manifests/checkpoints and exact/tamper-safe recovery replay. |
| [`test_skill_versions.py`](test_skill_versions.py) | Content-addressed Skill install, upgrade, publish, history, deletion, activation/rollback, scope isolation, and Store reopen. |
| [`test_skills.py`](test_skills.py) | Skill loader discovery/frontmatter/progressive context, bootstrap path, sidecar compilation/functions, and error handling. |
| [`test_store.py`](test_store.py) | Store schema/migrations and serializers for frames, execution dependencies, Artifact versions, coalescing, and lineage transactions. |
| [`test_structured_finalize.py`](test_structured_finalize.py) | Engine-owned `finalize_response` schema/validation, sole-call routing, canonical results, mixed-batch noncompletion, and CLI execution. |
| [`test_tool_schema.py`](test_tool_schema.py) | Pure-stdlib validation/normalization of the supported JSON Schema subset with nested paths, constraints, and unknown fields. |
| [`test_tools.py`](test_tools.py) | Named class-based control tools, registry/handler coverage, isolated schemas, no eager singletons, and owned security policy. |
| [`test_variable_inspector_service.py`](test_variable_inspector_service.py) | Narrow idle-kernel variable inspection, language/lease state checks, sanitized bounded results, and protocol failure handling. |
| [`test_webtools.py`](test_webtools.py) | HTML-to-Markdown conversion, arXiv metadata/abstract preservation, block/inline handling, and search backend behavior with offline fixtures. |
| [`test_webui_static_contract.py`](test_webui_static_contract.py) | Dependency-free UI asset existence, unique/stable DOM IDs, controls, event wiring, icon definitions, and static security invariants. |
| [`test_workbench_state_service.py`](test_workbench_state_service.py) | Safe Context/Security projections, compaction metadata, no message leakage, and truthful aggregation of Python/R sandbox state. |
| [`test_worker_runtime_alias.py`](test_worker_runtime_alias.py) | Pure-reexport compatibility of `openai4s_worker_runtime`, symbol identity, public `__all__`, and absence of shadow submodules/entrypoint. |

## Direct subdirectories

| Directory | Responsibility |
| --- | --- |
| `fixtures/` | Byte-sensitive captured HTML, fake interpreter helpers, and renderer samples. This subtree is intentionally excluded from directory README generation and automatic formatting. |

## Choosing the right place

Put a focused regression assertion here. Put reusable scripted scenarios, fake providers, normalized trajectories, scored evaluations, and reviewed goldens in the [`../harness/`](../harness/) layer. Default tests in both layers remain offline unless their entry point is explicitly marked and invoked as an opt-in smoke.
