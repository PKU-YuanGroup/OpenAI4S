# 面板数据预处理

[English](README.md)

通过明确的实体、时间或宽表列映射构造长格式面板，检查样本覆盖，计算基础统计，
并以图形核查持续的突变。用户提供的事件日期、二元处理状态变化与数值型候选
突变分别记录。本 Skill 不估计因果效应。

## 面板构建前的可选清理

先使用 `profile_cleaning` 查看数据类型、缺失与分布；测量或类别含义有歧义时，
先与科研人员确认。依据数据画像或科研人员明确选择采用合适方法，并记录
`decision.basis` 与 `decision.rationale`。KNN 是可选方法，不是缺省方案。
每次拟合都必须明确给出从 1 开始的 `fit_rows`；统计量、邻居、类别映射和文本词表
仅从选定的训练／基线记录学习。

`clean_data` 支持均值／中位数／KNN 数值填补、one-hot／label／显式 ordinal
类别编码和 count／TF-IDF 词汇文本特征。纯标准库实现保留原输入与原类别／文本列，
保护 ID／时间／处理状态，保留无法解决的缺失，并用 `_imputed_columns` 标记
实际填补的单元格。中文文本需要在上游分词并记录方法；count／TF-IDF 不是语义
embedding。`fit_cleaner`、`transform_cleaner` 支持 JSON 模型复用，不在未来数据上重拟合。

`preprocess_panel(..., cleaning={...})` 在构造面板之前执行明确配置的清理，并保存
`cleaning_model.json`、`cleaning_report.json`，包括方法决定及原始／处理后记录哈希。
编码列保留为协变量，除非明确选择为结果指标，否则不参与突变扫描。窗口检查与
数值事件筛选排除填补的结果值；描述性统计可能包含它们，同时注明解释范围。
可运行示例、邻居组别、回退规则与拟合／应用边界见 `SKILL.md`。

## 筛选前的窗口检查

`assess_windows(panel, window=3, noise_tolerance=...)` 在筛选前逐实体、逐指标
检查前后窗口是否连续、完整，并报告基线方差、趋势与噪声。给出测量单位内的正数
容忍度后，可以获得有条件的扩大窗口建议；通过筛选流程调用且未指定容忍度时，
正数 `min_abs_change` 可提供记录来源的缺省目标。短基线、趋势和序列相关分别
标记为需要复核的状态。默认五期噪声基线仅为工程审查标准；均值精度计算依赖
独立且稳定的噪声假设，不能当作检测功效计算。
默认检查范围按实体内的实际记录数限制上界，并始终保留请求的窗口宽度。
最多检查 64 个不同宽度（含请求值）；默认范围超过限制时，需要显式提供
有界的 `window_options`。

`preprocess_panel` 先完成检查，再筛选候选，保留 `window_assessment` 和
`analysis.window_assessment_summary`。`window_assessment.json`、
`window_assessment.csv` 保存覆盖情况、基线诊断与建议；可选的
`window-readiness.png`、`window-readiness.svg` 展示完整样本的窗口可用性。
建议仅供核查，不自动更改筛选窗口。具体接口、分指标容忍度、假设与同一时间锚点
的比较规则见 `SKILL.md`。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。有 Node 18+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install panel-data-preprocessing --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 `./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。npm 0.2.0 不包含本配方，请使用上面的 GitHub 安装命令。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）写法，只解出这一个目录：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/panel-data-preprocessing
python3 -m zipfile -c panel-data-preprocessing.zip panel-data-preprocessing
```

同一份内容的图形界面版本是点击下载整个[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)，Windows 上也走这条路：解压后把 `skills/panel-data-preprocessing/` 拷出来即可（`unzip main.zip 'OpenAI4S-main/skills/panel-data-preprocessing/*'` 只解这一个目录）。如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事：[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 可运行的仅训练子集拟合清理、预处理、筛选前窗口与噪声检查、可视化核查配方，以及方法选择规则、筛选公式和解释边界。 |
| [`kernel.py`](kernel.py) | 标准库数据画像／清理／编码与可复用模型、CSV 读取、显式宽转长与聚合、来源行与填补标记映射、日历完整性检查、基础统计、窗口可用性评估、事件筛选和结果导出；matplotlib 诊断图仅在调用时按需导入。 |

离线案例回放命令为 `python -m harness.evals.panel_data --json`，执行真实 sidecar，
无需模型或内核。绘图需要科学环境中的 matplotlib，预处理和离线回放不需要。
第一版在内存中处理记录，不提供流式大数据 ETL。
