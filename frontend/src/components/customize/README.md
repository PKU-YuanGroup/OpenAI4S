# frontend/src/components/customize

[中文说明](README_zh.md)

F-19 Customize modal. Nine tab components, a nested editor overlay, and `vendors/` cards. Class names (`#cust`, `.cust-tab`, `.prof-row`, `.cust-row`, `.toggle`) match the E2E contract. Tab unmount disposes the timer lease.

## Files

| File | Responsibility |
| --- | --- |
| [`ComputeTab.tsx`](ComputeTab.tsx) | Compute, remote GPU, jobs. Job poll 1500ms on the lease. |
| [`ConnectorsTab.tsx`](ConnectorsTab.tsx) | Connector list; DataPro card is isolated in `vendors/`. |
| [`Customize.tsx`](Customize.tsx) | `#cust` shell, tablist, Esc / backdrop close. |
| [`GeneralTab.tsx`](GeneralTab.tsx) | Theme, layout, language, API-key shortcut. |
| [`DiagnosticsTab.tsx`](DiagnosticsTab.tsx) | Passive security posture, explicit checks, and redacted support-bundle download. Mounted from General. |
| [`DiagnosticsTab.test.tsx`](DiagnosticsTab.test.tsx) | Page load is a single status GET; checks and bundle wait for a click. |
| [`MemoryTab.tsx`](MemoryTab.tsx) | Memory enable / add / edit / delete with explicit scope. |
| [`ModelsTab.tsx`](ModelsTab.tsx) | Profiles, local scan, probe, capability-receipt badges. |
| [`NestedEditor.tsx`](NestedEditor.tsx) | Skill / specialist / connector / job-output overlay. |
| [`SkillImport.test.tsx`](SkillImport.test.tsx) | Import review shows requirements, network mode, and readiness before enable. |
| [`NetworkTab.tsx`](NetworkTab.tsx) | Doubao card, allowlist, Tavily backup, telemetry drain. |
| [`PermissionsTab.tsx`](PermissionsTab.tsx) | Per-scope approval rules. |
| [`SkillsTab.tsx`](SkillsTab.tsx) | Personal / project / collection skills. |
| [`SpecialistsTab.tsx`](SpecialistsTab.tsx) | Custom specialists and builtin roles. |
| [`customize.css`](customize.css) | Lane-local modal chrome until F-21 ports `style.css`. |
| [`icons.tsx`](icons.tsx) | Lucide paths used by this modal. |
| [`index.ts`](index.ts) | Re-exports `Customize`. |
| [`ui.tsx`](ui.tsx) | Shared `Hdr` / `CustRow` / `Seg` / `Toggle` / `Pill`. |
| [`use-timer-lease.ts`](use-timer-lease.ts) | `useTimerLease` / `useAlive` bound to unmount. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`vendors/`](vendors/) | Volcengine / DataPro / Doubao cards, kept out of the tab shell. |
