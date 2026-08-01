# Retrosynthesis Planning Skill

围绕目标 SMILES 做路线搜索，再把搜索结果交给化学家审阅。真正的搜索由 AiZynthFinder 在它自己的环境里完成；本目录的 sidecar 以标准库为主，负责规范化和排序导出的路线、合并重复路线、优先保留更有差异的候选、执行确定性的结构检查，并渲染供审阅的 Artifact。装了 RDKit 后可以画真实结构图并补充分子级校验；使用 Host LLM call 时再增加化学注释。

这套流程服务于规划和化学家初筛，不等于路线在实验上得到了验证。反应条件、产率、可得性、安全性，以及 LLM 写出的内容，在用文献、ELN、供应商数据和专家审阅核验之前都只是假设。结构审计可以发现损坏的路线树和可疑的原子覆盖，但它不是正向反应模型，也不能证明路线可行。

## 推荐审阅流程

面向用户的编排优先使用 [`workflow.py`](workflow.py)，底层的路线规范化、证据、渲染和报告能力继续由 [`kernel.py`](kernel.py) 提供。

```python
from retrosynthesis_planning.kernel import load_aizynth_routes
from retrosynthesis_planning.workflow import (
    AiZynthSearchSpec,
    audit_routes,
    build_aizynth_search_command,
    prepare_routes,
)

search = AiZynthSearchSpec(
    policies=("uspto", "ringbreaker"),
    filters=("quick_filter",),
    stocks=("internal", "zinc"),
    cluster=True,
    nproc=4,
    checkpoint_path="checkpoint.json.gz",
)

command = build_aizynth_search_command(
    "CC(=O)Oc1ccccc1C(=O)O",
    "config.yml",
    output_path="aspirin_routes.json",
    conda_env="retro",
    search=search,
)

payload = load_aizynth_routes("aspirin_routes.json")
routes = prepare_routes(
    payload,
    max_routes=10,
    similarity_threshold=0.85,
    constraints={"require_solved": True},
)
audit = audit_routes(routes)
```

`AiZynthSearchSpec` 结构化暴露 `aizynthcli` 已公开的 policy、filter、stock、聚类、多进程、checkpoint 以及前后处理参数。搜索算法、深度、reward 和 bond constraint 仍然写在 AiZynthFinder 的 `config.yml` 中；这个封装不会暗中改写配置文件。

`prepare_routes(...)` 先沿用现有排序逻辑，再合并完全相同的路线树并保留原始排名来源，最后基于反应、产物和前体特征选出更有差异的审阅集合。为了维持固定 dashboard 数量而重新补入相似路线时，路线会标记 `diversity_relaxed=True`，避免把它误解为独立证据。

`audit_routes(...)` 在 LLM 注释之前运行。它会报告缺失路线树、空或无效的分子 SMILES、没有前体子节点的反应、重复前体、缺少反应标识，以及在安装 RDKit 时检查简单的“产物相对前体元素缺口”。每份结果都带有明确免责声明，因为这些检查不能替代正向预测、文献先例或实验审阅。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 从头到尾的 pipeline：目标 SMILES、AiZynthFinder `config.yml` 和工作目录等输入，搜索调用和 JSON 导出加载，以及后续路线排序；随后是目标、中间体、可购前体和未解决末端前体的分子简介，`host.llm` 注释，自包含 HTML dashboard 和 Markdown 报告，以及保证模型生成条件、产率和判断仍为假设的证据边界。 |
| [`kernel.py`](kernel.py) | 底层 sidecar。安装 RDKit 时规范化 SMILES；构造安全的 AiZynth 命令；加载、规范化和排序路线；收集分子与反应证据；按需调用 Host LLM；并渲染 dashboard 和 Markdown 报告。 |
| [`workflow.py`](workflow.py) | 面向用户的编排层：经过校验的 `aizynthcli` 参数，以及“规范化 → 排序 → 去重 → 多样性选择”的审阅流程。 |
| [`route_review.py`](route_review.py) | 稳定路线签名、重复路线来源记录，以及基于反应、产物、前体和末端原料特征的多样性选择。 |
| [`structural_audit.py`](structural_audit.py) | LLM 解释前的确定性路线树检查。它保持标准库优先，仅在安装 RDKit 时增加解析和元素检查。 |

这一层的定向回归测试位于 [`../../tests/test_retrosynthesis_scoring_regressions.py`](../../tests/test_retrosynthesis_scoring_regressions.py)。

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`examples/`](examples/) | 确定性的 aspirin 路线与注释 fixture、由它们生成的 HTML 与报告，以及重建两者的脚本。 |
