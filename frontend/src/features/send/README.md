# frontend/src/features/send

[中文说明](README_zh.md)

F-11 send chain and live cards. Composer `send()`, turn tickets, step / plan / permission / candidate cards, attachment and @-ref problem cards, and the admission tracker. Window names (`send`, `buildStepCard`, `renderAttachmentProblems`, `renderRefProblems`, `searchResultHttpUrl`, `admissionSettled`, `forgetAdmission`, `outstandingAdmissions`, `reconcileLastAdmission`, `rememberAdmission`) are assigned here, not left as F-05 stubs. `frame_update` stays F-06; the turn-ticket body is injected through `setFrameUpdateTurnHandler`.

## Files

| File | Responsibility |
| --- | --- |
| [`admission.ts`](admission.ts) | Admission tracker. Independent `openai4s.admission.{fid}.{id}` keys; legacy key migration; 60s grace. |
| [`admission.test.ts`](admission.test.ts) | Prefix, legacy migration, grace window, settled states. |
| [`bind.test.ts`](bind.test.ts) | `bindComposer` binds once; `installSend` stays DOM-free; Enter and the `#send-btn` click dispatch the text; one dispatch in flight at a time across both. |
| [`copy.ts`](copy.ts) | `sendCopy`: composer labels (the send button's title) kept out of the generated dictionaries. |
| [`candidate.ts`](candidate.ts) | Review gate three-state timing: `markCandidateReady` → `applyCandidateResolution` → `applyFinalReviewStatus`. |
| [`candidate.test.ts`](candidate.test.ts) | Three-state sequence, no verified demotion, durable-receipt rule. |
| [`first-send.test.ts`](first-send.test.ts) | The first message of a fresh session dispatches only after the shared creation has opened the conversation, so `openConversation`'s reset cannot land mid-turn; the ticket and the running state survive. |
| [`refused-send.test.ts`](refused-send.test.ts) | A send the server refuses before admission (409 `model_profile_needs_key` / `model_revision_unavailable` / `model_profile_needs_active`) keeps its text in the composer and removes the optimistic bubble -- or, when the composer already holds new text, keeps the bubble marked not sent so the refused text is never dropped -- and leaves the server's reason as the hint rather than "This turn failed"; a rebind's confirmation says what it actually bound (`rebindDoneText`), and the rebind prompt names the refusal's real reason from the server's code and message (`rebindConfirmText`: a profile moved to another provider or endpoint, a missing key, an unreadable pin, an ambiguous match; "no longer exists" only when the server said so, neutral "no longer usable" otherwise). |
| [`environment.ts`](environment.ts) | Standard-profile readiness banner used by `send()` / `turnDone`. |
| [`handlers.ts`](handlers.ts) | WS types for cards / candidate / step / plan / permission; `handleFrameUpdateTurn`. |
| [`host.ts`](host.ts) | `isReady` window lookups (`callLane` / `hostFn`); cancel-button visibility. |
| [`icon.ts`](icon.ts) | Extra step / plan / permission icons (globe, list-check, lock, …). |
| [`index.ts`](index.ts) | `installSend` assigns window names, registers WS handlers. DOM-free: `main.tsx` binds the composer after render. |
| [`install.test.ts`](install.test.ts) | Ten contract names pass `isReady`; does not register `frame_update`. |
| [`permission.ts`](permission.ts) | Permission gate cards. Frozen DOM classes `.perm-card` / `.resolved` / `.allowed` / `.denied`. |
| [`plan.ts`](plan.ts) | Structured plan card, progress, approve / revise / discard / resume. A step still `in_progress` pulses only while the plan is executing; a `completed` plan that still has one (rows written before the server refused that pair) is labelled as ended with steps not confirmed, from a feature-local copy table. |
| [`plan.test.ts`](plan.test.ts) | The terminal plan card: a completed plan with a step in progress is not shown as complete, the live glyph stays while executing, and a fully settled plan still reads complete. |
| [`problems.ts`](problems.ts) | Attachment problem cards (client wording) and @-ref problem cards (server wording). |
| [`send.test.ts`](send.test.ts) | `send()` outside refusals and first sends: a `/skill` send after a failed catalog read gets its directive on the next send. |
| [`send.ts`](send.ts) | Composer send chain. Plan-mode payload via F-07 `planModePayload`. `bindComposer` (called from `main.tsx` after `render`) binds Enter and the send button to one dispatch latch. |
| [`step.ts`](step.ts) | Semantic activity steps, `buildStepCard`, `searchResultHttpUrl`. |
| [`ticket.ts`](ticket.ts) | Turn ticket generation, `acceptTurnTicket` / `activateTurnTicket`, `resumeWatch`. |
| [`turn.ts`](turn.ts) | `turnDone` teardown; calls F-14 `notebookOnTurnDone()`; settles any activity card still running (`messages/cardState.ts`). |
