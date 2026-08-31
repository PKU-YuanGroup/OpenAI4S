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
| [`messages/`](messages/) | F-10 消息流：分帧历史、Markdown 双节点、StreamingPre、rAF 滚动。 |
| [`scrub/`](scrub/) | publicText 凭证涂抹。 |
| [`sessions/`](sessions/) | F-13 仪表盘 / 项目 / 会话、分页、分享/导入导出、hint + 断连横幅。 |
| [`stream/`](stream/) | appendLiveOutput 的 1MB 截断。 |
| [`theme/`](theme/) | 浅色/深色/跟随系统。运行时的唯一真值源是 `data-theme`。 |
| [`chrome/`](chrome/) | F-20：团队面、模态焦点陷阱、⌘K palette、上传/笔记/麦克风、布局、列宽拖拽。 |
| [`ws/`](ws/) | WebSocket 游标协议、handler 注册表、`connectWS`。 |
| [`artifacts/`](artifacts/) | F-17 Files + 版本缓存 + 科学渲染器胶水（M-03）。 |
| [`notebook/`](notebook/) | Notebook 面板：cell 合并/live 协议、按 producing_cell_id 键控的 CellList、kernel chips/REPL。 |
| [`timeline/`](timeline/) | F-15 Action Timeline：sanitize* / merge、虚拟化 ledger 孤岛、工作台 WS。 |
| [`autocomplete/`](autocomplete/) | F-12 作曲框（`@/#/`）与编辑器自动补全。关键词表来自 F-08 `editorKeywords`。 |
| [`send/`](send/) | F-11 发送全链、turn ticket、步骤/计划/权限/候选卡片、admission 追踪器。 |
| [`attention/`](attention/) | M-02 仪表盘「需要处理」卡片：B-05 `GET /attention`、闭集本地导航、可见页 4 秒轮询。 |
