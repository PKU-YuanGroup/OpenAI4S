# Protein Mutation Enhancement Skill

迭代式蛋白突变改造的流程：建突变文库、打分、排序，再决定要不要跑下一轮。它是编排层，不是模型。文库枚举、分数合并、排序和循环控制都是确定性的纯标准库实现；ESM、折叠和实验的分数来自别处——序列效应找 `fair-esm2`，结构找 `esmfold2`——再按 `A12V+G47D` 这样的稳定变体 ID 并进来。某个变体排在第一，并不能证明它获得了新功能。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install protein-mutation-enhancement --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install protein-mutation-enhancement`，只是
这个包还没有发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/protein-mutation-enhancement
python3 -m zipfile -c protein-mutation-enhancement.zip protein-mutation-enhancement
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/protein-mutation-enhancement/` 拷出来即可。如果你本来就在跑
OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优
先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝
去做的那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 输入契约；single/double/higher-order 文库怎么建；外部分数从哪来（序列效应用 `fair-esm2`，结构用 `esmfold2`）；带阈值的排序；一轮跑完是继续还是停下的判据；实用默认值；以及结果仍然需要的验证。 |
| [`kernel.py`](kernel.py) | 可选 sidecar，纯标准库：校验序列和 `A12V` 形式的突变写法，规范化并施加变体，确定性地枚举文库，并生成按位置排序的稳定 ID，让分数表可以放心按 `id` 关联。它用氨基酸类别、疏水性、电荷、体积的本地启发式给替换本身打一个 property 分；读取分数表，把文库写成 FASTA；合并并归一化各项指标；按加权综合分排序；对着接受阈值跑一轮筛选并给出是否继续；提出下一轮值得放开的位点；最后把排序结果写成 JSON 落盘。 |

内置的 property 分只是综合分里的一个启发式项，不是功能预测器。排在最前面的候选变体，仍然需要独立的计算与实验验证。
