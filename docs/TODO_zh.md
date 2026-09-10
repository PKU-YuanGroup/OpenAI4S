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
      `npx github:PKU-YuanGroup/OpenAI4S install --all` 今天就能用，也是各处文档
      和 CLI 自己的 `--help` 打头的写法——根 README、`docs/skills.md`、`tools/`，
      以及每个 Skill 自己的页面（那一节的措辞在发布之后依然成立）。2026-09-01
      实时执行 `npm view openai4s-skills version` 仍返回 `E404`。
      *做完的标准：* 在已发布 tag 的干净 checkout 上跑过
      `npm publish --access public`，在一台没有 checkout 的机器上
      `npx openai4s-skills list` 可用，并且仍写着"这个名字解析不到"的五个页面都已
      改掉这句话——`README.md`（同时是 npm 与 PyPI 的首页）、`README_zh.md`、
      `docs/skills.md`、`tools/skills-installer/README.md` 与 `README_zh.md`；
      `grep -rlE --exclude='TODO*' 'not on npm yet|name does not resolve|还没有发布到 npm|名字解析不到' README.md README_zh.md docs tools`
      列出的正是这五个。这需要一个有发布权限的 npm 账号——任何自动化 agent 都不该
      持有这份凭据。

- [ ] **冻结六个 retrosynthesis 生产数据集。**
      `skills/retrosynthesis_planning/scenarios/test_cases/database_sources.json`
      里六行全是 `"release_status": "not_frozen"`：USPTO-50K、PaRoutes、精选
      atom-mapping benchmark、MIT 许可的 USPTO forward split、复核过的 Parrot
      condition 快照，以及 Buchwald-Hartwig HTE 分布漂移 split。在冻结之前，
      随包分发的六个样例只是每个一行的确定性协议冒烟测试，不做任何科学准确性
      声明——这正是"六个 benchmark"与"六个接线检查"之间唯一的区别。阻塞点在代码
      库之外：获取每个来源、审查其再分发许可、并钉住一个 revision。
      *完成标志：* 每个 scenario 行都是 `release_status: "frozen"`，并带上
      `revision`、`license`、`split` 和 `sha256`，且
      `test_production_database_registry_fails_closed_until_frozen` 在冻结后的
      行上仍然通过。需要有许可决策权的维护者，不应由自动化 agent 决定。

## CI 与供应链

- [ ] **观察周一的跨生态依赖合批。** 配置已把 uv、npm、Docker、pre-commit
      和 GitHub Actions 归入一个 `weekly-dependencies` 组，统一周一调度，并以
      `patterns: ["*"]` 纳入全部依赖。组内明确包含大版本升级、black 和 isort，
      这些更新随批次一起审查；不使用 `allow` 或 `ignore` 过滤可更新依赖。
      离线治理测试检查共享调度、五个生态完整覆盖及不受限的匹配规则。
      *做完的标准：* 配置进入默认分支后，真实 Dependabot PR 同时包含多个生态
      的更新，且再下一个周一仍正常产生更新。配置落地和离线测试通过不能替代这
      两次真实调度结果。

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
