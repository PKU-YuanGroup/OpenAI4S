# Harness Provider

[English](README.md)

本目录保存外部平台边界的确定性替代实现。它们只消费已声明输入，不连接真实服务，并保留可检查的调用记录，使场景断言编排行为而不是传输可用性。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 导出 scripted LLM 与其结构化错误类型。 |
| [`scripted_llm.py`](scripted_llm.py) | 实现基于队列的模型 callable：深拷贝消息，按顺序返回声明的规范响应，抛出声明的 provider error，暴露剩余 step，并在脚本耗尽时失败。 |
| [`.gitkeep`](.gitkeep) | 在尚未提交其他 fake provider 时保留 provider 扩展目录。 |

## 直属子目录

无。

Provider script 由 [`../schema.py`](../schema.py) 定义，并被 [`../runner.py`](../runner.py) 使用。未来 compute、endpoint 或 lab fake 只有在默认离线时才应放入这里。
