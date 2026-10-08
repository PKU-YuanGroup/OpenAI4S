# Lab 仿真 Skill

[English](README.md)

面向 OpenAI4S Web daemon 的纯仿真实验配方：发现可用设备、检查能力、选择一个受限动作、分析公开观测，并将报告证据绑定到精确的实验。命令结果未知时只查询而不重发。完成状态由 Host 独立核验；配方不授予权限，也不控制真实硬件。目录没有 Python sidecar，不新增软件包依赖。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install lab-simulation --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。npm 0.2.0 不包含本配方，请使用上面的 GitHub 安装命令。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/lab-simulation
python3 -m zipfile -c lab-simulation.zip lab-simulation
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/lab-simulation/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/lab-simulation/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 能力发现、单动作执行、未知结果对账、完整数组分析，以及基于证据的完成与报告配方。 |
| [`README.md`](README.md) | 英文概览、安装说明与目录清单。 |
| [`README_zh.md`](README_zh.md) | 中文概览、安装说明与目录清单。 |
