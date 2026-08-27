# 内置 Skills

[English](README.md)

本目录树共暴露 604 个内置 Skill：43 份由 OpenAI4S 筛选维护的配方，加上固定版本的
GPTomics/bioSkills 全部 561 份配方。Skill 是一份 recipe——代码，加上把它跑起来所需的
运维知识——而不是 provider 的 JSON Tool。披露是渐进的：精选 Skill 各占一行摘要，大型
第三方集合合计只占一行，再通过搜索或精确名称展开。只有被选中的 `SKILL.md` 和可选
`kernel.py` sidecar 才会加载。

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`admet_genetic/`](admet_genetic/) | 从 seed SMILES 出发的遗传式优化循环，用 RDKit 描述符、QED、SA-Score 和 ADMET-AI 打分。sidecar 故意不提供固定的 GA 引擎：突变、交叉、过滤和打分权重都要你按当前目标自己设计。每一条记录在案的候选分子都必须带着生成它的血缘。 |
| [`alphafold2/`](alphafold2/) | 通过 ColabFold 的 `colabfold_batch` 跑 AF2 与 AF2-Multimer：一个 FASTA 加一条命令就能预测，不用在本地挂载 MSA 数据库。MSA 来自公共 MMseqs2 服务器，也就是说序列会被发到那里。只处理蛋白；要做配体或核酸，请转向 `boltz`、`chai1` 或 `openfold3`。 |
| [`audit-dataset/`](audit-dataset/) | 训练或对外发布之前该做的那次检查：schema 漂移、缺失、重复行与重复 ID、目标类别不平衡，以及同一实体横跨 train/validation/test。纯标准库实现。结构层面查干净了，仍然说明不了数据是否有代表性、标签是否正确。 |
| [`bioprobench/`](bioprobench/) | 用 BioProBench 基准给模型的方案推理能力打分：问答、步骤排序、错误纠正、方案生成，以及由 LLM 裁判评判的错误推理。真正的坑在输入约定——它评的是一份已经把标准答案合并进每条模型回复的文件；喂纯模型输出会在 `"status": "failed"` 之下返回全零。记得看 `Failed_Rate`。 |
| [`bioskills/`](bioskills/) | 固定版本、只读地引入 MIT 许可的 GPTomics/bioSkills 全部 561 份配方，覆盖 63 个生物信息学类别。中英边界文档、上游许可证和 SHA-256 manifest 保存来源；每份配方仍可搜索/加载，但不会把 561 条描述塞进每轮 system prompt。 |
| [`boltz/`](boltz/) | 对蛋白、DNA、RNA 与配体链做开放权重的 co-folding，还有一个可选的小分子亲和力头。在四个 co-folding Skill 里，它是 binder 验证类任务的默认选择：权重完全开放（MIT），采样最快。 |
| [`borzoi/`](borzoi/) | 输入 DNA，输出预测的实验信号覆盖：约 524 kb 窗口上的 RNA-seq、CAGE、DNase 和 ChIP track。给非编码变异打分的做法是跑 ref 与 alt 两个窗口，比较逐 track 的差值。如果你要的是序列似然而不是实验 track，请改用 `evo2`。 |
| [`catalyst_sar_screening/`](catalyst_sar_screening/) | 针对石墨烯 M–N–C 位点的单原子催化剂 SAR 筛选，能量引擎硬锁定在 FAIRChem UMA。禁止启发式、查表和其他 MLIP，也禁止把仓库里已提交的 demo 输出当作用户结果：每个答案都必须来自一次全新的 pipeline 运行。权重 hub 连不上时，它会停下来问，而不是换一种方法糊过去。 |
| [`chai1/`](chai1/) | 和 `boltz` 覆盖同样的 co-folding 场景，但换了一个模型——这正是它的价值：两个都跑，保留任一模型通过的设计。它的 Python 入口让它比 `boltz` 更容易嵌进循环里，而且 Apache-2.0 明确允许商用。 |
| [`diffdock/`](diffdock/) | 盲式对接。不需要预先划定搜索盒：扩散模型可以把配体放到表面任何位置，再由 confidence 头给采样排序。这个 confidence 反映的是构象是否正确，不是结合自由能，而且不同复合物之间的数值不可比。所以在做苗头化合物分诊之前，还要配一个打分工具。 |
| [`esmfold2/`](esmfold2/) | Biohub 的 ESM 发布：既有可以只凭单条序列跑的全原子 co-folding，也有 ESMC 语言模型给出的 embedding、突变打分和 contact 预测。当你没有 MSA 也能接受时，它优于其他几个 co-folding Skill。 |
| [`evaluate-model/`](evaluate-model/) | 在留出数据上评估二分类与回归：ROC AUC 会处理并列取值，不确定度来自确定性 bootstrap。它有一半是纪律而不是算术：指标要在看到测试集之前定下来，结果要对照 baseline，还要逐个子群检查。bootstrap 区间刻画的是抽样波动，它不会修正泄漏，也不会修正数据分布偏移。 |
| [`evidence-walkthrough/`](evidence-walkthrough/) | 端到端的参考流程，也是最该先跑的一个：固定查询、本地分析、每个派生产物都声明自己是从哪些版本推导出来的，最后导出一份会话包——接收方只要 `openai4s verify-package` 就能校验，不需要 daemon。accession 是写死的，因此两次运行可比，这也正是它能当基准用例的原因。校验通过说明这个包**完整**，不等于**可信**：这个格式不带签名。 |
| [`evo2/`](evo2/) | 长上下文的 DNA 语言模型。可以给出逐核苷酸的似然用于变异效应打分、基因组窗口的 embedding，以及从前缀出发的序列生成。它给的是序列概率，而 `borzoi` 给的是实验 track 预测。 |
| [`example_stats/`](example_stats/) | 用户自建 Skill 的范例（`origin: personal`），而且本身就能用：在普通 Python 列表上算均值、标准差、中位数、分位数、z-score 和 Pearson 相关，不依赖 NumPy 和 pandas。要自己写 Skill 之前，先读这一个。 |
| [`fair-esm2/`](fair-esm2/) | 通过 `fair-esm` 包使用 Meta 的 ESM-2：逐残基与整条序列的 embedding、掩码语言模型的突变打分、contact 预测。注意命名空间撞车：`fair-esm` 和 `esmfold2` 背后的 Biohub fork 都以 `esm` 导入，但是两个不同的库。 |
| [`figure-composer/`](figure-composer/) | 三个配图 Skill 中的中间层：把一张多 panel 图做好。它把一句话的主张变成 12 列栅格上的 panel 方案，每个 panel 派出一个 sub-agent，拼版并打上字母编号，然后做至多三轮的对抗式整图评审。 |
| [`figure-style/`](figure-style/) | 最内层：单张图的规则。它刻意是一份检查清单而不是一套视觉风格，涵盖数据忠实性、标注取舍、按数据形状选图型，以及先渲染再核对的验证步骤。正确性相关的章节在任何情况下都必须遵守；美学相关的章节只是默认值，有明确理由时可以推翻。每个 panel sub-agent 都会加载它。 |
| [`indication-dossier/`](indication-dossier/) | 五个可续做的阶段，围绕单个适应症构建 dossier，并且把它当作一个患者人群而不是一种疾病来写：这些人是谁、流行病学、疾病生物学、标准治疗、监管先例、里程碑临床试验。它期望有 clinical-trials 和 pubmed 这两个 MCP server；没有接上时，就退回到对公开数据源的网页检索。 |
| [`ligandmpnn/`](ligandmpnn/) | 当设计面不只有蛋白时用它做反向折叠：小分子、核酸和金属对网络是可见的原子，而 `proteinmpnn` 会直接忽略它们。它的 runner 也是唯一会把设计序列穿回结构并写出 PDB 的那个。 |
| [`literature-review/`](literature-review/) | 从「X 的奠基论文是哪篇」一直到完整的多源综述。它的内容其实就是纪律：先检索再动笔，每一个 DOI 都要解析核实而不是凭记忆写出，用 CrossRef 查撤稿，写出的段落要以你自己的综合判断开头，而不是以某位作者的名字开头。 |
| [`mineral_spectra_analysis/`](mineral_spectra_analysis/) | 对未知混合矿物的 Raman 光谱做解混。预处理只做一次，然后进入循环：检测残余峰、匹配参考谱库、对所有已选组分做 NNLS 重拟合、扣除。盲分析循环内不得读取 `truth.json`；对照真值的评估是单独一步，只在答案定稿之后才跑。 |
| [`openfold3/`](openfold3/) | AlphaFold3 的 Apache-2.0 复现，所以当你要的就是与 AF3 一致的行为时，选它。权重在 HuggingFace 上是 gated 的，需要先通过访问申请。MSA 服务器默认开启，也就是说除非你显式关掉，序列会离开本机。 |
| [`paper-narrative/`](paper-narrative/) | 配图三层里的最外层，而且它的起点比你以为的更靠前：它读整篇 manuscript 和整套配图，然后由一位「责任编辑」式的评审回答一个问题——就凭 Figure 1，这篇稿子会不会被送外审。产出包括叙事主线、放错了图的 panel、还缺哪些分析、哪些该砍掉。它从你的 manuscript 里推导出的 brief 是模型生成的，动手之前先自己过一遍。 |
| [`pdf-explore/`](pdf-explore/) | 在内核里把 PDF 解析一次并留住每页文本，之后靠大纲、相关性检索、逐页抽取和图片裁剪来干活。它是为那种要同时用到文档多处、甚至要扫遍每一页的问题准备的。如果只是查一到四页、并且下一条回复就要引用，那就跳过它，直接读页面。 |
| [`plan-ml-experiment/`](plan-ml-experiment/) | 训练开始之前要先写下来的东西：假设、baseline、指标、决策规则，以及一条能扛住分组结构或时间结构的划分边界。这里的可复现性是机械落实的，靠配置指纹、数据集校验和、记录在案的 seed 和 Artifact manifest。确定性并不等于结论成立，把一个有偏的划分重复一遍也修不好它。 |
| [`protein-design-mcp/`](protein-design-mcp/) | 它组合的是内置蛋白设计 MCP 工具，而不是去驱动某一个模型：靶点条件下的 binder 骨架、带约束的序列设计、单体与复合物预测、物理打分与 relaxation、序列自然度打分、能量最小化，以及可复现的候选比较。它自己写明了边界——背后的 RFdiffusion 工具要求给出靶点 hotspot，不表达 epitope-free、motif scaffolding、无条件或膜蛋白感知的生成——并且不附带任何权重或 GPU 环境；connector 及其外部后端需要另行配置。 |
| [`protein-mutation-enhancement/`](protein-mutation-enhancement/) | 它是编排层，不是模型。它构建确定性的突变体库并给出像 `A12V+G47D` 这样稳定的 ID，把序列、结构、性质和实验/代理打分合并成一个排序，并决定 gain-of-function 的这一轮是收手还是继续扩库。重量级的模型调用交给 `fair-esm2` 和 `esmfold2`。 |
| [`proteinmpnn/`](proteinmpnn/) | 设计面只有蛋白时的默认反向折叠步骤：输入 backbone 几何，输出序列，模型小到在 CPU 上跑几条设计就是几秒钟的事。它只写序列，不写别的，所以需要穿好序列的 PDB 时要用 `ligandmpnn` 的 runner；一旦涉及辅因子或可溶表达，就该换 Skill。 |
| [`reaction-atom-mapping/`](reaction-atom-mapping/) | 使用 RXNMapper 对完整反应做原子对应和变化键提取。它要求反应两侧都已知，不能当作 target-only 逆合成模型或可行性测试。 |
| [`reaction-condition-recommendation/`](reaction-condition-recommendation/) | 对固定反应用 Parrot 生成条件假设，保留 checkpoint 特定的标签词表与温度支持，并区分模型输出和文献/ELN 证据。 |
| [`reaction-forward-prediction/`](reaction-forward-prediction/) | 用 ReactionT5v2 做正向产物预测和逆合成步骤的 round-trip recovery 检查。产物排名表示模型一致性，不表示实验可行性。 |
| [`reaction-yield-estimation/`](reaction-yield-estimation/) | 对完整反应使用 ReactionT5v2 做有适用域门槛的收率筛选，不声称路线级成功概率。 |
| [`rfdiffusion/`](rfdiffusion/) | 用于 de novo binder、hotspot 条件生成和 motif scaffolding 的骨架生成配方。它明确 Hydra 引号与 contig 语义，保留 `.trb` 映射和批次溯源，并强制后续序列设计及独立的单体/复合物验证；生成本身不是结合证据。 |
| [`remote-compute-nvidia/`](remote-compute-nvidia/) | 把任务派发到 NVIDIA NIM，两种形态共用同一套 job 契约。`self_hosted` 在你自己的 GPU 上跑 nvcr.io 容器；`hosted` 不需要本地 GPU，但每一次任务请求都会发往 NVIDIA 的托管网关。只有声明过的 key 变量才会转发给受限的 helper，并且会从离开沙箱的日志尾部里抹掉。 |
| [`remote-compute-ssh/`](remote-compute-ssh/) | 在用户自己的 SSH 或 SLURM 主机上跑任务时的编排部分：分区、环境激活、作业脚本、文件暂存、结果回收、恢复。科学内容不归它管。每一次提交都会在用户面前弹出审批框，并且花掉他们的机时，所以一次好的运行应该是：先读已经记下来的主机信息，缺的一次问清，把第一次提交落地，再把学到的东西写下来。 |
| [`retrosynthesis_planning/`](retrosynthesis_planning/) | 多步科学问题：AiZynthFinder 从目标搜索到声明的库存，然后对路线做规范化、去重、排序、结构审计和化学家评审渲染。单步、正向、映射、条件和收率问题各有自己的 Skill。 |
| [`scgpt/`](scgpt/) | 面向单细胞数据的 transformer 基础模型：用于聚类的细胞 embedding、零样本或微调的细胞类型注释，以及可用于扰动或 GRN 分析的基因表示。checkpoint 是裸目录，不是 HuggingFace repo。代码是 MIT，但没有任何来源说明权重的许可证。 |
| [`single-cell-rna-analysis/`](single-cell-rna-analysis/) | 面向人/鼠已完成 cell calling 的 10x scRNA-seq 与 snRNA-seq counts 的 CPU Scanpy 工作流：支持单样本描述性 QC/聚类或 donor-aware 对比推断、仅显式请求时使用 Harmony、证据辅助注释、检查点与带校验和的结果包。 |
| [`scvi-tools/`](scvi-tools/) | `scgpt` 的概率式对应物：scVI 给出批次校正后的隐空间，scANVI 从部分标注的参考集迁移标签，还有贝叶斯差异表达。它需要的是原始整数 UMI counts。要做空间解卷积或映射，请改用 cell2location、DestVI 或 Tangram。 |
| [`solublempnn/`](solublempnn/) | ProteinMPNN 的同一套架构，在可溶 PDB 子集上重训，使输出偏离全 PDB 模型乐于放置的表面疏水残基。设计出来的蛋白老是聚集、进包涵体时，用它。代价是牺牲几个百分点的原生序列回收率；而且仅凭序列的先验并不是一次可溶性测量。 |
| [`single-step-retrosynthesis/`](single-step-retrosynthesis/) | 通过已有的隔离、manifest 校验 Syntheseus adapter 调用 RetroChimera，生成一步前体提案，并有意在库存搜索或递归规划前停止。 |
| [`using-model-endpoint/`](using-model-endpoint/) | 记录一个计划中的 endpoint 作用域推理工作流：一个网络出口被限定到单个已注册 endpoint 的 Python 内核，预置 `BASE_URL`，没有 job 生命周期。Host 目前实现了 endpoint 的注册与探测，但还没有把这个 provider 接进 `ComputeManager`，也不会创建对应的 scoped kernel。 |
| [`volcengine-datapro/`](volcengine-datapro/) | 一份刻意保持窄范围的专业数据集 MCP recipe：发现 `dataPro_search`、发起真实查询，并且只把结构化结果中整数零的 code 判为可用。仅仅发现工具绝不是鉴权结论。 |

## 在架构中的位置

- `openai4s/skills_loader/` 负责发现这些目录。可写的 user Skill 若声明了内置 Skill
  已经占用的名字，名字仍归内置 Skill。
- 含有 `COLLECTION.json` 的目录算作目录清单里的一个条目，而不是 N 个平级 Skill，它的
  成员位于下一层。这个标记文件就是全部规则：loader 里没有硬编码任何目录名或检索策略；
  可写包同样不得占用集合成员的目录名。
- 这里的东西都是只读的应用资源。用户自己写的 Skill 放在配置的数据目录下，替换不了
  同名的内置 Skill。
- `kernel.py` sidecar 只放定义。它在使用前会先过一遍 compile check，然后注入科学
  Python 内核；它不得给 core 引入强制依赖。
- Provider shim 是受信任的扩展代码，运行时会跨过另有文档说明的 compute 或 endpoint
  边界。光有一份 manifest，并不代表这项 capability 已经能用。
