# frontend/src/stores

[English](README.md)

F-05 把旧的 `S` 单例拆成的 signal 模块。后续 F 系列车道只 import 这些文件，不改本体。需要新状态时，在自己的目录建局部 signal，留 TODO 待集成时评估是否上升。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`MIGRATION.md`](MIGRATION.md) | 每个 `S.<name>` 字段一行：原名 → store 路径、来源行号、是否保引用同一性。 |
| [`artifacts.ts`](artifacts.ts) | 产物列表、Files 范围、渲染器目录、bust/version/表格缓存、3Dmol 句柄。 |
| [`customize.ts`](customize.ts) | 模型、默认模型、skills 目录、环境状态。 |
| [`index.ts`](index.ts) | 再导出七个 store、`S_FIELD_META`、`createSProxy`、`resetStoreFields`。 |
| [`migration.test.ts`](migration.test.ts) | 把 `MIGRATION.md` + `S_FIELD_META` 对照 `tests/webui-contract.md` 做 diff。 |
| [`notebook.ts`](notebook.ts) | cells、kernels、lineage、REPL 草稿、变量检查器、模块级 `_kc`。 |
| [`registry.ts`](registry.ts) | `S_FIELD_META`、`sSignals`、`createSProxy`（get/set ↔ `signal.value`）。 |
| [`session.ts`](session.ts) | 项目/会话身份、文件夹、消息游标、批注。 |
| [`signal-field.ts`](signal-field.ts) | `field(init)` + 测试用的 `resetStoreFields()`。 |
| [`stream.ts`](stream.ts) | WS 句柄、直播 wrap、plan/turn ticket、`_seqSeen` / `_streamEpoch`。 |
| [`timeline.ts`](timeline.ts) | Action timeline、执行队列、workbench 投影、ACTION_TIMELINE_* 常量。 |
| [`ui.ts`](ui.ts) | Dock、标签、provenance 外壳、菜单、轮询定时器、滚动跟随。 |
