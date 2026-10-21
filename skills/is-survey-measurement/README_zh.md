# IS 问卷测量

[English](README.md)

面向信息系统研究的问卷分析：明确构念与题项定义、反向计分、缺失数据决策、有序 CFA，以及信度和群体比较的解释边界。复用现有数据审计与文献技能。这是测量分析配方，不是已验证的问卷或因果模型。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install is-survey-measurement --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。npm 0.2.0 不包含本配方，请使用上面的 GitHub 安装命令。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/is-survey-measurement
python3 -m zipfile -c is-survey-measurement.zip is-survey-measurement
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/is-survey-measurement/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/is-survey-measurement/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 运行条件与范围

计分使用 Python 标准库。分析示例使用 OpenAI4S 独立 R 内核及可选 `lavaan` 包；有序观测量表的信度可选用 `semTools`。缺少 R 或分析包时会明确报告，不自动安装，也不编造结果。分析无需网络，不增加核心依赖。不需要 `kernel.py` sidecar 或外部 MCP。

## 归属与适配

改编自 AlterLab-IEU/AlterLab-Academic-Skills，精确提交记录在 `UPSTREAM.json`。该仓库的 SEM/心理测量单技能声明为 MIT。保留的 `LICENSE` 是上游声明；`origin: openai4s` 表示随项目分发，不表示原创归属。适配版明确拟合阈值及仅用载荷计算 omega 的模型假设，使用 OpenAI4S 资源读取、本地文件和现有技能替代 Claude 专用工具及不可用的兄弟技能。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 范围、题项审计与计分配方、可选有序 CFA、产物和解释要求。 |
| [`method-notes.md`](method-notes.md) | 拟合、基于模型的信度、有序与连续测量不变性及不受支持的比较。 |
| [`UPSTREAM.json`](UPSTREAM.json) | 固定仓库版本、源文件 SHA-256、许可依据与适配映射。 |
| [`LICENSE`](LICENSE) | 上游仓库的原样 MIT 声明。 |
| [`NOTICE.md`](NOTICE.md) | 上游归属、适配范围与继承的 MIT 声明。 |
