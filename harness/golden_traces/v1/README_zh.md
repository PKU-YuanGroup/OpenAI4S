# Golden Trace Schema v1

[English](README.md)

经审阅的 Harness trace 数据的第一个版本目录，里面的资产都在 schema 版本 1 上。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`r5_prechange.json`](r5_prechange.json) | 选定 r5 生产行为的经审阅快照，规范化成可逐字节比较的字节流：CLI max turns、限流后哪怕响应里带了 Retry-After 也只做一次传输尝试就报错、已经提交过一个 delta 之后断掉的流、compaction 摘要如何投影进各 provider 的请求体、超大 observation、headless 下的权限拒绝，以及被禁用的 MCP connector。每个 case 都记录生产现在的实际行为、期望的契约，以及这份快照是不是在冻结一个已知缺陷。 |

`uv run python -m harness.cli characterize` 只比较，不写入。对不上是需要人去看的信号，不是自动覆盖 golden 的许可。
