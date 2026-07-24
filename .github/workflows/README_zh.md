# GitHub workflows

[English](README.md)

仓库的 CI 全在这四个文件里：每个 PR 都要过的离线检查门，加上 release 发布、secret
扫描和 Scorecard。它们只用来跑这个仓库的代码，不会随 Python 包一起
发布。

## 文件

| 文件 | 职责 |
| --- | --- |
| `ci.yml` | 默认的离线检查门。检查分支命名、跑 pre-commit、核对双语目录文档是否齐全、对核心编排边界做类型检查、扫描源码中的 secret、构建 wheel 与 sdist 并核对二者的内容、再把 wheel 单独装进一个干净的虚拟环境并跑通装好的 CLI、在 Python 3.10 和 3.12 上跑离线测试套件与确定性的 harness 契约，并在 Chromium 里驱动真实的工作台。要求 Seatbelt 隔离真正生效的 macOS 任务只在定时和手动触发时运行。 |
| `release.yml` | 在非预发布的 `v*` GitHub Release 被发布时触发：从该 tag 构建发行包，核对 tag 与两处版本声明是否一致，重新扫一遍源码，再从 `pypi` environment 经由 OIDC 发布到 PyPI。 |
| `scorecard.yml` | 在 `main` 的 push 和每周定时上运行 OpenSSF Scorecard，公开发布评分结果，并把 SARIF 上传到 code scanning。 |
| `secret-scan.yml` | 用 Gitleaks 扫描 Git 历史中每一个可达 commit，而不只是本次改动；push、PR、每周定时和手动触发都会跑。Gitleaks 二进制按 checksum 固定，命中内容在日志里做脱敏。 |

默认测试套件必须保持离线。真实 provider、GPU、SSH、包发布与凭据都留在单独授权的
路径中。
