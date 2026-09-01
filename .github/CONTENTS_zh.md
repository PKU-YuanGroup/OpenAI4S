# 仓库治理

[English](CONTENTS.md)

面向 GitHub 的政策与自动化都放在这里：哪些路径由谁评审、依赖更新怎么进来、一个
PR 需要交代清楚哪些事，以及 GitHub 展示的社区健康文件（贡献指南、行为准则、
安全政策）。这些东西都不在 OpenAI4S daemon、Agent Engine 或内核里运行，
它们的作用是在变更进入这些运行时之前先把一道关。

## 文件

| 文件 | 职责 |
| --- | --- |
| `CODEOWNERS` | 把路径映射到评审人：先是兜底的默认负责人，再按运行时核心、安全敏感路径、Web 应用、compute、科学 Skill、测试和治理分别指定。匹配到的最后一条规则生效，因此具体规则会覆盖默认规则。 |
| `CODE_OF_CONDUCT.md` | 社区行为准则，GitHub 会从仓库的社区概况页链接到它。 |
| `CONTRIBUTING.md` | 治理文档：分支命名、PR/评审/发布政策、离线测试政策，以及带编号的 harness invariant。技术约定在根目录的 `CLAUDE.md` / `AGENTS.md` 里；这份文件负责流程侧。 |
| `SECURITY.md` | 私密漏洞报告流程，GitHub 会从 Security 标签页链接到它。疑似漏洞一律走这个流程，绝不通过公开 issue。 |
| `dependabot.yml` | 每周一为 `uv`、`npm`、`docker`、`pre-commit` 和 `github-actions` 五个生态提交依赖更新提案。`routine-dependencies` 多生态分组把四个 uv 开发工具的小版本/补丁升级、Black 之外的 pre-commit hook，以及 GitHub Action 的小版本/补丁升级合并为一个跨生态 PR。同一批生态另有带互补 ignore 规则的常规条目，只排除已被批次接管的更新类别，因此 uv 大版本与生产依赖、Black 和 Action 大版本仍各自恰好覆盖一次并走独立审查路径。npm 与 Docker 刻意保留各自独立的周一计划和 browser-tooling/base-image 生态内分组，因为它们不属于此前反复手工合并的范围。 |
| `pull_request_template.md` | 提 PR 时要填的清单：分支政策、改了什么、实际跑了哪些命令（没跑的也要写明原因）、核心依赖政策，以及哪些内容绝不能出现在一个公开仓库里。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| `ISSUE_TEMPLATE/` | 结构化的 issue 表单，以及公开 issue 里可以写什么的政策。 |
| `contributors/` | 贡献者头像，裁成圆形后提交在这里，供根目录的 README 引用。 |
| `workflows/` | 五个 GitHub Actions workflow：离线 CI 检查门、有界协议模糊测试、容器发布、draft-first 发布流水线和 OpenSSF Scorecard。凭据扫描是 CI 里的一个 job，而不是独立的 workflow。 |

## 在架构中的位置

路由、持久化、内核协议、权限或沙箱的变更，都得先通过这里定义的检查。但这并不意味着
本目录是一道安全边界。GitHub Actions 校验的是源码；真正在运行时生效的强制手段仍然在
`openai4s/security/`、`openai4s/host/` 和内核 manager 里。
