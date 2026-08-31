# frontend/src/features/send

[中文说明](README_zh.md)

F-11 send chain and live cards. Composer `send()`, turn tickets, step / plan / permission / candidate cards, attachment and @-ref problem cards, and the admission tracker. Window names (`send`, `buildStepCard`, `renderAttachmentProblems`, `renderRefProblems`, `searchResultHttpUrl`, `admissionSettled`, `forgetAdmission`, `outstandingAdmissions`, `reconcileLastAdmission`, `rememberAdmission`) are assigned here, not left as F-05 stubs. `frame_update` stays F-06; the turn-ticket body is injected through `setFrameUpdateTurnHandler`.

## Files

| File | Responsibility |
| --- | --- |
| [`admission.ts`](admission.ts) | Admission tracker. Independent `openai4s.admission.{fid}.{id}` keys; legacy key migration; 60s grace. |
| [`admission.test.ts`](admission.test.ts) | Prefix, legacy migration, grace window, settled states. |
| [`candidate.ts`](candidate.ts) | Review gate three-state timing: `markCandidateReady` → `applyCandidateResolution` → `applyFinalReviewStatus`. |
| [`candidate.test.ts`](candidate.test.ts) | Three-state sequence, no verified demotion, durable-receipt rule. |
| [`environment.ts`](environment.ts) | Standard-profile readiness banner used by `send()` / `turnDone`. |
| [`handlers.ts`](handlers.ts) | WS types for cards / candidate / step / plan / permission; `handleFrameUpdateTurn`. |
| [`host.ts`](host.ts) | `isReady` window lookups (`callLane` / `hostFn`); cancel-button visibility. |
| [`icon.ts`](icon.ts) | Extra step / plan / permission icons (globe, list-check, lock, …). |
| [`index.ts`](index.ts) | `installSend` assigns window names, registers WS handlers, binds composer. |
| [`install.test.ts`](install.test.ts) | Ten contract names pass `isReady`; does not register `frame_update`. |
| [`permission.ts`](permission.ts) | Permission gate cards. Frozen DOM classes `.perm-card` / `.resolved` / `.allowed` / `.denied`. |
| [`plan.ts`](plan.ts) | Structured plan card, progress, approve / revise / discard / resume. |
| [`problems.ts`](problems.ts) | Attachment problem cards (client wording) and @-ref problem cards (server wording). |
| [`send.ts`](send.ts) | Composer send chain. Plan-mode payload via F-07 `planModePayload`. `bindComposer`. |
| [`step.ts`](step.ts) | Semantic activity steps, `buildStepCard`, `searchResultHttpUrl`. |
| [`ticket.ts`](ticket.ts) | Turn ticket generation, `acceptTurnTicket` / `activateTurnTicket`, `resumeWatch`. |
| [`turn.ts`](turn.ts) | `turnDone` teardown; calls F-14 `notebookOnTurnDone()`. |
