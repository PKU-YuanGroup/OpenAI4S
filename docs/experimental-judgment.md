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
