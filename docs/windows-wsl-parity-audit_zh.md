# Windows / WSL2 与 macOS 对齐调查

[English](windows-wsl-parity-audit.md)

基线：`bca1183fcc1cd9f4820bb54139ecdaaf63adfcf6`，2026-09-07。
目标用户已经有 WSL2；目标是下载 Windows 包后，完成与 macOS 相同的科研操作。
分支现已在原调查之后加入产品修复。下方基线观察是历史记录，不能当作当前分支的运行结果。本次验收针对 WSL2 x86_64，不代表所有 Windows 配置均已认证。

## 已实现的修复

| 对应问题 | 实现 | 使用者看到的变化 |
| --- | --- | --- |
| W0 | 独立 WSL 适配层、Windows/系统会话挂载遮罩、私有 PID 和 AF_VSOCK 过滤；复用现有沙箱及中断机制 | 分析代码不能借 Windows 互操作绕过边界；Python/R 取消后仍可接着使用分析变量 |
| W1、W5 | 排除 Docker 等基础设施发行版，检查架构、普通用户和可用 bubblewrap，提供 Ubuntu 依赖准备入口 | 首次启动选择用户发行版，并说明、准备缺失的隔离组件 |
| W2 | SecretBroker 增加 Windows 用户 DPAPI 后端，保留已有可用 Linux 钥匙串 | 新 WSL 安装无需配置 Linux 桌面钥匙串，也能加密保存模型密钥 |
| W3、W4、W9 | CLI 使用随包 runtime，过滤 Windows 工具搜索路径，明确使用 Bash | 科学命令跟随所选分析环境，`source`、`[[ ]]` 等 Bash 语法正常工作 |
| W6–W8 | 按包摘要暂存、成功后原子切换，管理命令脱离新包，报告实际运行版本 | 坏更新保留旧安装；更新时提示稍后重启，不自动结束正在进行的分析 |
| C1、C2 | 同时修正 macOS/Linux 构建的默认网页和 Python 命令重定位，增强包校验 | 下载后能打开真正的工作台，科学工具搬到安装目录后仍能运行 |
| W10 — 浏览器验收新发现 | 由随后台服务存活的单个线程创建分析进程，不依赖短暂请求线程 | 网页执行完一格后内核不再被误杀，后续 Cell 和刷新页面仍可使用已有变量 |
| W11 — 独立启动验收新发现 | Windows 启动器持有隐藏的 WSL 进程，Linux 服务以前台方式运行 | 无需保持终端开启；服务停止时后台 WSL 进程一起退出 |
| C3 — 安装包验收新发现 | Linux/macOS 包内 CLI 用 `-P` 关闭 Python 对当前目录的隐式导入 | 项目中的 `openai4s.py` 或源码 checkout 不会遮蔽已安装应用，同时保留用户工作目录 |

最终全量回归也复现了原有 HTTP 超时竞争：连接自身先超时、看门狗后触发时，异常类型不一致。修复集中在共用的有界读取层；测试分别覆盖两种顺序，并避免把无关的 TLS 证书加载放进仅 200 ms 的 socketpair 测试时限。

分层上，WSL 策略放在 `security/wsl.py`，加密存储放在 `security/windows_dpapi.py`，沿用现有内核协议、科学服务和 UI。环境检查、自定义工具这两个短进程也补齐沙箱描述符传递。硬链接扫描保留在团队隔离和 Windows 文件系统工作区，不再给每个普通 Linux 开发目录增加全树扫描。

验收包括 `harness.smoke.wsl_sandbox`、真实 Python/R 中断、离线回归、原生 PowerShell 5.1 启动器测试，以及实际 Windows 浏览器和安装包操作。最终结果见本文末尾的修复验收记录。

## 基线结论

WSL 能运行现有 Linux Python 内核，问题集中在产品没有准备齐运行条件，以及 Windows 与 Linux 之间多出的边界。
macOS 包已经准备了 Python、科学依赖和启动环境，系统另外提供沙箱与钥匙串。
WSL2 的存在并不保证发行版里有可用的沙箱、已解锁的密钥服务，或正确的 Linux 工具环境。

最优先的问题是 **Cell 能通过 Windows 互操作绕过现有沙箱**。
普通 Linux 边界 smoke 全部通过后，真实的 enforced Cell 仍然成功运行 Windows PowerShell，修改了沙箱工作区外的一个合成标记文件。
现有 smoke 的绿色结果不能作为 WSL 安全边界验收。

## 实测边界与证据

- 在独立 `OpenAI4S-Parity-Test` 发行版内，以普通用户执行。系统是官方 Ubuntu 24.04.4、x86_64、WSL2 内核 `6.18.33.2-microsoft-standard-WSL2`。
- 初始环境没有 bubblewrap、secret-tool、Rscript、Conda 或 Linux Node。之后仅在测试发行版安装 bubblewrap 0.9.0 和开发工具，再补装 libsecret-tools、gnome-keyring、dbus-user-session。
- 已验证源码复制步骤、真实 `host.bash`、真实 Python kernel、合成坏包安装和生成 CLI 的环境。CLI 环境探针使用假解释器，只证明 PATH 传递，不冒充完整安装包执行。
- 所有安全探针只使用临时目录和自建标记文件；没有读取真实密钥、调用模型供应商或修改 Docker 发行版。
- macOS 对照来自当前构建和运行代码，本机没有执行真实 macOS DMG。已有 WSL2 不代表任意发行版、网络模式、CPU 架构都已通过。
- [可复现探针](../scripts/probe_wsl_parity.py)与[运行回执](windows-wsl-parity-evidence.json)保留了观察结果。回执中的临时路径只说明本次运行，不是产品默认路径。

官方镜像 SHA-256：`9b2f7730dc68227dd04a9f3e5eab86ad85caf556b8606ad94f1f29ff5c4fd3f5`，与 [Ubuntu 校验清单](https://releases.ubuntu.com/24.04/SHA256SUMS)一致。

完整产物也已实测：Linux payload 约 406 MiB、解包约 1.4 GiB；现有验证器成功导入 38 个科学包。
从实际 Windows ZIP 解压到含中文和空格的目录后，PowerShell 5.1 启动器退出 0 并报告 ready，普通用户安装与 daemon 启动成功。
Windows 访问测试端口得到未认证 401、认证后首页 **404**，安装目录确实没有默认 UI。
这证明连接和认证能够工作，而产物及 ready 检查没有覆盖默认页面。
真实 launcher 的 status 返回运行中的同一 PID，重复打开复用该 daemon，stop 返回 0，停止后 status 返回 1 并报告 not running；测试 daemon 已停止。
补装密钥组件后，D-Bus ReadAlias(default) 返回 `/`，Collections 仅有 session；SecretBroker 的存储自检超时，未得到可持久使用的默认密钥库。

## 从上到下：用户流程

```mermaid
flowchart LR
    A[Windows 解压并双击] --> B[选择 WSL 发行版和用户]
    B --> C[检查依赖并安装 Linux payload]
    C --> D[启动 Linux daemon]
    D --> E[Windows 浏览器打开认证地址]
    E --> F[配置模型与保存密钥]
    F --> G[上传数据并执行 Python / R / Bash]
    G --> H[查看与下载产物]
    H --> I[停止、重开、更新与恢复]
```

| 用户动作 | 当前证据 | 达到 macOS 对齐所需验收 |
| --- | --- | --- |
| 解压、双击 | 发行版选择器会把 docker-desktop 当候选；全新 Ubuntu 在 bwrap 预检处退出 | 排除基础设施发行版；验证实际发行版的架构、运行用户、运行时兼容性；缺依赖时提供可完成的准备流程 |
| 打开界面 | 实际 ZIP 安装后 launcher 报 ready，但认证后首页 404；完整 Linux verifier 仍通过 | 验证最终安装的默认 UI 及 HTML 引用的 JS/CSS；同时修复共享的 macOS 打包问题 |
| 配置模型 | 全新 WSL 的 SecretBroker 拒绝保存；仅安装密钥工具也没有建立可用服务 | 安全存储、解锁、保存、读取、退出后重开、WSL 重启后恢复；不能隐式改为明文 |
| 上传 CSV、做 Python 分析 | 真实内核的 Python、持久变量通过；包内 CLI 的 shell PATH 不正确 | Cell 和 shell 使用预期解释器；完成上传、分析、图像/表格捕获、下载，使用 Windows 浏览器验证 |
| 执行 Bash | POSIX 命令成功；`source`、`[[ … ]]` 在真实 `host.bash` 中以 127 退出 | 明确并统一前台与后台 shell；实际安装依赖后能够在对应环境中使用 |
| 执行 R / 专业工具 | R、Conda 不在两端基础包；213 个领域脚本没有全依赖执行 | 按能力准备环境，检查真实工具和版本；R 的 fd 通道、停止后继续执行单独验收 |
| 隔离 Cell | 普通 Linux smoke 通过；Windows 互操作改写沙箱外标记成功 | Cell 不能借 Windows 进程、IPC 或路径别名绕过文件、进程、网络和凭据边界 |
| 停止和重开 | 199 项现有测试通过；真实 ZIP 的 status、重复打开复用、stop、停止后 status 通过 | 补测端口冲突、有活动任务时停止、停止后完整科研重跑和重启恢复 |
| 更新与故障恢复 | 同版本坏包会删掉原 CLI；status/stop 仍依赖并安装当前 ZIP payload；存活 daemon 未核对版本 | 有活动任务时不误停；报告真实运行版本；安装失败保留可用版本；坏包不阻断诊断和停止 |
| WSL / Windows 重启 | 尚未完成产品全流程验收 | 发行版与用户选择稳定；密钥、会话、安装环境持久；地址变化后仍可连接 |

浏览器上传、下载和 WebSocket 已有 HTTP 通道，先验证这些通道，不需要先设计 Windows/Linux 全盘路径转换。
代理、Fake-IP、localhost 转发和回退已有实现，但本次未改变全局网络设置，不能把某一个网络模式通过推广到所有机器。

## 已确认的问题与调用链

| ID / 优先级 | 观察与用户影响 | 代码入口与对齐方向 |
| --- | --- | --- |
| W0 / 发版阻断 | enforced Python Cell 启动 Windows PowerShell，改写工作区外的自建文件；普通 Linux smoke 同时通过 | `kernel/manager.py` → `security/sandbox.py:wrap_bwrap_command`。WSL 互操作的程序、socket、挂载与 proc 别名都需要边界；worker 环境去掉 WSL_INTEROP 仍不足以阻断本次复现 |
| W1 / 首次运行 | 干净 Ubuntu 的 `bootstrap.sh preflight` 因缺 bwrap 返回 1，当前只打印 apt 指令 | `scripts/windows/bootstrap.sh:run_preflight`。已有 WSL2 后仍需要一次可理解、可重试的 sandbox 准备及真实自测，维持 enforce |
| W2 / 模型配置 | 无可用系统密钥后端，SecretBroker fail-closed；装上 secret-tool 也不等于用户会话与已解锁的 Secret Service 可用 | `security/secret_broker.py:SecretServiceBackend` / `SecretBroker._resolve`。先确定凭据归属、服务生命周期、重启与解锁契约，再实现安全后端；Windows 系统存储桥接也必须受 W0 边界保护 |
| W3 / 科研执行 | WSL 调用的 bundled CLI 未设置 runtime/bin 的 PATH；fixture 中 python3 解析为 `/usr/bin/python3`，python 缺失 | `build_linux_bundle.sh` 的 CLI → `bootstrap.sh serve` → `kernel/environment.py`。macOS desktop LAUNCHER 设置了 PATH。两端 CLI 都有同样缺口，统一启动环境，并验证选中的 Conda 环境仍优先 |
| W4 / 科研执行 | `host.bash` 的 `shell=True` 实际使用 Ubuntu `/bin/sh`；`source`、`[[ ]]` 失败，而 Jobs 显式使用 bash | `sdk/bash.py:BashExecutor`、`jobs.py`。统一约定 shell，保留授权、超时、进程组与审计。R 启动用 sh 做 POSIX fd 重定向本来正确，不能机械替换 |
| W5 / 发行版选择 | 用真实选择函数和合成发行版列表复现：只有 docker-desktop 时，它被选中；未在 Docker 内执行程序 | `openai4s.ps1:Select-Distro`。过滤基础设施发行版，再探测能力，保留现有数据归属及明确用户选择 |
| W6 / 更新 | 新包安装后，只检查 TCP 与 CLI status 就复用已有 daemon，没有比较正在运行的构建 | `openai4s.ps1:Test-OpenAI4SServing` 与主流程。区分安装版本、运行版本和有无活动任务，支持安全重启或明确的待重启状态 |
| W7 / 故障恢复 | 管理命令跳过 bwrap 检查，但仍走 Get-PackageFacts → install → cli；坏 payload 能阻断 status/stop/doctor | `openai4s.ps1` 的 Arguments 分支。管理已有安装不应依赖新 ZIP 完整，也不应为了查询状态而改写安装 |
| W8 / 安装恢复 | 对已有同版本目录提供摘要正确、内容无效的归档：tar 失败，原 CLI 已不存在 | `bootstrap.sh install` 的 rm → tar 顺序。临时目录解包并验证，成功后切换；并发与恢复规则需一并覆盖。不同版本目录已分开，不能声称所有升级都删旧版或会话 |
| W9 / 工具发现与延迟 | 本机 WSL PATH 有 43 项，其中 34 项在 /mnt 下；5 次查找不存在命令的中位数为 209 ms，纯 Linux PATH 为 0.034 ms。还会找到缺少对应 Linux node 的 Windows npx | CLI/daemon 继承的 PATH、`cli/main.py:_find_conda_tool` 等 which 调用。明确 Linux 工具解析范围，检查实际可执行能力并按环境变化失效缓存；不全局改用户 WSL 设置。这是本机实测，未对全量测试慢作完整归因 |
| C1 / 共享发版阻断 | 真正执行 rsync 后 `webui/dist/index.html` 缺失；`bundle_contract.check_sources` 却通过，仍识别到 604 个 Skills | 两个 bundle builder 都使用 `--exclude 'dist'`；共享 REQUIRED_SOURCES 只要求旧 UI。精确包含默认 UI 与引用资源，在三个桌面产物验证，而不只是 wheel |
| C2 / 科学命令重定位 | 本次实际 payload 有 22 个 console scripts 写死构建机解释器路径。仅在临时 mount namespace 隐藏构建目录后，已安装的 f2py 入口失败；同一安装的 `python3 -m numpy.f2py -v` 与 pip 成功 | Linux builder 的 pip 安装阶段及 runtime/bin；macOS 使用相同安装方式，需对照复验。修复 console script 重定位，并在看不到构建目录的环境中逐个检查；当前 verifier 的入口检查没有覆盖这些命令 |

Python 的 POSIX `shell=True` 默认调用 `/bin/sh`，见 [subprocess 文档](https://docs.python.org/3/library/subprocess.html#subprocess.Popen)。
这些问题不支持“WSL 的 Linux syscall 普遍不兼容”的结论；它们各有具体的环境或流程原因。

## 从下到上：脚本与进程盘点

[逐文件清单](windows-wsl-shell-inventory.json)记录了基线的全部 226 个 `.sh` / `.command` / `.ps1` / `.cmd` 文件、6 个生成的 shell heredoc，以及 25 处直接可识别的 Python subprocess / host.bash 调用。
还有两个远程计算 `.sh.tmpl` 模板，需要按渲染后的运行环境判断，不能混同桌面启动器。
224 个 `.sh` / `.command` 在真实 Ubuntu Bash 下全部通过 `bash -n`；**语法通过不是依赖齐全，也不是工作流通过**。
AST 清单不保证捕获动态别名、生成代码或配置里的任意命令。
针对领域 shell 脚本检索 brew、osascript、pbcopy/pbpaste、/Applications、/Users、/opt/homebrew 等 macOS 特征未发现匹配；这只排除了这组明确特征，不能替代逐工作流运行。

| 层级 | 文件 / 模块 | 检查重点与边界 |
| --- | --- | --- |
| 构建与分发 | `build_linux_bundle.sh`、`build_macos_dmg.sh`、`build_windows_zip.sh`；签名与 tag 脚本 | payload/架构、默认 UI、依赖、符号链接与权限、LF/CRLF；公证脚本在 macOS 构建机运行 |
| 桌面启动 | Windows 三个启动文件；Linux/macOS CLI 与 LAUNCHER heredoc；Linux INSTALL/UNINSTALL heredoc | Windows 参数 → wsl.exe argv → Linux shell 的路径、用户、PATH、环境、版本和生命周期 |
| 开发入口 | `setup.sh`、`start.sh`、`scripts/setup_envs.sh` | uv、已建 venv、Conda 是开发/扩展环境前提，不能拿开发环境成功冒充解压包成功 |
| 执行核心 | kernel transport、R kernel、SDK Bash、Jobs、dynamic tools、preinstall | 哪个进程持有何种身份与环境；shell 语义；权限；进程组；中断；stdout/stderr；持久命名空间 |
| 环境管理 | CLI setup、env_generations、preinstall、环境描述 | bundled base 与 named python/r 环境不同；Python、pip 和专业 CLI 必须属于用户实际选择的环境 |
| 外部程序 | MCP client、Ark CLI、git、科学服务 | 在 Linux daemon 的实际环境中启动并验证。`which` 能找到 Windows shim 不代表它是可用 Linux 工具；本机初始能找到 Windows npx，但没有 Linux node |
| 远程执行 | compute manager、Slurm broker、local worker backend、fold_remote、run/wrapper 模板 | SSH/远程 Linux 环境、GNU timeout/setsid、远程 R/科学依赖；需要独立凭据和服务器，未作真实远程验收 |
| 领域脚本 | `skills/` 下 213 个脚本 | recipe 已分发不等于程序已安装。根据实际能力检查 STAR/samtools/mafft 等工具，不把全部工具塞成首次启动前提 |
| 构建/研究辅助 | container smoke、benchmark、review scratch、BYOC confinement | 独立部署或研究能力，不应成为普通 Windows 首次启动的隐性依赖 |

R、Conda、完整领域 CLI 未随 macOS 基础包提供，属于共同的能力准备问题。
Stage 1 standard-profile 门禁默认关闭；不能根据它在开启时要求 named python/r 环境，推断默认 Python 必然不能运行。

## 验证记录

现有 CI 和 release 的 `windows-launcher` job 覆盖 PowerShell 解析及「机器没有 WSL 时正确拒绝」，没有执行有 WSL2 的安装成功路径。
Linux runner 的沙箱 smoke 也不包含 Windows 互操作。因此修复后需要独立的真实 WSL2 验收，不能只依赖这两个现有绿色检查。

| 检查 | 结果与限制 |
| --- | --- |
| 现有内核、Bash、平台、发版测试 | 199 项通过，真实 WSL 普通用户 |
| Bash 语法盘点 | 224 个 shell 文件通过，未执行全部领域工作流 |
| Linux 完整边界 smoke | enforce、文件写边界、网络和子进程密钥隔离检查通过；仍漏掉 W0 的 Windows 通道 |
| 完整 Linux payload 验证 | 通过，执行了嵌入 Python 并导入 38 个科学包；仍漏掉 C1/C2 |
| 实际 Windows ZIP | 中文/空格路径安装、认证、重复打开及状态/停止通过；默认首页 404 |
| 全文件 pre-commit / 类型检查 | 通过 |
| 目录双语覆盖 / 源码密钥扫描 | 160 个目录、1476 个直接文件覆盖通过；3787 个文件扫描通过 |
| 完整离线套件 | 已完成：8303 passed、136 skipped、2 failed、1 warning，耗时 852.74 秒。`uv run --locked pytest -n auto --maxprocesses=4 --dist loadfile`，明确 Linux PATH，私有 tmpfs 临时数据，`tmp_path_retention_policy=failed` |
| 失败项复验 | 改用 `uv run --locked pytest -n 0` 和普通磁盘临时目录，两项均通过，耗时 17.92 秒；不能将全量结果改记为通过 |

测试条件需要区分：第一次直接调用 `.venv/bin/pytest` 时，uv 未进入 PATH，产生 1 个发布构建命令断言失败；中断时另有 5172 passed / 103 skipped。改用正确 PATH 与 uv run 后，该失败项单独通过。随后磁盘上的全量尝试因 I/O 等待中断，1951 passed / 38 skipped，未算作全量通过。最终离线回归使用普通用户、私有内存临时目录，并保留失败用例数据；实际安装、密钥、沙箱和重定位探针使用真实 Windows/WSL 文件系统。

完整运行中，`tests/test_mcp_client.py::test_streamable_http_body_watchdog_interrupts_a_chunked_slow_drip` 得到原始 `TimeoutError`，而测试期待 `MCPTimeout`；`tests/test_skill_product_surface.py::test_team_member_cannot_reactivate_legacy_host_skill_poison_over_http` 的 HTTP helper 没有收到状态行。两项单独复验通过，根因尚未确定，不能归因于 WSL、tmpfs 或本次文档/探针改动。唯一 warning 来自刻意模拟原生 win32 不支持沙箱的测试。

## 修复顺序与完成条件

1. **先闭合 WSL 边界并确定安全密钥方案。** 新增真实 WSL 回归，涵盖 Windows 进程/IPC、文件与凭据读取、路径别名及网络通道。验证停止和持久 Python/R 仍然可用；不要依赖全局关闭用户机器的 WSL 功能来通过测试。
2. **修正共享产物与执行环境。** 默认 UI、资源检查、bundle CLI PATH、Linux 工具发现、console script 重定位、前后台 Bash；验收从 ZIP 解压后的应用出发，隐藏构建目录，不能依赖开发机已有 Python/Node/Conda。
3. **完成 Windows 启动与恢复流程。** 发行版能力探测、缺依赖准备、解包后切换、管理命令脱离新包、运行版本识别和安全重启；覆盖空格/中文路径、坏包、重复启动及并发。
4. **走完整科研流程和重启回归。** 配置真实模型、上传 CSV、Python/图表/下载、准备 R、代表性专业 CLI、Stop 后继续、关闭重开、WSL 重启和升级。MCP/Ark 登录作为相应扩展能力单独验收。

未完成项：真实 macOS 对照运行、Windows on ARM、多发行版、多网络模式、真实供应商登录/模型调用、所有领域脚本、WSL/Windows 重启后的完整产品恢复。
这些是明确的待验收范围，不应写成已经通过。

## 复现

在普通 WSL 用户的干净 checkout 中，先确保有 Bash、rsync；沙箱探针需要 bubblewrap。
将下例的 Windows 临时目录换成自己已有的目录；脚本只在其中新建并清理自己的随机子目录。

```sh
python3 scripts/probe_wsl_parity.py \
  --windows-scratch /mnt/c/path/to/audit-temp \
  --output /tmp/wsl-parity.json
OPENAI4S_KERNEL_SANDBOX=enforce python3 -m harness.smoke.linux_sandbox
```

第一条命令退出 0 只代表收集完毕，要读取 JSON 中的失败观察，特别是 `windows_marker_changed`。
第二条在当前基线能通过，却覆盖不到第一条证实的 Windows 互操作问题。

对实际安装包的重定位，使用自己构建时的目录和安装后的路径执行下面的检查。
`--tmpfs` 只在这个子进程的 mount namespace 内遮住构建目录，不会删掉或移动实际目录。
本次第一条 f2py 执行失败，后两条成功；包内另有 21 个命令带同类绝对 shebang，未声称全部逐个执行。

```sh
PARITY_APP=/path/to/installed/OpenAI4S-0.2.0-linux-x86_64
PARITY_BUILD_ROOT=/path/to/checkout/.build
bwrap --unshare-net --ro-bind / / --tmpfs "$PARITY_BUILD_ROOT" \
  --dev /dev --proc /proc -- "$PARITY_APP/runtime/bin/f2py" -v
bwrap --unshare-net --ro-bind / / --tmpfs "$PARITY_BUILD_ROOT" \
  --dev /dev --proc /proc -- "$PARITY_APP/runtime/bin/python3" -m numpy.f2py -v
bwrap --unshare-net --ro-bind / / --tmpfs "$PARITY_BUILD_ROOT" \
  --dev /dev --proc /proc -- "$PARITY_APP/runtime/bin/pip" --version
```

并发构建、安装和全量测试期间还观察到 WSL 服务连接超时 (`0x8007274c`)；一次 launcher 将失败展示为目录不可达，而同一中文/空格目录此前已成功安装。
本轮没有确定服务超时的根因，也不据此认定 Unicode 路径不兼容。后续应单独验证 WSL 服务故障时的超时、重试和诊断信息。

## 修复验收记录 — 2026-09-07

以下结果更新了上方的基线失败记录。测试仍在隔离的 Ubuntu 24.04 WSL2 x86_64 发行版内以普通用户运行；Windows 浏览器为 Chromium，启动器使用 Windows PowerShell 5.1。核心没有新增第三方依赖。

- 真实 WSL smoke 在强制隔离下通过：Windows 程序、复制到工作区的程序与互操作加载器、宿主进程路径别名、互操作 socket 和 Hyper-V socket 都不能绕过边界。受隔离的环境探针和动态工具能够运行。Python 与 R 都能在中断后继续使用原变量；R/jsonlite 是测试准备项，没有加入基础安装包。
- 实际 Windows 浏览器完成了认证、默认页面与资源加载、通过 DPAPI 保存/删除合成模型密钥、CSV 原样上传下载、均值 2.0 的计算、PNG 展示与下载、结果表精确校验、刷新后继续使用 dataframe、中断后继续计算和 Notebook 导出。Notebook 执行显式开启了 `OPENAI4S_NOTEBOOK_REPL=1`；没有调用真实模型供应商。
- 最终 payload 的随包 Python 和科学依赖运行校验通过。最终 Windows ZIP 的原生校验通过，覆盖 payload 摘要、PowerShell ASCII、CRLF/LF 和资源契约。wheel/sdist 构建及校验通过。全文件 pre-commit、严格 mypy、160 个目录的双语索引和源码凭据扫描通过；离线场景 harness 为 38/38 通过。
- 最终 ZIP 从 Windows 源码 checkout 工作目录启动后，通过了上述科研流程。启动器退出后，连续 120 秒的五次纯 Windows 健康检查均成功；期间没有通过 WSL 命令或 UNC 读取维持发行版运行。
- 在旧服务运行时安装最终 payload，旧 PID、运行包身份和内存中的 dataframe 均保留，已保存的结果内容完全一致。正常停止并仅重启测试 WSL 发行版后，最终包重新打开原结果，重新读取输入 CSV 算出总和 6。合成 DPAPI 密钥在重启后仍能读取，随后删除成功。这不表示内存变量能跨服务重启保存。
- 在没有 payload 的启动器目录中，管理命令仍然可用。最终包正常停止返回 0；较早一次停止超过默认五秒等待而返回 2，随后自行退出并释放隐藏 WSL 进程，没有使用强制停止。
- 更广的现有浏览器 smoke 在没有并发构建时，仍超过了 20 秒的排队准入等待。单独带计时的诊断观察到 18.585 秒后正常接手，其中创建内核 0.447 秒、科学环境初始化 10.247 秒；早先有负载时为 39.1 秒。未经修改的完整浏览器脚本**未通过**，首次计算的性能对齐仍未认证。并发负载下还出现过 WSL 服务连接超时。
- 并发全量回归在约 83% 时被 4 GB 测试虚拟机的 OOM 终止，未记作完整通过。清理负载后，独立启动又暴露 W11：脱离终端的 Linux 服务不能阻止本机 systemd WSL 发行版自动停止。相关生命周期差异也见 [Microsoft 的说明](https://learn.microsoft.com/en-us/windows/wsl/systemd)。修复由 Windows 启动器持有服务进程，不修改全局 WSL 设置。
- 最终独立全量离线回归完成：**8345 通过、107 跳过、0 失败**，耗时 1052.39 秒；唯一警告来自预期的不支持平台沙箱测试。使用两个测试进程和私有的 1 GiB tmpfs 临时目录，后段在隔离发行版内添加的 2 GiB 交换文件已停用并删除。内核生命周期/MCP 专项 37 项通过，打包/WSL 专项 113 项通过，Windows PowerShell 5.1 启动器契约测试通过。

归档摘要与最终运行回执记录在证据 JSON 的 `fix_verification` 中。构建验证复用了此前完整组装的科学运行时，并用产品修复后的代码重新执行构建器的源码复制、CLI 生成、字节码编译、完整 Linux 验证与 Windows 打包步骤。

这些证据覆盖 WSL2 x86_64，不代表已认证真实 macOS 执行、ARM、任意发行版/网络模式、真实供应商登录与推理、Windows 整机重启、Conda 环境准备或全部领域工作流。
