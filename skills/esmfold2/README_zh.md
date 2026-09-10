# ESMFold2 Skill

面向 Biohub ESMFold2 / ESMFold2-Fast co-folding 模型的渐进披露 recipe，同一批发布的 ESMC 蛋白语言模型也一并覆盖。这里不带任何实现：没有代码，没有权重，没有 Hugging Face 访问，也没有 GPU 环境。

模型、后端、版本到底有没有，必须到运行环境里现查。论文自己给的 FoldBench 数字里，抗体-抗原界面的 DockQ 通过率是 50%–55%，也就是说将近一半是错的；而这类数字背后的跑法是 25 个 seed 乘 5 个扩散样本，再从这 125 个结果里挑最好的一个当答案——只跑一次、再按 ipTM 自排的结果，分量要比这个数字听上去的轻。PDB 的训练截止时间是 2021 年 9 月，此后解出的结构，模型一个也没见过。结构、突变打分、残基接触和可解释性特征都是模型算出来的预测，不能当成经过实验验证的结论来讲。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install esmfold2 --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。对于 npm 发布版中包含的配方，可用 `npx @pku-yuangroup/openai4s-skills install esmfold2 --target claude` 安装发布版本。npm 目录可能与当前仓库不同。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/esmfold2
python3 -m zipfile -c esmfold2.zip esmfold2
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/esmfold2/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/esmfold2/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 完整的 recipe：蛋白、DNA、RNA、配体输入怎么描述，什么时候单序列模式就够、什么时候要喂 MSA，以及扩散步数和 trunk recycle 次数怎么设才能复现论文里的数字。此外还讲了切到 fused kernel backend 到底能换来什么：trunk 大约快 1.5–6 倍，序列越长收益越大，但短序列的折叠受扩散步主导，要到 L≈300–400 才追平。（gotchas 一节笼统地写成“比论文慢约 12 倍”，该信的是详细那一节。）fused 与参考实现的结构在噪声范围内一致。再往后是 confidence 输出怎么读、几个模型变体怎么选、上游权重和许可的出处。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`references/`](references/) | 按需读取：实验性 design hook 的说明，以及 ESMC 语言模型的 recipe。 |
