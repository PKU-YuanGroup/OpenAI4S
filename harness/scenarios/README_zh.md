# Harness 场景

[English](README.md)

本目录保存严格、带版本的 JSON 场景输入。一个场景声明 surface、task、fixture metadata、权限模式、scripted provider 序列、精确 occurrence fault、tag/tier，以及预期终止/event invariant。[`../schema.py`](../schema.py) 在 [`../runner.py`](../runner.py) 执行任何内容前拒绝未知或含糊字段。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`.gitkeep`](.gitkeep) | 使场景根目录不依赖具体场景分组而保持存在。 |

## 直属子目录

| 目录 | 职责 |
| --- | --- |
| [`baseline/`](baseline/) | 必需的离线 `tier:pr` 场景，覆盖确定性 provider 序列、终止提交与计划故障行为。 |

场景 JSON 只能作为已声明 Harness fake 的可执行输入；它不授权重放生产副作用。使用 `uv run python -m harness.cli run --tier pr --offline` 运行。
