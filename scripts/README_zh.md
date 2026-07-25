# 维护者与发布脚本

[English](README.md)

面向维护者的脚本：环境搭建、发布校验、密钥扫描、目录文档覆盖、贡献者墙，以及一个
需要显式启用的科学操作。它们都不是 Agent 的原生 Tool，正常的 daemon 循环也不会导入
它们。

## 文件

| 文件 | 职责 |
| --- | --- |
| `build_macos_dmg.sh` | 打包 macOS `.app` 与 `.dmg`。内核要靠 `sys.executable` 拉起 worker，一旦把应用 freeze 掉就会坏，所以这里改成内嵌一份可重定位的独立 CPython，源码以散装 `.py` 的形式原样带上，并把 CORE 科学栈预装进运行时，首次启动不需要联网。签名只是 ad-hoc，不使用 Apple Developer 凭据。 |
| `capture_response_schemas.py` | 重新生成（加 `--check` 则是校验）[`docs/response-schemas.json`](../docs/response-schemas.json)。装上捕获后跑一遍离线套件，记录每条 route 真正返回了什么；从真实响应导出的 schema 不可能描述代码根本不会产生的响应，覆盖率也因此是量出来的数字而不是一句断言。`--check` 只在会打断客户端的变化上失败——字段被删、保证被撤、类型被放宽。仅仅增量移动的形状、以及新增或丢失覆盖的 route 会打印但不失败：捕获结果本身还取决于装了哪些可选 extra、以及某个平台跳过了哪些测试，而一道总在狼来了的门最后只会被人重新生成到失去意义。两种模式都会逐条列出没有任何离线测试触达的 route——今天是 143 条里的 93 条——因为光有一个覆盖率数字，谁也没法据此行动。 |
| `check_directory_readmes.py` | 本文件必须通过的那项 CI 检查。每个受维护目录都要有 `README.md` 和 `README_zh.md`，两者标题序列与表格行数一致，每个直属文件和子目录都以反引号形式出现过，相对链接在磁盘上确实能解析到。 |
| `connector_canary.py` | 询问 UniProt、RCSB PDB、OpenAlex 是否仍返回 connector 所解析的东西。仅定时/手动运行——公共 API 的宕机不构成让 PR 失败的理由——它**仅**在真实 schema 漂移时（一个 200 响应里 required 字段没了）以非零退出，绝不在上游不可达时（超时、5xx、HTML 页面）失败。宕机与漂移的区分是整件事的核心，并用注入的 fetch 离线测过。 |
| `dmg_bundled_packages.txt` | 预装进 macOS 应用的科学栈,每行 `<pip 名> <import 名>`——即默认 `python.yml` 内核环境里可 pip 安装的超集(rdkit、scanpy、numba、umap、单细胞、化学信息学……)。单一事实来源:`build_macos_dmg.sh` 按 pip 名安装,`verify_macos_bundle.py` 校验每个 import 都从 bundle 内解析,两者不会漂移。torch/fair-esm 以及 conda 专属的 R 与 bioconda 工具刻意不含。 |
| `make_app_icon.py` | 按品牌标识的实测几何——五个成键原子、中央终端方块、红色提示符 `>` 与光标条——用平面矢量图元重绘 `assets/app-icon-1024.png`，超采样后落到 Big Sur 图标网格上。该标识在仓库里只有 150px 的字形和 64px 的 favicon 两份位图，放大到 `.icns` 需要的 1024px 都会糊。仅开发用：依赖 Pillow，而 DMG 构建真正消费的是它提交进仓库的产物。 |
| `fold_remote.sh` | 在事先配置好的可信 GPU 主机上做 Protenix 单序列折叠，全程离线，不用 MSA。输出 `model.pdb`、`model.cif`、`confidence.json` 和 `plddt.csv`，再把一行 JSON manifest 和 base64 编码的交付物打到 stdout，调用方从日志里就能全部取回。需要显式启用。 |
| `release_import_smoke.py` | 用隔离环境的解释器、在 checkout 之外导入已安装的零依赖 wheel；一旦发现导入的其实是源码树就判失败。随后检查普通 import 测试照不到的地方：打包进去的 R worker、compute 模板与 Web UI、四份环境规格、Skill 目录、`python -m openai4s --help` 能否跑通，以及核心是否仍然没有非 extra 依赖。 |
| `setup_envs.sh` | `python -m openai4s setup` 的一层薄 `sh` 包装，用来创建四个 conda 环境。参数原样透传，所以 `--only python`、`--dry-run` 经它照样可用。 |
| `source_secret_scan.py` | 扫描发布源码树里形似凭据的内容，失败即拒绝。它只打印检测器名、路径和行号，绝不回显匹配到的值。零依赖：候选文件由 git 挑出，git 不可用时（例如解包后的源码归档）退回到确定性的文件系统遍历。 |
| `update_contributors.py` | 重建 Community Contributors 墙。用仓库自己的 token 从 GitHub API 拉取贡献者，把每个头像裁成圆形 PNG 写入 `.github/contributors/`，再改写两份根 README 中 `CONTRIBUTORS` 标记之间的区块。需要 Pillow。 |
| `verify_macos_bundle.py` | 只用标准库检查构建好的 `.app` 或 `.dmg`——这是 wheel 检查看不到的那份契约。它以只读方式挂载镜像，然后在下列情况下失败关闭：内嵌解释器没能随 bundle 重定位、预装运行时缺了任何一个 `CORE_PACKAGES` 导入、`Info.plist` 与 `openai4s.__version__` 对不上、缺少 Web UI / R worker / compute 模板 / Skill 目录、`python -m openai4s --help` 无法离线运行、代码签名校验不过，或者镜像里混进了 dotenv 及任何形似凭据的内容。 |
| `describe_macos_image.py` | 挂载构建好的 `.dmg`，在旁边写两份佐证：从 `codesign` 读出的签名 authority 链，以及镜像里真正内嵌的运行时的包清单。发布暂存作业跑在 Linux 上、两样都拿不到，所以这份证据必须在构建镜像的那台机器上产生并随镜像一起走——没有它，发布闸门只能退回去读「签名身份变量是否非空」，而 ad-hoc 签名的镜像同样满足这一条。 |
| `verify_release_artifacts.py` | 只用标准库检查构建好的 wheel 与 sdist。先看必需文件是否齐全、有没有夹带不该带的东西（symlink、字节码、缓存、`.env`），再读 wheel metadata：MIT 许可、四个 Project-URL、`openai4s` 控制台入口点、平台无关的 `py3-none-any` tag，以及 wheel 里没有测试套件、核心没有非 extra 依赖。 |
| `verify_release_tag.py` | `vMAJOR.MINOR.PATCH` 形式的 release tag 必须与两处字面版本声明一致：`pyproject.toml` 里的 `[project] version` 和 `openai4s.__version__`；对不上就失败即拒绝。 |
| `release_pipeline.py` | 发布流程本身，写成脚本而不是 workflow YAML。嵌在事件触发里的步骤只能靠真发一次版来演练——被它本该保护的那件事反过来测试它——所以这份东西能在笔记本上跑、能 `--dry-run`、也能被 pytest 跑。所有不可逆的都排在最后：GitHub 翻牌发生在 PyPI 已经拿到该版本之后，并且在草稿与 PyPI 分发件不完全一致时拒绝执行。 |
| `capture_response_contract.py` | 通过驱动离线测试套件、记录每个 route 实际返回什么，来重新生成 [`docs/response-contract.json`](../docs/response-contract.json) 的抓取侧。手写的契约是由人签字的那一侧；这一侧回答的是服务端是否还与它一致。 |

## 在架构中的位置

发布与安全脚本是从控制平面外部检查它的，它们本身不属于控制平面。`fold_remote.sh` 同样
不是通用的部署保证：已注册的远程科学服务仍然要自己做 capability 检查，并在所需的远端
安装不可用时返回明确的错误。
