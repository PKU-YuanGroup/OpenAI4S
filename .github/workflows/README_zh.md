# GitHub workflows

[English](README.md)

仓库的 CI 全在这三个文件里：每个 PR 都要过的离线检查门，加上 release 发布和
Scorecard。它们只用来跑这个仓库的代码，不会随 Python 包一起发布。

凭据扫描在 `ci.yml` 的源码凭据扫描任务里，由
[`scripts/source_secret_scan.py`](../../scripts/source_secret_scan.py) 用具名的
provider detector 读取工作树。

此前另有一个 Gitleaks 全历史扫描与它并存，现已退役。并不是因为它坏了——#57 刚把它
修好，用能扛住历史重写的锚定值 allowlist 取代了会被 squash 合并复制走的 commit SHA
指纹。

退役的理由是那次修复触及不到的成本。一条跑遍**全部历史**的通用熵规则会打到合成
fixture 上，而"必须看起来像真的、才能被被测代码找到"的 fixture，恰恰是这个仓库反复
需要的那一类。每一个都会变成又一行需要评审者逐条论证的 allowlist，而这个列表只增不
减：#57 收录了两个值，#63 当天就加上了第三个——而那个字符串在工作树里本已被内联注释
压制，原因是内联的 `gitleaks:allow` 覆盖不到"引入该行时注释还不存在"的那个 commit。
这些压制每一条都没错，但没有一条是免费的。

留下来的 detector 是具名的而非基于熵的，因此不需要任何 allowlist 就能对占位符保持
安静，同时仍能拦住同一文件里的真钥匙。放弃掉的是：一个曾被提交、后来又删掉的凭据不
再会被标记。若哪天又需要这项能力，手动跑一次 gitleaks 扫历史即可，而不是重新立起一个
需要持续喂列表的定时任务。

## 文件

| 文件 | 职责 |
| --- | --- |
| `ci.yml` | 默认的离线检查门。检查分支命名、跑 pre-commit、核对双语目录文档是否齐全、对核心编排边界做类型检查、扫描源码中的 secret、构建 wheel 与 sdist 并核对二者的内容、再把 wheel 单独装进一个干净的虚拟环境并跑通装好的 CLI、在 Python 3.10 和 3.12 上跑离线测试套件与确定性的 harness 契约，并在 Chromium 里驱动真实的工作台。要求 Seatbelt 隔离真正生效的 macOS 任务只在定时和手动触发时运行。 |
| `release.yml` | 在非预发布的 `v*` GitHub Release 被发布时触发：从该 tag 构建发行包，核对 tag 与两处版本声明是否一致，重新扫一遍源码，再从 `pypi` environment 经由 OIDC 发布到 PyPI。 |
| `scorecard.yml` | 在 `main` 的 push 和每周定时上运行 OpenSSF Scorecard，公开发布评分结果，并把 SARIF 上传到 code scanning。 |

默认测试套件必须保持离线。真实 provider、GPU、SSH、包发布与凭据都留在单独授权的
路径中。
