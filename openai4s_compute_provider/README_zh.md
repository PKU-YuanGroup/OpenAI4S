# BYOC Worker Runtime

[English](README.md)

尽管包名是历史遗留名称，本包实际是 stdlib-only **worker runtime**，而不是具体 compute provider。Provider-specific shim 位于 `skills/remote-compute-<id>/provider.py`，实现这里的协议，也是唯一预期导入第三方 provider SDK 的层。Runtime 提供共享 authentication、secret 环境清理、ownership 检查、lifecycle operation、staging、output cap 和错误规范化。

## 在架构中的位置

[`ComputeManager`](../openai4s/compute/manager.py) 以 Python isolated mode 启动 [`__main__.py`](__main__.py) 执行 BYOC operation。在当前使用的 oneshot 路径中，request/reply 文件经过私有 staging directory，credential 通过 stdin 到达，从不放入子进程 environment。Runtime 加载一个 provider shim，在操作已有 sandbox 前验证 ownership，并把实际 sandbox create/exec/list/terminate 行为委托给该 provider。

Runtime 还实现了 long-lived REPL mode，使用 fd-3 control/auth channel 和公共 Python Cell 协议。存在这项支持并不证明所有 Host 路径或 UI 已端到端接好持久 provider kernel。

## 文件

| 文件 | 职责 |
|---|---|
| [`__init__.py`](__init__.py) | 说明包契约，并导出 provider protocol、resident、channel helper、limit、error kind、path 和 secret-scrub function。 |
| [`__main__.py`](__main__.py) | 通用 isolated 入口：在导入 `provider.py` 前执行 baseline 环境清理，按精确文件路径加载 `PROVIDER`，然后启动 oneshot 或 REPL mode。 |
| [`_channel.py`](_channel.py) | 实现有上限的 newline-framed fd-3 ready/event/auth message、stdin/fd-3 authentication 解析、字节格式化和防止意外输出 token 的 stdout/stderr courtesy scrubber。 |
| [`_constants.py`](_constants.py) | 集中 stream/harvest cap、idle timeout、stage/work path、协议 exit code、fd/line limit、已识别 credential-name pattern、provider-secret prefix 和规范化 error kind。 |
| [`_protocol.py`](_protocol.py) | 定义 `ByocProvider`、运行中 `ExecResult` 的 structural contract 和 typed `ByocError`；provider 可以不实现可选的 persistent-store browsing method。 |
| [`_resident.py`](_resident.py) | 运行强化 prologue 与 oneshot/REPL lifecycle；通过 owner-tag 检查、有界传输、脱敏、deadline 和结构化 reply 处理 create、submit、wait/harvest、batch probe、reconcile、tail、browse/read 和 terminate。 |

## 子目录

本包没有受跟踪的子目录。

## 生命周期与信任边界

- Secret 清理分两阶段：provider-agnostic baseline 在 provider import **之前**运行，随后在读取 credential 前清理 provider 声明的 prefix。这是基于名称的 heuristic；以未识别名称保存的 secret 不会被移除。
- Credential 会被有意传给 `provider.apply_auth`，因此 provider shim 具备该 credential 表示的权限。Stdout scrubber 只是防止意外打印的 courtesy protection，不能约束恶意 provider。
- Isolated mode（`python -I`）防止 provider sibling file 劫持 import，但不是 OS sandbox。Confinement 必须由启动 Host 提供并验证。只有调用方请求 `expect_confined` 且 runtime probe 失败时，oneshot mode 才以 exit 71 fail closed；未请求的调用方没有建立该边界。
- Linux confinement probe 比较 network-namespace identity；macOS probe 依赖 home-directory read denial。Probe 成功只验证这些 invariant，不代表完整隔离。
- Sandbox owner tag 把 operation 绑定到一个 OpenAI4S installation。Runtime 拒绝 ownership mismatch；新建 sandbox 无法正确回读 ownership 时会 best-effort terminate。
- Request/reply staging path 必须解析到预期 temp prefix 下。Transfer 和 log tail 有上限，但 harvest 的 provider 字节仍是不可信内容，需要 Host 侧安全解包/Artifact 处理。
- REPL idle/auth 过期会退出 resident。Oneshot signal 与协议错误使用专用 exit code；可能时，失败会规范化为有界 `ByocError` kind/message。
- 本 runtime 支持 provider contract，但不会让 `host.compute` 自动达到 scheduler-grade 或 durable。Host job record 与 warm-sandbox handle 仍可能只在内存中，provider/cloud 行为也可独立失败。

## 相关文档

- [Compute 后端](../openai4s/compute/README_zh.md)
- [远程计算](../docs/compute.md)
- [安全模型](../docs/security.md)
- [准确命名的 alias](../openai4s_worker_runtime/README_zh.md)
