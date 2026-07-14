# 仓库治理

[English](README.md)

此目录保存面向 GitHub 的政策与自动化。它不会在 OpenAI4S daemon、Agent
Engine 或 kernel 中运行；其作用是在变更进入这些 runtime 功能面之前进行保护。

## 文件

| 文件 | 职责 |
|---|---|
| `CODEOWNERS` | 为安全敏感路径和各子系统路径指定评审负责人。 |
| `dependabot.yml` | 配置自动依赖更新提案。 |
| `pull_request_template.md` | 定义公开 PR checklist、分支政策、验证证据和披露规则。 |

## 子目录

| 目录 | 职责 |
|---|---|
| `ISSUE_TEMPLATE/` | 结构化 issue 表单与 issue 创建政策。 |
| `contributors/` | 根 README 使用的已提交圆形贡献者头像。 |
| `workflows/` | CI、release、贡献者、scorecard 与 secret scan 自动化。 |

## 与框架的关系

路由、持久化、kernel protocol、permission 或 sandboxing 的变更必须通过这里定义的检查，
但此目录本身不是安全边界。GitHub Actions 校验源码；runtime enforcement 仍位于
`openai4s/security/`、`openai4s/host/` 与 kernel manager 中。
