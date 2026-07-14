# 与代码同行的文档

[English](README.md)

此目录保存随 OpenAI4S 源码分发的文档，并维持历史兼容链接。公开双语网站由
[`Nobody-Zhang/openai4s-docs`](https://github.com/Nobody-Zhang/openai4s-docs)
单独维护；此目录中的内部计划不会发布到该网站。

## 文件

| 文件 | 职责与状态 |
|---|---|
| `architecture.md` | 当前 dual-loop 架构与 Host API 概览，也是贡献者使用的兼容入口。 |
| `ark-agent-plan-9.9.png` | 源码仓库根 README 展示的火山方舟 Agent 套餐价格截图。 |
| `backend-extension-guide.md` | Tool、Host service、storage、provider、Skill 与 Web session service 的当前扩展接缝。 |
| `backend-refactor-architecture.md` | 历史 backend-refactor 设计记录；不能证明当前已端到端实现。 |
| `compute.md` | Remote compute、BYOC provider 与 `host.fold` 的行为及限制。 |
| `configuration.md` | Provider、environment、daemon、kernel 与 data directory 配置。 |
| `jupyter.md` | Jupyter adapter 行为、执行边界与兼容说明。 |
| `package-architecture.md` | 分解工作期间使用的历史 package/ownership inventory。 |
| `plan-corecoder-refactor.md` | 内部历史重构计划；不进入公开网站内容。 |
| `refactor-plan.md` | 为决策上下文保留的历史迁移计划。 |
| `release-validation.md` | 离线 CI、package artifact、import 与外部 release gate。 |
| `security.md` | 威胁模型、信任边界、enforcement layer 与已知覆盖缺口。 |
| `skills.md` | Bundled/user Skill 格式、加载、sidecar 与生命周期。 |
| `webapp-api.md` | 详细 REST/WebSocket 功能面与兼容行为。 |
| `webapp.md` | Web workbench 概念、projection、状态与运维行为。 |

## 与框架的关系

可执行行为与测试优先于 prose。历史计划若与 `openai4s/` 或 `tests/` 冲突，应以实现和
contract test 为当前事实，并更新独立文档仓库。
