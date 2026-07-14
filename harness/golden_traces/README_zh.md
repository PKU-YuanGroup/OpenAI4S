# Golden Trace

[English](README.md)

本目录保存经审阅的 canonical 比较数据。Golden trace 冻结具名契约或生产 characterization 的规范化观察；它绝不是可执行历史，也不能用于重放副作用。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`.gitkeep`](.gitkeep) | 保留带版本的 golden 根目录。 |

## 直属子目录

| 目录 | 职责 |
| --- | --- |
| [`v1/`](v1/) | Schema version 1 的经审阅 trace 资产，目前保存 r5 pre-change 生产 characterization。 |

Golden 更新必须显式进行：运行 `uv run python -m harness.cli characterize --write`，然后把字节 diff 与 `current_behavior`、`desired_contract`、`known_bug` 字段一起审阅。
