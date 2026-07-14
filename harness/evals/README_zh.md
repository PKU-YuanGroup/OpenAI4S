# Harness 评测

[English](README.md)

本目录保存可审阅的离线评测 fixture 与 scorer。Eval 跨一组 case 衡量架构或质量边界；它补充但不替代 [`../../tests/`](../../tests/) 中的聚焦断言。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 导出 Action routing 评测接口。 |
| [`action_routing.py`](action_routing.py) | 定义原生 Tool batch、Python/R Cell、Engine finalization、prose/no-action、不支持 fence 与优先级规则的录制 model-reply case；为 `route_action` 计分并报告 accuracy/confusion/failure。 |
| [`.gitkeep`](.gitkeep) | 使评测扩展目录不依赖当前 scorer 集合而保持存在。 |

## 直属子目录

无。

该 evaluator 完全确定性运行，无需 provider key、网络、内核或可选依赖；pytest 契约位于 [`../../tests/test_action_routing_eval.py`](../../tests/test_action_routing_eval.py)。
