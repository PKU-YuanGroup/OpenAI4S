# Baseline 场景

[English](README.md)

这些场景组成必需、确定性、离线的 `tier:pr` Harness 门禁。每个文件都由 [`../../schema.py`](../../schema.py) 校验、由 [`../../runner.py`](../../runner.py) 运行，并且必须消费完 scripted model 序列、满足声明的有序事件/终止 invariant。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`scheduled_timeout.json`](scheduled_timeout.json) | 在第一个 `before_model` fault point 注入可重试 timeout，并预期单次 attempt 的 `model_error` 生命周期与显式 `fault_injected` 事件。 |
| [`single_response_submitted.json`](single_response_submitted.json) | 提供一个成功 scripted response，预期一次 model attempt、`submitted` 终止原因、有序生命周期事件及脚本完全消费。 |
| [`two_response_sequence.json`](two_response_sequence.json) | 提供两步 response 序列，并检查两次 attempt 在最终 `submitted` 终止事件前保持顺序。 |

## 直属子目录

无。

不要仅为接受漂移而削弱预期 invariant；只有预期契约确实变化时才修改场景，并审阅产生的 trace。
