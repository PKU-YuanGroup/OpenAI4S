# 实验性语义判断层

这是仓库内「实验性」（默认关闭）语义判断层的设计正文，对应计划 §1、§4、§5。
运行时接线、HTTP、UI 和模板在后续 wave 落地；本文是它们要实现的契约。

## 状态

实验性。默认关闭。第一个厂商：TypeSafe Jev（`jev-1.13.0`）。核心代码保持
只用标准库；以官方 HTTP 契约为准，不依赖 `typesafe-sdk`。

## 目标与非目标

**目标。** 把散落的关键词规则和「让 LLM 返回 JSON 判决」替换成有类型、带概率、
可记录、可评测、可回放的判断步骤，并放在实验开关后面。

首批能力，全部默认关闭：

| 代号 | 能力 | 形态 | 发布状态 |
| --- | --- | --- | --- |
| `skill_suggest` | Skill 推荐（中英文语义匹配） | 给 `search_skills` 追加字段，只作推荐 | 实验性，默认关闭 |
| `literature_check` | 文献片段筛选 + 结论与引用核验 | `literature-review` Skill 的 helper | 实验性，默认关闭 |
| `text_features` | 可解释文本特征工程 | 新 Skill | 实验性，默认关闭 |
| `safety_shadow` | 代码门 / 注入 / 生物安全的影子判定 | 只记录分歧，不改变任何判决 | 实验性，默认关闭 |
| `task_mode_shadow` | 任务模式判定的影子记录 | 只记录，不绑定交付要求 | 实验性，默认关闭 |

**本次实验明确不做的事**

- 不替代主模型做规划、代码生成、解释或写作。Jev 不生成文本。
- 不让 Jev **执行**任何安全判决。安全相关一律只做 shadow；进入 enforcement 另开计划。
- 不做大小模型级联。`host.llm` 目前没有按请求选模型的受控接口。
- 不处理图像或结构文件。Jev 只接受文本；其他模态要先转成待判断的文本。

## 发布设计

### 开关、优先级与默认值

`ExperimentalJudgmentFlags` 挂在 `Config` 的 `experimental_judgment` 上。
能力字段是三态（`bool | None`），由 `_strict_env_tristate` 读取：未设置是
`None`，真/假词汇与 `_STRICT_TRUE_VALUES` / `_STRICT_FALSE_VALUES` 相同，
其他拼写（包括 `flase`）抛 `ValueError`。

| 开关 | 环境变量 | Store setting | 默认 |
| --- | --- | --- | --- |
| 总开关 | `OPENAI4S_EXPERIMENTAL_JUDGMENT` | `experimental.judgment.enabled` | 关 |
| Skill 推荐 | `OPENAI4S_JUDGMENT_SKILL_SUGGEST` | `experimental.judgment.capabilities.skill_suggest` | 关 |
| 文献核验 | `OPENAI4S_JUDGMENT_LITERATURE` | `experimental.judgment.capabilities.literature_check` | 关 |
| 文本特征 | `OPENAI4S_JUDGMENT_TEXT_FEATURES` | `experimental.judgment.capabilities.text_features` | 关 |
| 安全影子 | `OPENAI4S_JUDGMENT_SAFETY_SHADOW` | `experimental.judgment.capabilities.safety_shadow` | 关 |
| 任务模式影子 | `OPENAI4S_JUDGMENT_TASK_MODE_SHADOW` | `experimental.judgment.capabilities.task_mode_shadow` | 关 |
| 后端 | `OPENAI4S_JUDGMENT_PROVIDER` | `experimental.judgment.provider` | `typesafe`（后续可选 `llm`） |
| 模型 | `OPENAI4S_JUDGMENT_MODEL` | `experimental.judgment.model` | `jev-1.13.0`（钉死版本，不用 `jev-latest`） |
| 超时 | `OPENAI4S_JUDGMENT_TIMEOUT_S` | — | `3.0` 秒（0.1–30） |
| Key | `OPENAI4S_TYPESAFE_API_KEY`（headless） | secret `typesafe_api_key`，scope 为 `judgment` | 无 |

生效开关（`openai4s.judgment.flags.resolve`）遵循
`JUDGMENT_FLAG_PRECEDENCE`：

1. env 显式为 false → **强制关闭**（kill switch，UI 改不回来）。
2. env 显式为 true → 打开（CLI / headless / CI live 测试）。
3. 其余情况看 Store setting。
4. 都没有 → 关闭。
5. 总开关关闭时，所有子能力一律关闭。
6. 走 UI 路径打开时，还要求**当前版本**的数据披露确认，否则按关闭处理。走 env 路径打开时，默认视为 operator 已知情，启动时打一条 warning 日志。

### UI

Customize → General（或独立的 Experimental 标签）将提供总开关、带披露说明的
分项开关、只读的后端和模型、key 输入/清除、连接测试，以及状态行
（`disabled` / `ok` / `unavailable`）。语义 Skill 推荐显示成实验性 chip，
不和词法结果混排。UI 由后续 wave 实现。

### 「默认关闭」保证

没有显式打开实验开关时，行为、系统提示词、工具 schema、工具结果、网关响应
schema 和 harness golden trace 都必须逐字节不变。
`tests/test_judgment_default_off.py` 冻结了 Skill `system_context`、
`search_skills` 结果、`REGISTRY` schema，以及 heuristic 模式下
`classify_code` 的判决。这份快照是在本层任何产品代码落地之前抓取的。

### 数据披露

文案写在 `openai4s/judgment/disclosure.py`，版本号
`DISCLOSURE_VERSION = "2026-09-20"`。改了文本就升版本，要求重新确认。
按能力列出的、发给 `api.typesafe.ai` 的内容：

| 能力 | 外发内容 |
| --- | --- |
| `skill_suggest` | 用户当前请求文本；scope 内候选 Skill 的名称、描述和 `SKILL.md` 开头片段 |
| `literature_check` | 研究问题、论文片段、待核验结论 |
| `text_features` | 用户选中的数据行文本 |
| `safety_shadow` | 待执行的代码 cell、工具返回的内容片段、会话轨迹摘要（风险最高，UI 上单独强调） |
| `task_mode_shadow` | 用户请求文本 |

固定事实：服务托管在美国；隐私政策承诺不用输入训练模型，但**没有写明保留期限**；
零数据保留（ZDR）只对企业账户开放。敏感数据不要开启。

确认记录写入
`experimental.judgment.disclosure_ack = {version, capabilities, acked_at}`。
Session package 导出时可附带 `judgment_manifest.json`（用过的能力、后端、
模型、模板版本、调用次数和 token 数），**不含 key**。

### 毕业与移除

每项能力单独毕业：预先登记的测试集质量门槛、国内网络附加 p95 延迟门槛、
连续一个发布周期没有相关 P0/P1，以及维护者签字。`safety_shadow` 毕业也只是
保留 shadow，进入 enforcement 必须另写计划。

移除只需四步：删掉 `openai4s/judgment/`、`host/judgment.py`、
`sdk/judgment.py`；删掉各挂载点的 `if flags...:` 分支；删掉 UI 区块；
去掉 `[tool.mypy] files` 里追加的几行。没有 egress 分组要删。

## 目标架构

### 调用链

控制面挂载点（Skill 检索，以及后续的 shadow）和 kernel 的 `host.judge` RPC
都进入 `JudgmentService`，再调用 `JudgmentBackend`：`NullBackend`（永远
disabled）、`TypeSafeBackend`（后续）、可选 `LlmBackend`（后续，只能显式选择，
`calibrated=false`）。API key 永远不进入 kernel 环境。

### 数据契约

问题类型是 `openai4s/judgment/types.py` 里的 frozen dataclass：

- `Noul`：`instructions` 非空；`criteria` 为 `None` 或恰好包含 `true` 和
  `false`。`to_api()` 生成 `{"type":"noul","instructions":...}`，没有
  criteria 时省略该键。
- `Choice`：2–255 个选项，选项名为非空字符串；description 可以是字符串或对象。
  `to_api()` 把选项放进 `criteria`。
- `Score`：2–10 个有序档位。`to_api()` 把档位作为 `criteria` 列表。

`Answer` 含 `kind`、`value`，以及可选的 `probabilities` / `confidence`。
Noul 的答案带上其中任何一个都会被拒绝：Jev 对 Noul 只返回 P(yes)，
后端永远不会填的字段不应当可被表示。`value` 在 `noul` / `score` 下是数值，
在 `choice` 下是选中项的名称。`JudgmentResult` 是 Host RPC 的返回值；
`to_dict()` 只含 JSON 类型。`BackendReply` 是传输层回包：raw answers、usage、回显的
model、`request_id`（没有就是 `None`）。

缓存键（后续）：
`(provider, model, template_id, template_version, policy_version, state_sha256, candidate_versions_hash, scope_id)`。
回放一律用录制的结果。

### 状态语义

| 状态 | 含义 |
| --- | --- |
| `disabled` | 开关关闭，或走 UI 路径但还没做当前版本的披露确认。**不发请求。** |
| `unavailable` | 没配 key、网络或超时、401/429/529 在预算内仍失败、响应不合法、被 egress 拦截。**绝不编造默认分数。** `answers` 为空，`error_code` 必填。 |
| `uncertain` | 请求成功，但模板策略判定为不确定。答案照常返回，由调用方决定怎么处理。 |
| `ok` | 请求成功，并且通过了策略。 |

`host.judge` 把以上四种状态都当作正常返回值。只有调用方式本身出错（未知模板、
参数不合法）才走单键 `{"error": msg}` 软失败。

### 实现必须遵守的 Jev 约束

question 的 key 不会发给模型，所以每条 `instructions` 必须自成一体，并写明
引用的 `state` 字段。Choice 最多 255 个选项。Noul 只返回 P(yes)。Score 有
2–10 档，并保留完整分布。confidence 不等于正确率。state 加最长问题上限 32k
token；Service 层截断并打标。计数、算术、日期比较全部在代码里做。安全相关
只做 shadow；置信度永远不能作为授权依据。问题和 criteria 用英文写；state
可以含中文。

## Progress log

### W0 — 2026-09-20

把 `feat/judgment-w0-a-core-contracts`（分支 HEAD `8a861ceb`）合进
`feat/judgment`，merge commit 是 `cf51bc20`。W0-A 落地了带类型的契约
（`types.py`、`port.py`）、三态开关解析（`flags.py` 与 `config.py` 里的
`ExperimentalJudgmentFlags`）、带版本的披露文案，以及改代码之前的默认关闭快照。

合并前核验：快照提交确实是该分支的第一个提交，且只含测试和 fixtures；在基线
`909cf0ac` 上重跑 `capture()`，四份 fixtures 逐字节一致（10,686 / 403,295 /
43,854 / 2,556 字节）。`uv run mypy` 检查 12 个文件；在 `judgment/types.py`
里放一个无注解函数做探针，确认 mypy 会报错。

集成修正 `ab8f7212`：`Answer` 原本允许 `noul` 答案带 `probabilities` 和
`confidence`，而本文档和 plan 5.3 都写明 Noul 不带这两项。现在两者都会被拒绝，
并且 `value` 在 `noul` / `score` 下必须是数值、在 `choice` 下必须是选项名。
两条新回归测试已确认在修复前是红的。

带到后续 wave 的偏离：`JudgmentBackend.evaluate` 是全关键字参数并返回
`BackendReply`（plan 6 写的是 `RawAnswers`）；总开关走 Store 打开但确认版本过期时，
`no_disclosure` 记在总开关本身，而不只是记在各子能力上。

W1 待办：子能力开关已能解析，但还没有任何调用方，也还没有传输层；
`tests/conftest.py` 目前不会清除 `OPENAI4S_*JUDGMENT*` 变量——一旦 W1 接上运行时，
开发者本机 export 的总开关会污染离线套件。

### W1 — 2026-09-20

按顺序把三个分支合进 `feat/judgment`：`w1-a-transport`（`b753f40d` →
`0c9d37a0`）、`w1-b-host-service`（`52f36188` → `e8ed3ade`）、
`w1-c-settings-egress-gateway`（`00b0c63e` → `c077bba3`）。这一层现在有了
纯标准库的 TypeSafe 传输与严格响应校验、loopback 假端点、`host.judge` 背后的
`JudgmentService`，以及 `/api/v1/experimental/judgment` 设置面和 doctor 检查。

冲突只有 README 和登记表的追加行，按追加顺序取并集。W1-C 的合并里有一处语义
解决：它给延迟 import 的 `JudgmentService` 加的 `# type: ignore[import-not-found]`
在 W1-B 合入后变成无用 ignore，而 mypy 拒绝无用 ignore。

集成修正 `560c8f6f` 补上了三处单个分支看不见的接缝。`JudgmentService._store`
无条件调用 provider，而网关传进来的是本次请求的 Store 实例，于是总开关一开，
`POST /experimental/judgment/test` 就在路由里抛
`TypeError: 'Store' object is not callable`——W1-B 的测试一律传 lambda，
W1-C 的路由测试又把 probe 打了桩，两边都照不到。`BackendReply.fake` 停在传输层，
来自 loopback 假端点的答案被当作真实后端答案记录和审计。W1-C 的 ImportError
兜底会把真正的导入失败报成一句体面的 `unconfigured`。
`tests/test_judgment_w1_integration.py` 覆盖这三点，且其中每一条断言在没有该修正时都是红的。

集成修正 `a586e533`：探针路由的冻结形状把 `error_code` 收窄成了 `null`，
因为套件里唯一没打桩的调用走的是默认关闭路径。而只要开关打开又没配 key，
该路由就会返回 `"unconfigured"`，所以现在套件把两种状态都引出来，形状是二者的并集。

用 loopback 假端点在隔离数据目录下做了端到端：服务的 `probe()` 和一次三题
（Noul、Choice、Score）的 `run()` 返回 `ok` 且 `fake: true`，重复调用命中缓存；
真实 kernel cell 里经真实传输层调用 `host.judge("system.probe", …)`；
以及对运行中的 daemon 依次测 `GET` / `PUT` / `POST …/test`，探针 5 毫秒返回 `ok`。
通过 `OPENAI4S_TYPESAFE_API_KEY` 提供的 key 没有出现在数据目录下的任何文件里，
假端点的请求日志只记 `path`、`headers` 和 `body`，完全没有 `Authorization`。
`PUT {"clear_api_key": true}` 会同时清掉 Store 行和钥匙串条目。

`openai4s doctor` 在关闭时报告 `disabled (experimental, default off)`，
开启且有 key 时报告 `enabled (typesafe/jev-1.13.0)`，缺 key 或
`api.typesafe.ai` 不在强制 allowlist 内时给出 warn。

W2 待办：`tests/conftest.py` 仍不清除 `OPENAI4S_*JUDGMENT*`，而现在已经有运行时
路径会让它泄漏进来。`host.judge` 刻意不在 `GATEABLE_TOOLS`、`_SCREENED_METHODS`
和 `_m_capabilities()` 里。dispatcher 信封的 `log_host_call(method="judge")`
仍会记下原始 state，尽管命名审计事件 `judgment` 不记。

### W2 — 2026-09-20

按顺序合入三个分支：`w2-a-skills-backend`（`e491ff89` → `a66674d6`）、
`w2-c-skills-eval`（`ec8f1536` → `131470b5`）、`w2-b-frontend`（`3ee20db2` →
`04dd3594`）。`skill_suggest` 打开时 `search_skills` 会带上语义推荐；有了一份
冻结的 200 条中英文评测集和锁死的测试集划分；Customize → General 多了带逐项
披露的 Experimental 区块。

集成修正 `76876a59`：`_step_end` 把 `search_skills` 的结果当 list 迭代，
能力打开后拿到的 dict 只会吐出它的键——工作台步骤卡在有真实词法命中的情况下
显示 "no match"，推荐 chip 在真实会话里根本没有数据可渲染。W2-A 不拥有这层投影，
W2-B 的 Vitest fixture 又是直接喂 dict，两边都照不到。现在两种形态都能投影，
能力关闭时的路径逐字节不变。

用 loopback 假端点核过：能力关闭时 `search_skills` 返回的仍是原来的 list；
打开时包装结果里的 `results` 与那个 list 逐字节相同。关闭状态下
`browser_smoke.mjs` 通过，打开并接假端点时 `browser_judgment.mjs` 通过。
离线开发集评测复现了 W2-C 的数字（B0 top-3 0.859，B2 top-3 0.923）。

遗留，已开返工单给 W2-A：`SearchSkillsTool.execute` 先把词法结果填满整个
`output_limit`，再加语义信封，于是词法命中一大，推荐就会被整组丢弃，而
`semantic_status` 仍然报 `ok`。5 条 bioSkills 结果渲染后有 50,123 字符，
上限是 50,000，`suggest_skills` 确实产出的三条推荐一条都到不了调用方。

另一项遗留：`format_tool_result` 只把词法 `results` 展示给模型，所以只有在词法
一无所获时模型才看得到推荐。这种不一致是产品决定而不是缺陷，记录在案交给维护者。

#### W2 返工 — 2026-09-20

W2-A 的返工 `7edc1cc6` 以 `891c7ef6` 合入。`SearchSkillsTool.execute` 现在先给
语义信封留出份额再去适配词法命中，`fit_to_budget` 新增可选 `budget` 参数，
能力关闭的路径一个字没动。当信封连一条推荐都放不下时，返回值带上
`semantic_truncated: true`，并把 `ok` 改成 `uncertain`——「预算把推荐吃掉了」
不再和真正的弃权混为一谈。

在合并后的树上用当初触发缺陷的同一条 query 对着 loopback 假端点验过：
`suggest_skills` 给出 3 条，经过 `execute` 之后 3 条全部保留，与 5 条词法命中
共存，渲染长度 44,723 字符（上限 50,000），步骤卡两者都带。把信封压过上限也能
复现新信号——上限 600 时 3 条留下 2 条并带 `semantic_truncated: true`，
上限 300 时一条不剩且状态降为 `uncertain`。

集成修正 `07f0e9a8` 订正了预留逻辑的 docstring：它写的是「把预留上限卡在
`output_limit // 8`，以免大信封饿死结果列表」，而代码在有推荐时取
`max(needed, cap)`——那是下限不是上限。它得出的结论在真实上限下成立，
但它点名的机制不是实际实现的那个。行为未变。

给后续 wave 的新增约定：能力开启时的返回值可能带 `semantic_truncated`，
并且 `uncertain` 现在也可能表示预算丢掉了推荐，而不是模型犹豫。
真正的弃权是 `ok` + 空列表 + 没有这个键。

### W3 — 2026-09-20

合入 `w3-a-literature-skill`（`0cab84ab` → `547e63d2`）和
`w3-b-literature-eval`（`bc845b21` → `165eef9f`）。`literature-review` 增加了
`screen_passages` 和 `check_claims`，背后是 `literature.screen` 与
`literature.claim` 两个模板；旁边是一份基于开放获取片段的 304 对中英文冻结评测，
测试集那一半用摘要锁死。

默认关闭快照第一次发生变化，而且只变在它不得不变的地方：
`tests/fixtures/judgment_default_off/search.json` 里存着每个 Skill 的 `doc`，
新增的 SKILL.md 小节正是 `literature-review` 的一部分。接受之前核过——
只有一条查询的结果变了，其中只有一个 Skill、只有一个字段，新增 2,072 字符、
删除 0 字符，旧正文仍是新正文的子串。另外三份 fixture 一个字节没动。

集成修正 `1b1cc9d3`：模板靠 import 副作用注册，而 W3-A 把这个 import 放在
`JudgmentService.dispatch` 里，那只覆盖 `host.judge` 一条路。
`JudgmentService.run`——评测的 host shim 走的正是这条——在全新解释器里会抛
`unknown template: literature.claim`。已移到每个调用方本来就要经过的
`registry.get_template`。W4 的 features 和 safety 模板因此不再需要各自补一行 import。

集成修正 `05b34283`：`tests/conftest.py` 现在会逐测试清除所有
`OPENAI4S_*JUDGMENT*` 开关，与 rollout flags 并列。key 和 model 变量仍由既有的
`*API_KEY` / `*MODEL` 规则覆盖——这正是 live 判断测试改读
`OPENAI4S_JUDGMENT_LIVE_KEY` 的原因。

W1 起遗留的 host_only 边界现在验过了，而且两半需要不同的环境。对着真实端点，
声明了 `api.typesafe.ai` 的 Skill 能连通，只声明别的域名的 Skill 在发出连接之前
就被 `egress_blocked` 拒绝。对着 loopback 假端点，两者都不会被拒：配置了假端点时
传输层整条跳过 `egress.check_url`，Skill 的收窄因此不生效。那条路径只绑 loopback
且由测试变量控制，生产环境不会因此放宽——但这意味着**这条边界不能在假端点下测**。

`check_claims` 在真实 kernel cell 里对着假端点跑到全部状态出现为止：
`verified`、`contradicted`、`unsupported`、`uncertain`、`numeric_mismatch`、
`not_found_needs_review`。定位失败时完全不调用后端；数值不匹配会压过语义上的
`supports`——数字在代码里比，正如设计要求的那样。

### W4 — 2026-09-20

合入四个互相独立的分支：`w4-a-safety-shadow`（`4dcd92ca`）、
`w4-b-task-mode-shadow`（`59bf9dcd`）、`w4-c-text-features-skill`（`82337fed`）、
`w4-d-llm-backend`（`9381e4c9`）。影子判定现在与代码分类器、注入扫描、轨迹筛查
和任务模式规则并行运行；`text-features` Skill 把自由文本变成校准特征；
`provider=llm` 会选到一个明确标记为未校准的 LLM 后端。

三处安全判决没有变。每个公开函数都是先用原来的函数体算出判决，再在一个吞掉
所有异常的 `try/except` 里提交影子，然后**返回同一个对象**——代码里没有任何一处
基于影子答案的分支。选中的安全套件在 `OPENAI4S_SAFETY` × 影子开关的四种组合下
结果完全一致：每次都是 146 passed, 1 skipped，耗时相差不到一秒。

两条影子通道在默认关闭时都是惰性的：一个 import 了它们、跑过 `classify_code`
和 `resolve_task_mode` 的进程仍然只有一个线程、零提交。带标记的代码真实提交一次之后，
审计里有 `kind`、`existing_verdict`、`shadow_answers`、`agree`、`status`、
`latency_ms`、`state_sha256`——没有代码原文。

集成修正 `927cb277`：四个分支各自往 `templates/__init__.py` 追加了一行 import 并重写了
`__all__`。把这些冲突按并集解决——对旁边那些只追加的 README 表格是对的——
结果文件里留下了三个 `__all__` 赋值，最后一个生效，`safety` 和 `task_mode` 被丢掉了。
import 本身都还在，所以模板照常注册、什么都没报错。现在只有一个 `__all__`，
并有一条测试把它和下面的 import 钉在一起。

默认关闭快照又动了，这次的差异值得精确说明：往 605 个成员的语料里加一个 Skill，
会让按语料归一化的相关性 `score` 最多偏移 0.03。没有任何查询的命中集合或顺序发生变化，
没有 Skill 进入或离开任何结果集，已有行上唯一变化的字段就是 `score`。
`system_context.json` 只多了一行；工具 schema 和分类器那两份 fixture 一个字节没动——
这正是「包装三个安全函数没有改变任何可观察行为」的证据。

**刻意没做**：两条影子通道仍是两个模块。`shadow.py` 和 `task_mode_shadow.py`
把同一套机制实现了两遍，而且 `shadow.submit("task_mode", …)` 本来就能套进现有签名——
但 W4-B 的测试从它自己的通道 import 了八个符号，并且对另一套 `stats()` 契约断言了 18 次。
合并它们意味着由合并者重写另一个包的验证面，那比这点重复更糟。方案记在 W5 的待办里。
