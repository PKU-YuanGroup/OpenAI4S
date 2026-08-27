# 待办

[English](TODO.md)

本仓库已经决定要做、但还没做的后续事项。每条都写清"做完长什么样"，好让读者
分得清"待办"和"忘了"。凡是负责人在代码库之外的——一份凭据、一个 registry 账号、
一台机器——都该记在这里，而不是记在一句没人会去 grep 的注释里。

*已规划*而非待办的工作在
[`docs/next-version-progress.md`](docs/next-version-progress.md)；那份文档是
v0.3 计划的事实记录，由 `tests/test_progress_document.py` 校验。本文件收的是线头。

## 发布

- [ ] **把 `openai4s-skills` 发布到 npm。** 包已经完整并有关卡把守
      （`node tools/skills-installer/selftest.mjs`、
      `node tools/skills-installer/check_package.mjs`），`npm pack` 产出 6.4 MiB、
      带着全部 602 个 Skill。在发布之前，`npx openai4s-skills …` 解析不到；
      `npx github:PKU-YuanGroup/OpenAI4S install --all` 今天就能用，README 里也是
      和它并列写的。截至 2026-08-23，这个名字在 registry 上没人占。
      *做完的标准：* 在已发布 tag 的干净 checkout 上跑过
      `npm publish --access public`，并且在一台没有 checkout 的机器上
      `npx openai4s-skills list` 可用。这需要一个有发布权限的 npm 账号——
      任何自动化 agent 都不该持有这份凭据。

## CI 与供应链

- [ ] **让 action pin 在合并前就被校验，而不是合并后。** `scorecard.yml` 只在
      `push: branches: [main]` 和周六定时任务上触发，所以改在那里的 pin 从来
      不会为 PR 执行一次——一个解析不出来的 SHA 会全绿合入，最先露头的形式是
      SARIF 悄悄不再送达 code scanning。`tests/test_governance.py` 现在要求每个
      workflow 的每一行 `uses:` 都是 40 位 hex SHA 并带 `# vX.Y.Z` 注释，但它
      查不了注释是否名副其实：解引用 tag 需要联网，而这套测试按设计是离线的。
      也没有任何 workflow linter——`actionlint`、`zizmor`、`pinact`、`ratchet`
      在整棵树里都搜不到。
      *做完的标准：* 有一个由 PR 触发的检查，会在某行 `uses:` 的 SHA 解引用不到
      其注释所写的 tag 时失败。`pinact --check` 是能做到这件事的最小选择；再加
      一个 `actionlint` job 还能覆盖这里没有任何测试在看的 schema 错误。

- [ ] **把周一的依赖 PR 跨 ecosystem 合批。** `groups:` 按设计是 per-ecosystem
      的，所以 uv、pre-commit、github-actions 三路更新会分成三个 PR，至今已被
      手工并到同一分支上至少四次（#75、#97、#131）。Dependabot 支持用配置解决：
      顶层加 `multi-ecosystem-groups`，再给每个 `updates` 条目加
      `multi-ecosystem-group: <名字>`。这次没做，是因为那些条目得交出各自的
      `schedule:` 块，而配错会让 Dependabot 干脆不再开 PR——那比它要修的问题
      更糟；这件事该有自己的 PR，并观察一个真实的周一。
      *做完的标准：* 有一个 Dependabot PR 同时带着不止一个 ecosystem 的更新，
      且下一个周一的运行照常开 PR。

## 最近关掉的，记下来免得再查一遍

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
