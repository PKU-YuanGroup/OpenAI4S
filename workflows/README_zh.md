# `workflows/`

带版本的科学工作流基准清单。十个 workflow、共 20 个用例，每个都是一份声明「这次运行应该做什么、做到什么才算做到」的 JSON。

它们放在仓库里而不是 fixture 目录里只有一个理由：用例的改动必须是一份可评审的 diff。执行它们的 runner 在 [`openai4s/benchmark/`](../openai4s/benchmark/README_zh.md)，它走的每一步都驱动生产代码——真实的 Store、真实的 kernel manager、真实的 host dispatcher、真实的 compute manager。被注入的只有离线跑不了的那些：模型、网络，以及包管理器。

声明的结果是契约的一部分，不是一列状态。`failure`、`permission_denied`、`recovered`、`provenance` 这些用例在运行**成功**时判失败，因为一个只会打分「没抛异常」的基准，对系统中「职责就是拒绝」的那一半什么也没衡量。

| Workflow | 覆盖什么 |
| --- | --- |
| [`artifact-lineage/`](artifact-lineage/README_zh.md) | 派生产物携带 lineage |
| [`environment-provenance/`](environment-provenance/README_zh.md) | 产物环境 provenance |
| [`environment-transaction/`](environment-transaction/README_zh.md) | 环境 plan → apply → rollback |
| [`evidence-package/`](evidence-package/README_zh.md) | 证据包导出与验证 |
| [`permission-boundary/`](permission-boundary/README_zh.md) | workspace 边界拒绝越界写 |
| [`python-analysis/`](python-analysis/README_zh.md) | Python 分析产出可追溯产物 |
| [`r-analysis/`](r-analysis/README_zh.md) | R 通道独立执行 |
| [`remote-compute/`](remote-compute/README_zh.md) | 远程任务 submit → poll → harvest |
| [`science-retrieval/`](science-retrieval/README_zh.md) | 科学数据检索与来源证据 |
| [`telemetry-identity/`](telemetry-identity/README_zh.md) | 遥测身份随撤销一同销毁 |
