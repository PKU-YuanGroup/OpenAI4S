# 待办

[English](TODO.md)

本仓库已经决定要做、但还没做的后续事项。每条都写清"做完长什么样"，好让读者
分得清"待办"和"忘了"。凡是负责人在代码库之外的——一份凭据、一个 registry 账号、
一台机器——都该记在这里，而不是记在一句没人会去 grep 的注释里。

*已规划*而非待办的工作在
[`docs/next-version-progress.md`](next-version-progress.md)；那份文档是
v0.3 计划的事实记录，由 `tests/test_progress_document.py` 校验。本文件收的是线头。

## 发布

- [ ] **把 `openai4s-skills` 发布到 npm。** 包已经完整并有关卡把守
      （`node tools/skills-installer/selftest.mjs`、
      `node tools/skills-installer/check_package.mjs`）。在干净 checkout 上，已发布的
      `v0.2.0` tag 通过全部 16 项 installer 自测，并打出 2,212 个文件 / 603 个
      Skill / 6.4 MB；当前 `main` 则打出 2,236 个文件 / 604 个 Skill / 6.5 MB。
      在发布之前，`npx openai4s-skills …` 解析不到；
      `npx github:PKU-YuanGroup/OpenAI4S install --all` 今天就能用，README 里也是
      和它并列写的。2026-09-01 实时执行
      `npm view openai4s-skills version` 仍返回 `E404`。
      *做完的标准：* 在已发布 tag 的干净 checkout 上跑过
      `npm publish --access public`，并且在一台没有 checkout 的机器上
      `npx openai4s-skills list` 可用。这需要一个有发布权限的 npm 账号——
      任何自动化 agent 都不该持有这份凭据。

## CI 与供应链

- [ ] **观察新的周一跨 ecosystem 依赖批次。** 配置部分已经实现：
      `routine-dependencies` 持有周一计划，并把选定的 uv 开发工具、Black 之外的
      pre-commit hook，以及 GitHub Action 的小版本/补丁升级合并起来。带互补
      ignore 规则的常规条目只排除这些已分组的更新类别，因此 uv 大版本和生产依赖、
      Black、Action 大版本仍有覆盖。`tests/test_governance.py` 在离线环境中钉死了
      这个分区以及 npm/Docker 的独立策略。工作树无法证明的是 GitHub 会接受合并后的
      配置并按计划调度它。最新的真实周一证据仍是旧配置开出的两个独立 PR：
      pre-commit [#140](https://github.com/PKU-YuanGroup/OpenAI4S/pull/140) 与
      GitHub Actions [#141](https://github.com/PKU-YuanGroup/OpenAI4S/pull/141)。
      *做完的标准：* 有一个 Dependabot PR 同时带着不止一个 ecosystem 的更新，
      且下一个周一的运行照常开 PR。

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
