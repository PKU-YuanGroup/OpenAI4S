# 单细胞 RNA 分析 Skill

这是 OpenAI4S 自维护的人/鼠、已完成 cell calling 的 10x GEX scRNA-seq 与
snRNA-seq 工作流。它提供版本化配置契约、可执行的 Scanpy 流程、保守的科学门控、
可恢复的检查点和可审计的结果包；不会修改或复制仓库中固定版本的 `bioSkills` 集合。

## 文件

| 路径 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 面向 Agent 的简短入口：适用范围、公开调用、阶段路由、失败处理、Artifact 交付和解释边界。 |
| [`kernel.py`](kernel.py) | 延迟导入科学依赖，实现 `preflight(config)`、`run(config, output_dir)` 与 `resume(run_dir)`。 |
| [`references/`](references/) | 输入、科学流程、注释、统计和输出的详细契约，并配有独立的双语目录说明。 |

该工作流以保留证据为原则：raw counts 独立保存在 `layers["counts"]`，Harmony 只改变
embedding，cluster marker 不替代条件差异表达，未经确认的标签可以保留为 `Unknown`。
