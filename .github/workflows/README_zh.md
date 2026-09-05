# GitHub workflows

[English](README.md)

仓库的 CI 全在这五个文件里：每个 PR 都要过的默认检查门，加上 release 发布、
容器镜像发布、有界协议模糊测试和 Scorecard。它们只用来跑这个仓库的代码，不会随 Python 包一起发布。

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

`ci.yml` 里的 pinact job 是默认 PR 检查中唯一需要外网的元数据检查：它会把
每一个 action 的完整 SHA 与同行注释声称的精确 `vX.Y.Z` tag 做解析比对；其余
默认检查仍保持离线，也不会接收仓库 secret。

## 文件

| 文件 | 职责 |
| --- | --- |
| `ci.yml` | 默认 PR 检查门，拆成互相独立的 job 而不是一串 step。一个联网的 pinact job 校验每个 action 的完整 SHA 确实就是其注释所声称的那个精确 `vX.Y.Z` tag；其余默认检查全部离线。检查分支命名、跑 pre-commit、核对双语目录文档是否齐全、对核心编排边界做类型检查、扫描源码中的 secret、构建 wheel 与 sdist 并核对二者的内容、再把 wheel 单独装进一个干净的虚拟环境并跑通装好的 CLI、自测 npx Skill 安装器并（在另一个独立 job 里）证明发布的 npm 包仍然同时带着 CLI 和 Skill 目录树——「安装器行为正确」和「包里有东西可装」是两个不同的问题，一次 pytest 变红不该能把任何一个藏起来——在真实的 Windows runner 上解析随包发出的 Windows 启动脚本并验证它在没有 WSL 的机器上确实拒绝执行并给出出路，以及在 Python 3.10、3.12、3.13、3.14 上跑离线测试套件——分别是 `requires-python` 下限、这里其余各 job 都用的那个版本、macOS `.dmg` 内嵌的解释器，以及容器镜像里的解释器。套件以 `pytest -n auto --maxprocesses=4 --dist loadfile` 运行：它曾经占掉那个 job 全部 1122 秒里的 1094 秒，而这里其余每一个 job 都在两分钟内结束，所以它就是整条关键路径。并行的粒度是文件而不是单个测试，因为「文件之间互不干扰」才是这套测试当初被写出来时所依据的边界；四个 worker 是实测宽度，这一上限也避免高核机器无限倍增可启动 kernel 的测试进程。确定性的 harness 契约、路由响应契约和固化的响应形状是另外三个 job：作为 `pytest` 之后的 step 时，它们只有在套件已经全绿的情况下才可能运行，而「这道门没轮上跑」和「这道门跑了并且通过」在汇总页上长得一模一样。固化的响应形状是把整个套件装上捕获器再跑一遍，它同样被拆开了：每个 worker 在 xdist 报告成功前原子发布自己的未省略 shapes，并写明预期 worker 数与 run ID；脚本在 pytest 退出后用 `Recorder.observe` 内部同一个 `merge` 合并。缺失或混入其他 run 的 share 会在写出文档前被拒绝。`tests/test_response_capture_assembly.py` 同时断言完整性与相对于单进程结果的相等性，而不是靠假设。浏览器 E2E 在 Chromium、Firefox 和 WebKit 三个引擎里跑广度矩阵；完整的工作台走查、admission 故障用例和 P1 控件只在 Chromium 上跑——它们要的是深度而不是引擎覆盖。Stage 0 验收和团队模式各占一个自带 daemon 的浏览器 job，因为 smoke 的 daemon 两者都给不了：Stage 0 的整个主题就是一个 REPL 关闭、不占 8760 的一次性 daemon——它的脱敏/schema/投影自检在下载 Playwright 之前先跑——而团队模式需要 `OPENAI4S_TEAM_MODE=1` 加两个预置账号。容器镜像也单独占一个 job，并且跑在每一个 pull request 上而不是只在夜间：它构建 `Dockerfile`，然后把 daemon 在里面真跑一遍——回答的是「它能用吗」而不是「它构建成功了吗」——而且用的就是贡献者能在笔记本上执行的同一份 [`scripts/container_smoke.sh`](../../scripts/container_smoke.sh)。每次 CI 还会运行真实 Linux bubblewrap 的 Python/R 持久 kernel 中断任务：该任务固定使用 Ubuntu 24.04 并加载发行版自带、会剥离 capability 的 `bwrap-userns-restrict` profile；团队读取隔离以及 private PID/info-fd/procfs/pidfd 采纳仍被强制执行，同时有意允许 worker 原始网络，使进程身份的证据不依赖 private-network 配置。这个任务证明 SIGINT 目标和中断后复用，不证明 Linux egress 隔离。有三个 job 只在定时或手动触发时运行：要求 Seatbelt 隔离真正生效的 macOS 任务、Linux app bundle 以及把它当作 WSL2 载荷再包一层的 Windows 包，以及科学数据源探针——它只在真实的 schema 漂移上失败，上游不可达时不会。更完整、包含网络拒绝的 Linux 边界 smoke 仍需手动执行，因为它尚未在加载该发行版 profile 的 hosted runner 上重新评估。 |
| `fuzz.yml` | 在每个 pull request 中用 Atheris 对有界 WebSocket 与 share tunnel 解码器做 60 秒覆盖率引导测试，并在每周任务中跑 10 分钟。环境来自 `uv.lock`，不接收 secret、不持久化 cache，只有契约中明确的协议拒绝才算预期。 |
| `publish-image.yml` | 把 daemon 容器镜像发布到 GitHub Packages，形如 `ghcr.io/pku-yuangroup/openai4s:<version>` 与 `:latest`——在 release 正式发布时自动触发，也可对已存在的 `v*` tag 手动 dispatch。它先用 `scripts/verify_release_tag.py` 核对 tag 与两处版本声明，因此镜像不可能带上与自身 `__version__` 不符的 tag；然后用与 PR CI 完全相同的 [`scripts/container_smoke.sh`](../../scripts/container_smoke.sh) 构建并实证镜像——daemon 必须真的能在里面启动并服务——只有冒烟通过的镜像才会被推送。目前只出 linux/amd64：科学栈在 QEMU 下会成倍拉长构建时间而对面并没有用户，arm64 是留待日后的主动决定而不是一个开关。推送在 `ghcr` environment 中用 job 自己的 `GITHUB_TOKEN` 完成；仓库里不存在任何长期有效的 registry 凭据。 |
| `release.yml` | 只由手动 dispatch 触发，且是 draft-first。它此前挂在 `release: [created]` 上，而每个对外的 job 又都以「这个 release 是 draft」为条件——这个组合 GitHub 从不会发出，于是整条流水线在构造上就不可达。现在的入口是：维护者先建好稳定版 draft release，再针对那个 tag 手动触发本 workflow；不设 `publish` 时，一切照常构建与校验，但没有任何东西发出去。第三种模式 `pypi_only` 只为一个缺口而存在——release 已经公开、但对应的 PyPI 版本从未发布：此时守卫要求该 tag 处于公开而非 draft 状态，不向 release 上 stage 任何东西、也不翻转任何状态，只把新构建并验证过的 wheel/sdist 发到 PyPI。第一个 job 校验不可变的 workflow SHA，要求 tag 输入精确解析到该 SHA，并证明该 SHA 属于 `origin/main`；后续每次 checkout 都使用同一个 `github.sha`。其后依次是：非 prerelease 的 draft 守卫、在该 SHA 上重跑离线各道门并产出供 staging 校验的 receipt、macOS 上的强制 Seatbelt 隔离（完整的 Linux 文件系统与 egress 边界因为不是当前 CI gate 而明确记为尚未证明）、核对 tag 与两处版本声明是否一致、重新扫一遍源码、构建 wheel 与 sdist、macOS app image、Linux bundle、Windows 包以及在 Windows 上原生解析它的启动脚本、把产物挂到 draft 上、从 `pypi` environment 经由 OIDC 发布到 PyPI，最后才把 GitHub release 公开。顺序和每一项检查都写在 [`scripts/release_pipeline.py`](../../scripts/release_pipeline.py) 里，因此它们能在笔记本上和 pytest 里跑，而不是只能在一次 release 事件中跑。 |
| `scorecard.yml` | 在 `main` 的 push 和每周定时上运行 OpenSSF Scorecard，公开发布评分结果，并把 SARIF 上传到 code scanning。 |

默认测试套件必须保持离线。真实 provider、GPU、SSH、包发布与凭据都留在单独授权的
路径中。
