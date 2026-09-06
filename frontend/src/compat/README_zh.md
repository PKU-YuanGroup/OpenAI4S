# frontend/src/compat

[English](README.md)

F-05 的 E2E 兼容层。输入清单是 `tests/webui-contract.md`。后续 F 系列车道只能在 `window-exports.ts` 末尾的 `// === lane additions ===` 标记区每行 append 一处 window 赋值，不得改文件其余部分。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`stub.ts`](stub.ts) | 占位标记与 `isReady` 能力判定。无副作用——询问能力不会顺带装载任何东西。 |
| [`window-exports.ts`](window-exports.ts) | 把 F-01 全局挂到 `window`，`window.S` Proxy（get/set ↔ signals），以及车道标记区。 |
| [`window-exports.test.ts`](window-exports.test.ts) | Proxy 读写、`_timelineView` / `actionTimeline` / `executionQueue` 对象同一性、嵌套写入。 |
