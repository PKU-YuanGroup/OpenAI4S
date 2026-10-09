# frontend/src/features/automode

[中文说明](README_zh.md)

The read-only Auto Mode status block in the session options menu and its Audit view (issue #217). It implements the "Workbench status surface" of [`docs/auto-mode.md`](../../../../docs/auto-mode.md): three separate lines (Availability, Saved selection, Run), the deployment ceilings and usage meters, and an Audit entry. It reads `GET /frames/{id}/auto-mode` and `GET /frames/{id}/auto-audits` only — no PATCH, no transition, no model call, review, repair, resume or cancellation. The menu's legacy "Auto review" row is untouched and still talks only to `review-settings`.

A status read starts when the menu opens, on an explicit retry, on a canonical event hint, on a reconnect and on a reopen (`_openGen`). A response for another conversation or an earlier opening is dropped; reads that overlapped are ordered by the branch's `last_event_ordinal`, then the selection `revision`, then issue order, and a read issued after the shown one arrived always wins (a revert can move a cursor back). The registry takes one handler per event type: this lane registers five canonical types, and the send lane's `candidate_ready` / `auto_run_terminal` handlers pass their event on through `autoModeHint`.

## Files

| File | Responsibility |
| --- | --- |
| [`types.ts`](types.ts) | The closed vocabularies of both routes and the sanitized shapes. |
| [`sanitize.ts`](sanitize.ts) | Allowlist sanitizers. Fields are copied one by one; an unknown schema or closed value returns `null` ("Status unavailable"). |
| [`copy.ts`](copy.ts) | Feature-local English/Chinese copy (`autoModeT`), so the generated `i18n/en.ts` / `zh.ts` stay untouched. |
| [`present.ts`](present.ts) | Pure text for the three lines, the budget block (near/at ceiling, unfrozen token ceiling, tripped circuit) and audit rows. |
| [`status.ts`](status.ts) | The status store: context re-keying, read ordering, failure mapping. |
| [`menu.ts`](menu.ts) | The block the session options menu shows, repainted in place while open. |
| [`audits.ts`](audits.ts) | The Audit modal: kind filter, `before` cursor paging, the contract's error codes, refresh on hints. |
| [`hints.ts`](hints.ts) | Canonical events as refresh hints, the reconnect and reopen watchers. |
| [`index.ts`](index.ts) | Public exports and `installAutoMode()` (called from `main.tsx`). |
| [`automode.css`](automode.css) | Block and Audit view styles. Existing CSS tokens only. |
| [`testing.ts`](testing.ts) | Test support only: a minimal fake DOM, response builders shaped like the real service's output, a recording `fetch` stub. Nothing in the app imports it. |
| [`sanitize.test.ts`](sanitize.test.ts) | Allowlist, closed vocabularies, availability consistency, unknown usage, `has_more` only with a cursor, credential scrubbing. |
| [`present.test.ts`](present.test.ts) | Both languages' copy for every line and source, the worked example, the meter rules, and no "On"/"已开启" anywhere. |
| [`status.test.ts`](status.test.ts) | Out-of-order reads, unchanged-ordinal selection refresh, revert, frame/branch changes, failures, GET only. |
| [`menu.test.ts`](menu.test.ts) | The block inside the real `sessionOptionsMenu`: placement, the Auto review row unchanged, retry, Audit entry, hints. |
| [`audits.test.ts`](audits.test.ts) | Paging, filter, superseded answers, redaction, error codes, refresh and reopen, GET only. |
| [`hints.test.ts`](hints.test.ts) | Registry composition with the send lane, reconnect and reopen triggers. |
