# 内置 Skills

[English](README.md)

此目录树包含 32 个 OpenAI4S 内置 Skill。Skill 是渐进披露的代码 recipe 与运维指南，
不是 provider JSON Tool。Loader 先公开名称与摘要，仅在选中后加载 `SKILL.md` 和
可选的 `kernel.py` sidecar。

## 子目录

| 目录 | 职责 |
|---|---|
| [`admet_genetic/`](admet_genetic/) | 从 seed SMILES 构建 ADMET 引导的遗传分子优化 workflow，并保留明确 lineage、过滤、评分、dashboard 与候选分诊。 |
| [`alphafold2/`](alphafold2/) | 通过 ColabFold AlphaFold2 runner 运行单体或多聚体结构预测，并说明 MSA、GPU、置信度与自洽验证约束。 |
| [`audit-dataset/`](audit-dataset/) | 使用确定性的标准库 helper 审计表格数据的 schema drift、缺失、重复、类别不平衡与 entity leakage。 |
| [`boltz/`](boltz/) | 指导使用 Boltz-2 预测蛋白、核酸与 ligand complex，并覆盖可选 affinity 输出和运维故障模式。 |
| [`borzoi/`](borzoi/) | 使用 Borzoi 从 DNA 预测功能基因组 track，并通过 track delta 分析调控或非编码变异。 |
| [`catalyst_sar_screening/`](catalyst_sar_screening/) | 提供 hard-locked FAIRChem UMA 单原子催化剂 SAR screening workflow，并要求用户结果必须来自新的 pipeline run。 |
| [`chai1/`](chai1/) | 使用 Chai-1 对蛋白、核酸和小分子 complex 进行 co-folding，并明确下载、MSA 与 GPU 指南。 |
| [`diffdock/`](diffdock/) | 对小分子与蛋白结构执行 blind diffusion docking，并按模型置信度排序 pose geometry；不声称预测 affinity。 |
| [`esmfold2/`](esmfold2/) | 覆盖 Biohub ESMFold2 全原子 folding，以及 ESMC language model embedding、突变评分、contact 与 backend 性能约束。 |
| [`evaluate-model/`](evaluate-model/) | 使用 held-out metric、tie-aware AUC、确定性 bootstrap uncertainty、baseline 与 subgroup check 评估二分类和回归模型。 |
| [`evo2/`](evo2/) | 使用 Evo 2 对长上下文 DNA 进行评分、embedding 与生成，服务于变异、调控和编码序列 workflow。 |
| [`example_stats/`](example_stats/) | 演示一个小型 user-style Skill，其 sidecar 在不依赖 NumPy/pandas 时提供汇总统计、分位数、z-score 与相关性。 |
| [`fair-esm2/`](fair-esm2/) | 使用 Meta ESM-2 生成蛋白 embedding、masked-language-model 突变效应与 contact prediction。 |
| [`figure-composer/`](figure-composer/) | 把科学主张转换为多 panel figure 计划，委派 panel 工作、组合输出并执行有界的 adversarial review。 |
| [`figure-style/`](figure-style/) | 定义图形正确性、可读性、布局、palette、标注和 render-then-verify 规则，并提供可复用绘图 helper。 |
| [`indication-dossier/`](indication-dossier/) | 跨患者人群、流行病学、生物学、standard of care、监管、临床试验与综合阶段构建可恢复的治疗适应症 dossier。 |
| [`ligandmpnn/`](ligandmpnn/) | 在保留 ligand、核酸或金属上下文的情况下 inverse-fold 结构，用于 binding pocket 与配位位点设计。 |
| [`literature-review/`](literature-review/) | 检索、核验、扩展并综合科学文献，同时检查 DOI 身份、撤稿、证据强度与引用 grounding。 |
| [`mineral_spectra_analysis/`](mineral_spectra_analysis/) | 预处理混合矿物 Raman spectrum，迭代匹配 residual peak、执行 NNLS unmixing，并在不读取 hidden truth 时报告可靠性。 |
| [`openfold3/`](openfold3/) | 指导 OpenFold3 全原子结构预测的输入 JSON、weights、MSA 行为、输出与验证。 |
| [`paper-narrative/`](paper-narrative/) | 评审 manuscript 与 figure deck 讲述的故事，识别叙事 arc 和缺失证据，并把逐图 claim 交给 composer。 |
| [`pdf-explore/`](pdf-explore/) | 一次解析 PDF 为持久 page text，并提供 outline、搜索、提取、figure crop 与并行逐页分析 helper。 |
| [`plan-ml-experiment/`](plan-ml-experiment/) | 通过确定性 split、fingerprint、checksum、seed、baseline、ablation 与 manifest 规划防 leakage、可复现的 ML 实验。 |
| [`protein-mutation-enhancement/`](protein-mutation-enhancement/) | 构建确定性 mutant library，合并序列、结构与性质评分，排序候选并控制迭代 gain-of-function 轮次。 |
| [`proteinmpnn/`](proteinmpnn/) | 把蛋白 backbone inverse-fold 为序列，并覆盖 chain constraint、fixed position、checkpoint 选择与 temperature sweep。 |
| [`remote-compute-nvidia/`](remote-compute-nvidia/) | 定义 hosted/self-hosted NVIDIA NIM BYOC workflow，包括 provider policy、job submission、harvest 与 secret boundary。 |
| [`remote-compute-ssh/`](remote-compute-ssh/) | 定义面向 SSH/SLURM compute、考虑 approval 的 submit、notification、harvest、recovery 与 host-learning 模式。 |
| [`retrosynthesis_planning/`](retrosynthesis_planning/) | 规范化并排序 AiZynthFinder 风格 route、查询 molecule、渲染 route dashboard，并编写证据校准的合成报告。 |
| [`scgpt/`](scgpt/) | 使用 scGPT 生成单细胞 embedding、cell-type annotation 与用于 perturbation/调控分析的 gene representation。 |
| [`scvi-tools/`](scvi-tools/) | 使用 scVI/scANVI 执行概率单细胞整合、latent space、label transfer 与 Bayesian differential expression。 |
| [`solublempnn/`](solublempnn/) | 使用偏向可溶性的 ProteinMPNN model inverse-fold backbone，并说明仅凭序列作可溶性判断的限制。 |
| [`using-model-endpoint/`](using-model-endpoint/) | 记录计划中的 endpoint-scoped inference 工作流。当前 Host 已实现 endpoint 注册与探测，但尚未将该 provider 接入 `ComputeManager`，也不会创建 scoped kernel。 |

## 与框架的关系

- `openai4s/skills_loader/` 发现这些目录，并让 bundled name 优先于可写 user Skill。
- 内置 Skill 是只读应用资源。用户编写版本位于配置的数据目录，不能替换同名 bundled Skill。
- `kernel.py` sidecar 只能包含定义，需通过 compile check，并注入科学 Python kernel；
  它不得给 core 增加强制依赖。
- Provider shim 是受信任 extension code，会跨越单独记录的 compute 或 endpoint boundary
  运行；只有 manifest 并不表示 capability 已经可运维。
