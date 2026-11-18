# frontend/src/components/artifacts

[English](README.md)

F-17 Files dock 视图。冻结的 DOM id（`#dock-files`、`#results-list`、`#results-count`、`#files-scope`）与 E2E 契约一致。工作台壳车道落地后在那里挂载。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`FilesPanel.tsx`](FilesPanel.tsx) | 文件名搜索、按人能读懂的类型下拉（ART-12：表格、图片、报告、代码、其他）与来源过滤、本会话范围的排序（最新、最早、名称）、「显示已隐藏」、Load more，以及只读的项目文件夹分组（UP-5）。`mountFilesPanel` 挂进壳层 `#dock-files`。 |
| [`FilesPanel.test.tsx`](FilesPanel.test.tsx) | 类型过滤是按人能读懂的类别下拉，改变即生效（ART-12）。 |
| [`ProjectFolderSection.tsx`](ProjectFolderSection.tsx) | UP-5：Files 面板顶部的只读项目文件夹：路径、前 12 个顶层条目（点击在项目文件弹窗里打开该文件夹或文件）以及「浏览」；这里不写入文件夹。 |
| [`index.ts`](index.ts) | 再导出 `FilesPanel` / `mountFilesPanel`。 |
| [`DraftsPanel.tsx`](DraftsPanel.tsx) | 即使源文件已删除也可访问、复制和放弃的内存草稿。 |
