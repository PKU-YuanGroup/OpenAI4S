# 单细胞 RNA 分析 Skill

这是 OpenAI4S 自维护的人/鼠、已完成 cell calling 的 10x GEX scRNA-seq 与
snRNA-seq 工作流。它提供版本化配置契约、支持单样本描述性或 donor-aware 对比分析的
Scanpy 流程、保守的科学门控、可恢复的检查点和可审计的结果包；不会修改或复制仓库中
固定版本的 `bioSkills` 集合。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install single-cell-rna-analysis --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install single-cell-rna-analysis`，只是这个
包还没有发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/single-cell-rna-analysis
python3 -m zipfile -c single-cell-rna-analysis.zip single-cell-rna-analysis
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/single-cell-rna-analysis/` 拷出来即可。如果你本来就在跑 OpenAI4S，
那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 路径 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 面向 Agent 的简短入口：适用范围、公开调用、阶段路由、失败处理、Artifact 交付和解释边界。 |
| [`kernel.py`](kernel.py) | 延迟导入科学依赖，实现 `preflight(config)`、`run(config, output_dir)` 与 `resume(run_dir)`。 |
| [`references/`](references/) | 输入、科学流程、注释、统计和输出的详细契约，并配有独立的双语目录说明。 |

该工作流以保留证据为原则：raw counts 独立保存在 `layers["counts"]`，Harmony 只改变
embedding，cluster marker 不替代条件差异表达，未经确认的标签可以保留为 `Unknown`。
