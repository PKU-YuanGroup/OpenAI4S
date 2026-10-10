# DiD 分析

[English](README.md)

针对科研人员明确界定的面板干预，按固定顺序执行传统 DiD、动态事件研究、
伪时点／伪处理组安慰剂检验，再根据设计使用分批处理 group-time ATT 或共同
处理时点的三重差分。保留事件来源声明、原始观测、处理分配聚类和全部请求结果。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install did-analysis --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。npm 0.2.0 不包含本配方，请使用上面的 GitHub 安装命令。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/did-analysis
python3 -m zipfile -c did-analysis.zip did-analysis
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/did-analysis/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/did-analysis/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 使用与范围

通过 Skill loader 加载 `did-analysis`，按 `SKILL.md` 导入其 `kernel` sidecar。
估计只依赖标准库；可选绘图需要科学环境中的 matplotlib。输入为明确整数时期的
长格式面板和实际处理状态；分批处理使用固定采用 cohort。缺省处理方式拒绝
不完整样本；显式选择完整案例分析时记录被排除的实体。填补的结果值不计为观测。

传统 DiD 使用逐实体的前后期均值，按分配聚类计算 CR1 协方差和 Student-t 推断。
事件研究保留联合协方差。伪时点不能混入真实处理，伪处理组只从真正从未处理的
对照中选择完整聚类。随机伪组分布属于描述性诊断。分批估计是无条件 group-time
对比，明确记录比较组和聚合权重。DDD 限于无条件、共同处理时点的 2×2×2 设计。
条件／双重稳健估计、同时置信带和一般分批 DDD 需要另选有依据的估计器；sidecar
不宣称具备这些能力。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 科研设计信息收集、按顺序执行的分析配方、安慰剂规则、衍生方法选择和解释边界。 |
| [`kernel.py`](kernel.py) | 经审计的标准库估计、聚类推断、动态／安慰剂／衍生结果、来源记录与确定性导出，以及可选诊断图。 |
| [`method-notes.md`](method-notes.md) | 公式、估计目标、推断约定、比较组／权重规则及主要方法来源。 |

开发过程见 [`doc/skill-development_zh.md`](../../doc/skill-development_zh.md)。
独立实验见 [`experiments/did-skill-validation/`](../../experiments/did-skill-validation/README_zh.md)。
这些检查验证行为和数值计算，不能验证具体实证研究的识别假设。
