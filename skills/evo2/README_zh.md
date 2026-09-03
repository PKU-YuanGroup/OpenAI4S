# Evo 2 Skill

Evo 2 是一个长上下文的 DNA 语言模型，这里放的是它的操作指导：likelihood 打分、取 embedding、生成序列、比较变异。它回答的是关于序列本身的问题——这个碱基、这段窗口、这处改动有多可能。如果问的是某个实验会测到什么，那是 `borzoi` 的活；两条轴一起跑，才是完整的变异优先级排序。模型代码、checkpoint 和加速器 runtime 都在外部。

GPU 容量、真正可用的上下文长度、checkpoint 的取用方式和生成质量，都要在实际运行的环境里确认，不能照着这页假定。另外，likelihood 是模型给出的分数，不等于实验测到的变异效应。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install evo2 --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install evo2`，只是这个包还没有发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/evo2
python3 -m zipfile -c evo2.zip evo2
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/evo2/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没有任何东西需
要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills`
里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 序列要以朴素的 `list[str]` 传入；给 `score_sequences` 塞 tensor，它会因为 dtype 不匹配直接报错，因为分词是这个 API 自己做的。它每条序列返回一个平均 log-likelihood，变异则在固定窗口上按 `Δll = ll_alt - ll_ref` 打分。`generate` 返回 `.sequences`、`.logits` 和 `.logprobs_mean`，三者都会填好，不用另开开关。对基因组窗口取 embedding 同样在这个 Skill 的适用范围内。此外还有模型对照表——7B 约 22 GB、40B 约 78 GB，上下文都是一百万个核苷酸——这些 checkpoint 的远程计算路径，以及那些值得早点认出来的失败模式，比如 `HF_HOME` 指向只读挂载，要等到加载器去写 `refs/` 时才炸。 |
