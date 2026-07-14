# Harness 评测

[English](README.md)

离线 eval fixture 和给它们打分的代码放在这里。一次 eval 衡量的是一整组 case 上的某条架构或质量边界，这和 [`../../tests/`](../../tests/) 里那些聚焦断言不是一回事；它补充断言，不替代断言。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 导出 Action routing 评测的接口。 |
| [`action_routing.py`](action_routing.py) | 给确定性路由函数 `route_action` 打分。每条 fixture 是一份录制下来的模型回复，各代表一类任务：原生 Tool batch、Python 或 R Cell、Engine finalization、不能被当成完成信号的普通散文、不支持的 fence，以及两条优先级规则——原生 Tool batch 优先于 fence 里的 Cell，一条回复里也只路由第一个 Cell。报告给出准确率、混淆情况，以及每条 case 的通过与否。 |
| [`.gitkeep`](.gitkeep) | 把目录留在 git 里，与当前有哪些计分代码无关。 |

这个 evaluator 完全确定性，不需要 provider key、网络、内核，也不需要任何可选依赖；对应的 pytest 契约是 [`../../tests/test_action_routing_eval.py`](../../tests/test_action_routing_eval.py)。
