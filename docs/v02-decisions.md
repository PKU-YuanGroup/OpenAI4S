# v0.2 frozen decisions

Owner-signed answers to the open decisions in the nextgen improvement proposal
(section 8), plus the Phase 1 decisions already implemented. Frozen 2026-07-20
by the repository owner.

This file exists because the proposal makes owner sign-off a Phase 0 exit gate:
work that depends on one of these answers must not start until the answer is
recorded somewhere a reviewer can check it. Each row states what was chosen and,
more usefully, what the choice forecloses — a decision whose cost is invisible
gets silently reversed later.

## Phase 1 — decided and already implemented

| # | Decision | Choice | Consequence |
| --- | --- | --- | --- |
| 8.1a | Primary v0.2 user | Platform / engineering teams | Onboarding assumes a terminal is available; the "no CLI" gate still applies to the demo workflow, not to installation. |
| 8.2 | Headless secret storage | System keychain plus read-only env injection; **neither may degrade** | `auto` fails closed when no backend is available. There is deliberately no obfuscated-file fallback, so a headless box with neither backend cannot start with credentials — that is the intended behaviour, not a bug. |
| 8.3a | SSH compatibility surface | Keep, but constrained | Direct SSH/SCP carries the same path, size, approval, and audit constraints as the native job path. |
| 8.4a | Contract v1 URL | `/api/v1`, **no legacy alias** | The proposal's "maintain a legacy adapter for one minor release" requirement is deliberately not met. Old clients break at the version boundary instead of silently drifting. Accepted because the only clients are first-party. |
| 8.6a | Log export | Redacted diagnostics bundle | The bundle never includes the database. |
| 8.8a | Demo workflow | Local database → analysis → artifacts | No network dependency in the first-success path. |

## Phase 2–4 — decided 2026-07-20

| # | Decision | Choice | Consequence |
| --- | --- | --- | --- |
| — | Roadmap sequencing | Strict Phase 2 → 3 → 4 | A phase does not start until the previous one clears its exit gate. |
| — | PR granularity | One large PR per phase | Review burden is accepted in exchange for gate-at-once verification and no half-landed phase. |
| 8.3b | First production remote provider | SSH-to-HPC is the **only** production path | BYOC keeps its Prototype marking. The durable-job fault matrix is proven on SSH. |
| — | Fault matrix verification | Local real `sshd`, real processes | Not mocks: real process groups, real TERM/KILL, real partial transfer, real connection loss. A mocked `ssh` cannot reach the shell-behaviour layer where the previous async-list stdin defect lived. |
| 8.4b | Contract v1 remainder | WebSocket replay window and resume cursor, plus contract generation/validation from a single schema source | No SDK is published — publishing one would add a package to the Phase 4 release matrix and a compatibility promise. |
| 8.5 | Platform matrix | macOS arm64 stable (after Developer ID signing and notarization); Linux server/browser beta (after enforced bubblewrap E2E); Windows unsupported and fails closed | Linux beta is gated on a real enforced-sandbox smoke test, not on a probe that degrades. |
| 8.6b | Telemetry | Opt-in, anonymous, **off by default** | This is the one decision that breaks the loopback-only default. It is therefore constrained by the four rows below. |
| 8.7 | First connectors | Manifest coverage for all seven existing sources, plus real scheduled canaries for UniProt, RCSB PDB, and OpenAlex | Canaries run on scheduled/RC trend gates, never blocking an ordinary PR. All seven sources are free public APIs with no key, so the real cost is rate limits and the OpenAlex polite-pool contact, not money. |
| 8.1b | First 10 workflows / 20 benchmark cases | Framework first, cases later | Deliver the runner, versioned case schema, the six metric families, variance recording, and CI wiring, seeded with 3–5 deterministic cases. Domain cases are filled in by the team — an agent guessing at research questions produces a benchmark that measures nothing. |
| 8.8b | Claim/evidence boundary | Proposal default: schema spike plus the Artifact/annotation reference loop only | The full claim-level evidence graph stays deferred to v0.3 per proposal section 7. Not separately re-decided. |

## Telemetry constraints

Telemetry is the highest-risk item here, because this product handles
unpublished research data. Four constraints bound it:

| Aspect | Decision |
| --- | --- |
| Collector | An in-repo stdlib collector (`openai4s telemetry-serve`), structurally matching the existing `openai4s relay`: same token auth, rate limiting, Host validation, and proxy-trust switch. No third-party vendor sees the data, and no third-party dependency enters the tree. |
| Payload | **Counts and enumerations only — zero free text.** Event name, counts, duration buckets, error *type*, version, OS/architecture, anonymous install ID. No prompts, no file names, no error message bodies, no data-source query terms. Enforced by an allowlist with a regression test asserting the outgoing payload contains no key outside it. |
| Identity | An anonymous, locally generated install UUID, created at the moment of consent. No authentication. The server defends itself with rate limits and size caps rather than a distributed credential. |
| Endpoint | `https://log.openai4s.org`, hardcoded as the only built-in endpoint, HTTPS enforced, no downgrade, no cross-host redirect. Overridable via `OPENAI4S_TELEMETRY_ENDPOINT` for self-hosting. Off by default: with no consent, not a single packet leaves the machine. |

### Why `log.openai4s.org` does not collide with web sharing

Web sharing already consumes a wildcard on the same apex. It does not conflict,
and sharing does **not** need to move to a deeper subdomain:

- Share subdomains are constrained to exactly 26 base32 characters
  (`^[a-z2-7]{26}$`, `openai4s/share/relay.py`), and the relay refuses to route
  any label that does not match.
- `log` (3 characters) can therefore never be issued as a share ID, and neither
  can `share`, the tunnel control host.

Moving sharing to `*.share.openai4s.org` would invalidate every share URL
already handed out while still requiring a DNS-01 wildcard certificate, so it
costs something and buys nothing.

## Deliberately not decided here

- Which specific cloud GPU provider becomes production-grade. BYOC stays
  Prototype until one is named and can report confinement evidence.
- Apple Developer ID, notarization credentials, and the PyPI token. These are
  owner-held; the release pipeline is written to consume them but is never
  triggered by an agent.
- Ground truth, data licensing, and domain content for benchmark cases 6–20.
