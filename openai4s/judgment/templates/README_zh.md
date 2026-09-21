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
| [`features.py`](features.py) | `features.custom`：由调用方提供的 Noul/Score 题目，用于文本特征（`allow_custom`，每次最多 24 题，instructions ≤400 字符，state ≤8000 字符）。 |
