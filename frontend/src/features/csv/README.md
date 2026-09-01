# frontend/src/features/csv

[中文说明](README_zh.md)

CSV/TSV parser. `scientific_renderers.js` is not modified; the RFC-4180-ish loop from app.js `parseDelimited` is the fact source. `csvFields` and `parseTable` share that engine so a newline inside quotes cannot diverge.

## Files

| File | Responsibility |
| --- | --- |
| [`csv.ts`](csv.ts) | `parseDelimited`, `csvFields`/`csv`, `delimiterFor`, `parseTable`. |
| [`csv.test.ts`](csv.test.ts) | Quoted-newline sample: three paths, same grid. |
