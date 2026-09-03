# Borzoi Skill

Borzoi 直接从 DNA 序列预测功能性覆盖 track——RNA-seq、CAGE、DNase、ChIP。想拿一个位点上的整段预测 track，或者想看变异在实验层面的后果而不是语言模型给的 likelihood，就用它比较 ref/alt 窗口；likelihood 那一半归 `evo2`，两者回答的是同一个变异问题的不同侧面。这里讲的是怎么驱动一个外部的 PyTorch 移植版，模型 runtime 和 checkpoint 都不在本目录里。

能不能跑起来取决于环境：依赖包是否兼容、权重是否已经下载、track 元数据是否就位，以及有没有足够大的 GPU 显存。预测出来的 track delta 是可以用来排优先级的模型证据，但它不是因果证明，也不构成临床验证。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install borzoi --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install borzoi`，只是这个包还没有发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/borzoi
python3 -m zipfile -c borzoi.zip borzoi
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/borzoi/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没有任何东西
需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 输入窗口固定为 524,288 bp 的 one-hot DNA，而模型本身没有任何属性会告诉你这一点，所以最先撞上的就是 shape 不匹配：要么补齐，要么裁掉。输出是 7,611 条人类 track 在 32 bp bin 上的张量；另一套 2,608 条的小鼠 head 默认关闭，要显式打开并选中才会用到。接下来是：track 元数据到底放在哪里（`TRACKS_DF`，而不是 base 模型根本没有的 `targets` 属性）、怎么和输出对上号，ref/alt 变异打分怎么做，显存下限是多少，以及移植版权重的 CC-BY-4.0 条款——它和随之而来的 Apache-2.0 代码许可并不一致。 |
