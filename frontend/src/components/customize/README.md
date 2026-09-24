# frontend/src/components/customize

[中文说明](README_zh.md)

F-19 Customize modal. Nine tab components, a nested editor overlay, and `vendors/` cards. Class names (`#cust`, `.cust-tab`, `.prof-row`, `.cust-row`, `.toggle`) match the E2E contract. Tab unmount disposes the timer lease.

## Files

| File | Responsibility |
| --- | --- |
| [`ComputeTab.tsx`](ComputeTab.tsx) | Compute, remote GPU, jobs. One job poll chain, 1500ms on the lease; a newer read supersedes an older one. |
| [`ComputeTab.test.tsx`](ComputeTab.test.tsx) | Submit and Cancel re-read the job list without starting a second poll chain; an older read that answers late is dropped. |
| [`ConnectorsTab.tsx`](ConnectorsTab.tsx) | Connector list; DataPro card is isolated in `vendors/`. |
| [`Customize.tsx`](Customize.tsx) | `#cust` shell, tablist, Esc / backdrop close. |
| [`Customize.test.tsx`](Customize.test.tsx) | A selection drag that ends on the Customize or nested-editor backdrop keeps the dialog open; a press on the backdrop still closes it. |
| [`GeneralTab.tsx`](GeneralTab.tsx) | Theme, layout, language, API-key shortcut. |
| [`DiagnosticsTab.tsx`](DiagnosticsTab.tsx) | Passive security posture, explicit checks, and redacted support-bundle download. Mounted from General. |
| [`DiagnosticsTab.test.tsx`](DiagnosticsTab.test.tsx) | Page load is a single status GET; checks and bundle wait for a click. |
| [`MemoryTab.tsx`](MemoryTab.tsx) | Memory enable / add / edit / delete with explicit scope. |
| [`MemoryTab.test.tsx`](MemoryTab.test.tsx) | Save adds a memory once however often it is pressed while the write is in flight. |
| [`ModelsTab.tsx`](ModelsTab.tsx) | Profiles, local scan, probe, capability-receipt badges. With no active profile it shows the live `GET /config/llm` model (environment or saved settings) as the active row. |
| [`ModelsTab.test.tsx`](ModelsTab.test.tsx) | An `.env`-configured install with no profiles shows its active model instead of "No models configured yet"; no extra row when a profile is active; an unreadable config does not hide profiles; a profile on the environment key is labelled so, not "No key"; a local model is added once however often Add is pressed while the write is in flight; adding, deleting or adding a local profile re-reads the composer's `#model-select` list. |
| [`NestedEditor.tsx`](NestedEditor.tsx) | Skill / specialist / connector / job-output overlay. An edit form cannot save until its first read has succeeded. |
| [`NestedEditor.test.tsx`](NestedEditor.test.tsx) | A failed Skill or specialist read is shown with Retry and blocks Save, so blank fields are never saved over the server copy. |
| [`SkillImport.test.tsx`](SkillImport.test.tsx) | Import review shows requirements, network mode, and readiness before enable. |
| [`NetworkTab.tsx`](NetworkTab.tsx) | Doubao card, allowlist, Tavily backup, telemetry drain. |
| [`PermissionsTab.tsx`](PermissionsTab.tsx) | Per-scope approval rules. A rule's decision is optimistic and goes back when the server refuses it. |
| [`PermissionsTab.test.tsx`](PermissionsTab.test.tsx) | A decision the server took stays shown; a refused one goes back to the rule's decision (a render is asked for, so the select resets). |
| [`SkillsTab.tsx`](SkillsTab.tsx) | Personal / project / collection skills. |
| [`SpecialistsTab.tsx`](SpecialistsTab.tsx) | Custom specialists and builtin roles. |
| [`switches.test.tsx`](switches.test.tsx) | The Network egress and Memory switches stay disabled until the first read lands, ignore a click while a write is in flight, and go back to the confirmed value when a write fails. |
| [`customize.css`](customize.css) | Lane-local modal chrome until F-21 ports `style.css`. |
| [`icons.tsx`](icons.tsx) | Lucide paths used by this modal. |
| [`hooks.ts`](hooks.ts) | `useOptimistic` / `useOptimisticToggle`: a control bound to one server setting is disabled until the tab's first read lands (`null`), moves at once, allows one write at a time, and goes back to the confirmed value when a write fails. |
| [`index.ts`](index.ts) | Re-exports `Customize`. |
| [`ui.tsx`](ui.tsx) | Shared `Hdr` / `CustRow` / `Seg` / `Toggle` / `Pill`. |
| [`use-timer-lease.ts`](use-timer-lease.ts) | `useTimerLease` / `useAlive` bound to unmount. |
| [`ExperimentsTab.tsx`](ExperimentsTab.tsx) | Experimental semantic-judgment block on General: disclosure, capability toggles, TypeSafe key, connection probe. |
| [`ExperimentsTab.test.tsx`](ExperimentsTab.test.tsx) | Disclosure must be acknowledged before enable; `env_off` disables toggles; key is never echoed; probe shows ok / unavailable / disabled. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`vendors/`](vendors/) | Volcengine / DataPro / Doubao cards, kept out of the tab shell. |
