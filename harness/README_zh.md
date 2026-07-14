# Harness（确定性场景与评测层）

[English](README.md)

本目录是带版本、仅使用标准库的 prototype 场景层，用于 scripted provider、确定性故障注入、规范化 trace、离线契约 eval 与 opt-in smoke test。通用 runner 只校验 Harness 自身的 schema/event/fault 循环，并且刻意不导入生产 runtime；当前只有 `characterize.py` 和 action-routing eval 会在 fake 边界后触及选定的生产入口。

确定性的 `tier:pr` 场景是 CI 必需的 Harness 自契约门禁。pytest suite 还会在进程内验证 CLI 门禁（`tests/test_harness_contract.py`）；独立 CI step 让契约门禁不依赖 pytest collection（`pyproject.toml` 刻意只收集 `tests/`）。真实模型质量 eval 与需要外部资源的 smoke test 始终显式 opt-in。

## 为什么 `harness/` 与 `tests/` 分开

`tests/` 是**正确性门禁**：每个 PR 都必须通过的离线 pytest suite。它利用 fake 和临时数据目录断言 runtime 当前行为，包括内核协议、Host API、Gateway serializer 与安全 gate；从不需要网络、秘密、GPU、SSH、实验室硬件或真实 LLM。

`harness/` 是**prototype 评测与场景层**：提供 scripted-loop 场景、规范化 trace、质量 eval 和 fake 平台 provider 数据。当前通用 runner 不是端到端 Agent/Gateway adapter；`surface`、permission 和 fixture 是经校验的场景字段，而非已执行的生产集成。Scripted 自契约运行是必须通过的 pass/fail 门禁；带分数的质量运行可以更慢，只有明确 opt-in 时才可使用外部资源。

判断规则：

- 针对某个具体契约的回归断言放在 `tests/`。
- 可复用 fake provider、可重放场景、golden trajectory 或计分 eval 放在 `harness/`。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | Harness 公共门面，导出场景 schema、loader、result 与 runner；生产包不会导入它。 |
| [`characterize.py`](characterize.py) | 在标准库 fake 边界后驱动选定生产入口，规范化观察行为，并生成经审阅的 r5 pre-change characterization，其中已知缺陷会显式标注。 |
| [`cli.py`](cli.py) | 实现 `run` 场景选择/校验和 `characterize` 比较/写入命令，并提供确定性退出码与摘要。 |
| [`faults.py`](faults.py) | 提供 fake 单调时钟、稳定 UUID、精确 occurrence 故障计划与结构化注入异常。 |
| [`normalize.py`](normalize.py) | 替换 trace 中易变的 UUID/时间/路径/端口，同时保留事件及因果顺序，并输出 canonical bytes。 |
| [`runner.py`](runner.py) | 运行与生产无关的 Harness scripted loop，记录规范事件、应用计划故障、检查 Harness invariant，并返回 trace digest；它不导入或驱动 Agent/Gateway runtime。 |
| [`schema.py`](schema.py) | 定义并严格校验场景、provider step、fault、expectation 与 event envelope 的版本化 JSON 契约。 |

## 直属子目录

| 目录 | 预期内容 |
| --- | --- |
| [`scenarios/`](scenarios/) | 声明式 Harness 场景：prompt、经校验的 fixture/permission 元数据、scripted provider step、fault、tag 与预期结果；它们尚非端到端 Agent/Gateway 运行。 |
| [`providers/`](providers/) | 对模型、compute、endpoint 或 lab 边界提供测试侧等价实现的 fake/offline 平台 provider。 |
| [`golden_traces/`](golden_traces/) | 用于精确比较和审阅有意漂移的参考 trajectory；它们是数据，不是可执行 replay。 |
| [`evals/`](evals/) | 离线 eval fixture 与计分代码，包括确定性的 Action routing 质量/契约评测。 |
| [`smoke/`](smoke/) | 针对平台或外部资源检查、必须显式 opt-in 的 runtime smoke 程序。 |

## 基本规则

- **默认离线。** `harness/` 中的内容默认不得需要真实网络、API key、GPU、SSH、Docker、浏览器或实验室硬件。确需资源的入口必须显式 opt-in，并使用 `pyproject.toml` 注册的相应 pytest marker：`external`、`network`、`live_llm`、`gpu`、`ssh`、`docker`、`browser`、`lab`。
- **禁止秘密。** Harness 默认必须无秘密运行，PR CI 不提供任何秘密。
- **不放生产代码。** Runtime 实现留在 `openai4s/`（以及 `openai4s_compute_provider/`）。通用 runner 保持自包含；显式命名的 characterization/eval adapter 可在确定性 fake 边界后导入选定的生产公共入口。
- **不得粉饰顺序。** Normalization 可替换易变值，但不能排序事件列表；并发场景比较明确因果关系和各 stream 内顺序，而不是捏造全序。
- **不得重放副作用。** Golden trace 是比较数据，不是可执行历史；场景 playback 只能调用声明的 fake。
- **Core 保持标准库。** Harness helper 不能给 core package 引入硬性第三方 import。
- **不要把测试搬来。** 现有 `tests/` 文件继续保留；未来迁移必须用独立 PR 和 collect-only 证明没有漏测。

## 必需本地门禁

从仓库根目录运行（Harness 不安装进 venv，`python -m` 通过当前工作目录解析）：

```bash
uv run pytest
uv run python -m harness.cli run --tier pr --offline
```

遇到无效 schema、缺少选中场景、重复场景 ID、invariant 失败、声明但未触发的故障或空 tier 时，CLI 以非零退出。Golden 绝不隐式更新；运行时修复确实改变 r5 pre-change characterization 时，需要显式重新生成并审阅 diff：

```bash
uv run python -m harness.cli characterize          # 与 golden 比较
uv run python -m harness.cli characterize --write  # 审阅后重新生成
```

## Trace 资产不能混用

- **Canonical run trace** 是 scripted model/action/permission/lifecycle 事件与确定性契约比较的目标记录。
- **Host-call tape** 保存成功 Host call 结果，用于离线 Notebook playback；它不是完整 trajectory 或 crash-resume 记录。
- **Live-model eval snapshot** 衡量文本/任务质量，不是确定性 CI 真值来源。

## 治理

Harness 修改遵守项目维护的 [Harness invariant](../CONTRIBUTING.md#harness-invariants)与离线测试策略。新行为应有确定性场景契约支撑；有意修改 golden 时必须显式审阅。
