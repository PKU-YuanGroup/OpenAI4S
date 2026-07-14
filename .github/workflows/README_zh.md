# GitHub workflows

[English](README.md)

这些 workflow 构成仓库的验证与 release 控制平面。它们会运行代码，但不会随 Python
package 一起发布。

## 文件

| 文件 | 职责 |
|---|---|
| `ci.yml` | 强制执行分支命名、pre-commit、双语目录文档覆盖、typed core boundary、源码 secret scan、wheel/sdist 校验、Python 3.10/3.12 离线测试、浏览器 smoke 与定时 macOS sandbox smoke。 |
| `contributors.yml` | 定期重新生成贡献者头像及 README 中的贡献者区块。 |
| `release.yml` | 构建已验证发行包，并通过 OIDC 将获批 GitHub Release 发布到 PyPI。 |
| `scorecard.yml` | 运行 OpenSSF Scorecard 分析并发布安全结果。 |
| `secret-scan.yml` | 在 push、PR、定时和手动触发时用 Gitleaks 扫描 Git 历史。 |

默认测试套件必须保持离线。Live provider、GPU、SSH、package publication 与凭据应留在
单独授权的路径中。
