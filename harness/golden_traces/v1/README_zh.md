# Golden Trace Schema v1

[English](README.md)

本目录是经审阅 Harness trace 数据的第一个版本 namespace。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`r5_prechange.json`](r5_prechange.json) | 选定 r5 生产行为的 canonical 规范化 snapshot：CLI max turns、传输重试/部分流故障、provider compaction 投影、超大 observation、headless 权限拒绝及 disabled MCP 处理。每个 case 都记录 current behavior、desired contract 以及 snapshot 是否捕获已知缺陷。 |

## 直属子目录

无。

使用 `uv run python -m harness.cli characterize` 只比较而不写入。Mismatch 是审阅信号，不是自动覆盖 golden 的许可。
