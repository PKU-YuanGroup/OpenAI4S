# 维护者与发布脚本

[English](README.md)

面向维护者的脚本：环境搭建、发布校验、密钥扫描、目录文档覆盖、两道抓取式响应门禁、
上游 schema 金丝雀、贡献者墙，以及一个需要显式启用的科学操作。它们都不是 Agent 的原生
Tool，`openai4s/` 下也没有任何模块会导入它们。

## 文件

| 文件 | 职责 |
| --- | --- |
| `build_macos_dmg.sh` | 打包 macOS `.app` 与 `.dmg`。内核要靠 `sys.executable` 拉起 worker，一旦把应用 freeze 掉就会坏，所以这里改成内嵌一份可重定位的独立 CPython，源码以散装 `.py` 的形式原样带上，并把 CORE 科学栈预装进运行时，首次启动不需要联网。签名只是 ad-hoc，不使用 Apple Developer 凭据。 |
| `build_linux_bundle.sh` | 打包 Linux `.tar.gz`：同一份内嵌的可重定位 CPython、同一份预装科学栈、同样原样带上的源码树，外加一个自定位启动器、一份 `.desktop` 模板和面向单用户的 `install.sh`。刻意不做 AppImage——一个靠 FUSE 挂载的 squashfs，对于「本职就是在 bubblewrap 下拉起子进程」的程序是错误的宿主，而且它会在执行 cell 的时候才坏，不是在启动的时候。可以在 macOS 上交叉构建（全程不编译），只跳过那些必须**运行**目标才能做的检查，并且明说跳过了。 |
| `build_windows_zip.sh` | 打包 Windows `.zip`。这不是原生 Windows 构建：`platform_support.py` 在 win32 上拒绝启动内核，所以这里发的是一个包着 Linux bundle 的 Windows 启动器——首次运行把它装进 WSL2，再用 Windows 浏览器打开转发出来的端口。它消费的就是同一次发布里那个 Linux 制品本身，而不是另起一次「理应一致」的构建。源文件在 `windows/`。 |
| `bundle_contract.py` | 把「每个发出去的桌面包都必须满足什么」写在一处：预装包清单、只在运行时才暴露缺失的资源（Web UI、R worker、compute 模板）、Skill 目录数量下限、hash-based 字节码规则，以及凭据扫描。三个校验器共用它，理由和两个沙箱冒烟测试共用一份实现是同一个：两份拷贝迟早会漂移，直到某个平台悄悄不再检查别人还在检查的东西——而那个平台正是会把坏镜像发出去的那个。 |
| `capture_response_schemas.py` | 重新生成（加 `--check` 则是校验）[`docs/response-schemas.json`](../docs/response-schemas.json)。装上捕获后跑一遍离线套件，记录每条 route 真正返回了什么；从真实响应导出的 schema 不可能描述代码根本不会产生的响应，覆盖率也因此是量出来的数字而不是一句断言。`--check` 会在会打断客户端的变化上失败——字段被删、保证被撤、类型被放宽。仅仅增量移动的形状、以及新增或丢失覆盖的 route 会打印但不失败：捕获结果本身还取决于装了哪些可选 extra、以及某个平台跳过了哪些测试，而一道总在狼来了的门最后只会被人重新生成到失去意义。两种模式都会逐条列出没有任何离线测试触达的 route，因为光有一个覆盖率数字，谁也没法据此行动——而这份名单如今已经清空，`--check` 也就据此失败了：一条 route 不能再一边留在契约里、一边没有任何被冻结的形状，`/frames/<id>/admissions/<id>` 当年正是这样待了 43 个提交，那时这份名单只是被当成指标打印出来。整个产物还压在一条溯源规则上：凡是把某个服务换成 stub 的测试都必须挂 `stubbed_backend` marker，它会在该测试运行期间暂停记录器。少了它，stub 编造出来的 `{"ok": true}` 就会被当成这条 route 的真实契约发布出去——这是错误的溯源，比没有溯源更糟，因为读者会当真。 |
| `check_directory_readmes.py` | 本文件必须通过的那项 CI 检查。每个受维护目录都要有 `README.md` 和 `README_zh.md`，两者标题序列与表格行数一致，每个直属文件和子目录都以反引号形式出现过，相对链接在磁盘上确实能解析到。runtime/cache 目录不参与；逐字节 fixture、Web vendor 资产及固定版本、机械生成的 `skills/bioskills/` 集合由各自边界的中英文档统一说明，不去改写第三方/生成子目录。 |
| `connector_canary.py` | 询问 UniProt、RCSB PDB、OpenAlex 是否仍返回 connector 所解析的东西。仅定时/手动运行——公共 API 的宕机不构成让 PR 失败的理由——它**仅**在真实 schema 漂移时（一个 200 响应里 required 字段没了）以非零退出，绝不在上游不可达时（超时、5xx、HTML 页面）失败。宕机与漂移的区分是整件事的核心，并用注入的 fetch 离线测过。 |
| `container_smoke.sh` | 构建容器镜像，并验证 daemon 在里面真的能跑——这与「镜像构建成功」是两回事。它检查的是容器化专门坑过本程序的那些点：daemon 能否在无凭据下应答 `/health`、是否拒绝无凭据的 API 调用、是否接受自己铸造的令牌、是否以 uid 1000 运行、科学栈能否导入，以及收到 `SIGTERM` 是否以 0 退出。最承重的是重启那一项——它在 `SIGKILL` 之后往卷上强行写入一个会撞上的 pidfile，因为单例原先那套只看存活的判断，会让一个被杀掉的容器从此再也起不来。需要 Docker daemon；在笔记本上和在 CI 里跑的是同一份。 |
| `bundled_packages.txt` | 预装进每一个发出去的桌面包的科学栈，每行 `<pip 名> <import 名>`——即默认 `python.yml` 内核环境里可 pip 安装的超集（rdkit、scanpy、numba、umap、单细胞、化学信息学……）。跨平台的单一事实来源：两个构建脚本都按 pip 名安装，每个校验器都断言这些 import 是从包内解析的，所以既不会「装的」和「查的」漂移，也不会两个平台悄悄装了不同的栈。torch/fair-esm 以及 conda 专属的 R 与 bioconda 工具刻意不含。 |
| `make_app_icon.py` | 按品牌标识的实测几何——五个成键原子、中央终端方块、红色提示符 `>` 与光标条——用平面矢量图元重绘 `assets/app-icon-1024.png`，超采样后落到 Big Sur 图标网格上。该标识在仓库里只有 150px 的字形和 64px 的 favicon 两份位图，放大到 `.icns` 需要的 1024px 都会糊。仅开发用：依赖 Pillow，而 DMG 构建真正消费的是它提交进仓库的产物。 |
| `fold_remote.sh` | 在事先配置好的可信 GPU 主机上做 Protenix 单序列折叠，全程离线，不用 MSA。输出 `model.pdb`、`model.cif`、`confidence.json` 和 `plddt.csv`，再把一行 JSON manifest 和 base64 编码的交付物打到 stdout，调用方从日志里就能全部取回。需要显式启用。 |
| `import_bioskills.py` | 把经过审计并固定版本的 GPTomics/bioSkills checkout 确定性地转换到 `skills/bioskills/`：生成 561 个无目录冲突的 Skill，按 Codex 约定整理 `scripts/` 与 `references/`，补 OpenAI4S 只读/来源 frontmatter，并生成 SHA-256 manifest。脚本本身不联网；commit 不符、Skill 数量变化、声明名重复或目标非空都会拒绝执行。 |
| `release_import_smoke.py` | 用隔离环境的解释器、在 checkout 之外导入已安装的零依赖 wheel；一旦发现导入的其实是源码树就判失败。随后检查普通 import 测试照不到的地方：打包进去的 R worker、compute 模板与 Web UI、四份环境规格、Skill 目录、`python -m openai4s --help` 能否跑通，以及核心是否仍然没有非 extra 依赖。 |
| `setup_envs.sh` | `python -m openai4s setup` 的一层薄 `sh` 包装，用来创建四个 conda 环境。参数原样透传，所以 `--only python`、`--dry-run` 经它照样可用。 |
| `source_secret_scan.py` | 扫描发布源码树里形似凭据的内容，失败即拒绝。它只打印检测器名、路径和行号，绝不回显匹配到的值。零依赖：候选文件由 git 挑出，git 不可用时（例如解包后的源码归档）退回到确定性的文件系统遍历。 |
| `update_contributors.py` | 重建 Community Contributors 墙。用仓库自己的 token 从 GitHub API 拉取 commit 贡献者，追加一份维护中的、已公开署名的非 commit 贡献者名单，把每个头像裁成圆形 PNG 写入 `.github/contributors/`，再改写两份根 README 中 `CONTRIBUTORS` 标记之间的区块。需要 Pillow。 |
| `verify_macos_bundle.py` | 只用标准库检查构建好的 `.app` 或 `.dmg`——这是 wheel 检查看不到的那份契约。它以只读方式挂载镜像，然后在下列情况下失败关闭：内嵌解释器没能随 bundle 重定位、预装运行时缺了任何一个 `CORE_PACKAGES` 导入、`Info.plist` 与 `openai4s.__version__` 对不上、缺少 Web UI / R worker / compute 模板 / Skill 目录、`python -m openai4s --help` 无法离线运行、代码签名校验不过，或者镜像里混进了 dotenv 及任何形似凭据的内容。 |
| `verify_linux_bundle.py` | 只用标准库检查构建好的 `.tar.gz` 或已解包的目录。以下情况一律失败关闭：归档形状不对或启动器丢了可执行位、三处版本声明对不上、科学栈缺包、字节码是时间戳失效制、图标梯级的实际像素尺寸与目录名不符、`.desktop` 模板没人替换，或归档里混进了凭据material。在架构匹配的 Linux 主机上它还会多做一层——真正运行内嵌解释器——并在报告里写明这次达到了哪一层深度，而不是让「跳过的检查」看起来像「通过的检查」。 |
| `verify_windows_zip.py` | 只用标准库检查构建好的 `.zip` 或暂存目录，盯的是 Windows 包特有的那几种坏法：WSL bootstrap 混进了 CRLF（这会在用户机器上以 `bad interpreter` 死掉，而不是在这里）、payload 与校验和 sidecar 对不上、macOS 资源分支垃圾文件，以及最要命的一种——启动器长出了一条原生 Windows 执行路径，而 `platform_support.py` 本来就会拒绝它。 |
| `describe_macos_image.py` | 挂载构建好的 `.dmg`，在旁边写两份佐证：从 `codesign` 读出的签名 authority 链，以及镜像里真正内嵌的运行时的包清单。发布暂存作业跑在 Linux 上、两样都拿不到，所以这份证据必须在构建镜像的那台机器上产生并随镜像一起走——没有它，发布闸门只能退回去读「签名身份变量是否非空」，而 ad-hoc 签名的镜像同样满足这一条。 |
| `verify_release_artifacts.py` | 只用标准库检查构建好的 wheel 与 sdist。先看必需文件是否齐全、有没有夹带不该带的东西（symlink、字节码、缓存、`.env`），再读 wheel metadata：MIT 许可、四个 Project-URL、`openai4s` 控制台入口点、平台无关的 `py3-none-any` tag，以及 wheel 里没有测试套件、核心没有非 extra 依赖。 |
| `verify_release_tag.py` | `vMAJOR.MINOR.PATCH` 形式的 release tag 必须与两处字面版本声明一致：`pyproject.toml` 里的 `[project] version` 和 `openai4s.__version__`；对不上就失败即拒绝。 |
| `release_pipeline.py` | 发布流程本身，写成脚本而不是 workflow YAML。嵌在事件触发里的步骤只能靠真发一次版来演练——被它本该保护的那件事反过来测试它——所以这份东西能在笔记本上跑、能 `--dry-run`、也能被 pytest 跑。所有不可逆的都排在最后：GitHub 翻牌发生在 PyPI 已经拿到该版本之后，并且在草稿与 PyPI 分发件不完全一致时拒绝执行。 |
| `run_quality_gates.py` | 跑完全部离线门禁，并写出一份绑定到当前 checkout SHA 的收据。发布流水线此前回答「not run: the suite gated the build that produced these artifacts」，而构建 job 根本没跑任何测试；现在 staging 会校验这份收据，并且自己重新推导提交号，而不是相信收据里写的那个。 |
| `release_gates.py` | 发布质量门禁的唯一权威清单，由 `run_quality_gates.py`（负责执行）与 `release_pipeline.py`（要求收据与之完全一致）共同导入。此前生产方私有持有这份列表，而消费方只比较退出码，因此一份只有两行、把 `pytest` 的命令写成 `["pytest"]` 的收据也能通过 staging。现在校验是精确匹配：缺失、重复、未知或被替换命令的 gate，以及不同的 schema 版本和不同的 manifest 摘要，都会硬失败。同时承载 check-suite 证明——浏览器矩阵与 Python 支持矩阵由 GitHub 自身、绑定到发布 `head_sha` 的 check run 证明并记录 run id，而不是重新执行一遍。 |
| `release_receipts.py` | 构建收据与 staging 证明，以及构建 job 调用的 CLI。构建收据把某个 job 的产物绑定到被冻结的源码 SHA，并记录构建机的 OS/架构/解释器，因此 staging 能够检查 wheel 与 DMG 是否来自同一个提交——此前每个 job 各自 checkout 可变 tag，没有任何东西做过比较。staging 证明记录了被暂存资产的精确集合与摘要，并通过 workflow 制品通道传递，因为 `step_publish` 此前是拿 draft 自己的 `SHA256SUMS` 去重新校验 draft：这是一份自我担保的文件，任何能替换资产的人都能在同一动作里把它一起替换。 |
| `reaudit_crosswalk.py` | 为 [`docs/plan-crosswalk.json`](../docs/plan-crosswalk.json) 里每个 `closed` 行记录它所指证据文件的内容摘要；`--check` 会在任何一份证据变动后拒绝通过。此前 48 个 closed 行中有 25 个所指的测试文件在文档自称的审计点之后又被改过（其中一个跨了 12 个提交），而现有断言全部通过——因为它们检查的是文件“存在”。用内容摘要而不是 commit SHA：做这次重新审计的那个提交，无法在自身内部校验自己的 SHA。 |
| `capture_response_contract.py` | 配套的另一道门，问的是另一个问题：`capture_response_schemas.py` 冻结的是测试恰好触发到的那些 JSON body 的**形状**，而它冻结的是每条 route 究竟给出**哪一类**回答——json、stream、redirect、binary 还是空——以及配的是哪些状态码。它不会重跑测试套件，而是把清单里的每条 route 不带参数地直接打到真实 handler 与真实 Store 上，于是大多数返回 4xx，而这正是重点：错误响应同样是一种承诺，一条根本驱动不起来的 route 会以「条目缺失」的形式暴露，而不是变成一个填满空值的条目。清单里那种连自身具体化都路由不到的条目会被单独点名，绝不计入覆盖。[`docs/response-contract.json`](../docs/response-contract.json) 是抓取出来的，不是手写的，理由和它的搭档一样：手工维护的一份「这些 route 是流式的」清单，只在写下它的那天是对的，之后就会悄悄变错。抓取时会在临时数据目录里把 `OPENAI4S_SECRET_STORE` 钉成 `plaintext`，因为 `/search/config` 要读凭据，而 `auto` 解析到的是**执行抓取那台机器**上有什么——有钥匙串的笔记本会把这条 route 冻进去，两样都没有的 runner 会跳过它，于是这份产物在除了生成它那台机器之外的所有机器上都显得过期。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| `windows/` | Windows 启动器，以真正可读可 diff 的文件形式存在，而不是塞在构建脚本里的 heredoc：`OpenAI4S.cmd`、`openai4s.ps1`，以及在 WSL2 里跑的 `bootstrap.sh`。`build_windows_zip.sh` 会按各自需要的换行符把它们放进包里。 |

## 在架构中的位置

发布与安全脚本是从控制平面外部检查它的，它们本身不属于控制平面。`fold_remote.sh` 同样
不是通用的部署保证：已注册的远程科学服务仍然要自己做 capability 检查，并在所需的远端
安装不可用时返回明确的错误。
