# 待办

[English](TODO.md)

本仓库已经决定要做、但还没做的后续事项。每条都写清"做完长什么样"，好让读者
分得清"待办"和"忘了"。凡是负责人在代码库之外的——一份凭据、一个 registry 账号、
一台机器——都该记在这里，而不是记在一句没人会去 grep 的注释里。

*已规划*而非待办的工作在
[`docs/next-version-progress.md`](next-version-progress.md)；那份文档是
v0.3 计划的事实记录，由 `tests/test_progress_document.py` 校验。本文件收的是线头。

## 发布

- [x] **把 `@pku-yuangroup/openai4s-skills` 发布到 npm。**
      [0.2.0 已公开](https://www.npmjs.com/package/@pku-yuangroup/openai4s-skills/v/0.2.0)，
      且 registry 的 `latest` 已指向该版本。发布包采用已发布 `v0.2.0` 源码
      （`c5e80a38306e`），适配组织包名与 npm 命令文案；603 个 Skill 的内容
      保持原发布版。16 项安装器自测、打包检查与秘密扫描通过；registry 下载包
      与已验证候选包的 SHA256 一致：
      `c4c31b56e34e8d73c1fe73ee8a2c7246f7e9654c1e3ee0cec4138f449be94d47`。
      在仓库外空目录使用全新 npm 缓存运行
      `npx @pku-yuangroup/openai4s-skills@0.2.0 list --offline` 成功：42 个精选
      加 561 个 bioSkills，包内共 2,212 个文件 / 6.4 MiB。组织包安装说明已同步。
      当前源码树有 604 个 Skill；`single-cell-rna-analysis` 不在 npm 0.2.0 中，
      其文档保留 GitHub 安装入口。

- [ ] **冻结六个 retrosynthesis 生产数据集。**
      `skills/retrosynthesis_planning/scenarios/test_cases/database_sources.json`
      的六项仍为 `not_frozen`；随包单行样例仅验证协议，不作科学准确性声明。
      [已核实的来源候选](../skills/retrosynthesis_planning/scenarios/test_cases/README_zh.md#正式数据来源核验2026-09-09)
      现已列出上游版本、文件及许可元数据，包括 PaRoutes CC BY 4.0 与原始
      USPTO CC0 证据，不把代码许可当作派生数据授权。维护者仍需记录数据准入
      与署名决定，并选定独立 atom-mapping 真值文件及审核负责人。
      *做完的标准：* 实际获准数据及派生 split 的 SHA256 完成核验，六项均带
      revision/license/split/hash 并标记 `frozen`，且 fail-closed 注册测试通过。
      仅检查字段非空，不能证明许可、文件完整性或独立真值已验证。

## CI 与供应链

- [x] **在所需 Linux／平台门禁验证 C1–C7 最终补丁。** 提交
      `1b56dc100c142f48c5f8d45631720e7eb6592052` 的
      [完整 CI](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34443430003)
      首次运行全部通过：Python 3.10/3.12/3.13/3.14、容器 smoke、Linux Python/R
      中断与强制沙箱、Chromium/Firefox/WebKit、打包、类型、文档和契约。
      [Chromium](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34443430003/job/102763034866)
      已通过 C1/C3/C5/C7 产品场景、admission fault、sandbox preview 和 10/10
      matrix 检查。工作流为上次缺少 matplotlib 的真实图形 fixture 安装已有
      锁定 science extra，没有删除场景或断言。
      [响应捕获](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34443430003/job/102763034845)
      在未改性能阈值的情况下通过：1,165 个形状、212/212 路由，无破坏性变化。
      本地最终验证也通过：8,767 项离线测试 / 33 跳过、112 项定向测试、38 项
      harness 场景、完整 pre-commit，以及隔离 wheel/sdist 安装和导入 smoke。

- [ ] **观察周一的跨生态依赖合批。**
      [PR #155](https://github.com/PKU-YuanGroup/OpenAI4S/pull/155) 把 uv、npm、
      Docker、pre-commit 和 GitHub Actions 归入统一周一组。已选策略包含大版本、
      black 和 isort，不使用 allow/ignore 过滤；13 项治理测试与完整 pre-commit
      均通过，[完整 CI](https://github.com/PKU-YuanGroup/OpenAI4S/actions/runs/34441714995)
      也已通过。*做完的标准：* 经代码所有者审查并合入默认分支后，真实 Dependabot
      PR 同时包含多个生态，且再下一个周一仍正常产生更新。配置已准备，尚未取得
      这两次真实调度结果。

## 最近关掉的，记下来免得再查一遍

Action pin 的身份现在会在普通 pull request CI 中校验。`action-pins` job 以只校验、
不修写的模式运行按 commit 固定的 `pinact-action`，并启用 tag 核验；离线治理测试则继续
要求每个 workflow action 都带精确的 40 位 hex SHA 和 `# vX.Y.Z` 声明。真实 pinact
运行接受了当前工作树；把 Checkout 的 `v7.0.1` SHA 错注成 `# v7.0.0` 的负对照则因
身份不匹配而失败。

CPython 3.14 现在既有 classifier，也进入 CI 测试矩阵，并安装 3.14 容器所用的
science extra。环境绑定夹具改为创建不带 pip 的真实虚拟环境，而不是裸解释器符号链接，
所以它对 `sys.executable` 的精确断言在 3.13 与 3.14 上都仍有意义。bring-up verifier
改用 fail-closed 的 `lstat` 检查，避开 3.14 中会吞掉探测错误的
`Path.is_symlink()` 行为；嵌套 xdist capture 测试也只显式加载其契约真正使用的插件。
最终锁定环境的 Python 3.14.4 完整套件结果为 **8094 通过 / 23 跳过**。

本地 kernel worker 现在会 spawn 进自己的 session，因此投向 daemon 进程组的信号
不再同时投向其下的每一个 cell——这正是 Linux + bubblewrap 本来就没有的那处分歧。
它是连同两件让它成为改进而非交易的事一起落地的：worker 的进程组在 spawn 时就被
记下、`kill` 改走既有的停止阶梯，从而能收掉 cell 自己起的子进程（这在以前做不到，
因为 worker 的组**就是** daemon 的组）；以及 `openai4s run` 装上了一个 SIGINT
处理器，做终端那个组级 Ctrl-C 从前所做的事。

`tests/test_mcp_lifecycle.py`、`tests/test_local_jobs.py`、
`tests/test_cluster_session_production_wiring.py`、
`tests/test_orchestration_routes.py`、`tests/test_telemetry_transmission.py`
与 `tests/test_cell_watchdog.py` 里的墙钟预算，现在都改成等待条件而不是等钟。
值得记下原因，因为当初标出它们的那次审计对了一半：它们没有一个在 CI 里红过，
而其中两处根本不是 flake，而是静默的覆盖流失——sleep 太短时测试照样是绿的，
但它走的恰恰是它被写出来要避开的那条路径。
