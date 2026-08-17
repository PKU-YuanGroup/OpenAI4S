# Golden trace schema v1

[中文说明](README_zh.md)

The first versioned namespace for reviewed Harness trace data. Everything here is at schema version 1.

## Files

| File | Responsibility |
| --- | --- |
| [`auto_mode_contract_expected.json`](auto_mode_contract_expected.json) | Independent, reviewed Stage 0 Auto Mode contract expectations: canonical and specific event sequences, canonical fixture and audit-request digests, completion-assessment digests, terminal payloads, and an explicit `production_state_machine: false`. Request and assessment digests are deliberately distinct; the assessment binds the attempt, verdict or decision, findings, risk, authorization, outcome, rationale, and failure kind. The adapter reads this data; scenarios do not derive it from their own replay result. |
| [`auto_mode_terminal_contract_expected.json`](auto_mode_terminal_contract_expected.json) | Independent, reviewed expectations for the five non-Guardian Stage 0 stops. It freezes exact terminal payloads plus canonical identity and reason-condition digests, keeps `auto_user_state` null, and explicitly rejects any claim that the production state machine already exists. |
| [`r5_prechange.json`](r5_prechange.json) | The reviewed snapshot of selected r5 production behavior, normalized to canonical bytes: CLI max turns, a 429 carrying a `Retry-After`, a stream that fails after a delta has already been committed, how the compaction summary projects onto provider payloads, an oversized observation, headless permission denial, and a disabled MCP connector. Each case records what production does today, the contract that is wanted instead, and whether the snapshot is freezing a known bug — and "pre-change" is by now only the file's name: four of the seven were recorded as known bugs, all four have since been fixed, and their `current_behavior` lines were rewritten to describe the fix. That is the file doing its job, not drifting. |

`uv run python -m harness.cli characterize` compares without writing. A mismatch means someone has to look at it; it is not permission to overwrite the golden automatically.

Ordinary-denial assessments keep both breakers closed when no threshold history
exists and bind `terminal_basis=no_safe_continuation`. The denial circuit budget
remains three consecutive denials or 10 denials in the latest 50 decisions.

The non-Guardian terminal golden is intentionally separate from Reviewer and
Guardian decisions. Its conditions bind admission, rollback, reconciliation,
and circuit facts without manufacturing an audit identity or permitting an
automatic continuation.
