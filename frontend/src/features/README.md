# frontend/src/features

[中文说明](README_zh.md)

Per-lane domain modules. F-08 adds the pure-function kernels; later items add `components/<area>/` and `islands/` beside this tree, and only import `stores/` (F-05) rather than editing it.

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`csv/`](csv/) | RFC-4180-ish CSV/TSV parser. Converges parseDelimited / csvFields / parseTable. |
| [`md/`](md/) | renderMd / mdInline / esc chain and the unified mdHighlight scanner. |
| [`scrub/`](scrub/) | publicText credential redaction. |
| [`stream/`](stream/) | appendLiveOutput 1MB cap. |
