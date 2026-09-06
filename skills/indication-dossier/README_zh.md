# Indication Dossier Skill

针对单个治疗适应症做研究、并写成一份 dossier。这里把适应症当成一个患者群体来看，而不是一个疾病实体：这些患者是谁、有多少、生物学上出了什么问题、今天怎么治、监管机构此前认可过什么、哪些临床试验改变了这个领域。有些群体根本对不上任何一个可计费的诊断，把这一点讲明白也是分内事，因为它会改变监管路径。这是一份研究与写作的 recipe，不给医疗建议，产出的内容也没有和任何已核验的证据数据库比对过。

检索由 Agent 自己完成。它必须找到当前的权威来源、精确引用，把不确定性和来源之间的分歧原样留在文里而不是抹平，并遵守各阶段参考文档里禁止捏造的硬性规则。本 Skill 不附带 sidecar，也不附带实时数据源。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install indication-dossier --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。包发布到 npm 之后，同一条命令的简写是 `npx openai4s-skills install indication-dossier --target claude`。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/indication-dossier
python3 -m zipfile -c indication-dossier.zip indication-dossier
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/indication-dossier/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/indication-dossier/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 驱动整轮研究：五个阶段，每个阶段写一个 waypoint 文件；第 1 阶段结束后有一次身份确认，工作目录里已经有 waypoint 时可以续跑。它同时规定输入、Skill 期望的工具（以及某个 MCP 未连接时的退路）、输出布局，以及综合阶段还允许补取什么、什么只能记成缺口。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`references/`](references/) | 按需加载：跨阶段通用的研究标准、每个阶段一份的操作说明、写作规则，以及 waypoint 文件的 JSON schema。 |
