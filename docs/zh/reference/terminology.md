---
title: 术语表
description: OpenAI4S 文档采用的规范术语与身份边界。
status: current
audience:
  - contributors
  - operators
  - users
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# 术语表

| 术语 | 含义 |
|---|---|
| **动作（Action）** | 从一次模型回复中选出的唯一执行路线：原生工具批次、Engine 自有的终结动作、一个完整的 Python/R Cell，或无动作。 |
| **原生工具批次（Native tool batch）** | 按顺序执行的 provider-native JSON 调用，用于编排、元数据、外部服务、权限和工作流控制。 |
| **FinalizeAction** | 由 Engine 持有并校验的唯一 `finalize_response` 调用；它不是注册表中的 Tool。 |
| **Cell** | 由持久语言 worker 执行的一段完整 fenced Python 或 R 程序。 |
| **科学运行时（Science runtime）** | 用于计算和分析的 Python、R Cell 通道。 |
| **Host RPC** | Python worker 在 Cell 尚未结束时向 Host 发出的同步请求。正常线序列为 `host_call → host_response`。 |
| **Tool** | 已注册、确定性的控制平面类，带有 schema、副作用策略和执行行为。 |
| **HostDispatcher** | 受信任的进程内路由封装，会在已配置处执行能力特定的权限、审批、审计和筛查。不同操作的覆盖不同；它不是插件沙箱。 |
| **Host service** | `openai4s/host/` 下的聚焦实现，例如文件、LLM、Skills、委派、MCP 或远程计算。 |
| **Kernel** | Host 侧 manager 与一个使用 JSON-lines 协议的 Python 或 R worker 进程。 |
| **Kernel slot** | Web 会话 supervisor 持有的 Python 或 R 生命周期位置。 |
| **Generation** | 某一个 worker 实例的持久身份。重启会创建新的 generation。 |
| **Kernel lease** | 指向某一精确 generation 的冻结引用，用于限定作用域的中断、关闭和恢复。 |
| **Execution ticket** | FIFO 准入记录，将会话、execution ID 和 owner 绑定到唯一活动的科学写入者。 |
| **Frame** | 持久的 actor 或会话记录。委派出的子代理拥有自己的 `frame_id`。 |
| **Root frame** | 由后代继承的 Web 会话与 Artifact 集合边界。 |
| **Project** | 会话和部分 project-level 状态的共享分组作用域。 |
| **Branch** | 从不可变 checkpoint 和追加式记录中选出的逻辑历史投影。 |
| **Workspace** | 会话级文件系统区域，供 Cell 使用并捕获交付物。文件独立于内存 namespace 持久存在。 |
| **Artifact** | 持久的逻辑交付物，包含以追加为主的 version record 和一个当前 head。只有 verified snapshot binding 成功的版本才具有不可变 bytes。 |
| **Provenance** | 关联文件、Cell、环境及受支持对象变换的 best-effort 证据；它不是通用 Python 数据流跟踪。 |
| **Action Ledger** | 以追加为主的记录，保存建议动作、规范化结果和终态事件。Attempt 与生命周期行另有可变状态。 |
| **Projection** | 面向某一消费者、从持久事件或状态导出的视图，例如 provider history、UI 对话、Notebook、Timeline 或 Artifacts。 |
| **Completion** | 由 Engine 自有的 `finalize_response` 或 Python `host.submit_output(...)` 显式触发的成功终止。普通文本、工具成功、R Cell、取消及 max-turn 耗尽都不算 completion。 |
| **Checkpoint** | 在已知历史边界记录所选 workspace 内容和结构化会话状态的不可变持久记录。 |
| **Recovery** | 分阶段重建 workspace/运行时状态的尝试。Workspace materialization 与 worker publish 具有不同的原子性保证。 |
| **Skill** | 逐步加载到科学工作流中的版本化代码配方（`SKILL.md`，可选 `kernel.py`）。 |
| **Compute provider** | 用于暂存和执行远程工作的平台注册层。当前通用远程计算并不是完整调度器。 |
