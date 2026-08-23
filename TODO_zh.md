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

## 中断路径

- [ ] **决定本地 kernel worker 要不要 `start_new_session=True`**
      （[`openai4s/kernel/transport.py`](openai4s/kernel/transport.py)）。有
      bubblewrap 时，包装后的 argv 已经带了 `--new-session`；没有它时，worker
      留在 daemon 的进程组里，于是一个投向进程组的 SIGINT（运维在前台 daemon 上
      按的 Ctrl-C、一次 CI job 取消）会打到每一个 kernel worker。两种配置因此
      有不同的信号语义——而这种差异，恰恰只在你不用来开发的那个平台上才会显形。
      *做完的标准：* 要么改了，并在 Linux 上验证过交互式 `openai4s run` 的
      Ctrl-C；要么刻意保留这个分歧，并把理由写进那个文件的注释块。

## 最近关掉的，记下来免得再查一遍

`tests/test_mcp_lifecycle.py`、`tests/test_local_jobs.py`、
`tests/test_cluster_session_production_wiring.py`、
`tests/test_orchestration_routes.py`、`tests/test_telemetry_transmission.py`
与 `tests/test_cell_watchdog.py` 里的墙钟预算，现在都改成等待条件而不是等钟。
值得记下原因，因为当初标出它们的那次审计对了一半：它们没有一个在 CI 里红过，
而其中两处根本不是 flake，而是静默的覆盖流失——sleep 太短时测试照样是绿的，
但它走的恰恰是它被写出来要避开的那条路径。
