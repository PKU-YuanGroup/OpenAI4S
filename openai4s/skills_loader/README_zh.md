# Skill 加载与版本管理

[English](./README.md)

**状态：已实现。** 本包发现以 recipe 为中心的 Skill，在请求渐进披露前只暴露摘要，对可选 Python sidecar 做结构校验，并通过原子 materialized view 管理不可变的用户 Skill 版本。

## 架构位置

Skill 是 Code-as-Action 的扩展面，不是原生 JSON 工具 schema。一个 Skill 目录包含 `SKILL.md`、可选 `kernel.py` 和可选资源。外层循环 prompt 只看到名称/摘要元数据；[`../tools/skills.py`](../tools/skills.py) 和 Host 服务按需加载完整 recipe。Agent 编写的 Python 随后可以在科学 Worker 内导入已校验 sidecar。

内置 Skill 为只读，并在名称冲突时优先。可写用户 Skill 位于配置的数据/project root 下，通过 Store 进行版本管理。能力状态针对当前 Store generation 解析，而不是保留已关闭 Store 的引用。

## 本目录直属文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 说明 Skill 目录契约，并重新导出 discovery、loader、value 和 version service API。 |
| [`loader.py`](./loader.py) | 解析 frontmatter，发现内置/用户 Skill，计算摘要/搜索匹配，解析能力状态，渐进暴露完整 recipe，构建 import 元数据，并编译检查 `kernel.py` sidecar。 |
| [`versions.py`](./versions.py) | 校验有界、无 symlink 的 Skill package；存储不可变版本；在旁路构建 personal/project view；并通过文件系统与数据库 compare-and-swap 恢复来激活或回滚。 |

## 直属子目录

无。

## Skill 编写与安全契约

- 把 `SKILL.md` 视为生成代码的 recipe，而不是可执行控制工具声明。
- sidecar 编译检查只能证明 Python 语法/结构；执行时仍适用正常 kernel 沙箱、权限和 import 规则。
- materialize 前拒绝不安全路径、symlink、超大文件/package 和非法规范名称。
- 保持内置 root 只读，并保留其相对可写名称的优先级。
