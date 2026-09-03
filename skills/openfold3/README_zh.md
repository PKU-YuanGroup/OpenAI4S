# OpenFold3 Skill

OpenFold3 的渐进披露 recipe。OpenFold3 是 AlQuraishi Lab 用 PyTorch 复现的 AlphaFold3，权重开放。这份 recipe 说明模型怎么装、怎么跑，但一样都不带：没有 OpenFold3 代码，没有数据库，没有参数，也没有准备好的环境。它和另外三份 co-folding recipe 有两点不同。OpenFold3 根本不读 FASTA，一次查询是一个 JSON 对象，还要过一遍严格的 schema 校验。另外，它的 MSA 服务开关默认是开的，也就是说除非明确关掉，序列会离开本机。

蛋白质、核酸、配体、模板与加速器这几条路实际能不能走通，取决于装的是哪个上游版本、资产齐不齐。聚合 confidence 文件要按它本来的含义读。`sample_ranking_score` 只在同一次运行的若干样本之间排序，所以哪怕这次查询压根没有真实答案，其中最好的那个样本照样排在第一位。`has_clash: 0.0` 说的是原子没有重叠，那是模型画出来的几何的性质。这些数字都是模型在给自己打分。复合物到底存不存在，它们够不着。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install openfold3 --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install openfold3`，只是这个包还没有发布到
npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/openfold3
python3 -m zipfile -c openfold3.zip openfold3
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/openfold3/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没有任何
东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 先讲查询 JSON，因为写错它的结构是最先撞上的失败：一个 `queries` 字典，每条链一个 `molecule_type`，蛋白质/DNA/RNA 给 `sequence`，配体给 `smiles` 或 `ccd_codes`，整个对象要过一遍 pydantic 校验，多余的键直接被拒。接着是那两个不关就一直开着的开关——`--use-msa-server` 会把序列 POST 给 `api.colabfold.com`，`--use-templates` 还额外要求能连上 `data.rcsb.org`——所以离线或出网受限的环境必须把两个都显式设成 `false`。然后是 Hugging Face 上要先过访问申请的权重下载、默认的 DeepSpeed attention kernel 以及 DeepSpeed 缺失时改用 cuEquivariance 的回退办法、聚合 confidence 文件里的数值该长什么样、一张针对导入报错和显存不足的排查表，最后是上游许可。 |
