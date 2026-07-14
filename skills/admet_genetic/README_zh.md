# ADMET Genetic Optimization Skill

这个渐进披露 Skill 描述从 seed SMILES 出发的 ADMET-guided 遗传分子优化流程。Python sidecar 提供可复用 normalization、scoring contract、lineage 与 visualization helper，但刻意**不**实现固定 genetic algorithm，也不对候选化学做实验验证。

可选 RDKit、pandas、matplotlib、ADMET-AI、PyTorch 与模型资产必须安装在所选环境。Agent 负责读取 data contract、构建 mutation/crossover/selection 逻辑、保留 lineage，并把预测视为 triage 证据。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 关于 prerequisite、seed normalization、必读 contract、GA 组装、ADMET/SA/QED/property scoring、filter、diversity、lineage、输出、报告与限制的主 recipe。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：标准化/canonicalize SMILES；分类并聚合 ADMET-AI endpoint；生成规范 operation-detail JSON；校验 generation-log lineage；用可选 RDKit molecule SVG 与 matplotlib plot 渲染 self-contained optimization-history dashboard。 |

## 直属子目录

| 目录 | 职责 |
| --- | --- |
| [`examples/`](examples/) | 可复现的 committed 演示输入、录制 generation、派生 selection、报告与 dashboard；不是实时优化结果。 |
| [`references/`](references/) | 通过渐进披露按需读取的 ADMET runtime、data-contract/lineage 与 GA 设计说明。 |
