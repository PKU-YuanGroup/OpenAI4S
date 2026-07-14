# Retrosynthesis Planning Skill

这个渐进披露 Skill 描述围绕外部 AiZynthFinder 环境的 target-SMILES route-search/review 工作流。以纯标准库为主的 sidecar 规范化/排序导出 route 并渲染审阅 Artifact；可选 RDKit 增加结构图，可选 Host LLM call 增加 annotation。

工作流支持规划与 chemist triage，不是实验 route validation。Condition、yield、availability、safety 与 LLM annotation 都是 hypothesis，直到用 literature、ELN/vendor data 与专家审阅核验。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Target/config/workdir 输入、AiZynthFinder 调用、route normalization/ranking、molecule lookup、LLM annotation、dashboard/report deliverable 与审阅限制的主 recipe。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：在 RDKit 存在时 canonicalize SMILES；构建安全 AiZynth command；加载/规范化/排序 route tree；收集 molecule role/query/structure source；构建/解析 Host LLM annotation prompt；渲染 self-contained route table/AND-OR tree/knowledge graph/dashboard；写 Markdown analyst report。 |

## 直属子目录

| 目录 | 职责 |
| --- | --- |
| [`examples/`](examples/) | 确定性的 aspirin-shaped route/annotation fixture、生成 HTML/report 与重建脚本。 |
