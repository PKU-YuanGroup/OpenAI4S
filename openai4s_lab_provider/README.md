# Lab provider process

| File | Purpose |
| --- | --- |
| `__init__.py` | Protocol and adapter version constants. |
| `__main__.py` | Isolated script bootstrap, environment scrub and protocol fd. |
| `protocol.py` | Bounded JSON frames and safe errors. |
| `server.py` | Single-session dispatch, receipts and fencing. |
| `client.py` | Serialized subprocess client with deadlines and process-group cleanup. |
