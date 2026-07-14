# Indication Dossier 参考资料

Skill 按需加载的文档。它们把一条很长的证据工作流切成可审计的若干阶段，并定死了在阶段之间承载状态的 waypoint 文件。它们规定怎么检索、怎么写；但它们本身都不是任何疾病结论的证据来源。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`00-research-standards.md`](00-research-standards.md) | 所有阶段共同继承的规则。每条发现都要带 `source_url`、`source_type` 和一段原文 `quote`；URL 只能用真正取到过的，不许自己拼、不许猜。任何内容都不得捏造或推断，在权威一手来源里查不到的数字要记成缺口，而不是填一个占位值。此外还规定什么时候优先用 MCP 工具、怎么拿到 PDF 及其中的图表，以及怎么区分洞见和背景。 |
| [`01-meta-initialization.md`](01-meta-initialization.md) | 第 1 阶段。先弄清这个适应症到底是什么：临床定义、ICD-10 编码、别名、上级适应症，以及它究竟算不算一个公认的诊断实体。再用 CT.gov 快速扫一遍试验版图，对这个适应症的临床成熟度有个初步判断。写出 `meta.json`，并初始化 `sources_evaluated.json`。 |
| [`02-epidemiology-research.md`](02-epidemiology-research.md) | 第 2 阶段。刻画这个人群：诊断标准、患病率与发病率、人口学特征与风险因素、自然病程。优先采用系统综述和 meta 分析，并且必须点明每个估计背后的证据质量。写出 `epidemiology.json`。 |
| [`03-biology-soc-research.md`](03-biology-soc-research.md) | 第 3 阶段。病理生理、生物标志物、已获批疗法、治疗指南，以及现有疗法留下的未满足需求。写出 `biology_soc.json`。 |
| [`04-regulatory-trials-research.md`](04-regulatory-trials-research.md) | 第 4 阶段。监管机构认可什么终点、哪些获批构成先例、这个适应症的 Phase 3 通常长什么样（规模、周期、对照臂）、哪些试验改变了临床实践、哪些失败在机制层面留下了教训。写出 `regulatory_trials.json`，其中含试验版图计数。 |
| [`05-synthesis.md`](05-synthesis.md) | 第 5 阶段。逐节给出报告骨架，并规定怎么把四个 waypoint 文件收成一篇带引用的叙述：不开新的研究线；只允许一次定向补取，用来填某个已有字段里缺的具体数值；如果一张图并没有讲出正文之外的信息，就删掉它，而不是重画。产出报告、`research_output.json` 和最终的 `progress.json`。 |
| [`06-writing-style.md`](06-writing-style.md) | 正文怎么给引用。每条事实性陈述都要挂一个行内 markdown 链接，链接文字就是该陈述，链接目标是来源 URL，且必须指向具体页面，绝不指向首页。来源互相矛盾时两边都引，并写明采用哪一个、为什么。定稿前，每个数字、日期、研发阶段和疗效数据都要挂上具体链接；不许出现不点名的“studies show”，也不许写完再回头补引用。 |
| [`waypoint-schemas.md`](waypoint-schemas.md) | 每个 waypoint 文件的 JSON 结构：循环进度、适应症身份、流行病学、生物学与标准治疗、监管与试验、已评估来源，以及综合阶段的两个输出。 |
