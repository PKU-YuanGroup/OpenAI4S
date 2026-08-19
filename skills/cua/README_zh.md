# CUA Skill

这个内置 Skill 是一份 recipe：把用户目标经托管 `cua` MCP 连接器交给 CUA 云端
Windows 电脑，并跟随其 outcome 状态机推进。目标原样传递给云桌面，只有
`completed` 状态下的 `result.text` 才被当作最终答案。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 先 ping，再原样委托目标，按 outcome 信封驱动 `cua_watch` / `cua_answer`，用 `cua_observe` 获取短期有效的桌面访问链接，只在用户明确要求时才取消。 |
