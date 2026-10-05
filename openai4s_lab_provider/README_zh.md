# Lab provider 进程

| File | Purpose |
| --- | --- |
| `__init__.py` | 协议与适配器版本常量。 |
| `__main__.py` | 隔离脚本入口、环境清洗与协议文件描述符。 |
| `protocol.py` | 有界 JSON 帧与安全错误。 |
| `server.py` | 单会话派发、回执与 fencing。 |
| `client.py` | 带期限和进程组清理的串行子进程客户端。 |
