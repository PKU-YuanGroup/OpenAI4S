# Golden Trace

[English](README.md)

经审阅的参考数据放在这里。一条 golden trace 把某个具名契约、或某次生产 characterization 的规范化观察冻结下来，供后续运行逐字节比对。它不是可执行历史，也不允许拿它去重放副作用。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`.gitkeep`](.gitkeep) | 把这个根目录留在 git 里；trace 本身在下一层，按 schema 版本分目录存放。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`v1/`](v1/) | schema 版本 1 的经审阅 trace 资产。目前包括 r5 pre-change 生产 characterization，以及独立审阅的 Stage 0 Auto Mode 契约预期和非 Guardian 终态契约预期。 |

更新 golden 永远是有意为之的动作。对 characterization golden，先运行 `uv run python -m harness.cli characterize --write`，再把字节 diff 和它改动的 `current_behavior`、`desired_contract`、`known_bug` 字段放在一起审阅。Auto Mode 契约预期则根本没有 `--write` 路径：它们只能手工编辑、当作数据来审阅——正是为了让场景无法自行重新生成用来比对自己的那份东西。
