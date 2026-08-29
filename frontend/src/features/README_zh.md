# frontend/src/features

[English](README.md)

按车道划分的功能模块。每个 F 系列工作项只在自己的子目录里加文件，不改其他车道的文件。
按车道划分的领域模块。F-08 加入纯函数内核；后续工作项在旁边加 `components/<area>/` 和 `islands/`，对 `stores/`（F-05）只 import、不改本体。

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`csv/`](csv/) | RFC-4180 风格 CSV/TSV 解析。收敛 parseDelimited / csvFields / parseTable。 |
| [`customize/`](customize/) | F-19 Customize 的 tab 状态机、定时器租约、API 客户端、vendor 辅助函数。 |
| [`md/`](md/) | renderMd / mdInline / esc 全链，以及统一的 mdHighlight 扫描器。 |
| [`scrub/`](scrub/) | publicText 凭证涂抹。 |
| [`stream/`](stream/) | appendLiveOutput 的 1MB 截断。 |
| [`theme/`](theme/) | 浅色/深色/跟随系统。运行时的唯一真值源是 `data-theme`。 |
| [`ws/`](ws/) | WebSocket 游标协议、handler 注册表、`connectWS`。 |
