# IS 质性研究分析

基于 AlterLab 质性分析技能的 MIT 许可适配版，面向信息系统研究中的访谈与案例分析。保留证据定位、负面案例和研究者反思，区分编码信度与反思式主题分析，并提供小型标准库名义分类一致性助手。无需额外依赖或联网。

助手返回 Krippendorff alpha、双编码者 Cohen kappa、观察到的两两一致比例及缺失审计。它不计算置信区间、不自动确定主题，也不证明研究有效。未支持的设计和不可估计的系数会明确报告。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install is-qualitative-analysis --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。npm 0.2.0 不包含本配方，请使用上面的 GitHub 安装命令。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/is-qualitative-analysis
python3 -m zipfile -c is-qualitative-analysis.zip is-qualitative-analysis
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/is-qualitative-analysis/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/is-qualitative-analysis/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 技能发现、方法选择、基于 Artifact 的可运行编码示例及报告要求。 |
| [`kernel.py`](kernel.py) | 校验输入后计算名义分类点估计和缺失审计，不进行 I/O。 |
| [`analysis-guide.md`](analysis-guide.md) | 方法区分、来源定位、案例比较和负面案例。 |
| [`agreement-guide.md`](agreement-guide.md) | 系数定义、适用边界、不确定性及人机比较。 |
| [`UPSTREAM.json`](UPSTREAM.json) | 精确上游提交、源文件 SHA-256 及适配映射。 |
| [`LICENSE`](LICENSE) | AlterLab 原始 MIT 许可证。 |
| [`NOTICE.md`](NOTICE.md) | 署名、本地修改和继承的 MIT 声明。 |

## 来源与边界

源仓库为 `AlterLab-IEU/AlterLab-Academic-Skills`，固定提交为 `e4836c08a20da195a11f30f203a8cf23ec30aa95`，仅适配 MIT 许可的 `skills/social-science-workflow/alterlab-qualitative-analysis` 模块。

这是经过适配的内置技能；`origin: openai4s` 表示分发渠道，不表示原创作者。适配移除了依赖原技能套件的调用，修正了单类别 kappa 和输入校验，并未引入上游带非商业限制的研究流水线模块。详见 [`NOTICE.md`](NOTICE.md)。
