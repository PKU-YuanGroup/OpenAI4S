# Protein-design MCP Skill

[English](README.md)

这个 Skill 指导 agent 组合使用内置 protein-design MCP 工具，完成通用的蛋白质设计与
重设计任务。覆盖 target-conditioned binder backbone、带约束的序列设计、单体与复合物
结构预测、物理打分与 relaxation、sequence naturalness 打分、minimization，以及可复现
的候选比较。

文档也明确说明当前科学能力边界：RFdiffusion 工具要求提供 target hotspot，尚不能表达
无表位约束、motif scaffolding、unconditional 或 membrane-aware backbone generation。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install protein-design-mcp --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。对于 npm 发布版中包含的配方，可用 `npx @pku-yuangroup/openai4s-skills install protein-design-mcp --target claude` 安装发布版本。npm 目录可能与当前仓库不同。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/protein-design-mcp
python3 -m zipfile -c protein-design-mcp.zip protein-design-mcp
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/protein-design-mcp/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/protein-design-mcp/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 通用工具选择工作流、可复现控制、当前能力缺口和模型证据边界。 |
| [`README.md`](README.md) | 英文目录边界和文件清单。 |
| [`README_zh.md`](README_zh.md) | 中文目录边界和文件清单。 |

本 Skill 不内置模型包、权重或 GPU 环境；connector 及其外部 backend 需要单独配置。
