# 安全分层

[English](README.md)

本包为 Code-as-Action 提供纵深防御组件，但并不定义单一、整体的安全边界：代码分类、内核 OS 隔离、子进程环境过滤、CPython audit hook、Host 权限/审批、shell capability、应用 egress、prompt-injection 标注和 biosecurity screening 都是独立控制，具有不同的执行与失败行为。

## 在架构中的位置

- Python/R Cell 执行前，外层 runtime 可调用 [`classifier.py`](classifier.py)。被拒绝的 Cell 会变成 observation，而不会进入 worker。
- [`kernel/manager.py`](../kernel/manager.py) 请求 [`sandbox.py`](sandbox.py) 包装 worker 进程，并发布实测 sandbox 状态。
- [`kernel/worker.py`](../kernel/worker.py) 在 CPython 内安装 [`audit_hook.py`](audit_hook.py)；`host.bash` 在 capability 授权执行前也会应用 [`shellcheck.py`](shellcheck.py)。
- Tool/MCP/web 输出可经过 [`injection.py`](injection.py)，把疑似指令标注为不可信数据。
- [`biosecurity.py`](biosecurity.py) 提供校准后的 prompt policy 和可选 trajectory verdict。Host 权限与持久化人工审批位于本目录之外的 [`HostDispatcher`](../host_dispatch.py) 和 [`storage/permissions.py`](../storage/permissions.py)。

## 文件

| 文件 | 职责 |
|---|---|
| [`__init__.py`](__init__.py) | 说明分层模型，并重新导出代码分类、injection 和 biosecurity verdict API。 |
| [`audit_hook.py`](audit_hook.py) | 安装进程内 CPython audit hook，阻止从可写 workspace/scratch/Artifact root 执行 `ctypes.dlopen`，同时允许解释器和 package prefix；捕获依赖以抵抗后续 monkeypatch。 |
| [`biosecurity.py`](biosecurity.py) | 定义 calibrated-accountability prompt、低成本 relevance trigger、独立 ALLOW/ESCALATE/BLOCK trajectory-screening 调用及稳健 verdict 解析。 |
| [`classifier.py`](classifier.py) | 通过 fast-path 安全 token、高置信静态攻击特征，以及可选的残余风险代码 LLM review 对 Cell 分类；返回结构化 SAFE/UNSAFE 证据且不抛出异常。 |
| [`injection.py`](injection.py) | 使用静态 marker 和可选 LLM 扫描不可信 tool 返回文本，对疑似内容添加警告前缀而不是删除。 |
| [`sandbox.py`](sandbox.py) | 探测 Seatbelt（macOS）或 bubblewrap（Linux），构造 workspace/private-temp/read-denial/network 策略，运行真实 allow/deny 自测，包装 worker command，并报告 enforced、disabled 或 unavailable 状态。 |
| [`shellcheck.py`](shellcheck.py) | 在内核本地 `host.bash` 前对明显灾难性的 literal shell command 做小范围静态 best-effort blocklist 检查；有意不尝试完整 shell 解析。 |

## 子目录

本包没有受跟踪的子目录。

## 执行与失败矩阵

| 层 | 命中后的行为 | 重要失败行为 |
|---|---|---|
| 代码分类器 | 静态或 LLM `UNSAFE` 会阻止该 Cell 执行。 | `off` 会禁用；分类器异常或 LLM 未配置时 fail open，而已调用 LLM 但响应不可解析时会偏向 `UNSAFE`。Heuristic 模式会允许包含风险 token 但未命中静态攻击特征的残余代码。 |
| OS sandbox | 已 enforced 的 backend 把写入限制在 workspace/private temp，应用 secret read mask，并在未显式允许时阻止 raw network。 | `enforce` fail closed。探测或自测失败时，`auto` 会记录并返回可见的 `state="unavailable"`，但可能在没有 OS 隔离的情况下运行 worker。 |
| CPython audit hook | 拒绝覆盖范围内从可写路径触发的 `ctypes.dlopen` event。 | 它只针对 Python/event，不是通用 native-code 或 R sandbox，且 trusted-prefix 策略本身必须正确。 |
| Shell precheck | 拒绝少数无歧义的破坏性 command string。 | 它基于正则、自身错误时 fail open，并明确不是抗混淆 sandbox；仍需要 Host capability 与 OS 隔离。 |
| Injection scan | 给内容添加供模型读取的警告 banner。 | 不删除或阻断内容；静态扫描后，错误、未配置模型和不可解析的 LLM 输出都会 fail open。 |
| Biosecurity screen | 返回 ALLOW、ESCALATE 或 BLOCK 供调用方应用策略。 | 仅在关键词触发后运行，不可用时 fail open 为 ALLOW；verdict 本身不是执行隔离。 |

## 运维注意事项

- 不能只根据配置推断 worker 已被 sandbox；应检查 runtime 实测 `SandboxStatus` 和 warning。自测成功只针对当前 backend/policy，并非完整 containment 声明。
- Sandbox raw-network 规则与 Host/application egress 策略彼此独立。允许其中一个不等于授权另一个。
- [`kernel/environment.py`](../kernel/environment.py) 的环境过滤是额外 secret 边界。无论基于名称的过滤还是输出脱敏，都无法识别 secret 的所有表示形式。
- 本项目是 local/trusted-user workbench，不是强化的公网 multi-tenant execution service。将 daemon 暴露到公网需要外部 authentication 与 isolation 设计。

## 相关文档

- [安全模型](../../docs/security.md)
- [系统架构](../../docs/architecture.md)
- [配置](../../docs/configuration.md)
