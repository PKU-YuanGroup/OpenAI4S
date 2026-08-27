# 单细胞 RNA 分析参考契约

详细契约按决策点拆分，使 Agent 在每个阶段只加载当前需要的材料。

## 文件

| 文件 | 使用场景 |
| --- | --- |
| [`input-contract.md`](input-contract.md) | 版本化配置、样本表与 h5ad 要求、counts 验证、标识符、reference 一致性、contrast 与混杂检查。 |
| [`scientific-workflow.md`](scientific-workflow.md) | 分样本 QC、Scrublet、表达表示保留、可选 Harmony、resolution sweep、聚类和描述性 marker。 |
| [`annotation-contract.md`](annotation-contract.md) | marker panel 证据、冲突、参考兼容性、`Unknown` 和用户确认映射。 |
| [`statistics-contract.md`](statistics-contract.md) | donor-aware pseudobulk DE、配对、显式设计公式、重复数门控和 Milo DA。 |
| [`output-contract.md`](output-contract.md) | 检查点、resume 失效规则、结构化状态、报告、manifest、校验和与 Artifact 登记。 |
| [`README.md`](README.md) | 与本文件结构一致的英文目录说明。 |

这些文档定义科学和执行契约，不代表生物学真实标签，也不能替代针对具体研究设计的审阅。
