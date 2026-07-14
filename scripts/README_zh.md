# 维护者与发布脚本

[English](README.md)

面向维护者的脚本：环境搭建、发布校验、密钥扫描、目录文档覆盖、贡献者墙，以及一个
需要显式启用的科学操作。它们都不是 Agent 的原生 Tool，正常的 daemon 循环也不会导入
它们。

## 文件

| 文件 | 职责 |
| --- | --- |
| `build_macos_dmg.sh` | 打包 macOS `.app` 与 `.dmg`。内核要靠 `sys.executable` 拉起 worker，一旦把应用 freeze 掉就会坏，所以这里改成内嵌一份可重定位的独立 CPython，源码以散装 `.py` 的形式原样带上，并把 CORE 科学栈预装进运行时，首次启动不需要联网。签名只是 ad-hoc，不使用 Apple Developer 凭据。 |
| `check_directory_readmes.py` | 本文件必须通过的那项 CI 检查。每个受维护目录都要有 `README.md` 和 `README_zh.md`，两者标题序列与表格行数一致，每个直属文件和子目录都以反引号形式出现过，相对链接在磁盘上确实能解析到。 |
| `fold_remote.sh` | 在事先配置好的可信 GPU 主机上做 Protenix 单序列折叠，全程离线，不用 MSA。输出 `model.pdb`、`model.cif`、`confidence.json` 和 `plddt.csv`，再把一行 JSON manifest 和 base64 编码的交付物打到 stdout，调用方从日志里就能全部取回。需要显式启用。 |
| `release_import_smoke.py` | 用隔离环境的解释器、在 checkout 之外导入已安装的零依赖 wheel；一旦发现导入的其实是源码树就判失败。随后检查普通 import 测试照不到的地方：打包进去的 R worker、compute 模板与 Web UI、四份环境规格、Skill 目录、`python -m openai4s --help` 能否跑通，以及核心是否仍然没有非 extra 依赖。 |
| `setup_envs.sh` | `python -m openai4s setup` 的一层薄 `sh` 包装，用来创建四个 conda 环境。参数原样透传，所以 `--only python`、`--dry-run` 经它照样可用。 |
| `source_secret_scan.py` | 扫描发布源码树里形似凭据的内容，失败即拒绝。它只打印检测器名、路径和行号，绝不回显匹配到的值。零依赖：候选文件由 git 挑出，git 不可用时（例如解包后的源码归档）退回到确定性的文件系统遍历。 |
| `update_contributors.py` | 重建 Community Contributors 墙。用仓库自己的 token 从 GitHub API 拉取贡献者，把每个头像裁成圆形 PNG 写入 `.github/contributors/`，再改写两份根 README 中 `CONTRIBUTORS` 标记之间的区块。需要 Pillow。 |
| `verify_release_artifacts.py` | 只用标准库检查构建好的 wheel 与 sdist。先看必需文件是否齐全、有没有夹带不该带的东西（symlink、字节码、缓存、`.env`），再读 wheel metadata：MIT 许可、四个 Project-URL、`openai4s` 控制台入口点、平台无关的 `py3-none-any` tag，以及 wheel 里没有测试套件、核心没有非 extra 依赖。 |
| `verify_release_tag.py` | `vMAJOR.MINOR.PATCH` 形式的 release tag 必须与两处字面版本声明一致：`pyproject.toml` 里的 `[project] version` 和 `openai4s.__version__`；对不上就失败即拒绝。 |

## 在架构中的位置

发布与安全脚本是从控制平面外部检查它的，它们本身不属于控制平面。`fold_remote.sh` 同样
不是通用的部署保证：已注册的远程科学服务仍然要自己做 capability 检查，并在所需的远端
安装不可用时返回明确的错误。
