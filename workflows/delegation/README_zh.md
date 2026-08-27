# `workflows/delegation/`

**父代理被告知子代理做了什么** — 子代理的 `submitted` 只说明它选择了提交，完全不说明任务是否完成。这个工作流用脚本化模型驱动真实的 `DelegationRunner`、真实的子 `Agent` 与真实的 Store，并在父代理能读到的两个面上都断言机器可读的 `task_status`：返回的信封，以及持久投影。

只断言终态。这里刻意没有任何时序握手——委派时序在负载高的 runner 上很容易抖动，靠等待来断言的用例测的是 runner 而不是契约。

Steps: `open_session`, `run_delegation`
Permissions: `workspace:read`, `workspace:write`, `kernel:execute`

| 文件 | 用途 |
| --- | --- |
| `workflow.json` | 版本化清单：步骤、权限、失败条件、脚本化的子代理回复，以及下面这些用例。版本 `1.0.0`。 |

## 用例

| 用例 | 声明结果 | 它钉住什么 |
| --- | --- | --- |
| `delegation/blocked-child-is-not-done` | `provenance` | 诚实申报 `blocked` 的子代理，在信封里**和**持久投影里都读作 `blocked`，绝不会被读成完成。它的生命周期状态仍是 `done`——它确实提交了——这恰恰说明 `task_status` 才是父代理该读的那一栏 |
| `delegation/completed-child-is-done` | `provenance` | 真正完成的子代理，两处记录彼此一致 |
| `delegation/max-turns-child-fails` | `provenance` | 一个只说话不行动、直到耗尽回合预算的子代理，落到 `failed` 生命周期，并把 `max_turns` 作为停止原因持久记录下来 |
