# third_party

[English](README.md)

OpenAI4S 以 git 子模块形式固定的源码树。它们不在 `openai4s` wheel 里，核心也从不依赖它们。

| 条目 | 说明 |
| --- | --- |
| [`OpenMHS`](OpenMHS) | [OpenMHS](https://mhs.openai4s.org)，开源的 Model Hardware Standard：SDK（`MHS/sdk/src`，导入名 `mhs`）、OpenAI4S 参考宿主（`integration/openai4s`，导入名 `openai4s_mhs`）和设备市场（`MHS/drivers`，746 个包）。固定在本版本 OpenAI4S 测试过的发行版。`openai4s/mhs_lab/openmhs.py` 会自动找到它，无需设置 `PYTHONPATH`。 |

用 `git clone --recursive` 获取；已有检出可运行 `git submodule update --init third_party/OpenMHS`。没有子模块的安装运行
`openai4s lab setup`，它会把同一个固定版本克隆到 `~/.openai4s/openmhs`。详见 [docs/openmhs_zh.md](../docs/openmhs_zh.md)。
