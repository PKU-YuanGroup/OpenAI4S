# Lab 仿真轨迹

[English](README.md)

七个 `offline`、`tier:pr`、`lab-sim` 场景由具名 `harness/lab.py` 适配器驱动真实 Store 与 LabManager、显式假设备。`lab` 标签属于外部资源，不能用在这些场景。

通用 schema 要求非空模型脚本，因此 `provider_script` 只有明确的 `lab_simulation` 哨兵；这里不会调用 LLM。每个 `fixtures.lab_simulation.case` 声明完整故障配方：`lost_response` 同时注入响应丢失与首次查询失败，`provider_lost` 使用派发后死亡钩子。黄金轨迹独立固定实际状态、错误码和计数；不读取或输出评价、真值、标识或时间。CLI 与 pytest 都进行同一黄金比对，默认不更新。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`lab_lost_response.json`](lab_lost_response.json) | 响应丢失且首次查询失败后保持未知，再由 status 对账，不重发。 |
| [`lab_duplicate_submission.json`](lab_duplicate_submission.json) | 同一个幂等键重复提交只执行一次。 |
| [`lab_stale_revision.json`](lab_stale_revision.json) | 陈旧 revision 在设备执行之前被拒绝。 |
| [`lab_provider_lost.json`](lab_provider_lost.json) | 派发后 provider 死亡保持 outcome_unknown，并以 provider_lost 结束运行。 |
| [`lab_budget_exhausted.json`](lab_budget_exhausted.json) | 耗尽步数预算后拒绝下一命令并释放 provider。 |
| [`lab_approval_denied.json`](lab_approval_denied.json) | 真实 HostDispatcher 与 broker 拒绝批准，不写命令、不触达设备。 |
| [`lab_recovery_forbidden.json`](lab_recovery_forbidden.json) | 恢复上下文拒绝重放有副作用的 Lab 命令。 |
