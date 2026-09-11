# Example Stats Skill

一个小型的渐进披露 Skill，用零依赖的描述性统计演示 `SKILL.md` 加 Python sidecar 这个模式。sidecar 只在 Skill 被选中时才加载，处理的也只是调用方传进来的数值序列。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install example_stats --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。对于 npm 发布版中包含的配方，可用 `npx @pku-yuangroup/openai4s-skills install example_stats --target claude` 安装发布版本。npm 目录可能与当前仓库不同。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/example_stats
python3 -m zipfile -c example_stats.zip example_stats
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/example_stats/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/example_stats/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 汇总、分位数、z-score 与 Pearson 相关系数的 import 示例和简短用法。 |
| [`kernel.py`](kernel.py) | 可选 sidecar，作用在普通的 Python 数字列表上：`mean`、样本或总体 `std`、`median`、线性插值的 `quantile`、`zscore`、`correlation`，以及把它们合到一起的 `summary`。每个函数都会先检查输入，空序列直接报错。 |

这些都是教学和通用场景下的普通计算。它们不会替你挑统计设计，也不能让一个推断变得成立。
