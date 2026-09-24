# frontend/src/features/stream

[English](README.md)

实时工具输出截断。移植 app.js `appendLiveOutput`：满 1MB 加截断标记，之后再追加是空操作。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`cap.ts`](cap.ts) | `appendLiveOutput`、`LIVE_OUTPUT_CHAR_CAP`、`LIVE_OUTPUT_TRUNCATION`；`liveOutputIncrement`：同一截断规则，但依据调用方记录的长度与截断标记（不必每块都搜索整段输出）。 |
| [`cap.test.ts`](cap.test.ts) | 未超限拼接；截断后幂等；增量形式与 `appendLiveOutput` 产出完全一致。 |
