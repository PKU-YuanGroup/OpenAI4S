# frontend/src/features/stream

[English](README.md)

实时工具输出截断。移植 app.js `appendLiveOutput`：满 1MB 加截断标记，之后再追加是空操作。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`cap.ts`](cap.ts) | `appendLiveOutput`、`LIVE_OUTPUT_CHAR_CAP`、`LIVE_OUTPUT_TRUNCATION`。 |
| [`cap.test.ts`](cap.test.ts) | 未超限拼接；截断后幂等。 |
