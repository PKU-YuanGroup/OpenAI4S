# Evaluate Model Skill

留出集评估，外加几个纯标准库的指标函数。一个分数单独摆着没有意义，所以这个 Skill 反复逼你做的那个决定是“和谁比”：这个数字要压过哪个基线，以及主指标是不是在任何人看见测试集之前就定下来的。加载时可以把 [`kernel.py`](kernel.py) 挂进常驻的 Python 内核。它不会替你产生任何东西：不训练模型，不给预测，不划分 split，也不下科学结论。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install evaluate-model --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install evaluate-model`，只是这个包还没有发
布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/evaluate-model
python3 -m zipfile -c evaluate-model.zip evaluate-model
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/evaluate-model/` 拷出来即可。如果你本来就在跑 OpenAI4S，那这里没有
任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 主指标要从问题里定，不能从结果里挑——看过测试集之后再选的指标，已经不算证据了——并且要对着一个简单基线来打分，还要给区间，而不是只甩一个点估计。此外：确认评估用的行以及与它们相关的实体，从未参与过拟合、预处理、特征选择或阈值选择；把概率质量、排序质量和阈值化决策分开看，因为 target 不均衡时 accuracy 基本说明不了问题；子组与失败模式的检查；二分类与回归的辅助函数怎么调用；以及报告的硬性要求，包括这一条：bootstrap 区间描述的是重采样带来的波动，它修不了泄漏、分布偏移，也修不了行与行之间的依赖。 |
| [`kernel.py`](kernel.py) | 可选 sidecar。`binary_classification_metrics` 返回混淆矩阵计数、accuracy、precision、recall、specificity、F1、balanced accuracy，传入分数时还会算上处理了并列的 ROC AUC；分母为空的比值返回 `None`，不会悄悄写成 0。`regression_metrics` 返回 MAE、RMSE、bias 和 R²。`bootstrap_ci` 对标量观测给出百分位区间，同一个 seed 下结果确定。 |

这些函数只是把你交给它的观测汇总一遍。数据是不是真的留出、彼此是否独立、有没有代表性、有没有临床意义，它们都验证不了。
