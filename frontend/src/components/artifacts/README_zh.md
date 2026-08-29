# frontend/src/components/artifacts

[English](README.md)

F-17 Files dock 视图。冻结的 DOM id（`#dock-files`、`#results-list`、`#results-count`、`#files-scope`）与 E2E 契约一致。工作台壳车道落地后在那里挂载。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`FilesPanel.tsx`](FilesPanel.tsx) | 文件名搜索、content-type / 来源过滤、Load more。 |
| [`index.ts`](index.ts) | 再导出 `FilesPanel`。 |
