# Harness 场景

[English](README.md)

场景输入放在这里，一个场景一个严格、带版本的 JSON 文件。一个场景写明它代表的 surface 和 task，带上 fixture 元数据和权限模式，脚本化地给出假 provider 依次返回的内容，并把每条故障钉在指定点的第 N 次访问上。它的 tag 决定它属于哪个 tier，它的预期部分写明这次运行必须达成的终止原因与事件 invariant。文件先经 [`../schema.py`](../schema.py) 加载：出现未知或含糊的字段，加载直接失败，[`../runner.py`](../runner.py) 根本没有机会执行它。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`.gitkeep`](.gitkeep) | 让场景根目录不依赖下面的具体场景分组而始终存在。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`baseline/`](baseline/) | 必需的离线 `tier:pr` 场景，覆盖确定性的 provider 序列、终止提交，以及计划内的故障行为。 |

场景 JSON 只是喂给已声明的 Harness fake 的输入，仅此而已；它不构成重放生产副作用的许可。跑一个 tier 用 `uv run python -m harness.cli run --tier pr --offline`。
