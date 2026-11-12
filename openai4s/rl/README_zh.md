# RL runtime

[English](README.md)

OpenAI4S 可以作为 agentic 强化学习的**环境**：外部 Trainer 采样策略的下一步动作，
OpenAI4S 用真实的工具、权限、内核与 Artifact 执行这个动作，本包把发生的一切记录成
版本化的 *Science Trajectory*，Trainer 再把它转成训练样本。

这个模块只负责边界。它**不包含损失函数、优化器和奖励模型**——这些留在 Trainer 里；
它**只用标准库**，所以引入它不会给核心增加任何依赖。

## 这个包是什么

Trainer 通常只有两个糟糕的选择：导入 Agent 的 Python 内部实现，或者去读它的数据库。
这个包的存在就是为了两者都不必做。所有数据都以纯 JSON 穿过边界：

- **reset** 一个任务，拿到第一条 observation；
- **step** 提交一次采样回复，拿到执行结果和下一条 observation；
- **finish** 结束 Episode，得到可导出为 JSON、JSONL 或 ATIF 的终态轨迹；
- **release** 释放 Episode 及其 workspace。

职责划分是严格的：

- OpenAI4S 继续负责路由、权限、内核、完成判定、取消和 Action Ledger。这里不会启动
  第二套执行循环。
- Trainer 负责采样、奖励、归属（attribution）和优化。
- **执行事实只追加、不改写。** Reward Annotation 和 Cost 是独立、带版本的记录，
  所以重新打分、甚至整体替换 Verifier，都不会改写历史。
- Agent 不能给自己打分：没有自评分的 Host API。每一个 reward 都由可信 Verifier
  或 Trainer 写入。

规范的数据格式见[轨迹契约](../../docs/rl/trajectory-contract-v1_zh.md)。

## 一次 rollout 如何运行

1. **`reset`** 通过你的工厂创建全新的 `AgentEngine` 和 `TrajectoryRecorder`，以及
   独立的 workspace。Reset 元数据记录初始 Artifact 引用、配置和预算，但不暴露绝对
   workspace 路径。
2. **`observe`** 返回策略应当看到的精确消息，以及当前原生工具的声明。
3. **`step`** 把采样得到的回复交给引擎；引擎执行 code cell、原生工具 batch 或
   finalize，然后返回结果和下一条 observation。
4. **`finish`** 结束 Episode 并冻结轨迹。`export` 返回带 schema 版本、并对精确字节
   计算 SHA-256 的载荷。
5. **`release`** 销毁环境与 workspace。

一个 Episode 对应一个引擎、一个 recorder 和一个 workspace。Segment 把动作组织成有意义
的阶段：默认每个路由后的动作生成一个机械 Segment；科学代码也可以用
`host.begin_segment(...)`、`phase.observe(...)`、`phase.close()` 打开跨越多个决策的
显式阶段。

## 快速开始

### 在本地跑一次 rollout

```python
from openai4s.agent.engine import AgentEngine
from openai4s.rl import Episode, PolicyAdapter, RolloutEnvironment, RolloutWorker

def build_engine(reset):
    """按你平时的方式组装引擎：model port、action executor、工具、最大轮数。"""
    return AgentEngine(model=..., executor=..., max_turns=reset.max_turns)

worker = RolloutWorker(
    RolloutEnvironment(build_engine, workspace_root="/data/rollouts"),
)

def sample(observation, *, tools):
    """任意 ``(observation, *, tools) -> ModelReply | mapping`` 可调用对象都可以。

    它也可以是 async。用 ``PolicyAdapter(provider, "vllm", "step-1200")``
    包装推理服务客户端即可记录 behavior policy 身份。
    """
    return my_inference_server.chat(observation, tools=tools)

trajectory = worker.run(
    Episode("ep-1", "tune the reaction yield", "session-1", created_at=0),
    [{"role": "user", "content": "Tune the reaction yield."}],
    sample,
)
print(worker.export(trajectory, "jsonl").sha256)
```

`worker.run()` 返回终态 `Trajectory`。导出是确定性的，并带有 schema 版本和字节哈希，
所以训练分片无需重跑即可校验。

### 通过 JSONL 为 Trainer 提供服务

如果 Trainer 在另一个进程、另一台机器或 GPU 主机上，就用 stdin/stdout 启动 worker，
再用内置客户端与它通信。工厂是管理员配置，绝不能由 RPC 指定：

```bash
python -m openai4s.rl.serve my_deployment:build_environment
```

```python
from openai4s.rl import RolloutClient

client = RolloutClient(
    ["ssh", "gpu-box", "python", "-m", "openai4s.rl.serve", "my_deployment:build_environment"]
)
client.reset({"episode": {...}, "messages": [{"role": "user", "content": "..."}]})
```

传输是一行一个 JSON 的单流协议。日志走 stderr，stdin/stdout 只用于协议。`step`、
`recover`、`result`、`step_result` 都是幂等的：完全相同的重试会返回已记录的响应。
如果传输在请求中途断开，客户端会把状态报告为 unknown，你必须去查询持久化结果——
客户端绝不会自动重提副作用。

## 从 rollout 到训练数据

`DatasetSnapshot` 在内容寻址的边界上冻结一条终态轨迹；`RolloutDataset` 再为每个
`PolicyDecision` 投影出一个 `TrainingSample`：

```python
from openai4s.rl import DatasetSnapshot, RolloutDataset

snapshot = DatasetSnapshot.from_trajectory(trajectory, "snap-ep1", created_at=1)
dataset = RolloutDataset.from_trajectory(trajectory, snapshot)
open("shard.jsonl", "w").write(dataset.jsonl())
```

每个样本包含 observation、decision（sampler 提供时会带上 prompt token ID 和 loss
mask），以及只与该 decision 显式关联的执行事件和 reward。Segment 级和 Episode 级
reward 不会进入逐决策样本：把它们拆成逐 token 目标属于归属算法，是 Trainer 的选择，
本包不会替你猜。本包也不选择 advantage estimator。

## 奖励、成本与预算

- **奖励。** `apply_verifier(recorder, verifier)` 针对不可变快照运行与 Provider 无关的
  `RewardVerifier`，校验身份、作用范围、关联关系和证据引用，然后才追加带版本的
  Annotation。
- **成本。** `cost.py` 只接受明确经过认证、且绑定到精确 compute job 与 receipt 的
  Provider 成本证据。缺失的测量保持 unknown；job 的实际耗时绝不会被改称为 GPU 时间
  或账单金额。
- **准入与预算。** `admission.py` 在开工前绑定 Provider receipt，并把排队等待与计算
  时间分开报告。`budget.py` 把预估预留与实际用量分开，并明确 hard/soft 限额。两者都有
  持久化封装，守护进程重启后不会重建或重复扣除同一项预留。

## 安全与失败行为

- 持久化 step 是 claim-once：已接受且已有记录的请求会返回原响应，而已 claim 未响应
  或 legacy-unknown 的工作绝不会自动重放。
- 恢复是 fail-closed 的。被中断的 Episode 以 `recovery_required` 和持久 receipt 报告，
  不会替 Trainer 重跑执行。
- 取消按精确 owner 生效。请求中断后，会先等真实执行退出，再释放 lane 和 workspace；
  卡住的执行保持占用，而不是被静默脱离。
- 导出与 RPC 流都很严格：格式错误或超长的行会得到结构化错误，但不会让流失步；正在
  运行的 Episode 不能被导出。

## 模块地图

| 文件 | 作用 |
|---|---|
| `__init__.py` | 公共记录与适配器；较重的模块按需惰性加载。 |
| `models.py` | 版本化 JSON 记录：Episode、Observation、PolicyDecision、Segment、ExecutionEvent、RewardAnnotation、CostRecord、RunReceipt、Trajectory。 |
| `recorder.py` | 线程安全的追加式 recorder，幂等写入，确定性 JSON/JSONL 导出。 |
| `segments.py` | Segment 生命周期：每个路由动作一个机械 Segment，另支持跨决策的显式科学阶段。 |
| `verifier.py` | 与 Provider 无关的 `RewardVerifier` 协议和 `apply_verifier()`；校验身份、范围、关联与证据。 |
| `snapshot.py` | `DatasetSnapshot`：内容寻址的终态视图，冻结轨迹哈希与事件边界。 |
| `dataset.py` | `RolloutDataset` 与 `TrainingSample`：逐决策样本，带 token ID、loss mask 和显式 reward 关联。 |
| `requests.py` | 严格请求规范化、幂等 step 语义和持久终态 receipt。 |
| `environment.py` | `RolloutEnvironment`（每个 Episode 全新的引擎、recorder 与 workspace）以及 JSON 安全的 `RolloutEnvironmentAdapter`。 |
| `rollout.py` | `RolloutSession`：面向 Trainer 的 reset/observe/step/finish 生命周期，复用未修改的 `AgentEngine`。 |
| `agent_sink.py` | 把 `AgentEngine` 事件流接入这些记录，同时不取代 Action Ledger。 |
| `policy.py` | `PolicyProvider` 与 `PolicyAdapter`：规范化采样回复并记录 behavior policy 身份；缺失的 logprob 保持缺失。 |
| `worker.py` | `RolloutWorker`：单 Episode 从 reset 到 release，确定性 JSON/JSONL/ATIF 导出，以及显式恢复结果。 |
| `replay.py` | `TrajectoryReplayer`：支持 cursor 的事件、决策与 Artifact 重放；执行重放需要显式 runner。 |
| `atif.py` | `ATIFExporter`：面向 Harbor 的确定性 ATIF-v1.8 投影，以及正向终态证据校验。 |
| `host_events.py` | 有界的 Host RPC 生命周期事实（`started`、`completed`、`failed`），用于把嵌套执行关联到决策。 |
| `projector.py` | 只读投影执行事件已明确引用的持久化事实。 |
| `cost.py` | 只接受已认证、绑定 receipt 的 Provider 成本证据；绝不估算 GPU 时间或账单。 |
| `admission.py` | 与 Provider 无关、幂等、绑定 receipt 的资源准入，排队时间与计算时间分开。 |
| `budget.py` | hard/soft 预算账本，区分预估与实际用量；未知 allocation 保持预留直到 reconcile。 |
| `collector.py` | `RolloutCollector`：固定并发、有界的 pending/result 队列、按完成顺序返回。 |
| `async_lane.py` | 每个 Episode 的同步生命周期跑在一条专属线程上，采样仍留在 event loop。 |
| `client.py` | `RolloutClient`、`AsyncRolloutClient` 与 `RemoteRolloutWorker`：有界 JSONL 客户端。 |
| `serve.py` | 可信入口：`python -m openai4s.rl.serve module:factory`。 |
| `rpc.py` | `RolloutRPC` 与标准库 JSON-lines 服务端；每行输入对应一条结构化响应。 |
