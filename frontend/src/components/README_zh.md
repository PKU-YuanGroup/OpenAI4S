# frontend/src/components

[English](README.md)

F 系列车道拥有的视图组件。每个区域一个子目录（`customize/`，以及后续的 `timeline/`、`notebook/` 等）。车道只 import `stores/`，不改本体。
Preact 视图容器。每个 F 系列车道只改自己的 `components/<area>/`，不改其他车道的文件。
按车道划分的 Preact 视图。每个 F 系列工作项只在 `components/<area>/` 里加文件，不改其他车道的文件。

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`artifacts/`](artifacts/) | F-17 Files dock（M-03 搜索 / 过滤 / 分页 / 深链）。 |
| [`customize/`](customize/) | F-19 Customize 模态：九个 tab，以及火山 / DataPro / 豆包 vendor 卡。 |
| [`dashboard/`](dashboard/) | F-13 仪表盘 / 工作台外壳（冻结 id、`#composer-hint`、断连横幅）。 |
| [`timeline/`](timeline/) | F-15 `#dock-timeline` 宿主。ledger 本体是 `features/timeline/island.ts` 里的命令式孤岛。 |
| [`attention/`](attention/) | M-02 仪表盘「需要处理」卡片（`#dash-attention`）。 |
