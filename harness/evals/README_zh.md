# Harness 评测

[English](README.md)

离线 eval fixture 和给它们打分的代码放在这里。一次 eval 衡量的是一整组 case 上的某条架构或质量边界，这和 [`../../tests/`](../../tests/) 里那些聚焦断言不是一回事；它补充断言，不替代断言。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 导出 Action routing 和逆合成外部后端评测接口。 |
| [`action_routing.py`](action_routing.py) | 给确定性路由函数 `route_action` 打分。每条 fixture 是一份录制下来的模型回复，各代表一类任务：原生 Tool batch、Python 或 R Cell、Engine finalization、不能被当成完成信号的普通散文、不支持的 fence，以及两条优先级规则——原生 Tool batch 优先于 fence 里的 Cell，一条回复里也只路由第一个 Cell。报告给出准确率、混淆情况，以及每条 case 的通过与否。 |
| [`retrosynthesis_backends.py`](retrosynthesis_backends.py) | 不加载模型权重，而是把版本化的外部模型响应重新送入生产响应规范化器。它评测 schema、预期成功/错误行为、预测数量、checkpoint provenance 完整度、带分数预测覆盖率，以及确定性的响应摘要。 |
| [`retrosynthesis_backend_cases.json`](retrosynthesis_backend_cases.json) | 公开安全的合成响应 tape：一个成功的 RetroChimera 形状预测批次，以及一个禁止 checkpoint 自动下载时的拒绝结果。fixture 不包含模型权重、私有化学信息、网络结果或真实 checkpoint 路径。 |
| [`.gitkeep`](.gitkeep) | 把目录留在 git 里，与当前有哪些计分代码无关。 |

两个 evaluator 都完全确定性，不需要 provider key、网络、内核、GPU 或可选模型包。Action routing 对应 [`../../tests/test_action_routing_eval.py`](../../tests/test_action_routing_eval.py)；外部模型协议与 replay 契约由 [`../../tests/test_harness_contract.py`](../../tests/test_harness_contract.py) 覆盖。
