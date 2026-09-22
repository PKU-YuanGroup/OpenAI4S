# Text Features Skill

从自由文本得到校准过的数值特征，对着有监督目标来建模，测试集在方案冻结之后只用一次。主模型提出问题，TypeSafe Jev 逐行作答，本 sidecar 负责拟合和评估。每个特征都保留来源问题和测量误差，**不是**人工金标准。实验能力开启时，用户选中的行文本会发往托管在美国的服务。加载时可以把 [`kernel.py`](kernel.py) 挂进常驻的 Python 内核。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install text-features --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。npm 0.2.0 不包含本配方，请使用上面的 GitHub 安装命令。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/text-features
python3 -m zipfile -c text-features.zip text-features
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/text-features/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/text-features/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 什么时候适合用（大量自由文本、有监督目标）、什么时候不适合（小样本、没有标签、敏感数据）；如何打开 `text_features`；行文本会外发；Jev 特征不是人工金标准；以及 `propose_questions`、`featurize`、`run_feature_study` 怎么调用。 |
| [`kernel.py`](kernel.py) | 可选 sidecar。`propose_questions` 用 `host.llm` 提出 Noul/Score 问题并校验去重。`featurize` 对每一行调用 `host.judge("features.custom")`：每个 Noul 一列 P(yes)，每个 Score 两列（归一化期望档位和分布标准差），`unavailable` 填 NaN 并计数。`run_feature_study` 复用 `audit-dataset`、`plan-ml-experiment` 和 `evaluate-model`，只在开发集上改问题，测试集冻结后评一次。numpy/pandas/sklearn 都是可选的延迟导入。 |
