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
| [`__main__.py`](__main__.py) | 所有 provider 共用的唯一入口。它先做一遍基线的环境清理，再导入 `provider.py`，按精确的文件路径加载 `PROVIDER`，然后启动 oneshot 或 REPL 模式。 |
| [`_channel.py`](_channel.py) | 底层传输管道，不认识 provider，也不认识具体操作：带上限的、按换行分帧的 fd-3 ready/event/auth 消息，从 stdin 或 fd-3 读取的认证握手，字节数格式化，以及一个防止 token 被顺手打印出去的 stdout/stderr scrubber。 |
| [`_constants.py`](_constants.py) | 把 resident、控制通道和各个 shim 必须取得一致的数值集中在一处：流与回收的字节上限、空闲超时、stage 与 work 路径、协议退出码、fd 与行长上限、已识别的凭据命名模式、provider secret 前缀，以及规范化后的错误类别。 |
| [`_protocol.py`](_protocol.py) | `ByocProvider` 与运行中 `ExecResult` 的结构化契约，外加带类型的 `ByocError`。没有可浏览持久存储的 provider，直接不实现那几个可选的浏览方法即可。 |
| [`_resident.py`](_resident.py) | 承载 provider 的受限进程：先跑强化 prologue，再进入 oneshot 或 REPL 生命周期。创建、提交、等待/回收、批量探测、对账、tail、浏览/读取、终止都由它处理，一路带着所有者标签检查、有界传输、脱敏、超时期限和结构化回复。 |

## 生命周期与信任边界

- 清理 secret 分两阶段：与 provider 无关的基线清理在导入 provider **之前**跑，随后在读取凭据之前，再按 provider 自己声明的前缀清理一遍。这是基于名称的启发式判断；以未被识别的名称保存的 secret 不会被清掉。
- 凭据是有意交给 `provider.apply_auth` 的，因此 provider shim 就拥有这份凭据所代表的权限。stdout 的 scrubber 只挡意外打印，挡不住一个恶意的 provider。
- isolated 模式（`python -I`）能防止 provider 目录里的同级文件劫持 import，但它不是 OS 沙箱。隔离必须由启动它的 Host 提供，而且必须验证。只有当调用方要求 `expect_confined` 且 runtime 的探测失败时，oneshot 模式才会以退出码 71 失败即拒绝；没有提出这个要求的调用方，等于没有建立这条边界。
- Linux 上的隔离探测比较的是网络命名空间的身份，macOS 上则依赖对 home 目录的读取被拒绝。探测通过只说明这些不变量成立，不代表隔离是完整的。
- 沙箱的所有者标签把每个操作绑定到某一个 OpenAI4S 安装实例。所有权对不上时 runtime 直接拒绝；新建的沙箱如果回读不到正确的所有权，runtime 会尽力而为地把它终止掉。
- 请求/回复的暂存路径必须解析到预期的临时目录前缀之下。传输和日志 tail 都有上限，但从 provider 回收来的字节仍然是不可信内容，需要 Host 侧安全地解包并按 Artifact 处理。
- REPL 空闲超时或认证过期都会让 resident 退出。oneshot 收到信号或遇到协议违规时使用专门的退出码；在可能的情况下，失败会被规范化成有界的 `ByocError` 类别与消息。
- 本 runtime 支持的是一份 provider 契约。它不会让 `host.compute` 变成调度器级别的、可持久化的东西：Host 侧的任务记录和热沙箱句柄仍可能只存在于内存中，provider 与云端本身也可能各自出问题。

## 相关文档

- [Compute 后端](../openai4s/compute/README_zh.md)
- [远程计算](../docs/compute.md)
- [安全模型](../docs/security.md)
- [准确命名的 alias](../openai4s_worker_runtime/README_zh.md)
