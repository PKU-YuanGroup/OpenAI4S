# Molecular Dynamics Skill

使用 OpenMM 和 MDAnalysis 完成分子动力学工作流：准备蛋白质与小分子体系，对溶剂化体系做能量最小化和分阶段平衡，运行 NVT/NPT 模拟，并用 RMSD、RMSF、接触、PCA、氢键和相对自由能面分析轨迹。这份 recipe 会把力场选择、周期性几何、采样局限和 provenance 说清楚；一条看起来稳定的轨迹本身并不能证明结合自由能或驻留时间。

本 recipe 从 [K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/molecular-dynamics) 导入，来源 revision 为 `a1043ef544e3a100c28e70a736b6cf32554d0677`（2026-10-16）。随附参考资料按 OpenMM 8.6.1 和 MDAnalysis 2.10.0 审阅；安装、力场资源、GPU 支持以及可选的 PDBFixer/OpenFF 依赖仍需另行准备。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install molecular-dynamics --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。npm 0.2.0 不包含本配方，请使用上面的 GitHub 安装命令。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/molecular-dynamics
python3 -m zipfile -c molecular-dynamics.zip molecular-dynamics
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/molecular-dynamics/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/molecular-dynamics/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 离线配方检查

按 `SKILL.md` 创建独立环境后，在仓库根目录运行：

```bash
uv pip install --python .venv-md/bin/python pytest
.venv-md/bin/python -m pytest tests/test_molecular_dynamics_skill.py
```

测试使用 OpenMM 8.6.1 和 MDAnalysis 2.10.0 在小型合成数据上直接执行文档代码，
覆盖能量最小化、NVT/NPT 阶段、从日志恢复轨迹时间、RMSF 索引及含无效几何的周期接触。
使用 Reference 平台，不需要网络或 GPU。默认离线套件检查语法，并在可选依赖缺失时
跳过执行测试。这些检查提供 API 与几何行为的证据，不证明蛋白质配体模型有效或采样收敛。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`LICENSE.md`](LICENSE.md) | 本导入 Skill 的上游 MIT 许可证。 |
| [`SKILL.md`](SKILL.md) | OpenMM 模拟和 MDAnalysis 轨迹分析的操作手册，涵盖体系准备、分阶段平衡、验证、周期性几何以及 provenance 边界。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`references/`](references/) | 按需读取：MDAnalysis 分析模式，以及保守的体系准备和力场指导。 |
