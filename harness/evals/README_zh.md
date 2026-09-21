# Harness 评测

[English](README.md)

离线 eval fixture 和给它们打分的代码放在这里。一次 eval 衡量的是一整组 case 上的某条架构或质量边界，这和 [`../../tests/`](../../tests/) 里那些聚焦断言不是一回事；它补充断言，不替代断言。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 导出 Action routing 和逆合成外部后端评测接口。 |
| [`action_routing.py`](action_routing.py) | 给确定性路由函数 `route_action` 打分。每条 fixture 是一份录制下来的模型回复，各代表一类任务：原生 Tool batch、Python 或 R Cell、Engine finalization、不能被当成完成信号的普通散文、不支持的 fence，以及两条优先级规则——原生 Tool batch 优先于 fence 里的 Cell，一条回复里也只路由第一个 Cell。报告给出准确率、混淆情况，以及每条 case 的通过与否。 |
| [`retrosynthesis_backends.py`](retrosynthesis_backends.py) | 不加载模型权重，而是把版本化的外部模型响应重新送入生产响应规范化器。它评测 schema、预期成功/错误行为、预测数量、checkpoint provenance 完整度、带分数预测覆盖率，以及确定性的响应摘要。 |
| [`retrosynthesis_backend_cases.json`](retrosynthesis_backend_cases.json) | 公开安全的合成响应 tape：一个成功的 RetroChimera 形状预测批次，以及一个禁止 checkpoint 自动下载时的拒绝结果。fixture 不包含模型权重、私有化学信息、网络结果或真实 checkpoint 路径。 |
| [`.gitkeep`](.gitkeep) | 把目录留在 git 里，与当前有哪些计分代码无关。 |
| [`judgment_skills.py`](judgment_skills.py) | 冻结的 Skill 推荐评测（plan §7）。离线打分 B0 词法检索和 B2 词典扩展；B1（主模型改写）、J1（只跑 `suggest` 第一次请求）和 J2（完整 `suggest`）需要 `--live`。报告 top-1 / top-3、错误推荐、无谓推荐、弃权、延迟、token 和 bootstrap 95% 置信区间，并按语言和类别分开。`--split test` 必须带与模板版本一致的 `--frozen-thresholds`。 |
| [`judgment_skills_cases.json`](judgment_skills_cases.json) | 约 200 条 agent 撰写的中英文 query，带 gold Skill 名，按 `lang × category` 分层、种子 20260920、开发/测试各 50%。仍需人工抽检至少 20%。 |
| [`judgment_skills_cases.lock`](judgment_skills_cases.lock) | 测试集（按 id 排序后的规范 JSON）的 SHA-256。测试 query 或 gold 一变，锁校验就会失败。 |
| [`judgment_skills_zh_en_terms.json`](judgment_skills_zh_en_terms.json) | B2 用的中英术语词典，最多 300 条，只用开发集构建；构建方法写在 `_construction`。 |
| [`judgment_literature.py`](judgment_literature.py) | 冻结的文献结论核验评测（plan §7）。离线 `CODE` 打分原文定位和数值/单位比较；`LLM`（主模型三选一）和 `J`（`check_claims`）需要 `--live`。报告各状态准确率、混淆矩阵、高置信错误、支持/矛盾召回、人工复核比例、延迟、token 和 bootstrap 95% 置信区间，并按语言分开。`--split test` 必须带与模板版本一致的 `--frozen-thresholds`。 |
| [`judgment_literature_cases.json`](judgment_literature_cases.json) | 约 300 对开放获取（结论, 片段），带 CC 许可证和 DOI，中文约 30%，按 `lang × perturbation` 分层、种子 20260920、开发/测试各 50%。结论是合成改写，仍需人工抽检至少 20%。 |
| [`judgment_literature_cases.lock`](judgment_literature_cases.lock) | 测试集（按 id 排序后的规范 JSON）的 SHA-256。测试结论、引文、来源或 gold 一变，锁校验就会失败。 |

两个 evaluator 都完全确定性，不需要 provider key、网络、内核、GPU 或可选模型包。Action routing 对应 [`../../tests/test_action_routing_eval.py`](../../tests/test_action_routing_eval.py)；外部模型协议与 replay 契约由 [`../../tests/test_harness_contract.py`](../../tests/test_harness_contract.py) 覆盖。
