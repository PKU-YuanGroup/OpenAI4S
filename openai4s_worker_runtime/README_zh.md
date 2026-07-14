# Worker Runtime 兼容 Alias

[English](README.md)

本包为 [`openai4s_compute_provider`](../openai4s_compute_provider) 提供名称更准确的 import alias。它不包含独立 runtime 实现，也不改变任何行为。

## 在架构中的位置

新的 provider 或 Host 代码可以从 `openai4s_worker_runtime` 导入公开 worker-runtime symbol；已有集成继续导入 legacy package。两个名称解析到相同的 class/function object。Private implementation module 和可执行入口仍位于 `openai4s_compute_provider`。

## 文件

| 文件 | 职责 |
|---|---|
| [`__init__.py`](__init__.py) | 重新导出 legacy package 的公开 provider contract、resident、channel helper、limit、error kind、path 和 scrub function，并保持 object identity。 |

## 子目录

本包没有受跟踪的子目录。

## 兼容性边界

- 本 alias 没有 `__main__.py`；受限进程入口必须通过 `openai4s_compute_provider` 启动。
- `_resident.py`、`_protocol.py` 等 private module 不会被复制或作为 submodule alias。
- Alias 不新增 confinement、persistence、provider discovery 或成熟度保证。实际 trust 与 failure boundary 参见[主 runtime README](../openai4s_compute_provider/README_zh.md)。

## 相关文档

- [主 Worker runtime](../openai4s_compute_provider/README_zh.md)
- [Compute 后端](../openai4s/compute/README_zh.md)
- [包边界](../docs/package-architecture.md)
