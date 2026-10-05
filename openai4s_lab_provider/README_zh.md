# Lab provider 进程

| File | Purpose |
| --- | --- |
| `__init__.py` | 协议与适配器版本常量。 |
| `__main__.py` | 隔离脚本入口、环境清洗与协议文件描述符。 |
| `protocol.py` | 有界 JSON 帧与安全错误。 |
| `server.py` | 单会话派发、回执与 fencing。 |
| `client.py` | 带期限和进程组清理的串行子进程客户端。 |
| `toy.py` | Deterministic stdlib test backend / 确定性的标准库测试后端。 |
| `chemgymrl/` | Isolated upstream adapter and manifests / 隔离的上游适配器和清单。 |
