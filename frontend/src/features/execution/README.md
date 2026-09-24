# frontend/src/features/execution

[中文说明](README_zh.md)

F-16 execution view. Executed-code history, the variable inspector, the artifact Provenance tab, and 409 presentation for fork-without-checkpoint. Sanitize of recovery/branch payloads stays in F-15 `features/timeline/sanitize.ts`; this lane wires the REST mutations and surfaces a missing cursor checkpoint as HTTP 409 without retry or rewrite.

F-14 already gates `toggleExecutedCode` / `buildExecutedCodeView` through `isReady`. F-17 Viewer calls `window.renderProvenanceInto` when `provMode` is set. This lane assigns those names and composes F-14 `renderNotebook` with the inspector.

Does not edit `stores/` or `compat/window-exports.ts` above the lane-additions marker.

## Files

| File | Responsibility |
| --- | --- |
| [`types.ts`](types.ts) | Executed-code / lineage / env snapshot records. |
| [`api.ts`](api.ts) | Same-origin fetch that keeps HTTP status on `ApiError` (409 vs other). |
| [`conflict.ts`](conflict.ts) | Fork 409 presentation: no retry, server sentence intact. |
| [`lineage.ts`](lineage.ts) | Provenance chain transforms (cell / captures / env honesty). `envPackageCount` is null when the record's list was never read (`packages_unavailable` with no packages). |
| [`exec.ts`](exec.ts) | `execSourcesState` / `toggleExecutedCode` / `buildExecutedCodeView` (app.js:10148-10229). |
| [`inspector.ts`](inspector.ts) | Variable inspector (app.js:10265-10332). |
| [`provenance.ts`](provenance.ts) | Provenance tab (app.js:10631-10833). An unread package list shows "Packages unknown" ("n/a" for a non-Python kernel) and no "No packages to report."; the reason stays as the warn note. |
| [`branch.ts`](branch.ts) | Fork / recovery REST with 409 presentation. |
| [`boot.ts`](boot.ts) | Window names + notebook/viewer composition. |
| [`index.ts`](index.ts) | Public re-exports. |
| [`lineage.test.ts`](lineage.test.ts) | Provenance chain data transforms; the Environment panel rendered for a legacy 0.2.x snapshot, a really empty Python list and an R kernel. |
| [`conflict.test.ts`](conflict.test.ts) | 409 presentation; `forkOnce` is single-shot; the branch-error banner is published as a new object. |
| [`inspector.test.ts`](inspector.test.ts) | Variable inspector refresh publishes loading and results as new state objects. |

- [`copy.ts`](copy.ts): Bilingual provenance read-state and evidence copy.
- [`validation.ts`](validation.ts): Strict network lineage/environment validation, with historical nullable fields preserved.
- [`provenance.test.ts`](provenance.test.ts): Read retries, out-of-order results, artifact owner and exact Cell navigation.
