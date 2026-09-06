# frontend/src/features/csv

[English](README.md)

CSV/TSV 解析。`scientific_renderers.js` 一个字都不改；事实源是 app.js `parseDelimited` 的 RFC-4180 循环。`csvFields` 与 `parseTable` 共用该引擎，引号内换行不会再分叉。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`csv.ts`](csv.ts) | `parseDelimited`、`csvFields`/`csv`、`delimiterFor`、`parseTable`。 |
| [`csv.test.ts`](csv.test.ts) | 引号内换行样本：三路径同一张表。 |
