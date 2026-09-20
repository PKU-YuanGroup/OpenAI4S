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
