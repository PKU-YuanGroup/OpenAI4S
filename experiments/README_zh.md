# 实验

[English](README.md)

研究实验分别保存来源数据、可执行代码、结果与明确的复现范围。这些是本地研究产物，不进入生产导入路径或内置 Skill 包。

## 内容

- `did-arxiv-replication/`：使用 panel-data-preprocessing Skill，将两篇 arXiv DiD 论文的方法与公开官方示例核对。详见[实验说明](did-arxiv-replication/README_zh.md)。
- `panel-cleaning-synthetic/`：使用固定种子的随机面板，检验仅拟合基线的清理、编码、窗口准备与可视化事件发现，并与独立保留的生成真值核对。详见[实验说明](panel-cleaning-synthetic/README_zh.md)。
- `did-skill-validation/`: 预设的合成 DiD、安慰剂、事件研究与衍生分析，独立核对聚类推断，并重复固定种子的模拟。 [实验说明](did-skill-validation/README_zh.md).
