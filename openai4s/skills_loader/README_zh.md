# Skill 加载与版本管理

[English](README.md)

这里管两件事：Skill 的发现，和 Skill 的版本。loader 找到以 recipe 为中心的 Skill，在渐进披露真正来取全文之前，只把摘要交给外层循环；版本服务把可写 Skill 的包体作为不可变版本存进 Store，磁盘上的目录视图则整体原子替换。两条路径都会先对可选的 Python sidecar 做编译检查，之后内核才可能 import 它。

## 在架构中的位置

Skill 是 Code-as-Action 的扩展面，不是原生 JSON 工具 schema。一个 Skill 目录里放着 `SKILL.md`、可选的 `kernel.py` sidecar 和可选资源。外层循环的 prompt 只看得到名称和一行摘要；[`../tools/skills.py`](../tools/skills.py) 与 Host 服务会在任务确实需要时才取回完整 recipe。Agent 写出的 Python 随后就能在科学 worker 里导入这个已通过编译检查的 sidecar。

内置 Skill 是只读的，名称冲突时也由它胜出。可写 Skill 位于配置好的数据目录和 project root 下，版本由 Store 管理。默认 loader 自己不持有仓储对象，每次读写能力状态都重新向当前的 Store generation 要一次：一个 loader 完全可能活得比创建它的那个 Store 更久，否则就会指向一条已经关闭的连接。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 用 docstring 写清 Skill 目录的约定，并重新导出公开名字：`Skill`、`SkillLoader`、`SkillVersionService` 和 `discover_skills`。 |
| [`loader.py`](./loader.py) | 负责找到 Skill，并决定露出多少。它解析 `SKILL.md` 的 frontmatter，扫描内置、project 与用户三个 root，解析能力状态，并按关键词重合度给搜索结果打分。系统 prompt 只拿得到摘要；完整 recipe、sidecar 的 import 提示，以及内核启动用的 bootstrap manifest 都按需生成。`kernel.py` sidecar 在被任何人 import 之前，先过一遍编译检查。 |
| [`versions.py`](./versions.py) | 可写 Skill 的安装、升级、发布、回滚与删除。包体先校验（大小有界、不含 symlink、路径不得越出目录），再作为不可变版本存起来；磁盘上的 personal/project 目录只是一份视图，先在旁边重建好再整体换入。数据库侧的激活走 compare-and-swap；这一步失败时，会先把原来的目录换回来，错误才向上抛。 |

## Skill 编写与安全契约

- `SKILL.md` 是给 Agent 照着写代码的 recipe，不是可执行的控制工具声明。
- 编译检查只能证明 sidecar 语法和结构没问题，证明不了它到底会做什么：真正执行时，内核沙箱、Host 权限和正常的 import 规则一样都不会少。
- 不安全路径、symlink、超限的单个文件或整个包、非法的规范名称，都会在写成可用的 Skill 目录之前被挡掉。
- 内置 root 保持只读；名称撞车时，内置的一方永远优先于可写的一方。
