# IS 研究 Skill 开发记录

[English](skill-development.md)

当前已经实现的 contribution 是 [panel-data-preprocessing](../skills/panel-data-preprocessing/SKILL.md)
和 [did-analysis](../skills/did-analysis/SKILL.md)。
它把明确记录的数据清理决定和原始记录转为可追溯面板，再检查事件窗口是否具备
足够数据，并支持从可视化探索持续突变。科学计算使用可复用的纯标准库
`kernel.py` sidecar，只有绘图才按需导入 matplotlib。独立 DiD Skill 接收数据准备
之后的明确干预设计，估计效应并记录诊断／识别边界。早期 DiD 估计器保存在
独立、范围明确的复现实验中。

## 已实现能力

| 阶段 | 已实现行为 | 研究边界 |
| --- | --- | --- |
| 数据画像 | 类型、缺失数、基数、数值分布摘要及方法建议／待确认问题。 | 建议需要类型与研究设计决定；画像不变换数据，也不能证明缺失可忽略。 |
| 数值清理 | 从指定拟合行学习均值、中位数或 KNN 填补；可按显式组别限制邻居／统计量。 | 不默认选 KNN，不从后续行借拟合邻居，不给拟合期全缺的目标编造数值。 |
| 类别／文本编码 | one-hot、label 或显式 ordinal 编码；仅拟合样本学习词表的 count／TF-IDF 特征。 | 原列保留；label 代码没有自然顺序，词汇特征不是语义 embedding。 |
| 面板构建 | CSV 字符串 ID、显式长宽表变换、时期标准化与明确的重复聚合。 | 不隐式推断频率、去重、补造缺失时期、平衡面板或聚合处理状态。 |
| 描述性审计 | 缺失、观测网格／完整日历平衡、间隔、晚进入／早退出、数值摘要及样本覆盖变化。 | 填补值可能进入统计且会标记；合并样本摘要不是因果效应。 |
| 窗口审查 | 完整真实观测的前后窗口、基线方差／趋势／相关性审查及有条件的扩大建议。 | 排除填补结果；均值波动建议依赖稳定独立噪声，不是功效或显著性。 |
| 事件探索 | 分别记录输入的事件日期、处理状态转换和实体内持续数值突变候选。 | 根据结果变量选出的候选不能识别外生处理日期。 |
| 可视化／导出证据 | 分实体轨迹、独立填补标记、覆盖／窗口图、来源行、可复用模型及记录／配置／产物哈希。 | 应同时审查原始与变换值；填补后的较小方差不增加真实观测证据。 |

## 变换前的方法选择

先确定变量角色和可用于拟合的样本，再调用清理器。画像用于选择方法时，对计划的
校准样本调用 `profile_cleaning`；未来结果不能决定训练方法。可能是数值 ID、
有序类别或自由文本的类型歧义，以及不明确的拟合期，需要先向科研人员确认。
类型清晰且研究设计有依据时，可以记录 `data_profile` 决定；科研人员明确指定
的方法使用 `researcher`。每个决定必须提供实质性的 `rationale`，不能使用没有
解释的默认方案。

| 数据／角色 | 候选方法 | 选择依据 |
| --- | --- | --- |
| 分布适合且较稳定的连续测量 | 均值填补 | 研究设计、拟合样本分布及可辩护的缺失假设。 |
| 偏斜测量或明显极端值 | 中位数填补 | 稳健基线选项；保留极端值并检查含义，不自动删除。 |
| 具有合理局部相似性的数值协变量 | KNN 填补 | 明确数值距离特征、单位／尺度与可用邻居组别；检查拟合组均值回退及未解决单元格。 |
| 无序类别 | one-hot；下游用途适合时才用 label | one-hot 避免等级假设；数值 label 只是类别标识，不自动成为连续回归变量。 |
| 有序类别 | 显式 ordinal 顺序 | 科研人员／领域定义的等级，包括拟合前预先定义的等级；字典顺序不是等级定义。 |
| 自然语言文本 | count 或 TF-IDF | 词汇分析目的、一致分词及仅拟合期学习的词表；中文需要上游分词。 |

画像中的分布建议是启发式提示，不是统计检验或已经验证的自动方法选择器。填补是
可选步骤：未指定的数值列保留缺失。函数不会默默缩尾、删行、修正单位、进行中文
分词或推断因果缺失机制。

## 仅拟合子集学习的清理 API

按 Skill 配方，在 OpenAI4S Skill bootstrap 环境中加载 sidecar：

```python
from importlib import import_module
import json

prep = import_module("panel-data-preprocessing.kernel")
rows = [
    {"firm": "001", "period": "1", "adopted": 0,
     "sales": "10", "size": "100", "sector": "retail", "review": "fast delivery"},
    {"firm": "001", "period": "2", "adopted": 0,
     "sales": "12", "size": "120", "sector": "retail", "review": "reliable delivery"},
    {"firm": "001", "period": "3", "adopted": 1,
     "sales": "", "size": "110", "sector": "retail", "review": "fast response"},
]
fit_rows = [1, 2]  # 从1开始的原始输入基线行位置
profile = prep.profile_cleaning(
    [rows[i - 1] for i in fit_rows], numeric_columns=["sales", "size"],
    protected_columns=["firm", "period", "adopted"],
)
cleaning = {
    "fit_rows": fit_rows,
    "numeric_columns": ["sales", "size"],
    "protected_columns": ["firm", "period", "adopted"],
    "imputations": {"sales": "median"},
    "categorical_encodings": {"sector": "onehot"},
    "text_encodings": {"review": "tfidf"},
    "unknown_categories": "indicator",
    "decision": {"basis": "researcher",
                 "rationale": "Fit a simple robust fill and nominal/lexical covariates only on the two pre-adoption records."},
}
cleaned = prep.clean_data(rows, **cleaning)
model = json.loads(json.dumps(cleaned["model"]))
later = prep.transform_cleaner(rows[2:], model)  # 不重新拟合
result = prep.preprocess_panel(
    rows, entity="firm", time="period", frequency="integer",
    numeric_columns=["sales"], treatment="adopted", cleaning=cleaning,
    window=2, window_options=[2], noise_tolerance={"sales": 0.5},
)
print(result["cleaning"]["report"])
print(result["window_assessment"]["summary"])
```

`profile_cleaning` 返回 `columns` 画像与待解决的 `questions`。
`fit_cleaner(rows, *, fit_rows, numeric_columns, ..., decision)` 返回带哈希校验、
可 JSON 序列化的模型。`clean_data` 合并拟合与应用；
`transform_cleaner(rows, model)` 返回 `rows`、`model`、`report`，不改变邻居、
统计量、类别或词表。上述独立调用与集成调用用于展示可选路径；从原始数据拟合，
不要用已经带 `_imputed_columns` 的清理结果重新拟合。

拟合行必须是有效、不重复、非空、从 1 开始的原始输入行位置。没有从全数据自动学习
的默认行为。均值、中位数、数值尺度、邻居、学习到的类别等级与词表／IDF 仅使用
这些位置。预先给出的 ordinal 顺序属于明确的 schema，有别于从样本学习的类别。
保存原始数据，并依据独立的研究设计信息选取训练／事前记录。宽表的一条选中记录
可能仍含事后列：需要限制源列和 KNN 特征的时间角色，或先显式变成长表，再选择
事前观测进行清理。

KNN 的 `knn_features` 指定数值相似性维度；记录下来的默认值是明确选择的数值列，
在每个目标的距离计算中排除该目标。使用仅拟合样本学习的组内尺度，并按共同真实
观测维度修正距离。`group_by` 将拟合与邻居限制在明确组内；组键必须有真实值，
不能同时被填补或编码。可用邻居较少时截断邻居数。默认 `n_neighbors=5`、
`weights="uniform"`，也支持距离加权。没有共同维度时记录拟合组均值回退；
新组或拟合期全缺的目标保持未解决，不跨组借值。报告的 `filled` 中保留邻居原始
行位置及距离证据。

类别编码保留原值与缺失指示列。one-hot 使用 `column__cat_0` 等列名；
label／ordinal 使用 `column__code`。未知类别默认报错，也可以通过
`unknown_categories="indicator"` 及 `column__unknown` 单独记录。文本使用
大小写统一的 Unicode 词、仅拟合期学习的 `column__text_0` 等词汇列及缺失指示。
TF-IDF 使用平滑 IDF 与 L2 归一化；记录未登录词计数，不扩充拟合词表。
默认 `text_max_features=1000` 限制单个文本词表，`max_generated_features=1000`
限制整个生成 schema（包含指示列）。两者均可配置至 10,000；超过总列预算时报错。

`group_by` 仅限制数值统计量与邻居；类别和文本 schema 在选定拟合行之间共享，
保证不同实体的特征含义一致。count 模型会保留词频／IDF 元数据，但实际应用的是
未加权词频；TF-IDF 才会应用学习到的权重。

## 清理、面板窗口与可视化证据

显式传给 `preprocess_panel(..., cleaning={...})` 的清理在 `prepare_panel`
之前执行。集成路径自动保护实体、时间与处理状态。清理器可包含额外数值协变量，
但必须覆盖每个原始结果源列；宽表填补使用这些源列名称。生成协变量保留在面板中，
不会自动进入结果指标列表或更改筛选。

填补数值单元格带有 `_imputed_columns`。宽转长将源标记映射到对应指标；重复
聚合取标记并集。窗口审查记录 `coverage.pre_imputed_values`、
`coverage.post_imputed_values` 及 `imputed_values_pre/post` 排除原因。
即使其他方面覆盖完整，窗口只要包含填补结果值，就不能进入真实观测筛选。
事前填补值也阻止数值噪声评估。描述性统计可能包含变换后的数值，并明确注明
解释范围；比较原始与处理后摘要时保留这个区别。

绘图器在缺失值、缺失日历记录或填补结果处断开真实观测轨迹；填补单元格使用
独立的空心菱形。应同时审查原始与填补序列。填补后方差降低是变换的性质，不能
证明独立真实观测足够。即使个体轨迹只展示明确选取的子集，完整样本覆盖与窗口
可用性图仍使用全部面板。

窗口建议比较同一锚点，仅用事前值计算波动，事后值用于判断完整性。五期基线下限、
趋势与残差相关性都是工程审查启发式。`SD/sqrt(n)` 依赖稳定独立噪声，不能证明
统计功效，也不替代当前变化／SD 的筛选得分。扩大窗口可能降低精度问题、减少
可用锚点或混入不同状态；建议不自动更改请求的筛选窗口。

## 导出的审计记录

| 产物或字段 | 含义 |
| --- | --- |
| `panel.csv` | 标准化记录、`_period_index`、源行位置及可能的填补标记；不补造缺失时期行。 |
| `summary.json` | 日历／样本覆盖、数值摘要及已配置清理的明确解释范围。 |
| `candidate_events.csv` | 探索数值候选、输入日期及不同的处理状态／删失事件类型。 |
| `window_assessment.json` / `.csv` | 完整窗口排除、事前诊断、假设与供审查的建议。 |
| `cleaning_model.json` | 可选的仅训练子集拟合模型、拟合行、决定、学习参数／映射与模型哈希。 |
| `cleaning_report.json` | 可选的填补／邻居／回退、未解决单元格、缺失、未知类别／未登录词计数及原始／处理后记录哈希。 |
| `manifest.json` | 面板、筛选与清理设置，源／配置／模型哈希及产物校验和。 |
| `raw_source_records_sha256` | 清理前原始记录在 manifest 中的身份。 |
| `transformation.source_records_sha256` | 实际传给面板构建器的记录身份，可能包含显式清理。 |
| 可选 PNG／SVG 与 `visualization.json` | 诊断图、选中／排除的实体 ID 与图像校验和。 |

记录哈希标识解析后的记录，不代表原始 CSV 的文件字节。需要文件级溯源时，另外
保留原始文件。输出助手拒绝覆盖已经存在的产物包。内存结果提供
`cleaning.model/report`、`window_assessment`，以及
`analysis.window_assessment_summary` 中的窗口摘要。

## 已有公开数据实验

独立的 [DiD 复现目录](../experiments/did-arxiv-replication/README_zh.md)保存公开输入、
精确源版本、执行代码、图形、参考值核对与[范围报告](../experiments/did-arxiv-replication/REPORT_zh.md)。
这两项是范围明确的官方示例点估计复现：

| 论文／官方示例 | 保留输入 | 已核对范围 |
| --- | --- | --- |
| [Callaway–Sant'Anna](https://arxiv.org/abs/1803.09015v4)／[did 示例](https://bcallaway11.github.io/did/articles/did-basics.html) | mpdta：500 个县 × 五年，共 2,500 行。 | 无条件方法示例；overall dynamic ATT −0.077240 匹配官方舍入值 −0.0772。 |
| [Sun–Abraham](https://arxiv.org/abs/1804.05785v2)／[fixest 示例](https://lrberge.github.io/fixest/articles/fixest_walkthrough.html) | base_stagg：95 个个体 × 十期，共 950 行。 | interaction-weighted 方法示例；固定模拟样本 ATT −1.133749 匹配官方值。 |

两项实验保留 24 个成功的参考值核对及独立点估计验证；完整论文应用和置信区间
超出已记录范围。后续的[窗口审查](../experiments/did-arxiv-replication/window-review/README_zh.md)
独立检查覆盖／波动，保存自己的配置与运行环境。其真实五年面板没有完整的前后三期
窗口；两期能放下，但不能提供可靠的五期噪声基线。明确标注的构造示例展示有条件
扩大、保留和趋势审查三个不同的建议分支。

本次新增清理器不改写这些归档实验的输入和结果。新研究的清理选择需要单独的设计、
拟合样本定义、审计和验证；过去复现成功不能验证新的缺失数据假设。

## 随机数据功能实验

独立的[随机面板实验](../experiments/panel-cleaning-synthetic/README_zh.md)使用种子
`20261007`、32 个字符串 ID 与 48 个潜在时期。八类预设场景区分持续变化、高噪声、
趋势、尖峰、季节性、稳定序列与晚进入。原始日历／测量缺失保留；完整生成真值仅
用于事后评估。

它直接执行真实 Skill，只用第 1–16 期做方法画像和拟合，完成分实体的均值／
中位数／KNN 填补、无序／有序／label 类别编码与 TF-IDF 文本特征。另一份显式
结果填补实验检查标记是否阻止填补值使事件窗口获得资格。窗口和阈值事先固定，
不因真值或建议调整。[报告](../experiments/panel-cleaning-synthetic/REPORT_zh.md)
保留全部数值候选、精确与邻近日期的检出、误报、插补误差，以及全体真事件和有资格
真事件两个分母。这一个样本检验实现并呈现探索局限，不能估计 Monte Carlo 准确率
或验证 DiD 识别。

归档运行有 1,462 行原始记录、603 个数值遮挡。主清理器填补 448 个协变量值，
生成 28 个编码列。12 个真实持续变化中，五个有完整请求窗口且全部检出；另有
六个季节性／噪声候选。单独对照填补 102 个结果缺失值后，窗口资格与候选仍相同。
独立核对通过，49 个生成文件在记录环境中复跑逐字节一致。

## 数据准备的边界

这个 Skill 负责数据准备和探索证据。它不推断有效的缺失机制、不传播单次插补
不确定性、不自动选择因果事件日期，也不估计因果效应。发现的结果突变是需要独立
事件信息的假说。已知日期记录来源，但其因果地位仍未经验证。

当前实现在内存中处理记录。KNN 比较与稠密词汇特征存在实际规模限制；它不是
流式 ETL 或语义 embedding 服务。核心依赖仍只有标准库，科学环境中的绘图可选。

## DiD Skill：传统分析、安慰剂与衍生方法

`did-analysis` 是独立可调用的 Skill。科研人员必须明确独立事件来源、处理分配、
平行趋势的识别理由、实体／时间／结果变量、分配聚类和拟用时期。这些声明作为
假设保留，代码不能验证其成立。缺省处理方式拒绝不完整估计样本；显式选择完整
案例分析时报告排除情况，不把填补的结果值当作新观测。

| 分析阶段 | 已实现行为 | 边界 |
| --- | --- | --- |
| 传统 DiD | 处理／对照实体前后均值变化之差；明确共同采用时点，使用聚类 CR1 和 Student-t 推断。 | 实体等权、时期明确；不自动调整协变量或拟合任意逐行 TWFE。 |
| 事件研究 | 相对明确的未处理基期估计各期效应，使用共同完整样本和联合聚类协方差。 | 逐点区间不是同时置信带；事前效应不显著不能证明平行趋势。 |
| 伪处理时点 | 对真实最终处理组应用预先指定的较早日期。 | 两侧窗口严格早于真实处理日期，保留全部请求结果。 |
| 伪处理组 | 只将真正从未处理的单位按完整分配聚类划分；支持固定种子重复抽样。 | 随机安慰剂尾部比例属于描述性指标，不是实际政策的有效随机化检验 p 值。 |
| 分批 DiD | 无条件 group-time ATT，基期 g-1，明确从未／尚未处理对照、cohort 规模动态权重及联合协方差聚合。 | 需要不可逆采用、无预期和无条件识别；不是完整条件／双重稳健 C&S 或其乘子 bootstrap。 |
| 三重差分 | 实体变化对处理组、固定二元子组及其交互项的饱和回归，保留联合聚类协方差。 | 限于无条件、共同处理时点的 2×2×2；不推广到一般分批或依赖协变量的 DDD。 |
| 证据导出 | 顺序结果、科研声明、请求设计、样本损失、来源／校验和及可选图形。 | 实现可靠不能证明具体研究的因果识别。 |

配方先进行传统 DiD，再做事件研究与伪时点／伪处理组检验，然后根据处理设计
选择衍生方法。对于分批采用，先说明共同日期为何不适用；预设的单一 cohort 与
从未处理组传统对比可作基准，不能悄悄把不同 cohort 合并到共同日期估计器。
预处理的类别／文本编码仍只是数据准备，不自动成为调整集；处理后的协变量可能
改变因果估计目标。

[Skill 配方](../skills/did-analysis/SKILL.md)包含可执行接口；
[方法记录](../skills/did-analysis/method-notes.md)说明公式与主要来源。
独立的[验证实验](../experiments/did-skill-validation/README_zh.md)预设合成真值、
聚类误差、无效应、违反事前趋势、DDD 和异质分批效应，独立核对估计与协方差，
并记录重复模拟的性能及 Monte Carlo 不确定性。它不覆盖早期公开数据归档，
也不宣称复现了整篇论文的实证应用。

离线契约见 [DiD 测试](../tests/test_did_analysis_skill.py)：

```bash
uv run pytest tests/test_did_analysis_skill.py
uv run python -m harness.evals.did_analysis --json
```

2026-10-10 的 DiD 更新通过 90 项专门离线测试和 12 个预设真实 sidecar 回放案例。
独立的零售商月度试用核对数据准备 → CSV → DiD 的衔接、前导零 ID、完整案例
损失、保留拒绝与新版图形，共通过 142 项独立检查。归档实验通过 12,237 项独立
statsmodels／SciPy／完整性检查，最大数值差异为 4.31e-12；在记录环境中，45 个
确定性产物及其清单逐字节复跑一致。

最终全仓库离线回归通过 11,543 项测试，跳过 97 项，没有失败。格式、核心严格
类型检查、目录文档、38 个 PR harness 情景及两种构建分发格式也通过验证。

512 个预设模拟面板分为每种场景 128 次。在 24 个聚类的无效应设计中，95% 区间
覆盖 115/128 个真效应（89.84%），13/128 次拒绝零效应（10.16%）。刻意加入
事前趋势偏差的设计仅在 62/128 次事前检验中被发现。报告保留这些局限和 Monte
Carlo 区间；数值正确不等于推断达到标称水平，不拒绝也不能验证平行趋势。

## 验证与主要方法资料

2026-10-07 的清理更新在面板构建前加入基于数据画像或科研人员的方法选择，
通过 61 个清理测试和 22 个面板回放案例；完整离线测试为 11,413 passed、
97 skipped。独立的公司月度试用从事前记录选择了分公司均值填补，保留结果变量
缺失，完成无序类别与词汇文本编码，并核对导出哈希及诊断图。这些检查验证实现
行为，不证明某个缺失机制假设成立。

可观察契约由[清理测试](../tests/test_panel_cleaning.py)、
[面板预处理测试](../tests/test_panel_data_preprocessing_skill.py)与
[窗口测试](../tests/test_panel_window_assessment.py)验证。
[离线面板案例回放](../harness/evals/panel_data.py)用明确的合成案例执行真实 sidecar，
不需要模型或联网：

```bash
uv run pytest tests/test_panel_cleaning.py tests/test_panel_data_preprocessing_skill.py tests/test_panel_window_assessment.py
uv run python -m harness.evals.panel_data --json
uv run python scripts/check_directory_readmes.py
```

主要方法资料：[数值填补](https://scikit-learn.org/stable/modules/impute.html)、
[类别预处理](https://scikit-learn.org/stable/modules/preprocessing.html#encoding-categorical-features)、
[词汇文本特征](https://scikit-learn.org/stable/modules/feature_extraction.html#text-feature-extraction)
与[避免拟合泄漏](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage)。
这些说明方法概念；Skill 的标准库实现另有明确记录的组别、保留与回退行为。
