# Remote Compute SSH Skill

把活派到用户已经配好的 SSH/SLURM 主机上：先搞清这台主机到底提供什么，把文件 stage 上去，提交并让审批弹窗完成它该做的事，然后在后续 cell 里轮询 `.result()` 直到任务进入终态，把输出 harvest 回来，最后把这次摸清的主机知识记下来，让下一轮直接从这里接着走。每一次提交都会在用户面前弹出审批框，一旦批准就在花他们的配额——接连提交失败，烧的是他们的注意力和机时——所以这份 recipe 的整个形状，都是围绕“第一次提交就落地”来设计的。它本身不注册 SSH provider，也不授予任何主机的访问权限。

这一切能不能跑通，取决于用户的配置、credential、调度器与配额状态、远端到底装了什么软件，以及审批。提交任务会消耗真实资源，所以 recipe 坚持要验证结果、要有明确的意图，不许把“命令已排队”当成成功。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install remote-compute-ssh --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install remote-compute-ssh`，只是这个包还没
有发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/remote-compute-ssh
python3 -m zipfile -c remote-compute-ssh.zip remote-compute-ssh
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/remote-compute-ssh/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里
没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | `host.compute` 与控制内核的 runbook：为什么这些调用要走 `repl` 工具而不是 `python` 工具、怎么读 compute details 文档并判断还剩多少东西要摸、怎么找到一条能用的环境激活方式、输入怎么 stage、直接执行还是走 SLURM 提交、怎么轮询 `.result()` 等到终态、怎么 harvest、怎么取消和恢复，以及事后怎么更新主机笔记。 |
