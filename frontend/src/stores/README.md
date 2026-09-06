# frontend/src/stores

[中文说明](README_zh.md)

F-05 signal modules for the old `S` singleton. Later F-series lanes import these files and do not edit them. Need a new piece of state? Keep a local signal in your own directory and leave a TODO for integration.

## Files

| File | Responsibility |
| --- | --- |
| [`MIGRATION.md`](MIGRATION.md) | One row per `S.<name>` field: original name → store path, origin line, identity. |
| [`artifacts.ts`](artifacts.ts) | Artifact list, Files scope, renderer catalog, bust/version/table caches, 3Dmol handles. |
| [`customize.ts`](customize.ts) | Models, default model, skills catalog, environment status. |
| [`index.ts`](index.ts) | Re-exports the seven stores, `S_FIELD_META`, `createSProxy`, `resetStoreFields`. |
| [`migration.test.ts`](migration.test.ts) | Diffs `MIGRATION.md` + `S_FIELD_META` against `tests/webui-contract.md`. |
| [`notebook.ts`](notebook.ts) | Cells, kernels, lineage, REPL drafts, variable inspector, module-level `_kc`. |
| [`registry.ts`](registry.ts) | `S_FIELD_META`, `sSignals`, `createSProxy` (get/set ↔ `signal.value`). |
| [`session.ts`](session.ts) | Project/session identity, folders, messages cursor, annotations. |
| [`signal-field.ts`](signal-field.ts) | `field(init)` + `resetStoreFields()` for tests. |
| [`stream.ts`](stream.ts) | WS handle, live stream wrap, plan/turn ticket, `_seqSeen` / `_streamEpoch`. |
| [`timeline.ts`](timeline.ts) | Action timeline, execution queue, workbench projections, ACTION_TIMELINE_* constants. |
| [`ui.ts`](ui.ts) | Dock, tabs, provenance chrome, menus, poll timers, scroll-follow. |
