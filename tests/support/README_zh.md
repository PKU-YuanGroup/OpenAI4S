# 测试用运行时前缀辅助

[English](README.md)

给那些通过 `sys.executable` 观察环境绑定的测试用的共享夹具。裸的
`bin/python -> sys.executable` 符号链接不是虚拟环境：CPython 3.13 会把
符号链接路径写进 `sys.executable`，3.14 则报告解析后的基解释器，于是
点名所选解释器的 provenance 断言只在 3.14 上失败。带 `pyvenv.cfg` 的
前缀才是真正的选择机制，能让这些断言在两个版本上都保持强度。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`runtime_prefix.py`](runtime_prefix.py) | `install_runtime_prefix` 与 `install_named_runtime` 创建一个真实 venv（`pyvenv.cfg` 加上符号链接启动器），其解释器会自我报告这个前缀。 |
