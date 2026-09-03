# 正向反应预测 Skill

使用本地 ReactionT5v2-forward 预测产物，并对逆合成前体做严格标注的
round-trip recovery 检查。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install reaction-forward-prediction --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install reaction-forward-prediction`，只是
这个包还没有发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/reaction-forward-prediction
python3 -m zipfile -c reaction-forward-prediction.zip reaction-forward-prediction
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/reaction-forward-prediction/` 拷出来即可。如果你本来就在跑
OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优
先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝
去做的那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 环境安装、直接推理、round-trip 比较、provenance、输出 schema 和失败模式。 |
| [`README.md`](README.md) | 英文目录索引。 |
| [`README_zh.md`](README_zh.md) | 中文目录索引。 |
