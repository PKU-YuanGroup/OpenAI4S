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
| `ci.yml` | 默认的离线检查门，拆成互相独立的 job 而不是一串 step。检查分支命名、跑 pre-commit、核对双语目录文档是否齐全、对核心编排边界做类型检查、扫描源码中的 secret、构建 wheel 与 sdist 并核对二者的内容、再把 wheel 单独装进一个干净的虚拟环境并跑通装好的 CLI、在真实的 Windows runner 上解析随包发出的 Windows 启动脚本并验证它在没有 WSL 的机器上确实拒绝执行并给出出路，以及在 Python 3.10、3.12、3.13 上跑离线测试套件——分别是 `requires-python` 下限、这里其余各 job 都用的那个版本，以及 macOS `.dmg` 内嵌的那个解释器。套件以 `pytest -n auto --maxprocesses=4 --dist loadfile` 运行：它曾经占掉那个 job 全部 1122 秒里的 1094 秒，而这里其余每一个 job 都在两分钟内结束，所以它就是整条关键路径。并行的粒度是文件而不是单个测试，因为「文件之间互不干扰」才是这套测试当初被写出来时所依据的边界；四个 worker 是实测宽度，这一上限也避免高核机器无限倍增可启动 kernel 的测试进程。确定性的 harness 契约、路由响应契约和固化的响应形状是另外三个 job：作为 `pytest` 之后的 step 时，它们只有在套件已经全绿的情况下才可能运行，而「这道门没轮上跑」和「这道门跑了并且通过」在汇总页上长得一模一样。固化的响应形状是把整个套件装上捕获器再跑一遍，它同样被拆开了：每个 worker 在 xdist 报告成功前原子发布自己的未省略 shapes，并写明预期 worker 数与 run ID；脚本在 pytest 退出后用 `Recorder.observe` 内部同一个 `merge` 合并。缺失或混入其他 run 的 share 会在写出文档前被拒绝。`tests/test_response_capture_assembly.py` 同时断言完整性与相对于单进程结果的相等性，而不是靠假设。浏览器 E2E 在 Chromium、Firefox 和 WebKit 三个引擎里跑广度矩阵；完整的工作台走查、admission 故障用例和 P1 控件只在 Chromium 上跑——它们要的是深度而不是引擎覆盖。容器镜像也单独占一个 job，并且跑在每一个 pull request 上而不是只在夜间：它构建 `Dockerfile`，然后把 daemon 在里面真跑一遍——回答的是「它能用吗」而不是「它构建成功了吗」——而且用的就是贡献者能在笔记本上执行的同一份 [`scripts/container_smoke.sh`](../../scripts/container_smoke.sh)。有三个 job 只在定时或手动触发时运行：要求 Seatbelt 隔离真正生效的 macOS 任务、Linux app bundle 以及把它当作 WSL2 载荷再包一层的 Windows 包，以及科学数据源探针——它只在真实的 schema 漂移上失败，上游不可达时不会。这里有意不设 Linux bubblewrap 任务：GitHub 托管的 runner 不允许 `bwrap` 在新的网络命名空间里拉起 loopback，这个 job 永远不可能通过，因此改由 `docs/platforms.md` 写清楚哪些结论已被证明，而不是指向一个天天都红的任务。 |
| `release.yml` | 只由手动 dispatch 触发，且是 draft-first。它此前挂在 `release: [created]` 上，而每个对外的 job 又都以「这个 release 是 draft」为条件——这个组合 GitHub 从不会发出，于是整条流水线在构造上就不可达。现在的入口是：维护者先建好稳定版 draft release，再针对那个 tag 手动触发本 workflow；不设 `publish` 时，一切照常构建与校验，但没有任何东西发出去。第一个 job 把 tag 剥成唯一的 commit SHA，后续每个 job 都 checkout 这个 SHA——tag 是可变的，五个 job 各自解析一次，就可能出现「门跑在一个 commit 上、wheel 从第二个构建、桌面包从第三个打出」。其后依次是：非 prerelease 的 draft 守卫、在该 SHA 上重跑离线各道门并产出供 staging 校验的 receipt、macOS 上的强制 Seatbelt 隔离（Linux 沙箱边界因 GitHub 托管 runner 无法执行而明确记为尚未证明）、核对 tag 与两处版本声明是否一致、重新扫一遍源码、构建 wheel 与 sdist、macOS app image、Linux bundle、Windows 包以及在 Windows 上原生解析它的启动脚本、把产物挂到 draft 上、从 `pypi` environment 经由 OIDC 发布到 PyPI，最后才把 GitHub release 公开。顺序和每一项检查都写在 [`scripts/release_pipeline.py`](../../scripts/release_pipeline.py) 里，因此它们能在笔记本上和 pytest 里跑，而不是只能在一次 release 事件中跑。 |
| `scorecard.yml` | 在 `main` 的 push 和每周定时上运行 OpenSSF Scorecard，公开发布评分结果，并把 SARIF 上传到 code scanning。 |

默认测试套件必须保持离线。真实 provider、GPU、SSH、包发布与凭据都留在单独授权的
路径中。
