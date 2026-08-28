# Figure Style Skill

这个渐进披露 Skill 是科学 figure 的正确性与可读性 checklist，背后配一组可选的 matplotlib helper。规则按元素承担的角色来定，而不是规定一套固定的视觉风格：边框、字体、配色都是可调参数。至于交给它的数据在科学上是否成立，这份 checklist 管不着。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install figure-style --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install figure-style`，只是这个包还没有发布
到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/figure-style
python3 -m zipfile -c figure-style.zip figure-style
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/figure-style/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没有任
何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 规则本体：数据保真与自洽、把结论式标题拿去和每一行数据对账、标注的下限与上限、坐标轴与标度、配色、字体、按数据形状选图型、版式与叙事、anti-pattern 清单，以及画完再验的 QA 循环。第 1–3、8、9 节属于正确性，任何场合都适用；第 4–7 节是指引，有意为之的替代方案可以覆盖——但其中陈述事实性/感知性不变量的那几条仍然必须遵守（发散配色以语义零点为中心、CVD 安全配色、引导线锚定到它所标注的点）。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：`apply_figure_style` 在作图前一次性设置 rcParams（按角色映射的字号阶梯、朝外的刻度、无框图例、300 dpi 输出、字体内嵌）；`set_frame` 与 `panel_letter` 负责边框和面板字母；`focal_palette`、`bar_with_points`、`strip_with_median`、`end_of_line_labels` 实现规则反复要求的那几种编码，`goodness_arrow` 和 `two_tier_label` 负责标注；`panel_crops` 给出已保存 PNG 里每个 panel 的 crop box，方便回头看一眼真正画出来的东西。 |

本 Skill 用到的 matplotlib 属于可选的运行时状态，必须装在所选的内核里。图通过了几何碰撞检查，只说明几何上没问题，仍然需要感知层面的复核和领域读者的判断。
