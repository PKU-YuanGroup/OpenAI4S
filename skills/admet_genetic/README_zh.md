# ADMET Genetic Optimization Skill

以 ADMET 为导向、从种子 SMILES 出发做分子优化，并让每个候选分子都能沿血缘追回它来自哪颗种子。它的 Python sidecar 只提供可复用的那部分：SMILES 归一化、打分契约、血缘校验和可视化。它有意不实现一套固定的遗传算法，也不对候选分子做任何实验验证。

RDKit、pandas、matplotlib、ADMET-AI、PyTorch 和模型资产都是可选依赖，必须先装进所选的运行环境。剩下的事情归 Agent：读数据契约，搭出变异、交叉和选择逻辑，把血缘记录完整保留下来，并且把每一个预测都当成初筛证据，而不是事实。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 主 recipe：前置条件、种子分子归一化、动手前必须先读的契约、如何组装 GA、ADMET/SA/QED/性质打分、过滤、多样性、血缘、输出、报告，以及报告里要写清的局限。 |
| [`kernel.py`](kernel.py) | 可选的 sidecar。它标准化并规范化 SMILES，对 ADMET-AI 的 endpoint 分类、聚合成一个分数加一组风险标记，生成规范的 `operation_detail` JSON，并按血缘契约校验 generation log。`render_optimization_history` 把这份日志渲染成自包含的 dashboard；装了 RDKit 和 matplotlib 时，还会带上分子 SVG 和统计图。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`examples/`](examples/) | 提交在仓库里、可复现的一份演示：输入、录下来的各代结果、由此派生的候选选择、报告和 dashboard。它是 fixture，不是实时优化结果。 |
| [`references/`](references/) | ADMET 运行环境说明、数据契约与血缘规则、GA 设计说明，通过渐进披露按需读取。 |
