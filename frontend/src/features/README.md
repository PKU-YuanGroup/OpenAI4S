# frontend/src/features

[中文说明](README_zh.md)

Lane-owned feature modules. Each F-series item adds its own subdirectory and does not edit another lane's files.
Per-lane domain modules. F-08 adds the pure-function kernels; later items add `components/<area>/` and `islands/` beside this tree, and only import `stores/` (F-05) rather than editing it.

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`csv/`](csv/) | RFC-4180-ish CSV/TSV parser. Converges parseDelimited / csvFields / parseTable. |
| [`customize/`](customize/) | F-19 Customize tab state machine, timer lease, API client, vendor helpers. |
| [`md/`](md/) | renderMd / mdInline / esc chain and the unified mdHighlight scanner. |
| [`messages/`](messages/) | F-10 message stream: framed history, dual-node markdown, StreamingPre, rAF scroll. |
| [`scrub/`](scrub/) | publicText credential redaction. |
| [`sessions/`](sessions/) | F-13 dashboard / projects / sessions, paging, share/import-export, hint + disconnect banner. |
| [`stream/`](stream/) | appendLiveOutput 1MB cap. |
| [`theme/`](theme/) | Light/dark/system preference. `data-theme` is the only runtime source of truth. |
| [`chrome/`](chrome/) | F-20: team mode, modal focus trap, ⌘K palette, upload/notes/mic, layout, column resizers. |
| [`ws/`](ws/) | WebSocket cursor protocol, handler registry, `connectWS`. |
| [`artifacts/`](artifacts/) | F-17 Files + version cache + scientific renderer glue (M-03). |
| [`notebook/`](notebook/) | Notebook dock: cell merge/live protocol, CellList keyed by producing_cell_id, kernel chips/REPL. |
| [`timeline/`](timeline/) | F-15 Action Timeline: sanitize* / merge, virtualized ledger island, workbench WS. |
| [`autocomplete/`](autocomplete/) | F-12 composer (`@/#/`) and editor autocomplete. Keywords from F-08 `editorKeywords`. |
| [`send/`](send/) | F-11 send chain, turn tickets, step/plan/permission/candidate cards, admission tracker. |
