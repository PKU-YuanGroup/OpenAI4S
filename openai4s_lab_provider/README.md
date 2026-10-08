# Lab provider process

| File | Purpose |
| --- | --- |
| `__init__.py` | Protocol and adapter version constants. |
| `__main__.py` | Isolated script bootstrap, environment scrub and protocol fd. |
| `protocol.py` | Bounded JSON frames and safe errors. |
| `server.py` | Single-session dispatch, receipts and fencing. |
| `client.py` | Serialized subprocess client with deadlines and process-group cleanup. |
| `toy.py` | Deterministic stdlib test backend / 确定性的标准库测试后端。 |
| `chemgymrl/` | Isolated upstream adapter and manifests / 隔离的上游适配器和清单。 |

The host `openai4s lab setup chemgymrl` installs and verifies an atomic provider generation; `lab smoke` runs one seeded step without a database. The entrypoint requires `-I`, keeps protocol stdin on a private non-inheritable fd and redirects fd 0 to `/dev/null`. The client request lock is non-reentrant and never signals a process group after its leader has been reaped.
