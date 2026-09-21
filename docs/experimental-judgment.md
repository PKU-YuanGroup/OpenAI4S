# Experimental semantic judgment layer

This is the in-tree design for the **experimental** (default-off) semantic
judgment layer. It is the formal record of plan §1, §4, and §5. Runtime
wiring, HTTP, UI, and templates land in later waves; this document is the
contract they implement.

## Status

Experimental. Default off. First vendor: TypeSafe Jev (`jev-1.13.0`). Core
code stays stdlib-only; the official HTTP contract is the source of truth,
not `typesafe-sdk`.

## Goals and non-goals

**Goal.** Replace scattered keyword rules and "ask the LLM for a JSON
verdict" with typed, probabilistic, recorded, evaluable, replayable judgment
steps, behind an experimental flag.

First capabilities, all default-off:

| Id | Capability | Shape | Release status |
| --- | --- | --- | --- |
| `skill_suggest` | Skill recommendation (zh/en semantic match) | Extra fields on `search_skills`; recommendation only | Experimental, default off |
| `literature_check` | Passage screening + claim/citation check | Helper on the `literature-review` Skill | Experimental, default off |
| `text_features` | Interpretable text feature engineering | New Skill | Experimental, default off |
| `safety_shadow` | Shadow judgment on code-gate / injection / biosecurity | Record disagreement only; never change a verdict | Experimental, default off |
| `task_mode_shadow` | Shadow record of task-mode classification | Record only; never bind delivery requirements | Experimental, default off |

**Non-goals for this experiment**

- Do not replace the main model for planning, code generation, explanation, or writing. Jev does not generate text.
- Do not let Jev *execute* a safety decision. Safety stays shadow; enforcement is a separate plan.
- No small/large model cascade. `host.llm` has no per-request model profile yet.
- No images or structure files. Jev takes text; other modalities must be converted first.

## Release design

### Flags, precedence, defaults

`ExperimentalJudgmentFlags` lives on `Config` as `experimental_judgment`.
Capability fields are tri-state (`bool | None`) via `_strict_env_tristate`:
unset is `None`, the true/false vocabulary matches `_STRICT_TRUE_VALUES` /
`_STRICT_FALSE_VALUES`, and any other spelling (including `flase`) raises
`ValueError`.

| Switch | Environment variable | Store setting | Default |
| --- | --- | --- | --- |
| Master | `OPENAI4S_EXPERIMENTAL_JUDGMENT` | `experimental.judgment.enabled` | off |
| Skill recommendation | `OPENAI4S_JUDGMENT_SKILL_SUGGEST` | `experimental.judgment.capabilities.skill_suggest` | off |
| Literature check | `OPENAI4S_JUDGMENT_LITERATURE` | `experimental.judgment.capabilities.literature_check` | off |
| Text features | `OPENAI4S_JUDGMENT_TEXT_FEATURES` | `experimental.judgment.capabilities.text_features` | off |
| Safety shadow | `OPENAI4S_JUDGMENT_SAFETY_SHADOW` | `experimental.judgment.capabilities.safety_shadow` | off |
| Task-mode shadow | `OPENAI4S_JUDGMENT_TASK_MODE_SHADOW` | `experimental.judgment.capabilities.task_mode_shadow` | off |
| Backend | `OPENAI4S_JUDGMENT_PROVIDER` | `experimental.judgment.provider` | `typesafe` (optional `llm` later) |
| Model | `OPENAI4S_JUDGMENT_MODEL` | `experimental.judgment.model` | `jev-1.13.0` (pinned; not `jev-latest`) |
| Timeout | `OPENAI4S_JUDGMENT_TIMEOUT_S` | — | `3.0` seconds (0.1–30) |
| API key | `OPENAI4S_TYPESAFE_API_KEY` (headless) | secret `typesafe_api_key`, scope `judgment` | none |

Effective flags (`openai4s.judgment.flags.resolve`) follow
`JUDGMENT_FLAG_PRECEDENCE`:

1. Env explicitly false → force off (kill switch; the UI cannot override it).
2. Env explicitly true → on (CLI / headless / CI live tests).
3. Otherwise consult the Store setting.
4. If neither is set → off.
5. When the master switch is off, every capability is off.
6. Enabling via the UI path also requires a **current-version** data-disclosure acknowledgement; otherwise the capability is treated as off. Enabling via env treats the operator as informed and logs a warning at startup.

### UI

Customize → General (or a dedicated Experimental tab) will expose the master
switch, per-capability switches with the disclosure text, read-only backend
and model, key input/clear, a connection probe, and a status line
(`disabled` / `ok` / `unavailable`). Semantic Skill suggestions render as
experimental chips, not mixed into lexical hits. That UI is a later wave.

### Default-off guarantee

With no experimental switch on, behaviour, system prompt, tool schemas, tool
results, gateway response schemas, and harness golden traces stay
byte-identical. `tests/test_judgment_default_off.py` freezes Skill
`system_context`, `search_skills` results, `REGISTRY` schemas, and heuristic
`classify_code` verdicts. The snapshot was captured before any product code
for this layer landed.

### Data disclosure

Copy lives in `openai4s/judgment/disclosure.py` with
`DISCLOSURE_VERSION = "2026-09-20"`. Changing the text bumps the version and
requires a fresh acknowledgement. Per capability, this is what is sent to
`api.typesafe.ai`:

| Capability | Payload |
| --- | --- |
| `skill_suggest` | Current user request; in-scope candidate Skill names, descriptions, and `SKILL.md` opening fragments |
| `literature_check` | Research question, paper passages, claims to check |
| `text_features` | User-selected data-row text |
| `safety_shadow` | Pending code cell, tool-result fragments, session trajectory summary (highest risk; UI must emphasise this) |
| `task_mode_shadow` | User request text |

Fixed facts: the service is hosted in the United States; the privacy policy
says inputs are not used to train models, but it does **not** state a
retention period; Zero Data Retention is enterprise-only. Do not enable this
for sensitive data.

The acknowledgement is stored as
`experimental.judgment.disclosure_ack = {version, capabilities, acked_at}`.
A session package export may attach `judgment_manifest.json` (capabilities
used, backend, model, template versions, call/token counts) and must never
include the key.

### Graduation and removal

Each capability graduates independently: pre-registered test-set quality
bar, domestic p95 latency bar, one release cycle without related P0/P1, and
a maintainer sign-off. `safety_shadow` graduating still means shadow, not
enforcement.

Removal is four steps: delete `openai4s/judgment/`, `host/judgment.py`,
`sdk/judgment.py`; delete the `if flags...` mounts; delete the UI block;
drop the extra `[tool.mypy] files` lines. There is no egress group to
remove.

## Architecture

### Call chain

Control-plane hooks (Skill search, later shadows) and kernel `host.judge`
RPC both reach `JudgmentService`, which talks to a `JudgmentBackend`:
`NullBackend` (always disabled), `TypeSafeBackend` (later), optional
`LlmBackend` (later, explicit only, `calibrated=false`). The API key never
enters the kernel environment.

### Data contract

Questions are frozen dataclasses in `openai4s/judgment/types.py`:

- `Noul`: non-empty `instructions`; `criteria` is `None` or exactly
  `{true, false}`. `to_api()` emits `{"type":"noul","instructions":...}` and
  omits `criteria` when absent.
- `Choice`: 2–255 options with non-empty names; descriptions are strings or
  objects. `to_api()` puts options in `criteria`.
- `Score`: 2–10 ordered levels. `to_api()` puts levels in `criteria` as a
  list.

`Answer` carries `kind`, `value`, optional `probabilities` / `confidence`.
A Noul answer is rejected if it carries either: Jev answers a Noul with
P(yes) alone, and a field the backend never fills must not be representable.
`value` is a number for `noul` / `score` and the selected option name for
`choice`. `JudgmentResult` is the Host RPC value; `to_dict()` is JSON-only. `BackendReply` is the transport reply: raw answers, usage,
echoed model, `request_id` (or `None`).

Cache key (later):
`(provider, model, template_id, template_version, policy_version, state_sha256, candidate_versions_hash, scope_id)`.
Replay always uses the recorded result.

### Status semantics

| Status | Meaning |
| --- | --- |
| `disabled` | Flag off, or UI path without current disclosure. **No request is sent.** |
| `unavailable` | Missing key, network/timeout, 401/429/529 exhausted, invalid response, egress blocked. **Never invent default scores.** `answers` empty; `error_code` required. |
| `uncertain` | Request succeeded; template policy is uncertain. Answers are returned; the caller decides. |
| `ok` | Request succeeded and passed policy. |

`host.judge` treats all four as normal return values. Only a bad call
(unknown template, bad arguments) uses the single-key `{"error": msg}` soft
failure.

### Jev constraints that the implementation must honour

Question keys are not sent to the model, so each `instructions` string must
be self-contained and name the `state` field it reads. Choice is at most 255
options. Noul returns P(yes) only. Score is 2–10 levels with a full
distribution. Confidence is not accuracy. State + longest question ≤ 32k
tokens; the service truncates and marks it. Counting, arithmetic, and date
comparison stay in code. Safety stays shadow; confidence is never
authorization. Questions and criteria are written in English; state may
contain Chinese.

## Progress log

### W0 — 2026-09-20

Merged `feat/judgment-w0-a-core-contracts` (branch HEAD `8a861ceb`) into
`feat/judgment` as `cf51bc20`. W0-A landed the typed contract
(`types.py`, `port.py`), tri-state flag resolution (`flags.py`,
`ExperimentalJudgmentFlags` in `config.py`), versioned disclosure copy, and
the pre-change default-off snapshot.

Verified before merging: the snapshot commit is the branch's first commit and
contains only tests and fixtures; re-running `capture()` on the base commit
`909cf0ac` reproduced all four fixtures byte for byte (10,686 / 403,295 /
43,854 / 2,556 bytes). `uv run mypy` checks 12 files, and an untyped-function
probe in `judgment/types.py` was confirmed to fail it.

Integration fix `ab8f7212`: `Answer` accepted `probabilities` and
`confidence` on a `noul` answer, which this document and plan 5.3 both say
a Noul never carries. Both are now rejected, and `value` is pinned to a
number for `noul` / `score` and to the option name for `choice`. The two
new regression tests were confirmed to fail against the pre-fix type.

Deviations carried forward: `JudgmentBackend.evaluate` is keyword-only and
returns `BackendReply` (plan 6 sketched `RawAnswers`); a master switch
enabled through the Store with a stale acknowledgement reports
`no_disclosure` on the master flag itself, not only on its capabilities.

Open for W1: capability flags are resolved but nothing consumes them, no
transport exists, and `tests/conftest.py` does not yet purge
`OPENAI4S_*JUDGMENT*` variables — a developer who exports the master switch
will leak it into the offline suite once W1 wires a runtime path.

### W1 — 2026-09-20

Merged three branches into `feat/judgment` in order: `w1-a-transport`
(`b753f40d` → `0c9d37a0`), `w1-b-host-service` (`52f36188` → `e8ed3ade`), and
`w1-c-settings-egress-gateway` (`00b0c63e` → `c077bba3`). The layer now has a
stdlib TypeSafe transport with strict response validation, a loopback fake
endpoint, `JudgmentService` behind `host.judge`, and the
`/api/v1/experimental/judgment` settings surface with a doctor check.

Conflicts were README and registry appends only, resolved as unions in append
order. One semantic resolution inside the W1-C merge: its
`# type: ignore[import-not-found]` on the deferred `JudgmentService` import
became unused once W1-B was merged, and mypy refuses an unused ignore.

Integration fix `560c8f6f` closed three seams no single branch could see.
`JudgmentService._store` called its provider unconditionally while the gateway
hands over the request's Store, so `POST /experimental/judgment/test` raised
`TypeError: 'Store' object is not callable` inside the route as soon as the
master switch was on; W1-B's tests always passed a lambda and W1-C's route test
stubbed the probe away. `BackendReply.fake` stopped at the transport, so an
answer from the loopback fake was reported and audited as a real one. And
W1-C's ImportError fallback would have reported a genuine import failure as a
tidy `unconfigured`. `tests/test_judgment_w1_integration.py` covers all three
and every assertion in it fails without the fix.

Integration fix `a586e533`: the frozen shape for the probe route had narrowed
`error_code` to `null`, because the only unstubbed call in the suite hit the
default-off path. The route returns `"unconfigured"` whenever the switch is on
without a key, so the suite now elicits both and the shape is the union.

Verified end to end against the loopback fake on an isolated data directory:
service `probe()` and a three-question `run()` (Noul, Choice, Score) returning
`ok` with `fake: true` and a cache hit on repeat; `host.judge("system.probe",
…)` from a real kernel cell through the real transport; and `GET` / `PUT` /
`POST …/test` against a running daemon, where the probe answered `ok` in 5 ms.
A key supplied through `OPENAI4S_TYPESAFE_API_KEY` appeared in no file under
the data directory, and the fake's request log records `path`, `headers` and
`body` with no `Authorization` at all. `PUT {"clear_api_key": true}` removed
both the Store row and the keychain item.

`openai4s doctor` reports `disabled (experimental, default off)` when off,
`enabled (typesafe/jev-1.13.0)` when on with a key, and warns without a key or
when `api.typesafe.ai` is outside an enforced allowlist.

Open for W2: `tests/conftest.py` still does not purge `OPENAI4S_*JUDGMENT*`,
and a runtime path now exists for it to leak into. `host.judge` is deliberately
absent from `GATEABLE_TOOLS`, `_SCREENED_METHODS` and `_m_capabilities()`. The
dispatcher envelope's `log_host_call(method="judge")` still records the raw
state even though the named `judgment` audit event does not.

### W2 — 2026-09-20

Merged three branches in order: `w2-a-skills-backend` (`e491ff89` →
`a66674d6`), `w2-c-skills-eval` (`ec8f1536` → `131470b5`), and
`w2-b-frontend` (`3ee20db2` → `04dd3594`). `search_skills` now carries
semantic suggestions when `skill_suggest` is on, there is a frozen 200-case
bilingual evaluation with a locked test split, and Customize → General has an
Experimental section with per-capability disclosure.

Integration fix `76876a59`: `_step_end` projected a `search_skills` result by
iterating it as a list, so the wrapped dict yielded its keys — the workbench
card read "no match" beside real lexical hits and the suggestion chips had
nothing to render in a real session. W2-A does not own the projection and
W2-B's Vitest fixtures feed the dict in directly, so neither branch could see
it. Both shapes are projected now; the capability-off path is byte-identical.

Verified against the loopback fake: with the capability off `search_skills`
returns the same list it always did, and with it on the wrapper's `results`
are byte-identical to that list. `browser_smoke.mjs` passes with the switch
off and `browser_judgment.mjs` passes with it on. The offline dev-split
evaluation reproduces W2-C's numbers exactly (B0 top-3 0.859, B2 top-3 0.923).

Open, and tracked as a rework ticket for W2-A: `SearchSkillsTool.execute`
budgets the lexical results to the full `output_limit` before the semantic
envelope is added, so a query whose lexical hits are large drops every
suggestion while still reporting `semantic_status: ok`. Five bioSkills rows
render to 50,123 characters against a 50,000 limit, and three suggestions that
`suggest_skills` did produce never reach the caller.

Also open: `format_tool_result` shows the model the lexical `results` only, so
suggestions are model-visible exactly when lexical search found nothing. That
inconsistency is a product decision, not a defect, and is recorded for the
maintainer.

#### W2 rework — 2026-09-20

W2-A's rework `7edc1cc6` merged as `891c7ef6`. `SearchSkillsTool.execute` now
reserves the semantic envelope's share before fitting lexical hits, and
`fit_to_budget` takes an optional `budget` so the capability-off path is
untouched. When the envelope still cannot hold a single suggestion the payload
carries `semantic_truncated: true` and a status of `ok` becomes `uncertain`,
so "the budget ate them" is no longer indistinguishable from a genuine
abstention.

Verified on the merged tree against the loopback fake, with the same query
that produced the defect: `suggest_skills` yields 3 suggestions and all 3 now
survive `execute` beside 5 lexical hits, rendering to 44,723 characters
against the 50,000 limit, and the step card carries both. Forcing the envelope
past the limit reproduces the new signal — at a 600-character limit 2 of 3
suggestions survive with `semantic_truncated: true`, and at 300 the status
drops to `uncertain` with none.

Integration fix `07f0e9a8` corrects the reserve's docstring: it claimed a cap
at `output_limit // 8` "so a large envelope cannot starve the result list",
while the code takes `max(needed, cap)` — a floor. The conclusion holds at the
real limit; the named mechanism was not the implemented one. Behaviour
unchanged.

New for later waves: the enabled payload may carry `semantic_truncated`, and
`uncertain` can now mean the budget dropped the suggestions rather than the
model hedging. A genuine abstention is `ok` with an empty list and no such key.

### W3 — 2026-09-20

Merged `w3-a-literature-skill` (`0cab84ab` → `547e63d2`) and
`w3-b-literature-eval` (`bc845b21` → `165eef9f`). `literature-review` gains
`screen_passages` and `check_claims`, backed by the `literature.screen` and
`literature.claim` templates; a frozen 304-pair bilingual evaluation over
open-access excerpts sits beside them, with its test half locked by digest.

The default-off snapshot changed for the first time, and only where it had to:
`tests/fixtures/judgment_default_off/search.json` carries each Skill's `doc`,
and the new SKILL.md section is part of `literature-review`'s. Verified before
accepting it — one query's results changed, one Skill within it, one field,
2,072 characters added and none removed, with the old text still a substring
of the new. The other three fixtures are untouched.

Integration fix `1b1cc9d3`: templates register as an import side effect, and
W3-A put that import in `JudgmentService.dispatch`, which covers `host.judge`
and nothing else. `JudgmentService.run` — the path the evaluation's host shim
takes — raised `unknown template: literature.claim` in a fresh interpreter.
Moved to `registry.get_template`, the one function every caller already goes
through. W4's feature and safety templates now need no call-site import.

Integration fix `05b34283`: `tests/conftest.py` now purges every
`OPENAI4S_*JUDGMENT*` switch per test, alongside the rollout flags. The key
and model variables stay covered by the existing `*API_KEY` / `*MODEL`
patterns, which is why live judgment tests read `OPENAI4S_JUDGMENT_LIVE_KEY`.

The host_only boundary left open since W1 is now verified, and the two halves
need different setups. Against the real endpoint, a bound Skill declaring
`api.typesafe.ai` reaches it while one declaring only another domain is
refused with `egress_blocked` before any connection. Against the loopback
fake, neither is refused: the transport skips `egress.check_url` entirely when
the fake endpoint is configured, so the Skill's narrowing does not apply
there. That path is loopback-only and gated on a test variable, so it widens
nothing in production — but it means this boundary cannot be tested with the
fake endpoint on.

`check_claims` was driven from a real kernel cell against the fake endpoint
until every status appeared: `verified`, `contradicted`, `unsupported`,
`uncertain`, `numeric_mismatch` and `not_found_needs_review`. A locate failure
makes no backend call at all, and a numeric mismatch overrides a semantic
`supports` — the numbers are compared in code, as the design requires.
