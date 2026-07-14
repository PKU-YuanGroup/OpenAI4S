---
title: Skills
description: OpenAI4S Skills 的发现、执行、所有权和版本管理。
outline: deep
status: current
audience: [contributors, operators, users]
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# Skills——代码能力，而非 schema

> 已于 2026-07-14 对仓库 revision `a92e736` 完成核验。Inventory 数量由
> `skills/` 下的内置目录生成，不构成兼容性承诺。

一个 Skill 是
[`skills/`](https://github.com/PKU-YuanGroup/OpenAI4S/tree/main/skills)
下的一个目录：

```
skills/example_stats/
    SKILL.md      以配方为中心的文档（代码示例，而非 JSON schema）
    kernel.py     可导入的 sidecar 模块（辅助函数）
```

Skill 通过**编写代码**来使用。Loader 以 *progressive disclosure* 向模型展示每个 `SKILL.md`：开始时只有一行摘要，需要时再用 `host.search_skills(query)` 获取全文。Kernel 将 `skills/` 加入 `sys.path`，agent 随后可以运行例如 `from example_stats.kernel import summary`。Skill 的能力最终表现为**内核中的可调用 Python**，与核心范式一致，而不是另一套工具 schema。

## 内置 Skills（32）

| 分类 | Skills |
|---|---|
| **结构预测**（GPU） | `alphafold2` · `openfold3` · `boltz` · `chai1` · `esmfold2` |
| **序列 / 组学 / 对接**（GPU） | `fair-esm2` · `evo2` · `borzoi` · `scgpt` · `scvi-tools` · `diffdock` |
| **蛋白质设计**（GPU） | `proteinmpnn` · `ligandmpnn` · `solublempnn` |
| **科研工作流** | `literature-review` · `pdf-explore` · `paper-narrative` · `figure-composer` · `figure-style` · `indication-dossier` · `retrosynthesis_planning` · `mineral_spectra_analysis` · `admet_genetic` · `protein-mutation-enhancement` · `catalyst_sar_screening` |
| **数据与模型工作流** | `audit-dataset` · `evaluate-model` · `plan-ml-experiment` |
| **平台** | `remote-compute-nvidia` · `remote-compute-ssh` · `using-model-endpoint` |

`example_stats` 是参考示例 Skill（纯标准库 descriptive-statistics helper）。

## 编写 Skill

1. 创建 `skills/<name>/SKILL.md`：先写简短 YAML frontmatter（`name`、`description`，以及可选的 `origin`、`category`、`requirements: [gpu]`），正文提供**可运行代码示例**。
2. 可选地添加包含可导入辅助函数的 `kernel.py`。
3. Loader 会在下次运行时发现它，并向 agent 显示一行摘要。内置 Skill（`origin: openai4s`）只读；你创建或导入的 Skill 可在 UI 的 **Customize → Skills** 中编辑。

GPU/模型 Skill（`requirements: [gpu]`）描述如何通过 [`host.compute`](compute.md) 请求兼容的远程 provider。配方能否运行，仍取决于所选 provider、它的实现状态及其暴露的环境。没有该要求的 Skill 通常在所选持久 kernel environment 中运行。

## 可写 Skill 的版本与回滚

内置 `openai4s` Skill 始终权威且只读。可写 Skill 有两个明确的分发作用域：

- `personal` 位于 `<data_dir>/user-skills`；除非 capability policy 禁用，否则每个 project 都可用；
- `project` 位于按 project identity 隔离的 overlay 中，只会被绑定相同 `project_id` 的 `SkillLoader` 发现。Project Skill 会覆盖同名 personal Skill，但两者都不能遮蔽内置 Skill。

`SkillVersionService` 是安装、升级、发布、列出历史和回滚这些 package 的窄 stdlib API。每次操作都会捕获 `SKILL.md`、`kernel.py` 的精确 bytes 及受限的 resource file。SQLite 保存不可变、按 SHA-256 寻址的 blob、不可变 canonical manifest 和追加式 installation event。Active version 以 compare-and-swap 语义修改；runtime directory 在替换前先 staged 并验证，pointer 更新失败时恢复原目录。回滚或删除后，较新的版本仍被保留。

```python
from openai4s.skills_loader import SkillVersionService

versions = SkillVersionService()
installed = versions.install(
    "assay-qc",
    {
        "SKILL.md": "---\nname: assay-qc\norigin: personal\n---\nQC recipe\n",
        "kernel.py": "def accepted(x): return x >= 0.9\n",
    },
)
history = versions.history("assay-qc")
versions.rollback("assay-qc", installed["version_id"])
```

对于 project-local 内容，向同一组 method 传递 `scope="project", project_id="..."`，并用相同 `project_id` 构造 runtime loader。Package ingestion 会拒绝 traversal path、symlink、超大 file/package、非法 UTF-8 文档、trusted-origin 声明，以及（在 install/publish 时）无法通过 compile gate 的 `kernel.py`。Draft editor 可以把损坏的 sidecar 保留为 versioned draft，但在它成功编译前，publish 始终 fail closed。

相同生命周期也通过三个命名 JSON control-tool class 暴露：`skill_status`、`skill_history` 和 `rollback_skill_version`。Status/history 只读；rollback 声明 runtime mutation，需要审批，由 `HostDispatcher` 审计，并且只能访问 `personal` 或 dispatcher 当前 `project` scope。Python Cell 暴露对应的 `host.skills.status(...)`、`host.skills.history(...)` 和 `host.skills.rollback(...)` method。

Customize 使用窄 HTTP route。Personal history/rollback 位于 `/api/skills/<name>/versions` 和 `/api/skills/<name>/rollback`；project-local 状态使用 `/api/projects/<project_id>/skills/<name>/versions` 和 `.../rollback`。Project ID 由 path 限定，并在 Store 中校验；内置 Skill 永远不暴露 rollback action。
