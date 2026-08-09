# BYOC Worker Runtime

[English](README.md)

包名是历史遗留。这里放的其实是一个只依赖标准库的 **worker runtime**，而不是某个具体的 compute provider。一个 provider 就是 `skills/remote-compute-<id>/provider.py` 下的一层 shim，它实现本包定义的协议，也是整个系统里唯一预期会导入第三方 provider SDK 的地方。这些 shim 共用的东西都在本包：认证、清理环境里的 secret、所有权检查、生命周期操作、暂存、输出上限，以及错误规范化。

## 在架构中的位置

[`ComputeManager`](../openai4s/compute/manager.py) 以 Python 的 isolated 模式启动 [`__main__.py`](__main__.py) 来执行 BYOC 操作。目前走的是 oneshot 路径：请求和回复以文件形式经过一个私有暂存目录，凭据从 stdin 进来，从不放进子进程的环境变量。runtime 只加载一个 provider shim，在操作已有沙箱之前先验证所有权，真正的沙箱创建/执行/枚举/终止行为则交给该 provider。

runtime 还实现了一个长驻的 REPL 模式，用 fd-3 作为控制/认证通道，走的是公共的 Python Cell 协议。这项支持存在归存在，但不能据此认为所有 Host 路径或 UI 都已经端到端接好了常驻的 provider 内核。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 对外的门面。说明本包的契约，并导出 provider 协议、resident、channel 辅助函数、各类上限、错误类别、路径以及清理 secret 的函数。 |
| [`__main__.py`](__main__.py) | 所有 provider 共用的唯一入口。它先做一遍基线的环境清理，再导入 `provider.py`，按精确的文件路径加载 `PROVIDER`，然后启动 oneshot 或 REPL 模式。它加载*本包*时也是按文件位置来的，而不是把上级目录塞进 `sys.path`：包导入会去*列出*它要搜索的那个目录，所以旧写法要求隔离内部能读到上级目录——在源码安装或 editable 安装下那就是仓库根目录，等于把未纳入版本管理的 `.env`、`.git` 递给一个按设计还带着网络的进程。 |
| [`_channel.py`](_channel.py) | 底层传输管道，不认识 provider，也不认识具体操作：带上限的、按换行分帧的 fd-3 ready/event/auth 消息，从 stdin 或 fd-3 读取的认证握手，字节数格式化，以及一个防止 token 被顺手打印出去的 stdout/stderr scrubber。 |
| [`_constants.py`](_constants.py) | 把 resident、控制通道和各个 shim 必须取得一致的数值集中在一处：流与回收的字节上限、空闲超时、stage 与 work 路径、协议退出码、fd 与行长上限、已识别的凭据命名模式、provider secret 前缀，以及规范化后的错误类别。 |
| [`_protocol.py`](_protocol.py) | `ByocProvider` 与运行中 `ExecResult` 的结构化契约，外加带类型的 `ByocError`。没有可浏览持久存储的 provider，直接不实现那几个可选的浏览方法即可。 |
| [`_resident.py`](_resident.py) | 承载 provider 的受限进程：先跑强化 prologue，再进入 oneshot 或 REPL 生命周期。创建、提交、等待/回收、批量探测、对账、tail、浏览/读取、终止都由它处理，一路带着所有者标签检查、有界传输、脱敏、超时期限和结构化回复。 |

## 生命周期与信任边界

- 清理 secret 分两阶段：与 provider 无关的基线清理在导入 provider **之前**跑，随后在读取凭据之前，再按 provider 自己声明的前缀清理一遍。这是基于名称的启发式判断；以未被识别的名称保存的 secret 不会被清掉。一个变量被删掉，是因为它的*名字*命中了凭据模式（`*_API_KEY`、`*_TOKEN`、`*_SECRET` 等）或某个基线/provider 前缀——这也正是那两个探测锚值必须活下来的原因：清掉其中一个，留下的不是一个坏掉的变量，而是一道无法验证边界的探测，而它如今是 fail closed 的。
- 凭据是有意交给 `provider.apply_auth` 的，因此 provider shim 就拥有这份凭据所代表的权限。stdout 的 scrubber 只挡意外打印，挡不住一个恶意的 provider。
- isolated 模式（`python -I`）能防止 provider 目录里的同级文件劫持 import，但它不是 OS 沙箱。隔离必须由启动它的 Host 提供，而且必须验证。Host 现在确实提供了一条——来自 [`security/byoc_confinement.py`](../openai4s/security/byoc_confinement.py) 的 Seatbelt 或 bubblewrap——并且只要它真的做了包装，就会同时要求 `expect_confined`：这两件事只有一起做才有意义，光要求自查却不建立边界，只会让 helper 以 71 退出，同时什么也没证明。不受限的那种回退形态有意不提这个要求，`auto` 的降级因此是可见的，而不是致命的。
- 探测检查的不变量在两个平台上都是**文件系统**那一条。macOS 上期望 `listdir($HOME)` 抛出 `PermissionError`。Linux 上比较的是 home 目录的设备号与 Host 递进来的锚值（`OPENAI4S_HOST_HOME_DEV`）：在 bubblewrap 的 `--tmpfs` 下，home 是可读且*空*的，而空 home 本身是合法的，所以"空"不能拿来当判据。原始设计里的网络命名空间比较，如今只作为落后一个版本的 Host 的回退分支保留。探测通过只说明这一条不变量成立，别的什么都不代表——尤其不代表网络被隔离，那是另一项能力，而且没有启用。
- 探测里每一条实际上没能完成检查的路径，现在都返回 **False**。它们过去用 `True`（边界在）来回答"我没能验证"；而这道检查只有在调用方传了 `expect_confined` 时才会被问到，于是那个 `True` 就放任一个不受限的 helper 继续去读凭据、调 provider，同时什么都没证明——正是锚值要防的那种隔离表演，而且是从唯一一条没有测试踩到的路上到达的。`/proc` 不存在不是被隔离的证据；而对一个 PID 1 属于 root 的非特权进程来说，`/proc/1/ns/net` 读不到本来就是常态。
- 沙箱的所有者标签把每个操作绑定到某一个 OpenAI4S 安装实例。所有权对不上时 runtime 直接拒绝；新建的沙箱如果回读不到正确的所有权，runtime 会尽力而为地把它终止掉。
- 请求/回复的暂存路径必须解析到预期的临时目录前缀之下。传输和日志 tail 都有上限，但从 provider 回收来的字节仍然是不可信内容，需要 Host 侧安全地解包并按 Artifact 处理。
- REPL 空闲超时或认证过期都会让 resident 退出。oneshot 收到信号或遇到协议违规时使用专门的退出码；在可能的情况下，失败会被规范化成有界的 `ByocError` 类别与消息。
- 本 runtime 支持的是一份 provider 契约。它不会让 `host.compute` 变成调度器级别的东西：Host 侧的任务记录如今是持久的，并带着重启后仍能找回沙箱所需的 receipt，但预热沙箱句柄仍然只在内存里，也没有任何后台轮询，provider 与云端本身还会各自出问题。

## 相关文档

- [Compute 后端](../openai4s/compute/README_zh.md)
- [远程计算](../docs/compute.md)
- [安全模型](../docs/security.md)
- [准确命名的 alias](../openai4s_worker_runtime/README_zh.md)
