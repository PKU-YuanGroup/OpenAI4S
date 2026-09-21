# 靶点成药性评估与先导化合物初筛技能 (Target Druggability Screening)

[English](README.md)

端到端生物医药靶点成药性评估与先导化合物初筛工作流。结合来自 STRING 数据库的蛋白质相互作用网络（PPI）与来自 BindingDB 的定量药理学结合亲和力（$K_i, K_d, IC_{50}, EC_{50}$），并基于 Lipinski 类药五规则（Rule of 5）评估小分子的成药性特征。最终将分析结果汇总为结构化的 Markdown 科学调研报告，直接保存至会话工件（Artifacts）。

本技能完全基于 Python 标准库与 OpenAI4S 原生科学数据库接口构建，无需额外依赖大型化学建模服务或 GPU 资源。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install target_druggability_screening --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。对于 npm 发布版中包含的配方，可用 `npx @pku-yuangroup/openai4s-skills install target_druggability_screening --target claude` 安装发布版本。npm 目录可能与当前仓库不同。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/target_druggability_screening
python3 -m zipfile -c target_druggability_screening.zip target_druggability_screening
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/target_druggability_screening/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/target_druggability_screening/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件清单

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 面向 Agent 的核心执行方案，规定四阶段分析流程、交互式 Cell 代码示例及工件输出格式。 |
| [`kernel.py`](kernel.py) | 纯标准库伴生模块，提供 SMILES 结构描述符估算、Lipinski 五规则评估、先导物综合打分及 Markdown 工件排版功能。 |
