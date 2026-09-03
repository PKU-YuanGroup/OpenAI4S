# SolubleMPNN Skill

SolubleMPNN 不是一个独立的包。它就是在可溶 PDB 子集上重训过的 ProteinMPNN，权重随外部 ProteinMPNN 仓库一起发布，LigandMPNN 也对外暴露同一套权重。这份渐进披露 recipe 讲的是怎么选中这套先验、怎么把它跑起来；本目录不捆绑任何运行时。

可溶性先验拿几个点的天然回收率，换来表面组成上的偏置，回收率掉下来是先验在起作用，不是出了 bug。但这套权重训练用的是「能结晶出来、说明足够可溶」的结构，这和「在大肠杆菌 37 °C 下能可溶表达」并不是同一句话。所以 SolubleMPNN 给的序列，只是比普通 ProteinMPNN 更值得押注，表达这道题并没有被解掉。它照样要折叠、要表达、别进包涵体、还得干成设计它时想让它干的事，后三件事只有实验说了算。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install solublempnn --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install solublempnn`，只是这个包还没有发布
到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/solublempnn
python3 -m zipfile -c solublempnn.zip solublempnn
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/solublempnn/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没有任
何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 说明仓库怎么装，以及选中可溶权重的两条路：ProteinMPNN 的 runner 加 `--use_soluble_model`，或者用 LigandMPNN 的 runner 加 `--model_type soluble_mpnn`，后者还会把设计好的序列贴回主链。两处硬边界单独讲。仓库只发布了 `v_48_010` 和 `v_48_020` 这两个可溶 checkpoint，所以 `--model_name v_48_002 --use_soluble_model` 会因为找不到文件直接报错——`--model_name` 保持默认就好。还有：某块表面每次都长出疏水残基，并不是先验失效，多半是这块正撑着整个折叠；用 `--omit_AAs` 硬把它改成极性的，之后必须重折一遍，确认这个约束不是白加的。其余部分：为什么那句 `cd` 进仓库不能省、为什么对天然序列的回收率会掉几个点，以及为什么「能结晶」的训练集并不是对你那个表达宿主的承诺。 |
