# evidence-walkthrough（证据走查）

一次可作为参考的完整研究流程：固定的数据库查询 → 本地分析 → 带 lineage 的
版本化产物 → 一个能在干净环境里校验的证据包。

适用于首次运行的示范、作为基准用例（输入固定，两次运行才可比），或者当一个
结果需要交给当时不在场的人时。

用接收方的方式校验导出的包，不需要 daemon：

```
openai4s verify-package <session>.openai4s-session.zip
```

通过表示这个包是**完整未被篡改**的，而不是"来源可信"——具体校验了什么、没有
校验什么，见 [`openai4s/evidence.py`](../../openai4s/evidence.py)。

配方本身在 [`SKILL.md`](SKILL.md)。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install evidence-walkthrough --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install evidence-walkthrough`，只是这个包还
没有发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/evidence-walkthrough
python3 -m zipfile -c evidence-walkthrough.zip evidence-walkthrough
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/evidence-walkthrough/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这
里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。
