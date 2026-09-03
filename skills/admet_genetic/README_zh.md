# ADMET Genetic Optimization Skill

以 ADMET 为导向、从种子 SMILES 出发做分子优化，并让每个候选分子都能沿血缘追回它来自哪颗种子。它的 Python sidecar 只提供可复用的那部分：SMILES 归一化、打分契约、血缘校验和可视化。它有意不实现一套固定的遗传算法，也不对候选分子做任何实验验证。

RDKit、pandas、matplotlib、ADMET-AI、PyTorch 和模型资产都是可选依赖，必须先装进所选的运行环境。剩下的事情归 Agent：读数据契约，搭出变异、交叉和选择逻辑，把血缘记录完整保留下来，并且把每一个预测都当成初筛证据，而不是事实。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install admet_genetic --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install admet_genetic`，只是这个包还没有发
布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/admet_genetic
python3 -m zipfile -c admet_genetic.zip admet_genetic
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/admet_genetic/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没有
任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 主 recipe：前置条件、种子分子归一化、动手前必须先读的契约、如何组装 GA、ADMET/SA/QED/性质打分、过滤、多样性、血缘、输出、报告，以及报告里要写清的局限。 |
| [`kernel.py`](kernel.py) | 可选的 sidecar。它标准化并规范化 SMILES，对 ADMET-AI 的 endpoint 分类、聚合成一个分数加一组风险标记，生成规范的 `operation_detail` JSON，并按血缘契约校验 generation log。`render_optimization_history` 把这份日志渲染成自包含的 dashboard；装了 RDKit 和 matplotlib 时，还会带上分子 SVG 和统计图。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`examples/`](examples/) | 提交在仓库里、可复现的一份演示：输入、录下来的各代结果、由此派生的候选选择、报告和 dashboard。它是 fixture，不是实时优化结果。 |
| [`references/`](references/) | ADMET 运行环境说明、数据契约与血缘规则、GA 设计说明，通过渐进披露按需读取。 |
