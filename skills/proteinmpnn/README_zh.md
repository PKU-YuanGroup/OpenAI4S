# ProteinMPNN Skill

ProteinMPNN 只看主链几何来设计序列。输入 PDB 里的配体、核酸、金属它一概看不见，`ligandmpnn` 就是为此而生；`solublempnn` 架构相同，换的是在可溶结构上训练的权重。所以设计面是蛋白-蛋白时它是对的一步，一旦界面上出现辅因子就是错的一步。这里放的是驱动外部仓库的操作手册：仓库怎么拉起来、哪些参数真正要紧、返回的 FASTA 怎么读。仓库要在任务里现拉——它没有 PyPI 发行版，checkpoint 就装在仓库本身里。

跑出来的是一条序列和一个似然。FASTA 头里的 `score=` 是平均负对数似然——在这条主链上，这条序列有多像 ProteinMPNN 会写出来的序列；`seq_recovery=` 是它和输入原本那条序列有多一致。这两个数都不说明设计能折叠、能表达、能结合；那要先过折叠模型，最后靠实验。小规模的任务用 CPU 就够；真实耗时、以及 GPU 到底值不值，取决于设计规模和运行环境。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install proteinmpnn --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install proteinmpnn`，只是这个包还没有发布
到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/proteinmpnn
python3 -m zipfile -c proteinmpnn.zip proteinmpnn
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/proteinmpnn/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没有任
何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 操作手册，围绕最容易先踩的两处展开。`--sampling_temp` 和 `--pdb_path_chains` 都是写在一对引号里、用空格分隔的字符串，逗号并不能把它们分开。还有 `--fixed_positions_jsonl`：文件如果少了最外层那一级 PDB stem 键，就会被当成“没有匹配到 PDB”，于是每个位点都被重新设计，而且不报任何警告——仓库自带的 helper 脚本能生成正确的结构。其余部分：按训练噪声划分的 checkpoint（贴近天然序列的重设计用 `v_48_002`，粗糙主链用 `v_48_030`）、什么时候 CPU 就够用，以及输出为什么只有 FASTA——ProteinMPNN 从不把设计好的序列贴回主链，这也是纯蛋白任务同样值得改用 `ligandmpnn` runner 的原因之一。 |
