# Experimental semantic judgment layer

**Experimental. Default off. Early access.** First vendor: TypeSafe Jev
(`jev-1.13.0`). Bring your own TypeSafe API key. The service is hosted in the
United States.

This is the operator guide for the in-tree semantic judgment layer. A
maintainer who needs to delete the experiment should follow
[experimental-judgment-removal.md](experimental-judgment-removal.md). Flag
names and environment variables are also listed in
[configuration.md](configuration.md). Outbound data is summarised in
[security.md](security.md).

Chinese counterpart: [experimental-judgment_zh.md](experimental-judgment_zh.md).

## What it is

A vendor-neutral **semantic judgment layer** behind experimental flags. It
turns discrete judgments that today live in keyword rules or "ask the LLM for
a JSON verdict" into typed, probabilistic, recorded, evaluable, replayable
steps. Kernel cells reach it through `host.judge(template, state, **params)`
on a registered template. Control-plane hooks (Skill search, safety screens,
task-mode detection) call the same Host service.

Questions are `Noul` (P(yes)), `Choice` (2–255 options), or `Score` (2–10
ordered levels). Every call returns one of four statuses as ordinary data:
`ok`, `uncertain`, `unavailable`, `disabled`. A missing key, a blocked
egress, or a timeout is `unavailable` with an `error_code`. The layer never
invents a default score.

Core code stays stdlib-only. The official HTTP contract is the source of
truth, not `typesafe-sdk`. The default backend POSTs to
`https://api.typesafe.ai/v1/systemone`. The model id is pinned to
`jev-1.13.0` (not `jev-latest`). Input is billed at **$0.042 per million
tokens**; output is free.

## What it is not

- It does not replace the main model for planning, code generation,
  explanation, or writing. Jev does not generate text.
- It does not *execute* a safety decision. `safety_shadow` only records
  disagreement with the existing classifier, injection scanner, and
  biosecurity screener. Those three functions still return the verdict they
  computed before the shadow ran.
- It does not bind a detected task mode to delivery requirements.
  `task_mode_shadow` records; `resolve_task_mode` still returns the rule
  result.
- It does not handle images or structure files. Jev takes text. Other
  modalities have to be converted first.
- It does not add `api.typesafe.ai` to the built-in egress catalog. In
  allowlist mode you still grant that host yourself, the same way as any
  other destination (`host.request_network_access(domain="api.typesafe.ai")`).
- It does not silently fall back from TypeSafe to the main LLM. `provider=llm`
  is an explicit choice and marks every result `calibrated=false`.

## Capabilities

All five are default-off. The master switch must also be on. Enabling a
capability through the UI requires a current-version data-disclosure
acknowledgement for that capability.

| Id | What it does | What is sent to `api.typesafe.ai` |
| --- | --- | --- |
| `skill_suggest` | Semantic Skill recommendations on `search_skills`. Lexical hits stay as they were. The workbench shows them as experimental chips (name, `p_fit`, confidence), not mixed into the lexical list. At most three suggestions. An explicit Skill name skips judgment (`skipped_explicit`). | Current user request text; in-scope candidate Skill names, descriptions, and `SKILL.md` opening fragments |
| `literature_check` | `screen_passages` and `check_claims` on the `literature-review` Skill. Quote location and number/unit comparison run in code. `host.judge` is asked only about semantic relationship. `supports` means the source text supports this sentence; it is not a proof that the sentence is scientifically true. `not_found` is not evidence of fabrication. | Research question, paper passages, claims to check |
| `text_features` | New `text-features` Skill. The main model proposes Noul/Score questions; `host.judge("features.custom", …)` answers each selected row; `audit-dataset` / `plan-ml-experiment` / `evaluate-model` fit on a development split and score the frozen test split once. Features keep their source question and measurement error. They are not a human gold standard. Unavailable rows are NaN, not a default. | User-selected data-row text |
| `safety_shadow` | Shadow judgment beside `classify_code`, `scan_tool_result`, and the biosecurity trajectory screen (`looks_biosecurity_relevant` / `screen_trajectory`). Highest outbound risk. | Pending code cell, fragments of tool results, a session trajectory summary |
| `task_mode_shadow` | Shadow record of `resolve_task_mode`. An explicit `--mode` / `task_mode` selection is not submitted. | User request text |

When the corresponding flag is off, each of those paths returns `disabled`
(or, for the two shadows, does not start a worker) and sends no request.

## How to enable

### UI

1. Open **Customize → General**. The Experimental block is on that tab
   (`data-judgment`).
2. Turn on the master switch. A disclosure dialog lists every capability,
   what it sends, and the hosting facts below. `safety_shadow` is marked
   separately as the highest outbound risk.
3. Check every capability you intend to use, then confirm. The
   acknowledgement is stored as
   `experimental.judgment.disclosure_ack = {version, provider, capabilities, acked_at}`
   for `DISCLOSURE_VERSION` (`2026-09-20`). Changing the disclosure text
   bumps the version and requires a fresh acknowledgement. Switching between
   TypeSafe and the main LLM provider also requires a fresh acknowledgement;
   older records without `provider` apply only to TypeSafe.
4. Paste a TypeSafe API key and save. The field never echoes a saved key;
   the UI shows Configured / Not configured only. Clear removes the Store
   row and the keychain item.
5. Turn on individual capability switches. Backend and model are read-only
   (`typesafe` / `jev-1.13.0` unless you overrode them by environment).
6. **Test connection** sends a fixed probe that contains no user data
   (`POST /api/v1/experimental/judgment/test`). The result is
   `ok` / `unavailable` / `disabled`, plus `error_code` and `latency_ms`.

If the master environment variable is an explicit false, every switch on
this page is greyed out. The UI cannot override a kill switch.

### Environment (CLI, headless, CI)

```bash
export OPENAI4S_EXPERIMENTAL_JUDGMENT=1
export OPENAI4S_JUDGMENT_SKILL_SUGGEST=1          # optional, per capability
export OPENAI4S_JUDGMENT_LITERATURE=1
export OPENAI4S_JUDGMENT_TEXT_FEATURES=1
export OPENAI4S_JUDGMENT_SAFETY_SHADOW=1
export OPENAI4S_JUDGMENT_TASK_MODE_SHADOW=1
export OPENAI4S_TYPESAFE_API_KEY=...              # headless; never logged
```

An environment enable treats the operator as already informed and logs a
warning at startup. It does not require a Store disclosure acknowledgement.

`Config` snapshots environment flags at construction. Exporting a variable
after `Config(...)` has been built does not turn a capability on; build a
new `Config`.

Closed vocabularies (`0`/`1`, `false`/`true`, `no`/`yes`, `off`/`on`). A
typo such as `flase` raises `ValueError` rather than enabling anything.

| Switch | Environment variable | Store setting | Default |
| --- | --- | --- | --- |
| Master | `OPENAI4S_EXPERIMENTAL_JUDGMENT` | `experimental.judgment.enabled` | off |
| Skill recommendation | `OPENAI4S_JUDGMENT_SKILL_SUGGEST` | `experimental.judgment.capabilities.skill_suggest` | off |
| Literature check | `OPENAI4S_JUDGMENT_LITERATURE` | `experimental.judgment.capabilities.literature_check` | off |
| Text features | `OPENAI4S_JUDGMENT_TEXT_FEATURES` | `experimental.judgment.capabilities.text_features` | off |
| Safety shadow | `OPENAI4S_JUDGMENT_SAFETY_SHADOW` | `experimental.judgment.capabilities.safety_shadow` | off |
| Task-mode shadow | `OPENAI4S_JUDGMENT_TASK_MODE_SHADOW` | `experimental.judgment.capabilities.task_mode_shadow` | off |
| Backend | `OPENAI4S_JUDGMENT_PROVIDER` | `experimental.judgment.provider` | `typesafe` (`llm` is explicit) |
| Model | `OPENAI4S_JUDGMENT_MODEL` | `experimental.judgment.model` | `jev-1.13.0` |
| Timeout | `OPENAI4S_JUDGMENT_TIMEOUT_S` | — | `3.0` seconds (0.1–30) |
| API key | `OPENAI4S_TYPESAFE_API_KEY` | secret `typesafe_api_key`, scope `judgment` | none |
| Audit raw state | — | `experimental.judgment.audit_raw_state` | off |

Effective flags (`openai4s.judgment.flags.resolve`) follow
`JUDGMENT_FLAG_PRECEDENCE`:

1. Env explicitly false → force off (kill switch; the UI cannot override it).
2. Env explicitly true → on.
3. Otherwise consult the Store setting.
4. If neither is set → off.
5. When the master switch is off, every capability is off (`master_off`).
6. Enabling via the UI path also requires a **current-version** disclosure
   acknowledgement; otherwise the flag is `no_disclosure` and treated as off.

The TypeSafe key is resolved per request through SecretBroker. It is never
copied into `os.environ` by the client, never echoed in JSON or logs, and
never injected into the kernel environment.

REST (same auth as `/search/config`):

- `GET /api/v1/experimental/judgment` — flags, source, provider, model,
  `key_configured` (boolean only), disclosure version and ack, acknowledged
  capabilities, and the active provider's egress host and report. With
  `provider=llm`, the model and credentials come from Models; local endpoints
  do not require a key. TypeSafe hosting facts do not apply to that provider.
- `PUT /api/v1/experimental/judgment` — `enabled`, `capabilities`,
  `acknowledge:{version,provider,capabilities}`, `api_key`, `clear_api_key`.
  Invalid fields reject the whole request before any setting or key is changed.
- `POST /api/v1/experimental/judgment/test` — connection probe.

## How to disable

The kill switch is an explicit false on the master environment variable:

```bash
export OPENAI4S_EXPERIMENTAL_JUDGMENT=0
```

That forces every capability off. Turning the UI master switch off, or
clearing the Store setting, also disables the layer when the environment
variable is unset. With the master off, `host.judge` returns `disabled`
and the transport is not called.

Clear the key from Customize → General (Clear) or
`PUT {"clear_api_key": true}`. Headless: unset `OPENAI4S_TYPESAFE_API_KEY`.

## How to see status

**UI.** Customize → General → Experimental. The block shows each flag's
source (`env_off` / `env_on` / `setting` / `default` / `master_off` /
`no_disclosure`), whether a key is configured, provider, model, egress
mode, and whether `api.typesafe.ai` is already authorized. Test connection
writes `ok` / `unavailable` / `disabled` into
`[data-judgment-test-result]`.

**`openai4s doctor`.** Check name `judgment`. It does not open a network
connection and never prints the key.

| Situation | Doctor line |
| --- | --- |
| Default / master off | `[ok] judgment  disabled (experimental, default off)` |
| On, key present, egress ok | `[ok] judgment  enabled (typesafe/jev-1.13.0)` |
| On, no key | `[warn] judgment  enabled, but no TypeSafe API key is configured` |
| On, allowlist without grant | `[warn] judgment  enabled, but api.typesafe.ai is not on the egress allowlist` |

**Audit events.** Successful and failed Host runs emit a named `judgment`
event (`openai4s.observability.log_event`) with purpose, template id and
version, status, `error_code`, usage, latency, `state_sha256`, full
probabilities, `cache_hit`, `truncated`, `fake`, provider, and model. Raw
state is omitted unless `experimental.judgment.audit_raw_state` is true.
Shadow channels emit `judgment_shadow` with `kind`, `existing_verdict`,
`shadow_answers`, `agree`, `status`, `latency_ms`, and `state_sha256` —
not the code or the request text.

The dispatcher envelope `log_host_call(method="judge")` still records the
RPC spec, including state. That is a separate audit surface from the named
`judgment` event.

A session package export may attach `judgment_manifest.json` (capabilities
used, backend, model, template versions, call and token counts). It must
never include the key.

## Hosting, privacy, price

Write these down before you enable the layer, including for sensitive
lab data:

- The TypeSafe Jev service is **hosted in the United States**.
- The privacy policy states that **inputs are not used to train models**.
- The privacy policy **does not specify a retention period**.
- **Zero Data Retention (ZDR) is enterprise accounts only.**
- TypeSafe Jev is **early access** (waitlist). Offline development uses a
  loopback fake; a missing key leaves the rest of OpenAI4S unchanged.
- Input is **$0.042 per million tokens**; output is free. Usage still goes
  through the same budget admission as `host.llm`.
- **Do not enable this for sensitive data.**

Disclosure copy lives in `openai4s/judgment/disclosure.py`.

## Known limitations

These are properties of Jev and of this experiment, not temporary bugs:

- Question keys are not sent to the model. Each `instructions` string has
  to be self-contained and name the `state` field it reads.
- Choice is at most 255 options. Catalogs larger than that are narrowed
  first (Skill suggestion fans out through bioSkills areas).
- Noul returns P(yes) only, with no confidence.
- Score is 2–10 levels with a full distribution. The same mean can come
  from very different distributions; keep the distribution.
- Confidence is computed from the probability distribution. It is not
  accuracy. Thresholds are calibrated per template and language on a
  development split.
- State plus the longest question is capped at 32k tokens. The service
  truncates and marks `truncated`.
- Counting, arithmetic, numeric closeness, and date comparison stay in
  code. Jev is asked only discrete semantic questions.
- Adversarial text can steer answers. Safety stays shadow. Confidence is
  never authorization.
- English questions and criteria work best; CJK is weaker. Templates are
  written in English; state may contain Chinese. Evaluations report
  languages separately.
- Rate limits (250k tokens/s, 1200 requests/minute) and 429/529 retries
  sit inside a total time budget (default 3s). Host concurrency for
  distinct states is capped at 4.
- The SDK has moved quickly. This tree depends on the HTTP contract and
  rejects a whole payload on any validation failure.
- Live quality numbers for Jev on the frozen eval sets have not been
  collected in this tree. Graduation waits on those runs, a domestic p95
  measurement, and a maintainer sign-off. Until then treat the layer as
  an experiment, not a replacement for lexical search or DOI verification.
- `format_tool_result` still shows the model the lexical `search_skills`
  list when that list is non-empty. Experimental chips are a workbench
  projection. The model sees semantic suggestions in the tool observation
  when lexical search returned nothing.
- Two shadow queues (`shadow.py` and `task_mode_shadow.py`) run as
  separate modules. Default-off, they start no threads.

## Graduation criteria

Each capability graduates on its own. Removing the Experimental badge
requires all of:

1. The pre-registered test set meets the quality bar that was written down
   **before** that test set was opened.
2. Added p95 latency from a domestic (mainland China) network stays inside
   the stated bar.
3. One release cycle with no P0/P1 issue attributed to this layer.
4. A maintainer sign-off.

`safety_shadow` graduating still means shadow, not enforcement. Enforcement
needs a separate plan.

Placeholder bars recorded with the eval protocol (to be frozen before any
`--split test` run): `skill_suggest` error-recommendation rate at least 30%
relative lower than lexical B0, Chinese top-3 recall at least 0.7, domestic
added p95 at most 1.5 s; `literature_check` high-confidence error at most
3%, human-review share at most 30%, total cost no higher than the LLM
control.

## FAQ

### Can I use this from a mainland China network?

There is no built-in relay. The client talks to `api.typesafe.ai`. You can
put an HTTPS proxy in the process environment the same way as for any other
stdlib `urllib` client. In `OPENAI4S_EGRESS=allowlist` mode, grant
`api.typesafe.ai` with `host.request_network_access` (the Network panel has
no judgment group to toggle — v1.3 never added one). Expect extra latency
and more `unavailable` results; callers already treat that status as "no
recommendation / needs review" rather than a crash. Measure p95 on the
network you will actually use before treating the layer as load-bearing.

### Can I run this without TypeSafe?

Yes. Set `OPENAI4S_JUDGMENT_PROVIDER=llm` (or the Store setting
`experimental.judgment.provider=llm`). The configured main model answers
the same typed questions. Every result is `calibrated=false`. TypeSafe
failures become `unavailable`; they never call `chat()`. Use this as an
explicit control baseline, not as a drop-in for Jev probabilities.

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

### W4 — 2026-09-20

Merged four independent branches: `w4-a-safety-shadow` (`4dcd92ca`),
`w4-b-task-mode-shadow` (`59bf9dcd`), `w4-c-text-features-skill`
(`82337fed`) and `w4-d-llm-backend` (`9381e4c9`). Shadow judgment now runs
beside the code classifier, the injection scanner, the trajectory screener and
the task-mode rule; a `text-features` Skill turns free text into calibrated
features; and `provider=llm` selects an explicitly uncalibrated LLM backend.

The three safety screeners keep their verdicts. Each public function computes
the verdict through the original body, submits the shadow inside a
`try/except` that swallows everything, and returns the same object — there is
no branch on the shadow answer anywhere. The selected safety suite gives
identical results in all four combinations of `OPENAI4S_SAFETY` and the shadow
switch: 146 passed, 1 skipped each time, within a second of each other.

Both shadow channels are inert by default: a process that imports them, runs
`classify_code` and `resolve_task_mode` still has one thread and zero
submissions. Driving a submission through with a marker in the code confirms
the audit carries `kind`, `existing_verdict`, `shadow_answers`, `agree`,
`status`, `latency_ms` and `state_sha256` — and not the code.

Integration fix `927cb277`: four branches each appended an import to
`templates/__init__.py` and rewrote `__all__`. Resolving those conflicts as a
union — correct for the append-only README tables beside them — left three
`__all__` assignments, and the last one won, dropping `safety` and
`task_mode`. The imports survived, so every template still registered and
nothing failed. One `__all__` now, with a test pinning it to the imports.

The default-off snapshot moved again, and the delta is worth stating exactly:
adding one Skill to a 605-item corpus shifts the corpus-normalised relevance
`score` by at most 0.03. No query changed its hit set or its order, no Skill
entered or left a result set, and `score` is the only field that changed on an
existing row. `system_context.json` gained exactly one line; the tool-schema
and classifier fixtures are untouched, which is the evidence that wrapping
three security functions changed nothing observable.

Not done, deliberately: the two shadow channels remain separate modules.
`shadow.py` and `task_mode_shadow.py` implement the same pattern twice, and
`shadow.submit("task_mode", …)` would fit its existing signature — but W4-B's
tests import eight symbols from its channel and assert on a different
`stats()` contract 18 times. Folding them means the merger rewriting another
package's verification surface, which is worse than the duplication. A plan is
recorded for W5.

### W5 — 2026-09-21

Merged `w5-a-docs-release` (`3cdb34d9` → `3e8001c7`): this document became
the operator guide, and `docs/experimental-judgment-removal.md` and
`docs/release-notes-judgment.md` were added, with sections in
`configuration.md`, `security.md`, `skills.md` and both root READMEs.

Release verification. Every standalone CI gate that can run on this machine
passed: the frozen response shapes, the route contract, `uv build` followed by
`verify_release_artifacts.py` (the wheel carries all 23 files of
`openai4s/judgment/`, the bioSkills area index, and `skills/text-features/`),
the npm package check (605 Skills, 6.5 MB), `browser_smoke.mjs` with the
switch off and `browser_judgment.mjs` with it on. `container_smoke.sh` was not
run: there is no Docker on this machine. CI runs it.

Default-off, confirmed on a brand-new data directory: `GET
/api/v1/experimental/judgment` reports the master switch off with source
`default` and every capability off with source `master_off`. A real browser
session on that daemon — 7 frames, 109 host calls, 12 cell executions — left
no row containing "judgment" in any of the database's 82 tables, and nothing
in the daemon log.

Removal, rehearsed. The list was followed on the throwaway branch
`chore/judgment-removal-dryrun` (never merged, never pushed): 138 files,
32,696 lines deleted. The directory-README gate reports 166 directories and
1,590 files, matching W5-A's own rehearsal, and the full offline suite passes
at 9,621 tests — 330 fewer, the judgment suites themselves. Two corrections
went back into the list: the experimental section of literature-review's
SKILL.md is the file's last section, not one followed by another heading, and
its TypeSafe `third_party` entry sits directly above the frontmatter's closing
`---`, so a cut that runs to the next entry deletes the delimiter and the
loader reports the Skill's network mode as `unknown`.

Integration fixes: `b67e6902` finished W4's curated-count bump in the one
README_zh row the count contract does not pin; `a8389209` corrected the
removal list; `f85d3b95` added the explicit LLM backend to the release notes,
which listed every capability but not that option.
