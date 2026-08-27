# 协议模糊测试

[English](README.md)

针对暴露给不受信任 WebSocket 与 relay 对端的字节解析器做覆盖率引导测试。目标有明确
大小边界、完全离线，也不读取会话数据或凭据。`.github/workflows/fuzz.yml` 会在每个
pull request 做一次短跑，并在每周任务中延长运行。

## 文件

| 文件 | 职责 |
| --- | --- |
| `protocol_fuzzer.py` | 把任意字节喂给 WebSocket 帧读取器和 share tunnel 的 control/data 解码器。契约内的协议拒绝属于预期；其它异常仍作为 crash 暴露。 |
