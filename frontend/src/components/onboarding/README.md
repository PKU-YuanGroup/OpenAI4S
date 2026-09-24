# frontend/src/components/onboarding

[中文说明](README_zh.md)

M-01 first-run wizard view. Four decision steps, skip/checklist, and tri-state capability badges. The API key is an uncontrolled password field and is never copied into wizard state or text nodes.

## Files

| File | Responsibility |
| --- | --- |
| [`CapabilityBadges.tsx`](CapabilityBadges.tsx) | `true` / `false` / `unknown` pills; unknown reason is shown verbatim. |
| [`index.ts`](index.ts) | Re-exports the host, badges, and readiness panel. |
| [`onboarding.css`](onboarding.css) | Lane-local overlay; ≥40px targets at ≤900px. |
| [`ReadinessPanel.tsx`](ReadinessPanel.tsx) | Standard-profile + network posture from GET `/onboarding`. `EnvironmentCard` / `CopyCommand` are shared with Settings -> Compute; Copy goes through `copyText`. |
| [`ReadinessPanel.test.tsx`](ReadinessPanel.test.tsx) | A readiness Copy works over plain http through the selection copy and reports a copy that did not happen; the card carries its host's head action. |
| [`Wizard.tsx`](Wizard.tsx) | `#onboarding` dialog; path / Test / readiness / project. A running Test disables only its own button: Skip, Checklist and Continue stay usable, and leaving the step drops the probe's late answer. Continue on a cloud or local path the wizard already saved updates that profile instead of adding a duplicate. |
