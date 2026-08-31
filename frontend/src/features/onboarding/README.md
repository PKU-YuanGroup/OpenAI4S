# frontend/src/features/onboarding

[中文说明](README_zh.md)

M-01 first-run wizard kernel. Four required decision steps, skip/checklist, and B-04 `capability_receipt` badge rows. GET `/onboarding` is redacted and contacts nobody; Test is the only action that probes a provider.

## Files

| File | Responsibility |
| --- | --- |
| [`api.ts`](api.ts) | `GET /onboarding`, `POST /onboarding/complete`, profile save/activate/probe. |
| [`badges.test.ts`](badges.test.ts) | Tri-state badge markup; unknown reason is not rewritten. |
| [`badges.ts`](badges.ts) | `capability_receipt` → `true` / `false` / `unknown` rows + stale. |
| [`boot.ts`](boot.ts) | `bootOnboarding()` mounts `#onboarding-root`. |
| [`copy.ts`](copy.ts) | Lane-local zh/en strings. Does not rewrite generated i18n. |
| [`index.ts`](index.ts) | Public re-exports. |
| [`machine.test.ts`](machine.test.ts) | Skip / checklist / request-id errors; providerRequests=0 before Test. |
| [`machine.ts`](machine.ts) | Four-step reducer. Credentials never enter wizard state. |
| [`status.ts`](status.ts) | Sanitize GET payload; drop credential-shaped keys. |
