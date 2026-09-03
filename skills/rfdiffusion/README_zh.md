# RFdiffusion Skill

RFdiffusion 用于 de novo binder、hotspot 条件生成与 motif scaffolding 的蛋白
骨架生成。本目录只提供调用外部 GPU 软件的操作配方，不内置 RFdiffusion
代码或权重，也不把生成出的骨架视为已经折叠或结合的证据。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install rfdiffusion --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install rfdiffusion`，只是这个包还没有发布
到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/rfdiffusion
python3 -m zipfile -c rfdiffusion.zip rfdiffusion
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/rfdiffusion/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没有任
何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | RFdiffusion 的可复现安装与推理指南，覆盖正确的 Hydra 引号和 contig 语义、残基与 `.trb` 溯源、分批执行、motif scaffolding，以及向 ProteinMPNN 和独立单体/复合物验证的必要交接。 |
