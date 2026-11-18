# frontend/src/components/artifacts

[English](README.md)

F-17 Files dock 视图。冻结的 DOM id（`#dock-files`、`#results-list`、`#results-count`、`#files-scope`）与 E2E 契约一致。工作台壳车道落地后在那里挂载。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`FilesPanel.tsx`](FilesPanel.tsx) | 文件名搜索、按人能读懂的类型下拉（ART-12：表格、图片、报告、代码、其他）与来源过滤、本会话范围的排序（最新、最早、名称）、「显示已隐藏」、Load more。`mountFilesPanel` 挂进壳层 `#dock-files`。 |
| [`FilesPanel.test.tsx`](FilesPanel.test.tsx) | content-type 过滤框在提交前重渲染也保留已键入的文字。 |
| [`index.ts`](index.ts) | 再导出 `FilesPanel` / `mountFilesPanel`。 |
| [`DraftsPanel.tsx`](DraftsPanel.tsx) | 即使源文件已删除也可访问、复制和放弃的内存草稿。 |
