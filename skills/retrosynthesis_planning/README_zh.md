# Retrosynthesis Planning Skill

围绕目标 SMILES 做路线搜索，再把搜索结果交给化学家审阅。真正的搜索由 AiZynthFinder 在它自己的环境里完成；本目录的 sidecar 以标准库为主，负责把导出的路线规范化、排序，并渲染供审阅的 Artifact。装了 RDKit 就能画出真实的结构图；用上 Host LLM call，就能补上化学注释。

这套流程服务于规划和化学家初筛，不等于路线在实验上得到了验证。反应条件、产率、可得性、安全性，以及 LLM 写出的一切，在用文献、ELN/供应商数据和专家审阅核验之前都只是假设。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 从头到尾的 pipeline：需要哪些输入（目标 SMILES、AiZynthFinder 的 `config.yml`、工作目录）、搜索怎么调起来、导出的 JSON 怎么加载，以及随后的排序——先看是否 solved，再看分数、步数和前体数量。接下来是目标分子、中间体、可购前体，以及那些没能解到底的末端前体各自的分子简介；`host.llm` 注释；自包含的 HTML dashboard 和 Markdown 分析报告；还有审阅者不能越过的那条线——模型写出来的反应条件、产率和路线判断，都只是假设。 |
| [`kernel.py`](kernel.py) | 可选 sidecar。装了 RDKit 就把 SMILES 规范化；构造安全的 AiZynth 命令；加载路线导出、规范化并排序；收集每个分子的角色、查询链接和结构图来源。它还负责构造 Host LLM 的注释 prompt 并把回复解析回来，最后渲染自包含的路线表、AND-OR 树、知识图谱和 dashboard，并写出 Markdown 分析报告。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`examples/`](examples/) | 确定性的 aspirin 路线与注释 fixture、由它们生成的 HTML 与报告，以及重建这两者的脚本。 |
