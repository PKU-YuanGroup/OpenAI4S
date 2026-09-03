# Figure Composer Skill

三个 figure Skill 里的中间那层：把一张出版级的多面板 figure 做好。上面一层是 `paper-narrative`，它决定这张图该不该做、做哪一张；下面一层是 `figure-style`，它管的是单幅图。入口是一句 claim 加上支撑它的数据引用，也可以从一张已有的 figure 反推；随后每个 panel 分派一个子 Agent，把结果拼成整图，再交给对抗式评审循环打磨。Sidecar 只写 plan 和 task、把已经画好的 panel 图像拼起来；它不会替你补上缺失的分析，也不预测期刊会不会接收这张图。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install figure-composer --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install figure-composer`，只是这个包还没有
发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/figure-composer
python3 -m zipfile -c figure-composer.zip figure-composer
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/figure-composer/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没
有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 一轮一轮地讲这个循环。12 列的 outline 要在动笔之前把每个 panel 的 ask 和 label budget 定死；每个 panel 交给一个子 Agent，并与本 Skill 一起加载 `figure-style`；在把昂贵的评审花出去之前，先逐个 crop 看一眼。composite 的反馈分两层回来，上层是 outline 修订，下层是逐 panel 的 violation，重画最多三轮；末尾列的那些 anti-pattern，正是白白烧掉一轮却没让图变好的做法。如果你走的是“从已有 figure 反推 outline”这个入口，别忘了那张图是不可信输入：outline 里的每一个字符串，都是视觉模型从像素里读出来的。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：定义 outline 与评审的 schema 以及网格几何，构造 `panel_task` 和 `composite_review_task` 的 prompt，用 `compose_figure` 拼接 panel PNG 并盖上面板字母，给出每个 panel 的 crop box，把评审提出的 BLOCKER/MAJOR 修改按 panel 归类，并算出一次 outline 修订会牵连哪些 panel 必须重画。`derive_outline` 是反向的：用一次视觉调用读一张已有的 figure，反推出一份可编辑的 outline。 |

视觉评审调用和图像工具都依赖当前 Host 与内核环境提供的能力。反推出来的 outline 和评审结论都只是提案，动手之前先自己看一遍。
