# Catalyst SAR Screening Skill

单原子催化剂 SAR 筛选的渐进披露 recipe，能量引擎是写死的：石墨烯 M–N4 结构只能用 FAIRChem UMA `uma-s-1p1` / `oc20` 评估，然后排序、出报告。UMA、Hugging Face 访问或凭据不可用时，这个 recipe 只能停下来问用户；悄悄换成查表、经验规则或别的 MLIP 都是禁止的。

Python sidecar 里是真实的 pipeline 代码，但要跑出可用的结果，还需要兼容的科学计算依赖包、模型权重、按需提供的 `HF_TOKEN` 或可达的 hub、算力，以及一个全新的工作目录。仓库里提交的 `metal_center_dissolution_*` 文件是刻意抽掉数值的开发者演示壳，绝不能当成真实运行结果返回给用户。

就算这一趟跑得干干净净，也别忘了 UMA 是什么。`oc20` 任务下的 `uma-s-1p1` 是机器学习势，是用来逼近 DFT 能量的代理模型，而不是真去解它；报告里每一个溶解电位、每一个 ORR 过电位，都是从它预测的结合能推出来的。所以这个 Skill 给出的排序只是一份分诊清单：接下来哪些金属中心值得排进 DFT 计算或者实验合成。它不是对这些催化剂真实稳定性或活性的测量。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`.gitignore`](.gitignore) | 把 Python 构建、缓存文件以及所有图片格式挡在 Skill 根目录之外，避免真实运行产生的图被误认成仓库里提交的用户交付物。 |
| [`SKILL.md`](SKILL.md) | 硬锁定的 recipe 本身：需要哪个 UMA 后端和运行环境、什么时候必须停下来问用户而不是自己变通、如何把 `run_pipeline` 跑进一个全新的工作目录、固定的流程阶段、哪些文件才算交付物、开发者演示的警告，以及分析人员的自检清单。 |
| [`kernel.py`](kernel.py) | 可选的 sidecar，也是 pipeline 真正的所在。它加载结构 catalog，把一条描述解析成 POSCAR（先在 catalog 里精确匹配，匹配不到就替换金属中心派生），提供只走 UMA 的 `CalculationTools`，并在开跑前检查依赖、hub 可达性和模型是否就绪。之后评估溶解与 ORR 指标、对候选结构排序、分析 SAR 趋势、把结构解析回来，并渲染图、dashboard 和报告。`run_pipeline` 把整条链路串起来；`run_metal_center_dissolution_case` 是受约束的 helper，固定 M–N4 motif，只改变金属中心。 |
| [`contcar_catalog.json`](contcar_catalog.json) | 内嵌 catalog 的第 2 版：28 段石墨烯 / pyridineN M–N4 slab 的 POSCAR 文本，能对上时作为精确模板，对不上时作为最接近的起始结构。它是合成的 fixture，明确不是实验数据集发布。 |
| [`build_example.py`](build_example.py) | 只重建文本和 HTML 的开发者演示壳：清掉数值结果、后端字段和图片路径，写入免责声明，全程不调用 UMA。 |
| [`metal_center_dissolution_descriptions.json`](metal_center_dissolution_descriptions.json) | 三个演示用的结构请求（Mn-N4、Fe-N4、Cu-N4），用来说明输入长什么样。 |
| [`metal_center_dissolution_summary.json`](metal_center_dissolution_summary.json) | 溶解模式下经过净化的三行演示元数据。里面没有收敛的数值预测，标了 `demo: true`，不是用户交付物。 |
| [`metal_center_dissolution_dashboard.html`](metal_center_dissolution_dashboard.html) | 生成出来的演示壳 dashboard：自包含的 HTML，带免责声明，不含真实的 UMA 图或指标。 |
| [`metal_center_dissolution_report.md`](metal_center_dissolution_report.md) | 生成出来的演示壳方法与报告文本，其中明说不包含任何已算出的候选结果。 |
