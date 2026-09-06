# frontend/src/features/table

[English](README.md)

M-04 表格 artifact 的结构 / 分布 / 导出。对接 B-07 `GET /artifacts/{id}/table/profile` 与 `GET /artifacts/{id}/table/export.csv`（`version_id` 必填）。`approximate: true` 明示「近似」，不改写成精确。导出是同源 `<a href>`，由浏览器流式下载；本车道不 `fetch()` 响应体。工作台下载链接会附加 `spreadsheet_safe=1` 来中和公式形态的单元格；直接 API 调用省略它时仍保留科研原始值。渲染器 catalog 的 `profile` / `export` / `parquet` 如实透传，flag 关闭时本地再挡一层——不会从文件名推断 parquet。flag=0 走 F-17 的客户端 `parseTable` + `renderSheet`。

不改 `stores/`，不改生成的 i18n 字典，不改 `openai4s/server/webui/app.js`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`catalog.ts`](catalog.ts) | 按 catalog + workbench flag 如实声明 `table` 能力。 |
| [`catalog.test.ts`](catalog.test.ts) | flag=0 去掉 profile/export/parquet；不发明 parquet。 |
| [`copy.ts`](copy.ts) | M-04 中英文字符串（不改生成的 i18n）。 |
| [`histogram.ts`](histogram.ts) | 箱数上限 50、数值边界、approximate 透传。 |
| [`index.ts`](index.ts) | 对外 re-export + 车道 CSS。 |
| [`query.ts`](query.ts) | profile/export 查询串；`version_id` 必填。 |
| [`table.css`](table.css) | 结构 / 分布 / 导出 的样式。 |
| [`types.ts`](types.ts) | Profile DTO、catalog 姿态、查看器计划。 |
| [`workbench.ts`](workbench.ts) | flag 关闭的 sheet + workbench 分页 + 三区挂载。 |
| [`workbench.test.ts`](workbench.test.ts) | 三区、近似横幅、histogram 边界、flag=0。 |
| [`zones.ts`](zones.ts) | 结构 / 分布 / 导出 DOM。 |
