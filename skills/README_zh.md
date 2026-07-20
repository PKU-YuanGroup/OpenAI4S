# 内置 Skills

[English](README.md)

32 个 OpenAI4S 内置 Skill 都在这里，一个 Skill 一个目录。Skill 是一份 recipe：
代码，加上把它跑起来所需要的运维知识，而不是 provider 的 JSON Tool。披露是渐进的，
loader 一开始只给出名称和一行摘要；某个 Skill 真被选中，它才去读该目录下的
`SKILL.md` 和可选的 `kernel.py` sidecar。

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`admet_genetic/`](admet_genetic/) | 从 seed SMILES 出发的遗传式优化循环，用 RDKit 描述符、QED、SA-Score 和 ADMET-AI 打分。sidecar 故意不提供固定的 GA 引擎：突变、交叉、过滤和打分权重都要你按当前目标自己设计。每一条记录在案的候选分子都必须带着生成它的血缘。 |
| [`alphafold2/`](alphafold2/) | 通过 ColabFold 的 `colabfold_batch` 跑 AF2 与 AF2-Multimer：一个 FASTA 加一条命令就能预测，不用在本地挂载 MSA 数据库。MSA 来自公共 MMseqs2 服务器，也就是说序列会被发到那里。只处理蛋白；要做配体或核酸，请转向 `boltz`、`chai1` 或 `openfold3`。 |
| [`audit-dataset/`](audit-dataset/) | 训练或对外发布之前该做的那次检查：schema 漂移、缺失、重复行与重复 ID、目标类别不平衡，以及同一实体横跨 train/validation/test。纯标准库实现。结构层面查干净了，仍然说明不了数据是否有代表性、标签是否正确。 |
| [`boltz/`](boltz/) | 对蛋白、DNA、RNA 与配体链做开放权重的 co-folding，还有一个可选的小分子亲和力头。在四个 co-folding Skill 里，它是 binder 验证类任务的默认选择：权重完全开放（MIT），采样最快。 |
| [`borzoi/`](borzoi/) | 输入 DNA，输出预测的实验信号覆盖：约 524 kb 窗口上的 RNA-seq、CAGE、DNase 和 ChIP track。给非编码变异打分的做法是跑 ref 与 alt 两个窗口，比较逐 track 的差值。如果你要的是序列似然而不是实验 track，请改用 `evo2`。 |
| [`catalyst_sar_screening/`](catalyst_sar_screening/) | 针对石墨烯 M–N–C 位点的单原子催化剂 SAR 筛选，能量引擎硬锁定在 FAIRChem UMA。禁止启发式、查表和其他 MLIP，也禁止把仓库里已提交的 demo 输出当作用户结果：每个答案都必须来自一次全新的 pipeline 运行。权重 hub 连不上时，它会停下来问，而不是换一种方法糊过去。 |
| [`chai1/`](chai1/) | 和 `boltz` 覆盖同样的 co-folding 场景，但换了一个模型——这正是它的价值：两个都跑，保留任一模型通过的设计。它的 Python 入口让它比 `boltz` 更容易嵌进循环里，而且 Apache-2.0 明确允许商用。 |
| [`diffdock/`](diffdock/) | 盲式对接。不需要预先划定搜索盒：扩散模型可以把配体放到表面任何位置，再由 confidence 头给采样排序。这个 confidence 反映的是构象是否正确，不是结合自由能，而且不同复合物之间的数值不可比。所以在做苗头化合物分诊之前，还要配一个打分工具。 |
| [`esmfold2/`](esmfold2/) | Biohub 的 ESM 发布：既有可以只凭单条序列跑的全原子 co-folding，也有 ESMC 语言模型给出的 embedding、突变打分和 contact 预测。当你没有 MSA 也能接受时，它优于其他几个 co-folding Skill。 |
| [`evaluate-model/`](evaluate-model/) | 在留出数据上评估二分类与回归：ROC AUC 会处理并列取值，不确定度来自确定性 bootstrap。它有一半是纪律而不是算术：指标要在看到测试集之前定下来，结果要对照 baseline，还要逐个子群检查。bootstrap 区间刻画的是抽样波动，它不会修正泄漏，也不会修正数据分布偏移。 |
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
| [`protein-mutation-enhancement/`](protein-mutation-enhancement/) | 它是编排层，不是模型。它构建确定性的突变体库并给出像 `A12V+G47D` 这样稳定的 ID，把序列、结构、性质和实验/代理打分合并成一个排序，并决定 gain-of-function 的这一轮是收手还是继续扩库。重量级的模型调用交给 `fair-esm2` 和 `esmfold2`。 |
| [`proteinmpnn/`](proteinmpnn/) | 设计面只有蛋白时的默认反向折叠步骤：输入 backbone 几何，输出序列，模型小到在 CPU 上跑几条设计就是几秒钟的事。它只写序列，不写别的，所以需要穿好序列的 PDB 时要用 `ligandmpnn` 的 runner；一旦涉及辅因子或可溶表达，就该换 Skill。 |
| [`remote-compute-nvidia/`](remote-compute-nvidia/) | 把任务派发到 NVIDIA NIM，两种形态共用同一套 job 契约。`self_hosted` 在你自己的 GPU 上跑 nvcr.io 容器；`hosted` 不需要本地 GPU，但每一次任务请求都会发往 NVIDIA 的托管网关。只有声明过的 key 变量才会转发给受限的 helper，并且会从离开沙箱的日志尾部里抹掉。 |
| [`remote-compute-ssh/`](remote-compute-ssh/) | 在用户自己的 SSH 或 SLURM 主机上跑任务时的编排部分：分区、环境激活、作业脚本、文件暂存、结果回收、恢复。科学内容不归它管。每一次提交都会在用户面前弹出审批框，并且花掉他们的机时，所以一次好的运行应该是：先读已经记下来的主机信息，缺的一次问清，把第一次提交落地，再把学到的东西写下来。 |
| [`retrosynthesis_planning/`](retrosynthesis_planning/) | 把 AiZynthFinder 的路线规范化成稳定 schema、排序，并渲染成供化学家评审和路线分诊的 dashboard。报告里的反应条件、收率区间、路线结论和安全提示都由 LLM 生成。它们是假设，不是实验验证，每一条都必须对照文献、ELN 数据、供应商可得性和专家意见去核。 |
| [`scgpt/`](scgpt/) | 面向单细胞数据的 transformer 基础模型：用于聚类的细胞 embedding、零样本或微调的细胞类型注释，以及可用于扰动或 GRN 分析的基因表示。checkpoint 是裸目录，不是 HuggingFace repo。代码是 MIT，但没有任何来源说明权重的许可证。 |
| [`scvi-tools/`](scvi-tools/) | `scgpt` 的概率式对应物：scVI 给出批次校正后的隐空间，scANVI 从部分标注的参考集迁移标签，还有贝叶斯差异表达。它需要的是原始整数 UMI counts。要做空间解卷积或映射，请改用 cell2location、DestVI 或 Tangram。 |
| [`solublempnn/`](solublempnn/) | ProteinMPNN 的同一套架构，在可溶 PDB 子集上重训，使输出偏离全 PDB 模型乐于放置的表面疏水残基。设计出来的蛋白老是聚集、进包涵体时，用它。代价是牺牲几个百分点的原生序列回收率；而且仅凭序列的先验并不是一次可溶性测量。 |
| [`using-model-endpoint/`](using-model-endpoint/) | 记录一个计划中的 endpoint 作用域推理工作流：一个网络出口被限定到单个已注册 endpoint 的 Python 内核，预置 `BASE_URL`，没有 job 生命周期。Host 目前实现了 endpoint 的注册与探测，但还没有把这个 provider 接进 `ComputeManager`，也不会创建对应的 scoped kernel。 |

## 在架构中的位置

- `openai4s/skills_loader/` 负责发现这些目录。可写的 user Skill 若声明了内置 Skill
  已经占用的名字，名字仍归内置 Skill。
- 这里的东西都是只读的应用资源。用户自己写的 Skill 放在配置的数据目录下，替换不了
  同名的内置 Skill。
- `kernel.py` sidecar 只放定义。它在使用前会先过一遍 compile check，然后注入科学
  Python 内核；它不得给 core 引入强制依赖。
- Provider shim 是受信任的扩展代码，运行时会跨过另有文档说明的 compute 或 endpoint
  边界。光有一份 manifest，并不代表这项 capability 已经能用。

- [`evidence-walkthrough/`](evidence-walkthrough/) —— 参考流程：固定查询、本地分析、带 lineage 的产物，以及能在干净环境校验的证据包。
- [`bioprobench/`](bioprobench/) —— 流程推理（protocol reasoning）评测。
