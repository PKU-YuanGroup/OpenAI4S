# frontend/src/features

[中文说明](README_zh.md)

Lane-owned feature modules. Each F-series item adds its own subdirectory and does not edit another lane's files.
Per-lane domain modules. F-08 adds the pure-function kernels; later items add `components/<area>/` and `islands/` beside this tree, and only import `stores/` (F-05) rather than editing it.

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`csv/`](csv/) | RFC-4180-ish CSV/TSV parser. Converges parseDelimited / csvFields / parseTable. |
| [`md/`](md/) | renderMd / mdInline / esc chain and the unified mdHighlight scanner. |
| [`messages/`](messages/) | F-10 message stream: framed history, dual-node markdown, StreamingPre, rAF scroll. |
| [`scrub/`](scrub/) | publicText credential redaction. |
| [`sessions/`](sessions/) | F-13 dashboard / projects / sessions, paging, share/import-export, hint + disconnect banner. |
| [`stream/`](stream/) | appendLiveOutput 1MB cap. |
| [`theme/`](theme/) | Light/dark/system preference. `data-theme` is the only runtime source of truth. |
| [`ws/`](ws/) | WebSocket cursor protocol, handler registry, `connectWS`. |
