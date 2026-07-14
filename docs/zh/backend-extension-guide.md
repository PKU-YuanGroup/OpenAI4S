---
title: 后端扩展指南
description: 新增控制平面、内核、存储与 Web 行为时应将代码放在哪里。
outline: deep
status: current
audience: [contributors]
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# 后端扩展指南

> 已于 2026-07-14 对照仓库修订版 `a92e736` 验证。

OpenAI4S 有两个行动平面；新增行为应归属何处，取决于一条规则：

- 对于编排、权限、外部服务、元数据或需要人工审批边界的操作，使用原生 JSON `Tool`。
- 对于计算、探索、分析、模拟和长时间运行的科学任务，使用 Python/R Code-as-Action。
- Engine 级完成使用闭合 schema 的 `finalize_response` action；Python 也可以在科学 Cell 内通过
  `host.submit_output(...)` 完成任务。两者都不是注册表中的 `Tool`，而且绝不能把普通文本或工具结果
  推断为完成信号。即使先前步骤运行过 Cell，Engine 也不会仅因此拒绝单独出现的
  `finalize_response`。

## 依赖关系图

```text
provider wire -> AgentEngine -> action router -> append-only Action Ledger
                                  |       |             |
                                  |       |             +-> FinalizeAction
                                  |       +-> Python/R kernel
                                  |                 |
                                  +-> Tool class    +-> synchronous host RPC
                                         |                      |
                                         +--- HostDispatcher ---+
                                                    |
                                   host service classes / repositories
                                                    |
                                           Store compatibility facade
```

`HostDispatcher` 负责共享策略封装层：Host RPC 参数解码、权限、人工审批、审计/重放、注入筛查，
以及 UI 活动事件。
业务行为应放在工具类或服务类中。`Store` 保持为兼容的公共 facade，并负责连接和迁移；SQL 行为应放在
共享该连接与锁的领域 repository 中。

## 添加原生控制工具

在 `openai4s/tools/` 下新建一个模块。类中必须包含其 schema、安全策略与行为，让维护者只需打开
一个文件就能理解该能力。

```python
from openai4s.tools.base import Tool


class CreateExperimentTool(Tool):
    name = "create_experiment"
    host_method = "create_experiment"
    description = "Create an approved scientific workflow record."
    parameters = {
        "properties": {
            "type": {"type": "string"},
        },
        "required": ["type"],
    }
    read_only = False
    requires_approval = True
    permission_target_key = "type"
    side_effect_class = "metadata_write"
    resource_key_prefix = "experiment"
    resource_target_key = "type"

    def execute(self, context, arguments: dict) -> dict:
        return context.invoke(self.host_method, {"type": arguments["type"]})
```

然后把类（而不是预先创建的实例）加入 `openai4s/tools/registry.py` 中的 `TOOL_TYPES`。
注册表是唯一的内置组合点，并以确定性顺序创建运行时实例。

注册与调用契约如下：

- `bash` 和 `submit_output` 永远不能成为原生工具；
- 工具名称必须能够在所有受支持的 provider 之间移植；
- 网络工具必须声明不可信结果筛查；
- 模型发起的调用必须经过 `Tool.invoke()` 和 dispatcher；应用代码不得直接调用 `execute()`
  来绕过策略；
- 变更状态的工具要声明有效的副作用类别和带命名空间的资源键；除非可信扩展明确选择开放 schema，
  否则拒绝未知的输入属性。

`requires_approval` 默认为 true。将其关闭的类必须记录并测试自身的安全边界；注册表保留由类定义的
这项策略，而 dispatcher 负责在调用时强制执行。`read_only` 与 `resource_keys()` 还决定批次调度：
只有队列开头一组互不冲突的只读调用可以并行执行；变更状态或能力未知的调用构成顺序执行屏障。
请保守声明这些字段；缺少资源标识时按冲突处理。

应为类行为与策略元数据添加直接测试；如果 wire 契约发生变化，还要为 provider-neutral 的调用/结果组
添加 Engine 测试。如果工具会修改工作区文件，请声明 `writes_files = True`。Web 控制适配器随后会为
每个原生调用分别创建快照，并把每个发生变化的文件/版本注册为 Artifact。不要在 dispatcher 中加入
Artifact 捕获：内核侧的 `host.write_file()` 已由 Cell 事务捕获，否则会被重复注册。

`finalize_response` 有意不放入 `TOOL_TYPES`。它的 schema、校验与完成记录位于
`openai4s/agent/finalize.py`；不要再注册同名工具，也不要让插件代码绕过 Engine 路由。

### 会话内创作的动态工具

由模型/会话创作的工具使用现有的 `DefineDynamicTool` 控制路径，而不是 `register_tool()`。
定义会经过 schema 检查、内容哈希，并在强制启用的 OS sandbox 中结合内核环境 allowlist 进行测试；
随后通过带会话 TTL 的可信代理对外提供。提升到更大作用域是单独的审批操作。模型创作的代码绝不会
被导入 Host 进程。如果 OS sandbox 不可用，定义操作将以 fail-closed 方式失败。

## 添加内核内 `host.*` 能力

面向 worker 的函数签名应放在 `openai4s/sdk/`。像 compute 这样内聚的命名空间应使用独立模块；
`sdk/host.py` 负责组合并以兼容方式重新导出它。

Host 侧实现应放在 `openai4s/host/` 下的类中：

```python
class ExperimentService:
    def __init__(self, store_provider):
        self._store_provider = store_provider

    def create(self, spec: dict) -> dict:
        store = self._store_provider()
        return store.create_experiment(**spec)
```

在 `HostDispatcher.__init__` 中只构造一次该服务；如果会话状态可在运行时替换，请使用小型 provider
回调。现有的 `_m_<method>` 方法只保留为轻量兼容适配器：

```python
def _m_create_experiment(self, spec: dict) -> dict:
    return self._experiment_service.create(spec)
```

仅在既定的 soft-fail 契约中返回 `{"error": message}`。未捕获的异常会在内核协议边界被转换。
不要在服务内部重复权限、审计、重放或注入策略；所有调用本来就会经过 dispatcher 封装层。

## 添加持久化数据

在 `openai4s/storage/` 下创建职责集中的 repository。Repository 接收现有 SQLite 连接、现有
`RLock` 和时钟回调；它们不会为应用写入另开连接。

```python
class ExperimentRepository:
    def __init__(self, connection, lock, *, clock_ms):
        self._connection = connection
        self._lock = lock
        self._clock_ms = clock_ms

    def get(self, experiment_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM experiments WHERE experiment_id=?",
                (experiment_id,),
            ).fetchone()
        return dict(row) if row else None
```

`Store` 负责 schema 创建与迁移，使用共享连接/锁构造 repository，并公开一个轻量转发方法。
跨越多个 aggregate 的复合写入必须维持现有的单锁和提交边界。如果旧代码会动态调用另一个
`Store` 方法，应注入晚绑定 lambda，而不是固化一个绑定方法并悄然破坏子类/monkeypatch 兼容性。

请遵守 Store generation 语义：`Store.close()` 是幂等的，并且只从 `get_store(path)` 缓存中移除
这个确切实例。可能比 Store 存活更久的服务必须在每次操作时解析当前 Store/repository；绝不能持有
由某个连接支撑的 repository，因为配置重载或测试清理可能关闭该连接。默认 `SkillLoader` 的能力
适配器是参考模式。

Repository 测试应锁定 SQL 可见结果、提交/回滚边界、排序、时间戳求值、JSON fallback 和旧版错误
结构。默认测试必须继续离线运行。

## 扩展 Skill 生命周期

内置 recipe 应放在 `skills/` 下，并保持只读。用户创作的文件应放在
`<data_dir>/user-skills`；对于符号链接/路径越界和名称冲突，应直接拒绝，而不是遮蔽内置 Skill。
内核内 Host 编辑器使用 `draft` 来源，并显式提升为 `personal`；Web Customize 以 `user` 来源写入
完整文档。发现过程必须保留这些用户来源，并且绝不能允许用户空间的 frontmatter 值冒充
`openai4s` 信任来源。能力启用状态必须持久化并限定作用域；默认 loader 必须像上文所述跟随当前
Store generation。

## 扩展 Action Ledger

`action_groups`、`action_events` 和 `execution_attempts` 是规范的运行时历史。聊天消息、Notebook
行、活动卡片和 Action Timeline 都是 projection。执行前先打开 group，按顺序追加每一项结果
（包括校验/权限失败），在延迟启动运行时之前分配 Cell attempt，并追加 terminal event。
绝不要更新旧事件，让一次重试看起来仿佛原先就已成功。

Provider wire 元数据和原始参数保留在持久化审计/重放记录中。面向研究者的服务必须使用经过脱敏且
限定字段范围的 `ActionTimelineService`；不要在新的 REST/WS payload 中暴露 `wire_state`、原始参数
字符串、凭据或不受限的结果。其页面大小限制为 1–500：首次读取返回最新窗口，`before_ordinal`
向更早的记录移动，`after_ordinal` 向更新的记录移动。游标必须是非负数并且互斥，projection 会返回
明确的截断/游标元数据。消费者不得把单个字段的截断误当作历史分页。

## 添加 Web 会话行为

HTTP 和 WebSocket 代码只是适配器。有状态行为应放在 `openai4s/server/` 下的服务中，并通过职责
狭窄的 protocol/callback 与持久化、内核生命周期、事件广播和配置交互。如果测试或集成依赖
`SessionRunner` 的私有转发方法，可以继续保留，但算法本身应在服务模块中清晰可见。

保持事件 payload 的键以及对顺序敏感的生命周期规则不变。凡是修改内核执行、Host RPC、Artifact
捕获、review、streaming 或 resume，都需要有针对性的测试，并且要针对 `./start.sh` 运行真实浏览器
流程。

科学执行必须进入 `WebExecutionCoordinator`：提交 owner（`agent`、`user_repl`、`lifecycle` 或
`recovery`），等待 FIFO 准入，绑定确切的 `KernelLease`，并在发布前标记为 finalizing。
取消和中断适配器必须要求确切的 execution ID 与 owner 组合；绝不能重新引入会话级的宽泛中断。

Checkpoint/revert、recovery projection、Timeline、Notebook 导出和 renderer 选择应置于
`SessionDomainService` 之后。它们的算法已经独立于 HTTP 实现，Gateway 路由只是通往该服务的轻量
适配器。应扩展这些适配器，而不是在 `gateway.py` 中重复 CAS、journal、`.ipynb`、Timeline 或
renderer 逻辑。Recovery 状态和全部五个 action descriptor 都是公开信息。只有 `restore`、`retry`
和 `restart_fresh` 是变更状态的操作；它们必须继续通过一个确切且经过协调的 Gateway execution
ticket 进入系统。

## 完成定义

对于每项后端扩展或提取工作：

1. 行为由类文件承载；registry/dispatcher/facade 只负责组合或转发。
2. 核心导入继续仅使用标准库。
3. 公共 SDK、`host.*`、CLI、REST/WebSocket、SQLite 与已保存会话的契约保持兼容，或者提供明确的迁移。
4. Terminal 行为保持显式：由 Engine 拥有的 `FinalizeAction`，或者由 `host.submit_output()` 作为
   Python Cell 内唯一发出的完成信号。普通文本与普通工具结果绝不会完成一次运行。
5. 运行有针对性的测试和完整离线测试套件；若涉及会话、内核、RPC、Artifact 或 UI 行为，还要运行
   浏览器流程。
6. 每次提交一个内聚变更。

避免模块级工具单例、重复的 agent loop、Host 侧 shell 执行、独立的 repository 连接、让 provider
响应类型泄漏进 `AgentEngine`，以及把科学计算伪装成 JSON 工具。
