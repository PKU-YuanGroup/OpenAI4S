# Audit Dataset Skill

在下游任何环节开始信任数据之前，先对行式表格数据做一次结构审计，全部用标准库写成。`plan-ml-experiment` 决定的是独立单元应该是什么，这个 Skill 则盯着你手上真实的行，问那条边界经不经得起它们的检验。Skill 加载后，[`kernel.py`](kernel.py) sidecar 会把可复用的辅助函数装进常驻的 Python 内核。它不会自己去读数据集，也不会替你决定某个领域特有的异常该怎么处理。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install audit-dataset --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install audit-dataset`，只是这个包还没有发
布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/audit-dataset
python3 -m zipfile -c audit-dataset.zip audit-dataset
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/audit-dataset/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没有
任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 分析前的流程，核心是泄漏这一问：同一个病人、同一个分子骨架、同一条时间序列，或者一对近似重复的记录，只要横跨了 split 的两侧，之后测出来的每个数字都会被抬高——哪怕行 ID 各不相同也一样。围绕它还有：schema 漂移和混合类型往往是解析或哨兵值的 bug，缺失要当成采集过程的事实来读而不是急着填补，重复 ID 未必是重复观测（也可能是合理的重复测量），target 是否均衡，以及审计必须留下的那份机器可读输出——没有说清查过哪些泄漏键、缺失值按什么策略处理，就不能把一份数据称作干净。 |
| [`kernel.py`](kernel.py) | 以 `audit_rows` 为核心的可选 sidecar：校验传入的行与列参数，把值规整成确定性表示以便比较，逐列汇总缺失、观察到的类型和唯一值个数，统计重复行和不唯一的 ID，统计 target 的分布，并找出同时出现在 split 两侧的实体。不用 pandas，也不用 numpy。 |

结构上干净，不等于数据有代表性、标注可靠，也不等于不存在近似重复带来的泄漏。这些仍然要靠领域审阅。
