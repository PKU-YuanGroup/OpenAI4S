# DiffDock Skill

DiffDock-L 是盲对接：它用扩散模型在整个蛋白表面上采样配体的摆放方式，不需要搜索盒，也不需要事先指定口袋，再用另外训练的 confidence head 给这些样本排序。要把一个 SMILES 或 SDF 对接到某个 PDB 上，或者给下游重打分准备一个起始 pose，就用它。DiffDock 仓库、权重、受体预处理和 GPU 环境都要另行准备，本目录一样都不捆绑。

DiffDock 的 confidence 排的是 pose 的合理性，不是结合亲和力。拿到 pose 之后仍然要做化学检查，通常还得再走一遍下游打分或结构优化；受体如果是从序列折叠出来的，还会再叠一层来自模型的不确定性。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install diffdock --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。包发布到 npm 之后，同一条命令的简写是 `npx openai4s-skills install diffdock --target claude`。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/diffdock
python3 -m zipfile -c diffdock.zip diffdock
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/diffdock/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/diffdock/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 单个 complex 走 CLI 的操作手册：SMILES、SDF、PDB 输入怎么给，排好序的 pose 文件和其中的 confidence logit 说明了什么、又不能说明什么，跑一次需要什么样的机器，以及哪几种报错值得一眼认出来。全篇都把几何构象和亲和力分开讲。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`references/`](references/) | 按需读取：批量与化合物库对接，以及只有序列、没有受体结构时的流程。 |
