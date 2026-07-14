# 安全分层

[English](README.md)

围绕 Code-as-Action 的安全层里，有六道住在这个包中：代码分类、内核 OS 隔离、CPython 审计钩子、shell 预检、提示注入标注、生物安全筛查。其余几道是有意放在别处的。子进程环境过滤在 [`kernel/environment.py`](../kernel/environment.py)，Host 权限和持久化审批在 [`host_dispatch.py`](../host_dispatch.py) 与 [`storage/permissions.py`](../storage/permissions.py)，shell capability 本身在 [`host/bash.py`](../host/bash.py) 和 [`sdk/bash.py`](../sdk/bash.py)，应用出网在 [`egress.py`](../egress.py)。这里没有一道可以指着说“就是它”的总边界，这正是设计意图：每道控制拦的东西不同，失败的方式也不同，谁都不是那道必须扛住一切的防线。

## 在架构中的位置

- Python/R Cell 执行前，外层 runtime 可以调用 [`classifier.py`](classifier.py)。Cell 被拒绝后不会进入 worker，只会作为一条 observation 回到外层循环。
- [`kernel/manager.py`](../kernel/manager.py) 请求 [`sandbox.py`](sandbox.py) 包装 worker 进程，并把实测到的沙箱状态发布出去。
- [`kernel/worker.py`](../kernel/worker.py) 在 CPython 里安装 [`audit_hook.py`](audit_hook.py)；`host.bash` 在 capability 授权执行之前，还会先跑一遍 [`shellcheck.py`](shellcheck.py)。
- 工具/MCP/web 的输出可以过一遍 [`injection.py`](injection.py)，把疑似指令标注成不可信数据。
- [`biosecurity.py`](biosecurity.py) 提供校准过的 prompt 策略，以及一个可选的轨迹判定。Host 权限和持久化的人工审批不在本目录，而在 [`HostDispatcher`](../host_dispatch.py) 和 [`storage/permissions.py`](../storage/permissions.py)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 说明这套分层模型，并重新导出代码分类、注入扫描和生物安全判定的 API。 |
| [`audit_hook.py`](audit_hook.py) | 在 worker 内安装 CPython 审计钩子：从可写的工作区、临时目录或 Artifact 根目录 `ctypes.dlopen` 一个动态库会被拒绝，而解释器与包安装前缀下的加载照常放行。它把依赖的函数捕获成定义期的关键字默认值，并在安装完成后删掉指向钩子自身的所有 Python 句柄，因此 Cell 内对模块命名空间的 monkeypatch 无法直接解除这道检查。这是抵抗，不是免疫：目标是让它在 Cell 内难以被绕过，而不是绝无可能。 |
| [`biosecurity.py`](biosecurity.py) | 存放校准问责式的 prompt 片段和轨迹筛查器。先由一个低成本的相关性触发器判断这次筛查值不值得花一次模型调用；真正的筛查是一次独立调用，返回 ALLOW、ESCALATE 或 BLOCK，解析时对格式松散的回复也留了余地。 |
| [`classifier.py`](classifier.py) | 分三层对一个 Cell 做分类：不含任何风险 token 的代码走快速通道；命中高置信度静态攻击特征的直接判定；剩下的在 `llm` 模式里交给模型复核。它返回结构化的 SAFE/UNSAFE 证据，自身不抛异常。 |
| [`injection.py`](injection.py) | 用静态特征、以及可选的一次 LLM 调用，扫描工具返回的不可信文本。疑似内容会被加上一段警告前缀，原文一个字都不删。 |
| [`sandbox.py`](sandbox.py) | 用 Seatbelt（macOS）或 bubblewrap（Linux）包装 worker 命令。它构造工作区、私有临时目录、secret 读取屏蔽和网络策略，用一次真实的 allow/deny 自测来验证策略确实生效，并回报一份状态，其中的 `state` 是 `enabled`、`disabled` 或 `unavailable`。 |
| [`shellcheck.py`](shellcheck.py) | 内核本地的 `host.bash` 执行前跑的一小份静态阻止名单，只拦明显灾难性的字面命令，再隐蔽一点的就拦不住。它不做完整的 shell 解析，这是有意为之。 |

## 执行与失败矩阵

| 层 | 命中后的行为 | 重要失败行为 |
| --- | --- | --- |
| 代码分类器 | 静态特征或 LLM 判出 `UNSAFE`，这个 Cell 就不会执行。 | `off` 会关掉这一层。分类器自身异常、LLM 未配置时失败即放行；但 LLM 确实被调用了、响应却解析不了时，会偏保守判成 `UNSAFE`。`heuristic` 模式会放行那些带风险 token、却没命中静态攻击特征的残余代码。 |
| OS 沙箱 | 已生效的 backend 把写入限制在工作区和私有临时目录，屏蔽对 secret 文件的读取，并在没有显式允许时封禁原始网络访问。 | `enforce` 失败即拒绝。探测或自测失败时，`auto` 会记录日志并返回可见的 `state="unavailable"`，但仍可能在没有 OS 隔离的情况下把 worker 跑起来。 |
| CPython 审计钩子 | 拒绝覆盖范围内、从可写路径发起的 `ctypes.dlopen` 事件。 | 它只管 Python、只管这一类事件，既不是通用的原生代码沙箱，也管不到 R；而且可信前缀策略本身必须是对的，否则这道检查没有意义。 |
| Shell 预检 | 拦下少数无歧义的破坏性命令字符串。 | 它基于正则，自身出错时失败即放行，并且明确不是抗混淆的沙箱。Host capability 与 OS 隔离仍然不可少。 |
| 注入扫描 | 给模型要读的内容加上一段警告横幅。 | 它不删除、也不阻断内容。静态扫描之后，出错、模型未配置、LLM 输出无法解析，都会失败即放行。 |
| 生物安全筛查 | 返回 ALLOW、ESCALATE 或 BLOCK，交给调用方按策略处理。 | 只有关键词触发之后才会运行；不可用时失败即放行，返回 ALLOW。一条判定本身不是执行隔离。 |

## 运维注意事项

- 不要只看配置就断定 worker 已经被沙箱包住；要去看 runtime 实测出来的 `SandboxStatus` 和它的 warning。自测通过只说明当前这个 backend、这套策略是生效的，并不等于宣称隔离是完整的。
- 沙箱的原始网络规则，与 Host/应用层的出网策略，是两回事。放开其中一个不等于授权了另一个。
- [`kernel/environment.py`](../kernel/environment.py) 里的环境过滤是另一道 secret 边界。无论是基于名称的过滤，还是输出脱敏，都不可能认出 secret 的所有表示形式。
- 本项目是本地的、面向可信用户的工作台，不是经过加固的公网多租户执行服务。要把 daemon 绑到公网上，需要另外设计认证与隔离方案。

## 相关文档

- [安全模型](../../docs/security.md)
- [系统架构](../../docs/architecture.md)
- [配置](../../docs/configuration.md)
