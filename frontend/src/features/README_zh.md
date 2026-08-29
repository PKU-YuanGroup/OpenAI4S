# frontend/src/features

[English](README.md)

按车道划分的领域模块。F-08 加入纯函数内核；后续工作项在旁边加 `components/<area>/` 和 `islands/`，对 `stores/`（F-05）只 import、不改本体。

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`csv/`](csv/) | RFC-4180 风格 CSV/TSV 解析。收敛 parseDelimited / csvFields / parseTable。 |
| [`md/`](md/) | renderMd / mdInline / esc 全链，以及统一的 mdHighlight 扫描器。 |
| [`scrub/`](scrub/) | publicText 凭证涂抹。 |
| [`stream/`](stream/) | appendLiveOutput 的 1MB 截断。 |
