# 实验性语义判断层

**实验性。默认关闭。Early access。** 第一个厂商：TypeSafe Jev（`jev-1.13.0`）。
需要自备 TypeSafe API key。服务托管在美国。

这是仓库内语义判断层的操作说明。维护者如果要删除这项实验，请按
[experimental-judgment-removal.md](experimental-judgment-removal.md) 做。
开关名和环境变量也写在 [configuration.md](configuration.md)。外发数据见
[security.md](security.md)。

英文正文：[experimental-judgment.md](experimental-judgment.md)。

## 它是什么

一个放在实验开关后面、厂商中立的**语义判断层**。它把现在散落在关键词规则和
「让 LLM 返回 JSON 判决」里的离散判断，变成有类型、带概率、可记录、可评测、
可回放的步骤。kernel cell 通过已注册模板调用
`host.judge(template, state, **params)`。控制面挂载点（Skill 检索、安全筛查、
任务模式检测）走同一个 Host 服务。

问题类型是 `Noul`（P(yes)）、`Choice`（2–255 个选项）或 `Score`（2–10 个有序档位）。
每次调用都把四种状态当作普通数据返回：`ok`、`uncertain`、`unavailable`、
`disabled`。缺 key、出网被拦、超时都是带 `error_code` 的 `unavailable`。
这一层绝不编造默认分数。

核心代码只用标准库。以官方 HTTP 契约为准，不依赖 `typesafe-sdk`。默认后端
POST 到 `https://api.typesafe.ai/v1/systemone`。模型 id 钉死为 `jev-1.13.0`
（不用 `jev-latest`）。输入 **$0.042 / 百万 token**，输出免费。

## 它不是什么

- 不替代主模型做规划、代码生成、解释或写作。Jev 不生成文本。
- 不**执行**任何安全判决。`safety_shadow` 只记录与现有分类器、注入扫描、
  生物安全筛查的分歧。那三个函数仍然返回影子提交之前就算好的判决。
- 不把检测到的任务模式绑定到交付要求。`task_mode_shadow` 只记录；
  `resolve_task_mode` 仍然返回规则结果。
- 不处理图像或结构文件。Jev 只接受文本。其他模态要先转成待判断的文本。
- 不把 `api.typesafe.ai` 加进出网白名单目录。allowlist 模式下仍要你自己授权，
  和其他目的地一样（`host.request_network_access(domain="api.typesafe.ai")`）。
- 不会在 TypeSafe 失败时静默退回主模型。`provider=llm` 必须显式选择，
  并且每条结果都标 `calibrated=false`。

## 能力

五项全部默认关闭。总开关也必须打开。走 UI 打开某一项时，还要对该项做
当前版本的数据披露确认。

| 代号 | 做什么 | 发给 `api.typesafe.ai` 的内容 |
| --- | --- | --- |
| `skill_suggest` | 给 `search_skills` 追加语义推荐。词法命中保持原样。工作台显示成实验性 chip（名称、`p_fit`、confidence），不和词法列表混排。最多 3 条。显式点名某个 Skill 时跳过判断（`skipped_explicit`）。 | 用户当前请求文本；当前 scope 内候选 Skill 的名称、描述和 `SKILL.md` 开头片段 |
| `literature_check` | `literature-review` Skill 上的 `screen_passages` 和 `check_claims`。引文定位和数字/单位比较在代码里做。`host.judge` 只问语义关系。`supports` 只表示源文本支持这句话，并不表示这句话在科学上已被证明。`not_found` 不是造假的证据。 | 研究问题、论文片段、待核验结论 |
| `text_features` | 新 Skill `text-features`。主模型提出 Noul/Score 问题；`host.judge("features.custom", …)` 对选中的每一行作答；`audit-dataset` / `plan-ml-experiment` / `evaluate-model` 在开发集上拟合，测试集冻结后评一次。特征保留来源问题和测量误差，不是人工金标准。`unavailable` 的行填 NaN，不填默认值。 | 用户选中的数据行文本 |
| `safety_shadow` | 挂在 `classify_code`、`scan_tool_result` 和生物安全轨迹筛查（`looks_biosecurity_relevant` / `screen_trajectory`）旁边的影子判定。外发风险最高。 | 待执行的代码 cell、工具返回的内容片段、会话轨迹摘要 |
| `task_mode_shadow` | `resolve_task_mode` 的影子记录。显式 `--mode` / `task_mode` 不会提交。 | 用户请求文本 |

对应开关关闭时，这些路径返回 `disabled`（两条影子通道则不启动 worker），不发请求。

## 如何开启

### 界面

1. 打开 **Customize → General**。Experimental 区块就在这个标签页上
   （`data-judgment`）。
2. 打开总开关。披露对话框会列出每一项能力、会发送什么数据，以及下面的托管事实。
   `safety_shadow` 单独标成外发风险最高。
3. 勾选你打算用的每一项，然后确认。确认写入
   `experimental.judgment.disclosure_ack = {version, capabilities, acked_at}`，
   版本是 `DISCLOSURE_VERSION`（`2026-09-20`）。改了披露文案就升版本，需要重新确认。
4. 粘贴 TypeSafe API key 并保存。已保存的密钥不会回显；界面只显示「已配置 /
   未配置」。清除会同时删掉 Store 行和钥匙串条目。
5. 打开各项能力开关。后端和模型是只读的（除非你用环境变量覆盖，否则是
   `typesafe` / `jev-1.13.0`）。
6. **测试连接**会发一个不含用户数据的固定探针
   （`POST /api/v1/experimental/judgment/test`）。结果是
   `ok` / `unavailable` / `disabled`，外加 `error_code` 和 `latency_ms`。

如果总开关的环境变量是显式 false，这个页面上的开关全部置灰。UI 改不回来。

### 环境变量（CLI、headless、CI）

```bash
export OPENAI4S_EXPERIMENTAL_JUDGMENT=1
export OPENAI4S_JUDGMENT_SKILL_SUGGEST=1          # 可选，按能力打开
export OPENAI4S_JUDGMENT_LITERATURE=1
export OPENAI4S_JUDGMENT_TEXT_FEATURES=1
export OPENAI4S_JUDGMENT_SAFETY_SHADOW=1
export OPENAI4S_JUDGMENT_TASK_MODE_SHADOW=1
export OPENAI4S_TYPESAFE_API_KEY=...              # headless；永不记入日志
```

走环境变量打开时，视为 operator 已知情，启动时打一条 warning。不要求 Store 里的披露确认。

`Config` 在构造时快照环境变量。先 `Config(...)` 再 `export` 不会打开能力；需要新建一个 `Config`。

封闭词表（`0`/`1`、`false`/`true`、`no`/`yes`、`off`/`on`）。拼错（例如 `flase`）会抛 `ValueError`，而不会打开任何东西。

| 开关 | 环境变量 | Store setting | 默认 |
| --- | --- | --- | --- |
| 总开关 | `OPENAI4S_EXPERIMENTAL_JUDGMENT` | `experimental.judgment.enabled` | 关 |
| Skill 推荐 | `OPENAI4S_JUDGMENT_SKILL_SUGGEST` | `experimental.judgment.capabilities.skill_suggest` | 关 |
| 文献核验 | `OPENAI4S_JUDGMENT_LITERATURE` | `experimental.judgment.capabilities.literature_check` | 关 |
| 文本特征 | `OPENAI4S_JUDGMENT_TEXT_FEATURES` | `experimental.judgment.capabilities.text_features` | 关 |
| 安全影子 | `OPENAI4S_JUDGMENT_SAFETY_SHADOW` | `experimental.judgment.capabilities.safety_shadow` | 关 |
| 任务模式影子 | `OPENAI4S_JUDGMENT_TASK_MODE_SHADOW` | `experimental.judgment.capabilities.task_mode_shadow` | 关 |
| 后端 | `OPENAI4S_JUDGMENT_PROVIDER` | `experimental.judgment.provider` | `typesafe`（`llm` 须显式选择） |
| 模型 | `OPENAI4S_JUDGMENT_MODEL` | `experimental.judgment.model` | `jev-1.13.0` |
| 超时 | `OPENAI4S_JUDGMENT_TIMEOUT_S` | — | `3.0` 秒（0.1–30） |
| Key | `OPENAI4S_TYPESAFE_API_KEY` | secret `typesafe_api_key`，scope 为 `judgment` | 无 |
| 审计原始 state | — | `experimental.judgment.audit_raw_state` | 关 |

生效开关（`openai4s.judgment.flags.resolve`）遵循 `JUDGMENT_FLAG_PRECEDENCE`：

1. env 显式为 false → 强制关闭（kill switch，UI 改不回来）。
2. env 显式为 true → 打开。
3. 其余情况看 Store setting。
4. 都没有 → 关闭。
5. 总开关关闭时，所有子能力一律关闭（`master_off`）。
6. 走 UI 路径打开时，还要求**当前版本**的披露确认；否则记为 `no_disclosure`，按关闭处理。

TypeSafe key 每次请求前经 SecretBroker 取用。客户端不会把它写进 `os.environ`，
不会在 JSON 或日志里回显，也不会注入 kernel 环境。

REST（鉴权与 `/search/config` 相同）：

- `GET /api/v1/experimental/judgment` — 开关、来源、provider、model、
  `key_configured`（只有布尔值）、披露版本与确认、egress 报告。
- `PUT /api/v1/experimental/judgment` — `enabled`、`capabilities`、
  `acknowledge`、`api_key`、`clear_api_key`。
- `POST /api/v1/experimental/judgment/test` — 连接测试。

## 如何关闭

Kill switch 是总开关环境变量的显式 false：

```bash
export OPENAI4S_EXPERIMENTAL_JUDGMENT=0
```

这会强制关闭所有子能力。环境变量未设置时，关掉 UI 总开关或清掉 Store setting
同样关闭这一层。总开关关闭时，`host.judge` 返回 `disabled`，不调用传输层。

在 Customize → General 点「清除」，或 `PUT {"clear_api_key": true}`。
Headless：取消设置 `OPENAI4S_TYPESAFE_API_KEY`。

## 如何查看状态

**界面。** Customize → General → Experimental。区块显示每项开关的来源
（`env_off` / `env_on` / `setting` / `default` / `master_off` /
`no_disclosure`）、key 是否已配置、provider、model、egress 模式，以及
`api.typesafe.ai` 是否已经授权。测试连接把 `ok` / `unavailable` / `disabled`
写到 `[data-judgment-test-result]`。

**`openai4s doctor`。** 检查项名称是 `judgment`。它不发起网络连接，也永不打印 key。

| 情形 | Doctor 行 |
| --- | --- |
| 默认 / 总开关关闭 | `[ok] judgment  disabled (experimental, default off)` |
| 开启且有 key、egress 正常 | `[ok] judgment  enabled (typesafe/jev-1.13.0)` |
| 开启但没有 key | `[warn] judgment  enabled, but no TypeSafe API key is configured` |
| 开启且 allowlist 未授权 | `[warn] judgment  enabled, but api.typesafe.ai is not on the egress allowlist` |

**审计事件。** Host 上成功和失败的运行都会发命名事件 `judgment`
（`openai4s.observability.log_event`），字段包括 purpose、模板 id 和版本、
status、`error_code`、usage、延迟、`state_sha256`、完整概率、`cache_hit`、
`truncated`、`fake`、provider、model。原始 state 默认不落，只有
`experimental.judgment.audit_raw_state` 为真时才落。影子通道发
`judgment_shadow`，字段是 `kind`、`existing_verdict`、`shadow_answers`、
`agree`、`status`、`latency_ms`、`state_sha256`——没有代码原文，也没有请求文本。

dispatcher 信封 `log_host_call(method="judge")` 仍会记下 RPC spec，包括 state。
那是和命名事件 `judgment` 分开的另一处审计面。

Session package 导出时可附带 `judgment_manifest.json`（用过的能力、后端、
模型、模板版本、调用次数和 token 数）。**不含 key**。

## 托管、隐私、价格

打开这一层之前先看清楚，敏感实验数据尤其如此：

- TypeSafe Jev 服务**托管在美国**。
- 隐私政策承诺**不用输入训练模型**。
- 隐私政策**没有写明保留期限**。
- **零数据保留（ZDR）只对企业账户开放。**
- TypeSafe Jev 目前是 **early access**（需要排 waitlist）。离线开发用 loopback
  假端点；没有 key 时 OpenAI4S 的其余部分不受影响。
- 输入 **$0.042 / 百万 token**，输出免费。用量仍然走与 `host.llm` 相同的预算准入。
- **敏感数据不要开启。**

披露文案写在 `openai4s/judgment/disclosure.py`。

## 已知限制

这些是 Jev 和本实验的性质，不是临时缺陷：

- question 的 key 不会发给模型。每条 `instructions` 必须自成一体，并写明引用的 `state` 字段。
- Choice 最多 255 个选项。更大的目录要先收窄（Skill 推荐会按 bioSkills 领域分层）。
- Noul 只返回 P(yes)，没有 confidence。
- Score 有 2–10 档，并保留完整分布。相同均值可能来自完全不同的分布，要保留分布。
- confidence 由概率分布算出来，不等于正确率。阈值按模板、按语言在开发集上校准。
- state 加最长问题上限 32k token。Service 层截断并打上 `truncated`。
- 计数、算术、数值接近度、日期比较全部在代码里做。只把离散的语义问题交给 Jev。
- 对抗性内容能左右答案。安全相关只做 shadow。置信度永远不能作为授权依据。
- 英文问题和 criteria 效果最好，CJK 较弱。模板用英文写；state 可以含中文。评测按语言分开报告。
- 限流（250k token/s、1200 请求/分钟）和 429/529 重试都在总时长预算内（默认 3 秒）。不同 state 的 Host 并发上限是 4。
- SDK 变动快。本仓库只依赖 HTTP 契约；校验失败就丢弃整份 payload。
- 本树还没有收集 Jev 在冻结评测集上的 live 数字。毕业要等这些运行、国内网络 p95，以及维护者签字。在那之前把它当实验，而不是词法检索或 DOI 核验的替代。
- `format_tool_result` 在词法 `search_skills` 列表非空时仍然只把词法列表给模型看。实验性 chip 是工作台投影。词法一无所获时，模型才能在工具观察里看到语义推荐。
- 两条影子队列（`shadow.py` 和 `task_mode_shadow.py`）仍是两个模块。默认关闭时不启动线程。

## 毕业标准

每项能力单独毕业。去掉 Experimental 徽标需要同时满足：

1. 在预先登记的测试集上达到**打开测试集之前就写下来的**质量门槛；
2. 从国内网络测得的附加 p95 延迟在门槛以内；
3. 连续一个发布周期没有归到本层的 P0/P1；
4. 维护者签字。

`safety_shadow` 毕业也只是保留 shadow，进入 enforcement 必须另写计划。

评测协议里的占位门槛（开 `--split test` 之前要冻结）：`skill_suggest` 错误推荐率相对词法 B0 至少降低 30%，中文 top-3 召回至少 0.7，国内附加 p95 至多 1.5 s；`literature_check` 高置信错误至多 3%，人工复核比例至多 30%，总成本不高于 LLM 对照。

## FAQ

### 国内网络怎么办？

没有内置中继。客户端直连 `api.typesafe.ai`。可以像其他标准库 `urllib` 客户端一样，在进程环境里放 HTTPS 代理。`OPENAI4S_EGRESS=allowlist` 模式下，用 `host.request_network_access` 授权 `api.typesafe.ai`（Network 面板没有判断层分组可开——v1.3 从未新增分组）。预期延迟更高、`unavailable` 更多；调用方已经把该状态当成「没有推荐 / 需要复核」，而不是崩溃。在你实际使用的网络上测 p95，再决定这一层能不能承重。

### 能不能不用 TypeSafe？

可以。设 `OPENAI4S_JUDGMENT_PROVIDER=llm`（或 Store setting
`experimental.judgment.provider=llm`）。当前配置的主模型回答同一套带类型的问题。
每条结果都是 `calibrated=false`。TypeSafe 失败会变成 `unavailable`，**不会**去调
`chat()`。把它当作显式对照基线，而不是 Jev 概率的即插即用替代。

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

### W5 — 2026-09-21

合入 `w5-a-docs-release`（`3cdb34d9` → `3e8001c7`）：本文档成为操作说明，新增
`docs/experimental-judgment-removal.md` 和 `docs/release-notes-judgment.md`，
并在 `configuration.md`、`security.md`、`skills.md` 和两份根 README 里各加了一节。

发布验证。本机能跑的独立 CI 门禁全部通过：冻结响应形状、路由契约、`uv build` 之后的
`verify_release_artifacts.py`（wheel 里带着 `openai4s/judgment/` 的全部 23 个文件、
bioSkills 领域索引和 `skills/text-features/`）、npm 包检查（605 个 Skill，6.5 MB），
以及开关关闭时的 `browser_smoke.mjs` 和开关打开时的 `browser_judgment.mjs`。
`container_smoke.sh` 没有跑：本机没有 Docker，由 CI 执行。

默认关闭在全新数据目录上确认：`GET /api/v1/experimental/judgment` 报告总开关关闭、
来源 `default`，每项能力关闭、来源 `master_off`。在这个 daemon 上跑完一次真实浏览器会话——
7 个 frame、109 次 host 调用、12 次 cell 执行——数据库 82 张表里没有任何一行含 "judgment"，
daemon 日志里也没有。

移除已演练。在一次性分支 `chore/judgment-removal-dryrun`（从未合并、从未推送）上照清单执行：
删除 138 个文件、32,696 行。目录文档门禁报告 166 个目录、1,590 个文件，与 W5-A 自己的演练一致；
完整离线套件以 9,621 条测试通过——少了 330 条，正是判断层自己的测试。有两处修正写回了清单：
literature-review 的 SKILL.md 里那节实验内容是文件的**最后一节**，后面没有别的标题；
它的 TypeSafe `third_party` 条目紧贴在 frontmatter 的闭合 `---` 之上，一直切到「下一个条目」
就会把分隔符一起删掉，loader 随即把这个 Skill 的网络模式报成 `unknown`。

集成修正：`b67e6902` 补完了 W4 的精选数调整里计数契约没有钉住的那一行 README_zh；
`a8389209` 修正了移除清单；`f85d3b95` 在发布说明里补上显式的 LLM 后端——
说明列出了每项能力，却漏了这个选项。
