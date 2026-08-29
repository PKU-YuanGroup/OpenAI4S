# frontend/src/components

[English](README.md)

Preact 视图容器。每个 F 系列车道只改自己的 `components/<area>/`，不改其他车道的文件。
按车道划分的 Preact 视图。每个 F 系列工作项只在 `components/<area>/` 里加文件，不改其他车道的文件。

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`artifacts/`](artifacts/) | F-17 Files dock（M-03 搜索 / 过滤 / 分页 / 深链）。 |
| [`dashboard/`](dashboard/) | F-13 仪表盘 / 工作台外壳（冻结 id、`#composer-hint`、断连横幅）。 |
| [`timeline/`](timeline/) | F-15 `#dock-timeline` 宿主。ledger 本体是 `features/timeline/island.ts` 里的命令式孤岛。 |
