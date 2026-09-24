# frontend/src/components

[English](README.md)

F 系列车道拥有的视图组件。每个区域一个子目录（`customize/`、`dashboard/` 等）。车道只 import `stores/`，不改本体。
Preact 视图容器。每个 F 系列车道只改自己的 `components/<area>/`，不改其他车道的文件。
按车道划分的 Preact 视图。每个 F 系列工作项只在 `components/<area>/` 里加文件，不改其他车道的文件。

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`artifacts/`](artifacts/) | F-17 Files dock（M-03 搜索 / 过滤 / 分页 / 深链）。 |
| [`customize/`](customize/) | F-19 Customize 模态：九个 tab，以及火山 / DataPro / 豆包 vendor 卡。 |
| [`dashboard/`](dashboard/) | F-13 仪表盘 / 工作台外壳（冻结 id、`#composer-hint`、断连横幅）。 |
| [`attention/`](attention/) | M-02 仪表盘「需要处理」卡片（`#dash-attention`）。 |
| [`onboarding/`](onboarding/) | M-01 首次运行向导遮罩与三态能力 badge。 |
