# 仓库治理

[English](README.md)

面向 GitHub 的政策与自动化都放在这里：哪些路径由谁评审、依赖更新怎么进来、一个
PR 需要交代清楚哪些事。这些东西都不在 OpenAI4S daemon、Agent Engine 或内核里运行，
它们的作用是在变更进入这些运行时之前先把一道关。

## 文件

| 文件 | 职责 |
| --- | --- |
| `CODEOWNERS` | 把路径映射到评审人：先是兜底的默认负责人，再按运行时核心、安全敏感路径、Web 应用、compute、科学 Skill、测试和治理分别指定。匹配到的最后一条规则生效，因此具体规则会覆盖默认规则。 |
| `dependabot.yml` | 每周一为 `uv`、`pre-commit` 和 `github-actions` 三个生态提交依赖更新提案，各自限制了同时打开的 PR 数量。Action 升级会合并成一个 PR，`uv` 也会把开发依赖的小版本和补丁升级合并；其余更新（生产依赖、大版本、`pre-commit`）仍然一个更新一个 PR。 |
| `pull_request_template.md` | 提 PR 时要填的清单：分支政策、改了什么、实际跑了哪些命令（没跑的也要写明原因）、核心依赖政策，以及哪些内容绝不能出现在一个公开仓库里。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| `ISSUE_TEMPLATE/` | 结构化的 issue 表单，以及公开 issue 里可以写什么的政策。 |
| `contributors/` | 贡献者头像，裁成圆形后提交在这里，供根目录的 README 引用。 |
| `workflows/` | GitHub Actions：CI、发布、贡献者墙、OpenSSF Scorecard 与 secret 扫描。 |

## 在架构中的位置

路由、持久化、内核协议、权限或沙箱的变更，都得先通过这里定义的检查。但这并不意味着
本目录是一道安全边界。GitHub Actions 校验的是源码；真正在运行时生效的强制手段仍然在
`openai4s/security/`、`openai4s/host/` 和内核 manager 里。
