# fair-esm2 Skill

通过外部 `fair-esm` 依赖包使用 Meta ESM-2：逐残基与整条序列的嵌入表示、掩码语言模型打分、突变效应，以及残基接触。ESM-2 只读序列。它拿不到结构，也不做反向折叠——接触图是纯从残基推出来的，方向和 MPNN 那几个 Skill 正好相反。开工前先绕开一个依赖包的坑：`fair-esm` 和 Biohub 的 ESM 分支都以 `esm` 的名字导入，却是两个不同的库。这份 recipe 讲的是 Meta 那个，分支那条路见 `esmfold2`。

本目录不捆绑任何权重，也不会替你留出它所需要的 CPU 或 GPU 容量。模型给出的似然与接触图都属于计算预测，它们对你的具体任务是否成立，要靠与任务匹配的验证来确认。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install fair-esm2 --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install fair-esm2`，只是这个包还没有发布到
npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/fair-esm2
python3 -m zipfile -c fair-esm2.zip fair-esm2
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/fair-esm2/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没有任何
东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 先选权重（8M 用来冒烟，默认 650M，要最好的嵌入就上 3B），再依次讲 alphabet 与 batch 转换怎么做、取哪一层的表示、整条序列池化的向量和逐残基向量的区别、怎么用掩码给突变打分、怎么拿接触图。批处理、内存开销和模型本身的限制放在最后。 |
