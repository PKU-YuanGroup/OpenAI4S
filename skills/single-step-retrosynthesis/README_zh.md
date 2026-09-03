# 单步逆合成 Skill

面向“一个产物到一步前体”的可执行 recipe。它复用已有的隔离
RetroChimera/Syntheseus adapter，并明确区分前体提案与完整多步路线。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install single-step-retrosynthesis --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install single-step-retrosynthesis`，只是这
个包还没有发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/single-step-retrosynthesis
python3 -m zipfile -c single-step-retrosynthesis.zip single-step-retrosynthesis
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/single-step-retrosynthesis/` 拷出来即可。如果你本来就在跑 OpenAI4S，
那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 模型选择、隔离调用、候选归一化、输出约定、科学边界和失败处理。 |
| [`README.md`](README.md) | 英文目录索引。 |
| [`README_zh.md`](README_zh.md) | 中文目录索引。 |

可执行的 class-unknown Benchmark 协议复用
[`../retrosynthesis_planning/single_step_benchmark.py`](../retrosynthesis_planning/single_step_benchmark.py)，
使前体规范化和 evaluator 语义只有一个实现，不在两个 Skill 中复制。
