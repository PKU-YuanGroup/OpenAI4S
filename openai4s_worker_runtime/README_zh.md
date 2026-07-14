# Worker runtime 兼容 alias

[English](README.md)

这里只有一个 `__init__.py`，把 [`openai4s_compute_provider`](../openai4s_compute_provider) 用一个名副其实的包名重新导出。它不含第二份 runtime 实现，走这个名字导入也不会有任何行为差异。

## 在架构中的位置

新写的 provider 或 Host 代码可以从 `openai4s_worker_runtime` 导入公开的 worker runtime 符号；已有集成继续用旧包名导入。两个名字解析到同一批 class 与 function 对象。私有实现模块和可执行入口仍然留在 `openai4s_compute_provider` 里。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 把旧包的公开接口重新导出：provider 契约、resident、channel 辅助函数、各项上限、错误类型、路径常量，以及 secret 清理函数。同一个符号在两个包名下是同一个对象，因此 identity 与 `isinstance` 判断在两种 import 之间通用。 |

## 兼容性边界

- 这个 alias 没有 `__main__.py`。受限进程的入口只能通过 `openai4s_compute_provider` 启动。
- `_resident.py`、`_protocol.py` 这类私有模块既不会被复制，也不会作为子模块 alias 出来。
- 换个名字导入不会多出任何东西：没有额外的隔离、持久化、provider 发现，也不代表成熟度更高。真正的信任边界与失败边界写在[主 runtime README](../openai4s_compute_provider/README_zh.md) 里。

## 相关文档

- [主 worker runtime](../openai4s_compute_provider/README_zh.md)
- [Compute 后端](../openai4s/compute/README_zh.md)
- [包边界](../docs/package-architecture.md)
