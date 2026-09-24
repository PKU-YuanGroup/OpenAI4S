# frontend/src/features/onboarding

[中文说明](README_zh.md)

M-01 first-run wizard kernel. Four required decision steps, skip/checklist, and B-04 `capability_receipt` badge rows. GET `/onboarding` is redacted and contacts nobody; Test is the only action that probes a provider.

## Files

| File | Responsibility |
| --- | --- |
| [`api.test.ts`](api.test.ts) | Existing-profile activation occurs before onboarding status refresh. |
| [`api.ts`](api.ts) | `GET /onboarding`, `POST /onboarding/complete`, profile save/update/activate/probe. |
| [`badges.test.ts`](badges.test.ts) | Tri-state badge rows and the pills `CapabilityBadges` renders from them; unknown reason is not rewritten. |
| [`badges.ts`](badges.ts) | `capability_receipt` → `true` / `false` / `unknown` rows + stale. |
| [`boot.ts`](boot.ts) | `bootOnboarding()` mounts `#onboarding-root`. |
| [`copy.ts`](copy.ts) | Lane-local zh/en strings (`ot`). Does not rewrite generated i18n. `copyLookup(table)` is the lookup `ot` and `judgmentT` share. |
| [`copy.test.ts`](copy.test.ts) | `copyLookup`: active language, then English, then the key; the loaded dictionary wins; positional holes. `ot` and `judgmentT` are built on it. |
| [`index.ts`](index.ts) | Public re-exports. |
| [`machine.test.ts`](machine.test.ts) | Skip / checklist / request-id errors; providerRequests=0 before Test; model identity changes clear receipts and Test decisions; a probe result measured for another profile is dropped. |
| [`machine.ts`](machine.ts) | Four-step reducer. Model identity changes clear the prior capability receipt and Test decision; display-only changes preserve them. A `testResult` names the profile it measured and is ignored unless that is the selected path. Credentials never enter wizard state. |
| [`status.ts`](status.ts) | Sanitize GET payload; drop credential-shaped keys. |
| [`wizard-integration.test.ts`](wizard-integration.test.ts) | Existing-profile Continue awaits activation and refreshes edited model identities before dispatching the next step. |
| [`wizard-save.test.ts`](wizard-save.test.ts) | Saving a new profile or activating an existing one re-reads the composer's model list. Continue on a path already saved updates that profile (the key is kept when the field is empty); an identical saved profile is reused; one deleted meanwhile is saved afresh. |
| [`wizard-skip.test.ts`](wizard-skip.test.ts) | Skip stays enabled while Test is still waiting and completes before the probe answers; a probe answer that arrives after Skip or Continue is dropped; a probe that fails while the user waits is still reported. A former model's late response/error cannot overwrite a newly selected model's test (a characterization: the test-step exits already retired the probe). Test probes the selected path and never the active profile in its place: an unsaved choice asks for a profile instead, and with no path the active profile is selected first. Editing a saved local model's name makes the choice unsaved again, so Test cannot measure the old model under the new name. |
