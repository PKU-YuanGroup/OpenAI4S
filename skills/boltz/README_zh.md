# Boltz Skill

Boltz-2 的渐进披露 recipe。Boltz-2 是开放权重的扩散式 co-folding 模型：输入蛋白质、DNA、RNA 与配体链，输出 mmCIF 结构和一组 confidence，还带一个可选的小分子 affinity 预测头。这份 recipe 教 Agent 怎么写输入 YAML、怎么驱动外部的 `boltz` 依赖包、怎么读回结构与 confidence 文件。Boltz 本身并不随本目录分发。四个 co-folding Skill 里，binder 验证类的任务默认走它：权重是完全开放的 MIT 许可，采样也是四者里最快的。

依赖包、权重、GPU 与可选的 MSA 服务均须另行可用。四个 co-folding 模型里只有 Boltz-2 会返回一个带实验单位的数——`affinity_pred_value` 就是 log10(IC50，单位 μM)——也正因为如此，它特别容易被当成某次实验测出来的结果去引用。它的作用是把候选排出先后，不是把它们测出来。ipTM 是同一类东西：它说的是模型把界面摆得前后一致。这两个数都不能证明两条链真的会结合。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install boltz --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install boltz`，只是这个包还没有发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/boltz
python3 -m zipfile -c boltz.zip boltz
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/boltz/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没有任何东西
需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 开头讲 YAML 的 entity 块和 `boltz predict` 的参数，篇幅的大头留给了另外三个 co-folding 模型都没有的 affinity 预测头：在 `properties:` 块里指定某一条 ligand 链作为 binder，`affinity_pred_value` 返回的是 log10(IC50，单位 μM)，排序命中要看的是 `affinity_probability_binary`。一次输入只能算一个 affinity 配体，Boltz v2.2.x 还把它限制在 128 个原子以内；用 FASTA 输入则根本没法要 affinity。两个坑各占一节：`msa: empty` 只换来精度损失，省不下显存，因为 MSA 搜索跑在 CPU 上；`--no_kernels` 会退回参考的 PyTorch 实现，SKILL 给的说法是慢一倍左右，但结果是正确的、不是降级的，适合临时救急，不适合整轮 campaign。本仓库既没有给这条路径做过基准测试，也没有拿它和 fused kernel 的输出对比过，所以这只是经验性的指引，而不是「输出完全一致」的实测结论。之后是 confidence JSON 的读法、一张报错对照表和许可。 |
