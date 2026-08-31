# frontend/src/features/table

[中文说明](README_zh.md)

M-04 table artifact Schema / Distribution / Export. Talks to B-07 `GET /artifacts/{id}/table/profile` and `GET /artifacts/{id}/table/export.csv` (`version_id` required). `approximate: true` is shown as 近似 / Approximate and is never rewritten as exact. Export is a same-origin `<a href>` so the browser streams the CSV; this lane does not `fetch()` the body. Renderer catalog capabilities (`profile` / `export` / `parquet`) are pass-through plus a flag-off kill switch — parquet is never inferred from a filename. Flag-off uses the F-17 client `parseTable` + `renderSheet` path.

Does not edit `stores/`, does not rewrite generated i18n, does not edit `openai4s/server/webui/app.js`.

## Files

| File | Responsibility |
| --- | --- |
| [`catalog.ts`](catalog.ts) | Honest `table` capabilities from the catalog + workbench flag. |
| [`catalog.test.ts`](catalog.test.ts) | Flag=0 strips profile/export/parquet; parquet is not invented. |
| [`copy.ts`](copy.ts) | M-04 zh/en strings (does not rewrite generated i18n). |
| [`histogram.ts`](histogram.ts) | Bin cap 50, numeric bounds, approximate pass-through. |
| [`index.ts`](index.ts) | Public re-exports + lane CSS. |
| [`query.ts`](query.ts) | Profile/export query builders; `version_id` required. |
| [`table.css`](table.css) | Schema / Distribution / Export chrome. |
| [`types.ts`](types.ts) | Profile DTO, catalog posture, viewer plan. |
| [`workbench.ts`](workbench.ts) | Flag-off sheet + workbench page + zone mount. |
| [`workbench.test.ts`](workbench.test.ts) | Three zones, approximate banner, histogram bounds, flag=0. |
| [`zones.ts`](zones.ts) | Schema / Distribution / Export DOM. |
