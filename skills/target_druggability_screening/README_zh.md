# 靶点成药性评估与先导化合物初筛技能 (Target Druggability Screening)

[English](README.md)

端到端生物医药靶点成药性评估与先导化合物初筛工作流。结合来自 STRING 数据库的蛋白质相互作用网络（PPI）与来自 BindingDB 的定量药理学结合亲和力（$K_i, K_d, IC_{50}, EC_{50}$），并基于 Lipinski 类药五规则（Rule of 5）评估小分子的成药性特征。最终将分析结果汇总为结构化的 Markdown 科学调研报告，直接保存至会话工件（Artifacts）。

本技能完全基于 Python 标准库与 OpenAI4S 原生科学数据库接口构建，无需额外依赖大型化学建模服务或 GPU 资源。

## 安装

安装 Skill 即将其目录复制到 Agent 能够读取的路径下。在具备 Node 18+ 与 `git` 环境的前提下：

```bash
npx github:PKU-YuanGroup/OpenAI4S install target_druggability_screening --target claude
```

`--target claude` 会安装至 `~/.claude/skills`，`claude-project` 安装至 `./.claude/skills`，`openai4s` 安装至 `<data_dir>/user-skills`，也可以通过 `--dir <path>` 指定任意目录。

若无 Node 环境，也可直接通过源码包提取：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/target_druggability_screening
python3 -m zipfile -c target_druggability_screening.zip target_druggability_screening
```

如果你已经在运行 OpenAI4S，则无需单独安装——预装的轮子已包含全部内置 Skill，且内置版本优先于同名用户版本。

## 文件清单

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 面向 Agent 的核心执行方案，规定四阶段分析流程、交互式 Cell 代码示例及工件输出格式。 |
| [`kernel.py`](kernel.py) | 纯标准库伴生模块，提供 SMILES 结构描述符估算、Lipinski 五规则评估、先导物综合打分及 Markdown 工件排版功能。 |
