# 信息系统研究设计 Skill

把信息技术的具体机制或设计原则连接到可论证的研究设计。本适配合并了 Bryce Wang 的 Awesome-Journal-Skills 中理论发展与研究方法两个配方，提供本地规划流程及尚未填写的“主张—证据”工作表；不运行估计器，也不要求新增 Python 依赖。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install is-research-design --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。npm 0.2.0 不包含本配方，请使用上面的 GitHub 安装命令。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/is-research-design
python3 -m zipfile -c is-research-design.zip is-research-design
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/is-research-design/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/is-research-design/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 范围与复用

用于行为、信息系统经济学、组织或设计科学研究问题。文献检索、面板预处理与 DiD 估计继续使用现有的 `literature-review`、`panel-data-preprocessing` 和 `did-analysis`。机器学习评估复用已有实验与评估技能。方法检查表是指导，不是运行时强制完成门控。

## 来源与许可

[`UPSTREAM.json`](UPSTREAM.json) 记录来源版本及原始文件哈希；[`LICENSE`](LICENSE) 原样保留 Bryce Wang 的 MIT 许可。本适配移除了上游期刊页数规则及不可用的 StatsPAI/Stata MCP 调用，加入 OpenAI4S 路由和明确为空的工作表，并合并重复的理论与设计指导。`origin: openai4s` 表示随项目内置分发，不代表原创作者。这是社区维护的指导，不是 MIS Quarterly 官方技能。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 发现说明、研究设计流程、既有技能路由及可运行的产物示例。 |
| [`design-template.json`](design-template.json) | 尚未填写的研究设计和主张—证据工作表，明确标记证据缺失。 |
| [`UPSTREAM.json`](UPSTREAM.json) | 固定上游版本、来源文件哈希与适配记录。 |
| [`LICENSE`](LICENSE) | 与上游逐字节一致的 MIT 许可证。 |

配方用 `host.skills.read` 读取资源，通过 `host.write_file` 保存草稿。保存工作表不代表理论已验证、研究已预注册或因果效应已建立。
