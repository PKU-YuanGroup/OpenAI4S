# Harness（确定性场景与评测层）

[English](README.md)

Harness 用来回放场景。一个场景会脚本化模型本该说出的话，在指定的点上注入故障，把整次运行记成规范化的事件 trace，再拿这条 trace 去核对场景声明的预期结果。它覆盖的正是单元测试不太好写的那些问题：事情发生的先后顺序、一次运行花了几次模型调用，以及故障落在某个点的第三次访问上，和落在第一次访问上，结果是否不同。

这里的东西都带版本、只用标准库，也不在生产代码的 import 图里。通用 runner 只验证 Harness 自身的 schema/event/fault 循环，刻意不导入生产运行时；目前的例外只有 `characterize.py` 和 action-routing eval，而且它们触及选定的生产入口时，一律隔着 fake。

确定性的 `tier:pr` 场景是 CI 必需的 Harness 自契约门禁。pytest suite 还会在进程内验证 CLI 门禁（`tests/test_harness_contract.py`）；之所以在 CI 里另设独立 step，是为了让契约门禁不依赖 pytest collection（`pyproject.toml` 刻意只收集 `tests/`）。真实模型的质量 eval 与需要外部资源的 smoke test 始终要显式启用。

## 为什么 `harness/` 与 `tests/` 分开

`tests/` 是正确性门禁：每个 PR 都必须通过的离线 pytest suite。它用 fake 和临时数据目录断言运行时的当前行为：内核协议、Host API、Gateway 序列化器、安全门禁。它从不需要网络、secret、GPU、SSH、实验室硬件或真实 LLM。

`harness/` 是原型评测与场景层：提供脚本化循环的场景、规范化 trace、质量 eval 和假的平台 provider 数据。今天的通用 runner 还不是端到端的 Agent/Gateway adapter：`surface`、permission 和 fixture 只是经过校验的场景字段，并没有真正跑通生产集成。脚本化的自契约运行是 pass/fail，必须通过；带分数的质量运行可以慢一些，也只有在显式启用时才允许使用外部资源。

判断规则：

- 针对某个具体契约的回归断言放在 `tests/`。
- 可复用 fake provider、可重放场景、golden trajectory 或计分 eval 放在 `harness/`。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | Harness 对外的一张脸：场景 schema、loader、result、runner。生产包永远不会导入它。 |
| [`characterize.py`](characterize.py) | 导入选定的生产入口，在标准库 `unittest.mock` 的 fake 后面驱动它们，把它们真正做了什么规范化成经审阅的 r5 pre-change characterization。快照记录了已知缺陷的地方会写清楚；缺陷被修掉时，这些快照本来就该跟着变。 |
| [`cli.py`](cli.py) | 两个子命令。`run` 按 tier 挑出场景，校验并执行，一个场景打印一行，末尾再加一行摘要；`characterize` 把 r5 characterization 与 golden 比对，或者重新写出 golden。退出码是确定的。 |
| [`faults.py`](faults.py) | 一次运行想要可重复所需要的东西：单调时钟（sleep 只是把它往前推）、按调用顺序发出的 UUID 形状标识符，以及一份故障计划。每条声明的故障只触发一次，落在指定点的第 N 次访问上；抛出的失败是结构化的，不是一个裸异常。 |
| [`normalize.py`](normalize.py) | 把 trace 里易变的 UUID、时间、路径和端口换掉，输出用于逐字节比较的规范编码。标识符在第一次出现时才拿到占位符，所以 parent 链仍然有意义，交换两个事件也会改变输出。事件列表从不排序。 |
| [`runner.py`](runner.py) | 跑一个场景的脚本化循环，记录规范事件 trace，途中按计划注入故障，返回 trace digest 之前先检查声明的 invariant。这是 Harness 中与生产无关的那一半：它不导入、也不驱动任何 Agent/Gateway 运行时代码。 |
| [`schema.py`](schema.py) | 一个场景的版本化 JSON 契约：provider step、fault、permission、expectation，以及一次运行会发出的 event envelope。校验很严格。碰到未知字段，或者 schema 版本不是当前版本，加载会直接失败，而不是被悄悄忽略。 |

## 子目录

| 目录 | 预期内容 |
| --- | --- |
| [`scenarios/`](scenarios/) | 一个场景一个 JSON 文件：prompt、要按顺序回放的 provider step、要注入的 fault、决定它属于哪个 tier 的 tag，以及预期结果。fixture 和 permission 元数据只做校验，尚未真正执行，所以这些仍然不是端到端的 Agent/Gateway 运行。 |
| [`providers/`](providers/) | 离线的假 provider，顶替一次运行本来要跨过的平台边界：模型、compute、endpoint、lab。 |
| [`golden_traces/`](golden_traces/) | 经审阅的参考 trajectory，留着做精确比较，也留着在漂移确属有意时逐行审阅。它们是拿来读的数据，不是拿来跑的 replay。 |
| [`evals/`](evals/) | 离线 eval fixture 和给它们计分的代码，其中包括确定性的 action-routing 质量与契约评测。 |
| [`smoke/`](smoke/) | 用来检查平台或外部资源的 runtime smoke 程序。不显式启用，这里什么都不会跑。 |

## 基本规则

这里的一切都要能离线跑，也要能在没有任何 secret 的情况下跑通，默认的 PR CI 本来就不提供 secret。`harness/` 里的内容不得需要真实网络、API key、GPU、SSH、Docker、浏览器或实验室硬件。确实绕不开这些资源的入口只能显式启用，并挂上对应的 pytest marker（`external`、`network`、`live_llm`、`gpu`、`ssh`、`docker`、`browser`、`lab`），也就是 `pyproject.toml` 里注册的那几个。

这里同样不放生产代码。运行时实现留在 `openai4s/` 和 `openai4s_compute_provider/`，通用 runner 保持自包含。只有那几个显式命名的 characterization 与 eval adapter 可以导入选定的生产公共入口，而且必须隔着确定性 fake。Harness 的 helper 也不能给核心包塞进硬性的第三方 import。

还有两条规则，护的是记录本身。规范化可以替换易变的值，但不能给事件列表排序：并发场景比较的是明确的因果关系和每条 stream 内部的先后，而不是捏造一个全序。还有，golden trace 是用来比对的数据，不是可执行的历史，场景回放只能调用声明过的 fake，别的一概不行。

最后，`tests/` 放着别动。现有的测试文件留在原处；将来真要迁移，得走独立 PR，并用 collect-only 证明没有漏掉任何测试。

## 必需本地门禁

开 PR 之前，从仓库根目录跑这两条命令（Harness 不会装进 venv，`python -m` 靠当前工作目录解析它）：

```bash
uv run pytest
uv run python -m harness.cli run --tier pr --offline
```

遇到无效 schema、选中的场景不存在、场景 ID 重复、invariant 失败、声明了却从未触发的故障，或者空 tier，CLI 都会以非零退出。Golden 绝不隐式更新：运行时的有意修复确实改变了 r5 pre-change characterization 时，要显式重新生成，并逐行审阅 diff：

```bash
uv run python -m harness.cli characterize          # 与 golden 比较
uv run python -m harness.cli characterize --write  # 审阅后重新生成
```

## Trace 资产不能混用

这里有三种记录挨在一起，回答的却是不同的问题。Canonical run trace 是目标记录，服务于脚本化的 model、action、permission、lifecycle 事件，也是确定性契约比较真正读的东西。Host-call tape 保存成功的 host call 结果，好让 Notebook 能离线回放；它既不是完整 trajectory，也不是崩溃恢复记录。Live-model eval snapshot 衡量文本和任务质量，它不是 CI 能倚仗的真值来源。

## 治理

Harness 的改动遵循项目维护的 [Harness invariant](../CONTRIBUTING.md#harness-invariants) 与离线测试策略。新行为应当有确定性的场景契约兜底；有意改动 golden 时，必须显式审阅。
