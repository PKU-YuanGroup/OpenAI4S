# Code-adjacent documentation

[中文说明](README_zh.md)

The documentation that travels with the OpenAI4S source lives here, along with
the historical paths older references still link to. The public bilingual
website is maintained separately in
[`Nobody-Zhang/openai4s-docs`](https://github.com/Nobody-Zhang/openai4s-docs),
and the internal plans in this directory are not published by that site.

## Files

| File | Purpose and status |
| --- | --- |
| `architecture.md` | Current dual-loop architecture and Host API overview; compatibility entry for contributors. |
| `ark-agent-plan-9.9.png` | Volcengine Ark Agent Plan pricing screenshot displayed by the repository's root README. |
| `backend-extension-guide.md` | Current extension seams: where a new tool, Host service, storage repository, provider, Skill, or Web session service is meant to plug in. |
| `backend-refactor-architecture.md` | Historical backend-refactor design record. It says what was agreed, so it is not proof of current end-to-end behavior. |
| `compute.md` | Remote compute, the BYOC providers, and `host.fold`: how they behave and where their limits are. |
| `configuration.md` | How the provider, environment, daemon, kernel, and data directory are configured. |
| `docker.md` | Bilingual container guide: the image, `compose.yaml`, the Kubernetes manifests, and what a wildcard bind actually changes. It states the trade a container makes with the kernel sandbox rather than implying the container replaces it, names the derived `OPENAI4S_SECRET_<SCOPE>_<NAME>` variables the image expects, and lists the limitations that are real today — no R, no IPv6, no access logs, and a credential-bearing startup line. |
| `jupyter.md` | The optional Jupyter adapter: what it exposes, the execution boundaries it keeps, and the compatibility notes. |
| `package-architecture.md` | Historical package/ownership inventory used during decomposition work. |
| `platforms.md` | The supported-platform matrix the code enforces: macOS stable, Linux beta, Windows **refused** rather than warned about. Names the gates not yet met (Developer ID signing and notarization) instead of implying they are, and explains why the tiers differ — not the code, but what has been proven. Also the separate question of what actually ships per platform, including why a Windows download and a refused Windows platform are not a contradiction. |
| `plan-corecoder-refactor.md` | Internal historical refactor plan; excluded from public website content. |
| `refactor-plan.md` | Historical migration plan retained for decision context. |
| `release-validation.md` | The gates a release passes: the local gate, the evidence bundle, the enforced contracts, the macOS app image, the Linux app bundle, the Windows package, trusted publication, the draft-first pipeline, and the external gates that deliberately stay outside CI. It also states the macOS signing state as one named value computed from evidence rather than from configuration, and says outright that `verified` is unreachable in this version, so no reader has to infer a limitation from an absence. |
| `science-connectors.md` | The seven public scientific databases behind `science_search`: per-source endpoint, disciplines, and the normalized record each returns. |
| `security.md` | Threat model, trust boundaries, enforcement layers, and known coverage gaps. |
| `skills.md` | Bundled/user Skill format, loading, sidecars, and lifecycle. |
| `startup-guide.md` | Bilingual macOS `.dmg` walkthrough: install, Gatekeeper, model setup, and one-key Doubao Search authorization in the UI, with Tavily/keyless backups. |
| `team-server.md` / `team-server_zh.md` | The operator's page for multi-user mode: what to turn on, in what order, and what each switch exposes. Everything in it is off by default, so a default install is the single-user workbench it has always been (INV-1). It is deliberately blunt about two things people get wrong — team mode adds accounts but does not make the daemon safe to expose, and the relay is not a third way to reach a lab server (it publishes a redacted projection of one session, not the workbench). |
| `team-server-plan.md` | Frozen execution plan for the multi-user Team Server mode (M1 tenancy → M2 governance → M3a/M3b Slurm orchestration → M4): product decisions, binding invariants, per-milestone work items with gates, and non-blocking rules written so an autonomous agent can run it end to end. The plan is the intent record; only its Deviation Log appendix may be appended to during execution. |
| `webapp-api.md` | Detailed REST/WebSocket surface and compatibility behavior. |
| `windows-wsl.md` | Bilingual Windows/WSL2 installation and operations guide: Ubuntu 24.04, the bubblewrap preflight, checksum-verified offline payload, authenticated browser launch, detached lifecycle, mainland mirrors, and the localhost:7897 NAT/mirrored-network distinction. |
| `response-schemas.json` | The shape of every HTTP response the offline suite exercises, captured from real responses rather than written by hand. Generated by [`scripts/capture_response_schemas.py`](../scripts/capture_response_schemas.py); a diff here means a route changed what it returns. Coverage is partial and deliberately visible: a route absent from the file is one no offline test reaches. Subtrees that describe the host rather than the API — the kernel's `sandbox` block, whose field *types* differ between a machine that can enforce a sandbox and one that cannot, and `default_host`, which the compute registry documents as `"<alias>" | null` so that a machine with an ssh alias configured and one without disagree about its type — are recorded as `machine_state` instead of being pinned. Pinning one of these does not catch drift; it freezes whichever machine captured the file and reports every other machine as a breaking API change. Tests marked `stubbed_backend` contribute nothing: a route driven against a stubbed service returns a fabrication, and publishing that as the contract is worse than leaving the route unpinned, because a reader believes it. Those routes are watched by unit tests instead. |
| `response-contract.json` | Which routes exist and what *kind* of answer each gives — the statuses and content types a caller branches on. Like its sibling it is captured, not hand-written, but by a different method: [`scripts/capture_response_contract.py`](../scripts/capture_response_contract.py) drives every known route against a real handler with no parameters, so most entries record the 4xx a caller gets wrong rather than a success body. That is why it does not rerun the suite and stays cheap. `--check` fails when a routable route has no entry at all — an unreachable route is a gap in the tests, not an absence to tolerate. The coarser of the two: this file says a route answers and in what form, `response-schemas.json` says what the JSON inside looks like. |
| `plan-crosswalk.json` | Where each of the integrated report's 56 original proposals actually went, as machine-checkable data rather than a table in prose. One row per `(source, original_id)` with the integrated destination, a status from a declared vocabulary, the tests that prove it, and — for anything unverified — the run that is missing. `tests/test_plan_crosswalk.py` enforces 56 unique keys each appearing once, so a duplicate, a lost row or a `closed` that names no existing test is a failure rather than an oversight. The statuses are from a read-only production call-chain audit, not inherited from [`next-version-progress.md`](next-version-progress.md). |
| `v02-decisions.md` | The owner-signed answers to the open decisions in section 8 of the nextgen improvement proposal, frozen 2026-07-20. Work that depends on one of these must not start until the answer is recorded somewhere a reviewer can check it. Each row also states what the choice forecloses, because a decision whose cost is invisible gets silently reversed later. |
| `v03-decisions.md` | The owner-signed answers for v0.3, frozen 2026-07-26, including the one that supersedes v0.2's PR-granularity choice and the acceptance criterion this version is measured against. It also names, in one table, everything that cannot be verified from a working copy — GitHub Actions execution, a Developer ID certificate, notarization, PyPI OIDC, a live browser, Linux CI — so their absence from the verified column is deliberate rather than an oversight. |
| `next-version-progress.md` | The v0.3 per-item factual record: what landed, in which commit, and — the load-bearing column — what was neutered to prove each new test can fail. A test that cannot fail measures nothing, and a class with the right name is not completion evidence. Items whose proving run needs a machine this repository does not have are marked `Implemented but unverified` with the missing run named. |
| `webapp.md` | Web workbench concepts, projections, status, and operator-facing behavior. |
| `webshare.md` | Web sharing: the read-only snapshot + outbound relay tunnel, deployment, and the trust model. |

## Where this fits

Executable behavior and tests outrank prose. When a historical plan conflicts
with `openai4s/` or `tests/`, treat the implementation and contract tests as the
current source of truth and update the standalone documentation repository.
