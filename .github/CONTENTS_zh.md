# 仓库治理

[English](CONTENTS.md)

面向 GitHub 的政策与自动化都放在这里：哪些路径由谁评审、依赖更新怎么进来、一个
PR 需要交代清楚哪些事。这些东西都不在 OpenAI4S daemon、Agent Engine 或内核里运行，
它们的作用是在变更进入这些运行时之前先把一道关。

## 文件

| 文件 | 职责 |
| --- | --- |
| `CODEOWNERS` | 把路径映射到评审人：先是兜底的默认负责人，再按运行时核心、安全敏感路径、Web 应用、compute、科学 Skill、测试和治理分别指定。匹配到的最后一条规则生效，因此具体规则会覆盖默认规则。 |
| `codeql-config.yml` | 只排除一份精确、字节级固定、仅被解析而不会执行的 arXiv 测试 fixture。即使某个合成 bioSkills 示例需按单告警消除误报，可执行 bioSkills 仍保持在扫描范围内。仓库的 `github-codeql-config-file` 属性必须指向这里；离线治理测试明确禁止通配符。 |
| `dependabot.yml` | 每周一为 `uv`、`npm`、`docker`、`pre-commit` 和 `github-actions` 五个生态提交依赖更新提案，各自限制了同时打开的 PR 数量；npm 与 Docker 条目分别持续更新带完整性锁定的浏览器驱动依赖图和按摘要固定的容器基础镜像。Action 升级合并成一个 PR；`uv` 合并开发依赖的小版本与补丁升级，`pre-commit` 也合并 hook 的小版本与补丁升级，`npm` 与 `docker` 出于同样的理由也各自合并——一个包一个 PR，就是一个包一整套 CI 矩阵。大版本被有意排除在所有分组之外，生产依赖则根本不分组，因此这两类仍然一个更新一个 PR——hook 的大版本升级是一次 lint 或代码风格政策的变更，必须单独审读，而不是作为版本表里的一行被合并掉。 |
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
