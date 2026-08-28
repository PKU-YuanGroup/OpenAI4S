# Paper Narrative Skill

三个渐进披露 figure Skill 里最外层的一个：审阅论文稿和整套 figure 讲出来的故事，并重新安排它。输入就是工作本身，一位“责任编辑”角色的评审会给出 hook 是否立得住、从 hook 到应用的叙事弧、哪些 panel 放错了 figure、还缺哪些 panel、哪些材料该砍掉。它给的是编辑意见，既不是科学证据，也不是接收概率的预测。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install paper-narrative --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install paper-narrative`，只是这个包还没有
发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/paper-narrative
python3 -m zipfile -c paper-narrative.zip paper-narrative
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/paper-narrative/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没
有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 说明什么时候加载（写稿或改稿阶段，早于 `figure-composer`），以及整个流程：从 abstract 和图注推导出 brief，以责任编辑的身份评审整套图，按叙事弧、figure 之间的搬迁、缺失 panel 和删除清单动手，把每张留下的 figure 的 claim 交给 `figure-composer`，最后对新一版重新评审。 |
| [`kernel.py`](kernel.py) | 可选 sidecar：`pn_sdk` 取得的 `host` 句柄不会因为内核里这个名字被重新绑定而失效；`paper_brief_schema` 与 `narrative_review_schema` 是两份结构化输出的 schema；`derive_paper_brief` 用一次强制走工具的 `host.llm` 调用，从 abstract 加图注里提取 pitch、vision 和逐图 claim；`narrative_review_task` 构造针对整套图的责任编辑 prompt。 |

模型给出的缺失 panel 建议，只是指出一项值得做的分析。它不等于这项分析已经做过。
