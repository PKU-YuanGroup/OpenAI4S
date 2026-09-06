# frontend/src/components/onboarding

[中文说明](README_zh.md)

M-01 first-run wizard view. Four decision steps, skip/checklist, and tri-state capability badges. The API key is an uncontrolled password field and is never copied into wizard state or text nodes.

## Files

| File | Responsibility |
| --- | --- |
| [`CapabilityBadges.tsx`](CapabilityBadges.tsx) | `true` / `false` / `unknown` pills; unknown reason is shown verbatim. |
| [`index.ts`](index.ts) | Re-exports the host, badges, and readiness panel. |
| [`onboarding.css`](onboarding.css) | Lane-local overlay; ≥40px targets at ≤900px. |
| [`ReadinessPanel.tsx`](ReadinessPanel.tsx) | Standard-profile + network posture from GET `/onboarding`. |
| [`Wizard.tsx`](Wizard.tsx) | `#onboarding` dialog; path / Test / readiness / project. |
