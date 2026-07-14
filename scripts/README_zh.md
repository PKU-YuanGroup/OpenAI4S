# Maintainer 与 release 脚本

[English](README.md)

这些脚本支持 setup、release validation、安全检查、目录文档覆盖、贡献者渲染和显式
选择的科学操作。它们不是 native Agent tool，也不会被导入正常 daemon loop。

## 文件

| 文件 | 职责 |
|---|---|
| `build_macos_dmg.sh` | 使用 relocatable CPython、源码、资源与可选科学栈构建 ad-hoc 签名的 macOS app/DMG。 |
| `check_directory_readmes.py` | 验证每个受维护目录都有结构成对的双语 README，覆盖全部直属文件/子目录，且本地 Markdown 链接可解析。 |
| `fold_remote.sh` | 为已配置的可信 GPU 主机提供显式选择的 Protenix 单序列 folding wrapper，并输出结构化 fold artifact。 |
| `release_import_smoke.py` | 在 checkout 外导入已安装的零依赖 wheel，并检查打包后的 runtime resource。 |
| `setup_envs.sh` | `python -m openai4s setup` 的薄 shell wrapper，用于四个 conda 环境。 |
| `source_secret_scan.py` | 零依赖、fail-closed 的凭据形状扫描器，不会回显匹配 secret。 |
| `update_contributors.py` | 获取 GitHub contributor、生成圆形 PNG 头像并更新双语 README 区块。 |
| `verify_release_artifacts.py` | 校验 wheel/sdist 路径、metadata、permission 与必需打包资源。 |
| `verify_release_tag.py` | 确保 release tag 与所有 literal package version 声明一致。 |

## 与框架的关系

Release 与安全脚本从控制平面外部验证它。`fold_remote.sh` 并不是通用部署保证：已注册的
remote-science service 仍须执行 capability check，并在远端安装不可用时返回明确错误。
