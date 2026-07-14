# Baseline 场景

[English](README.md)

这些场景组成必需的、确定性的离线 `tier:pr` Harness 门禁。每个文件都由 [`../../schema.py`](../../schema.py) 校验、由 [`../../runner.py`](../../runner.py) 执行：运行必须停在文件写明的终止状态上，发出文件声明的事件，顺序也要一致。声明了 `script_consumed` 的场景，还得把脚本化的 model 序列消费干净。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`scheduled_timeout.json`](scheduled_timeout.json) | 在运行第一次到达 `before_model` 这个 fault point 时，注入一条可重试的 `timeout` 故障。预期只有一次 model attempt、终止原因为 `model_error`，trace 里还必须出现显式的 `fault_injected` 事件；故障挡在前面的那条脚本化 response 是故意不被消费的。 |
| [`single_response_submitted.json`](single_response_submitted.json) | 顺利路径：一条成功的脚本化 response、一次 model attempt、`submitted` 终止原因、按顺序发出的生命周期事件，脚本一条不剩。 |
| [`two_response_sequence.json`](two_response_sequence.json) | 两条脚本化 response，循环因此跑两轮。检查两次 attempt 的先后顺序，以及只有第二轮才把这次运行带到最终的 `submitted` 终止事件。 |

不要为了迁就漂移就放松预期。只有预期的契约本身变了才去改场景，改完还要审阅产生的 trace。
