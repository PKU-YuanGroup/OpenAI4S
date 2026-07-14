# 科学 Kernel 环境

[English](README.md)

这些 conda 规格定义 `openai4s setup` 创建的四个可选任务环境。它们属于执行平面，
不是标准库控制平面的强制依赖。

## 文件

| 文件 | 职责 |
|---|---|
| `python.yml` | 用于数据分析和绘图 workload 的通用 Python 科学环境。 |
| `phylo.yml` | 面向系统发育与生物信息学的环境及命令行工具。 |
| `r.yml` | 独立 R kernel 通道所需的 R interpreter 与 packages。 |
| `struct.yml` | 面向结构生物学与化学信息学的 Python 环境。 |

所选环境会改变 worker interpreter，但路由、permission、storage 与 Host RPC 仍由
daemon 拥有。可选 package 应放在这里，而不是给零依赖 core 增加 hard import。
