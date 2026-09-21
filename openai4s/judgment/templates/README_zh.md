# `openai4s/judgment/templates/`

[English](README.md)

实验性判断层的问题模板。每份模板都是英文、带版本号，并通过
`register_template` 注册，这样 kernel 代码只能点名模板 id。阈值写在使用它的
模块顶部。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 导入各模板模块，使它们在包被 import 时完成注册。 |
| [`skills.py`](skills.py) | `skills.suggest` Skill 推荐：fan-out、bioSkills 展开、精排三个阶段。 |
| [`bioskills_areas.json`](bioskills_areas.json) | 生成的领域索引（55 个领域、561 个成员），给 bioSkills 展开阶段用。用 `uv run python scripts/build_bioskills_area_index.py` 重新生成。 |
| [`literature.py`](literature.py) | `literature.screen`（片段 Noul）和 `literature.claim`（supports / contradicts / insufficient）。原文定位和数值单位比较留在 literature-review sidecar 里做。 |
| [`safety.py`](safety.py) | `safety.code`（7 类攻击 Noul）、`safety.injection`、`safety.trajectory`（ALLOW/ESCALATE/BLOCK）、`safety.bio_prescan`。一致率阈值写在模块顶部。 |
| [`task_mode.py`](task_mode.py) | `task_mode.classify`：在 `analysis_run` / `reusable_pipeline` / `codebase_change` 中三选一，选项描述为结构化的 `{what, not_for, examples}`。只做记录，不绑定交付证据。 |
