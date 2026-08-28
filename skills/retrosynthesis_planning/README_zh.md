# Retrosynthesis Planning Skill

围绕目标 SMILES 做路线搜索，再把搜索结果交给化学家审阅。真正的搜索由 AiZynthFinder 在它自己的环境里完成；本目录的 sidecar 以标准库为主，负责规范化和排序导出的路线、合并重复路线、优先保留更有差异的候选、执行确定性的结构检查，并渲染供审阅的 Artifact。装了 RDKit 后可以画真实结构图并补充分子级校验；使用 Host LLM call 时再增加化学注释。

这套流程服务于规划和化学家初筛，不等于路线在实验上得到了验证。反应条件、产率、可得性、安全性，以及 LLM 写出的内容，在用文献、ELN、供应商数据和专家审阅核验之前都只是假设。结构审计可以发现损坏的路线树和可疑的原子覆盖，但它不是正向反应模型，也不能证明路线可行。

## 安装

一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent 会去读的地方。
有 Node 18+ 即可，无需先克隆仓库：

```bash
npx github:PKU-YuanGroup/OpenAI4S install retrosynthesis_planning --target claude
```

`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入
`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` 则
写到你指定的任意位置；`--dry-run` 只打印解析出的绝对路径，不写任何文件。重装时
若目标副本被你改过则拒绝覆盖，`uninstall` 也只删除它自己写过的文件。同一条命令
的发布名写法是 `npx openai4s-skills install retrosynthesis_planning`，只是这个
包还没有发布到 npm。

没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包：

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/retrosynthesis_planning
python3 -m zipfile -c retrosynthesis_planning.zip retrosynthesis_planning
```

同一份内容的图形界面版本是点击下载整个
[仓库 zip 包](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip)：解压
后把 `skills/retrosynthesis_planning/` 拷出来即可。如果你本来就在跑 OpenAI4S，
那这里没有任何东西需要安装——wheel 自带全部内置 Skill，且内置 Skill 优先于
`<data_dir>/user-skills` 里的同名副本。目标目录、来源记录，以及安装器拒绝去做的
那些事：
[`tools/skills-installer/`](../../tools/skills-installer/README_zh.md)。

## 推荐审阅流程

面向用户的编排优先使用 [`workflow.py`](workflow.py)，底层的路线规范化、证据、渲染和报告能力继续由 [`kernel.py`](kernel.py) 提供。

```python
from retrosynthesis_planning.kernel import load_aizynth_routes
from retrosynthesis_planning.workflow import (
    AiZynthSearchSpec,
    audit_routes,
    build_aizynth_search_command,
    prepare_routes,
)

search = AiZynthSearchSpec(
    policies=("uspto", "ringbreaker"),
    filters=("quick_filter",),
    stocks=("internal", "zinc"),
    cluster=True,
    nproc=4,
    checkpoint_path="checkpoint.json.gz",
)

command = build_aizynth_search_command(
    "CC(=O)Oc1ccccc1C(=O)O",
    "config.yml",
    output_path="aspirin_routes.json",
    conda_env="retro",
    search=search,
)

payload = load_aizynth_routes("aspirin_routes.json")
routes = prepare_routes(
    payload,
    max_routes=10,
    similarity_threshold=0.85,
    constraints={"require_solved": True},
)
audit = audit_routes(routes)
```

`AiZynthSearchSpec` 结构化暴露 `aizynthcli` 已公开的 policy、filter、stock、聚类、多进程、checkpoint 以及前后处理参数。搜索算法、深度、reward 和 bond constraint 仍然写在 AiZynthFinder 的 `config.yml` 中；这个封装不会暗中改写配置文件。

`extra_args` 是留给该类未建模开关的逃生舱。它会排在类型化开关之前，并且必须以自己的开关开头——因为 `--policy`、`--filter`、`--stocks` 和 `--post_processing` 都是变长参数，否则紧随其后的裸值会被它们吞掉。它既不允许重复、也不允许缩写封装已管理的开关——`aizynthcli` 使用 argparse 默认的 `allow_abbrev=True` 构建解析器，只拦 `--output` 而放行 `--out` 等于什么都没拦——因此无法悄悄改写目标、配置或输出路径。`kernel.build_aizynth_command` 对它自己拥有的三个开关执行同样的规则，因此直接调用底层入口的调用方同样受保护。

`prepare_routes(...)` 先沿用现有排序逻辑，再合并完全相同的路线树并保留原始排名来源，最后基于反应、产物和前体特征选出更有差异的审阅集合。为了维持固定 dashboard 数量而重新补入相似路线时，路线会标记 `diversity_relaxed=True`，避免把它误解为独立证据。

`audit_routes(...)` 在 LLM 注释之前运行。它会报告缺失路线树、空或无效的分子 SMILES、没有前体子节点的反应、重复前体、缺少反应标识，以及在安装 RDKit 时检查简单的“产物相对前体元素缺口”。每份结果都带有明确免责声明，因为这些检查不能替代正向预测、文献先例或实验审阅。

## 可选外部模型

[`external_backends.py`](external_backends.py) 增加了版本化、标准库优先的 subprocess 边界，用于运行可选单步模型。[`syntheseus_worker.py`](syntheseus_worker.py) 可以在独立 Python 或 conda 环境中调用 RetroChimera 或受支持的 Syntheseus wrapper，从而不把 PyTorch、CUDA、checkpoint 和模型专属依赖带入 OpenAI4S core 进程。

[`model_deployment.py`](model_deployment.py) 在不增加包依赖的前提下使 RetroChimera checkpoint 部署可复现：它登记三个公开上游归档，通过受防护的 Host capability 执行可选下载，校验经审阅的字节数、MD5 和本地计算的 SHA-256，拒绝不安全的 ZIP member，通过原子 staging 目录解压，并生成 backend 使用的不含路径 manifest。

校验结果与它真正读到的字节绑定：归档必须是普通文件，对同一个已打开的 inode 连算两遍哈希，其间路径或内容一旦发生变化就拒绝；记下的摘要也写明了范围——`checkpoint_sha256_scope` 的值是 `source_archive`，避免有人把它当成解压后目录树的哈希。随后，发布出去的目录会逐条与归档里取出的内容重新核对，核对通过才交给 backend。

默认禁止自动下载 checkpoint。模型运行可以携带不含本地路径的 manifest，记录模型版本、checkpoint 标识与 SHA-256、训练数据集和许可证信息。返回分数始终保留为原始模型输出，并附带科学免责声明；它们不会被转换成实验成功概率。

安装、manifest、协议、错误处理与 Harness replay 详见 [`MODEL_BACKENDS.md`](MODEL_BACKENDS.md) 和 [`MODEL_BACKENDS_zh.md`](MODEL_BACKENDS_zh.md)。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 从头到尾的 pipeline：目标 SMILES、AiZynthFinder `config.yml` 和工作目录等输入，搜索调用和 JSON 导出加载，以及后续路线排序；随后是目标、中间体、可购前体和未解决末端前体的分子简介，`host.llm` 注释，自包含 HTML dashboard 和 Markdown 报告，以及保证模型生成条件、产率和判断仍为假设的证据边界。 |
| [`kernel.py`](kernel.py) | 底层 sidecar。安装 RDKit 时规范化 SMILES；构造安全的 AiZynth 命令；加载、规范化和排序路线；收集分子与反应证据；按需调用 Host LLM；并渲染 dashboard 和 Markdown 报告。 |
| [`workflow.py`](workflow.py) | 面向用户的编排层：经过校验的 `aizynthcli` 参数，以及“规范化 → 排序 → 去重 → 多样性选择”的审阅流程。 |
| [`route_review.py`](route_review.py) | 稳定路线签名、重复路线来源记录，以及基于反应、产物、前体和末端原料特征的多样性选择。 |
| [`structural_audit.py`](structural_audit.py) | LLM 解释前的确定性路线树检查。它保持标准库优先，仅在安装 RDKit 时增加解析和元素检查。 |
| [`external_backends.py`](external_backends.py) | 版本化外部模型请求/响应校验、不含路径的 model manifest、超时与大小限制，以及 `SyntheseusBackend` subprocess adapter。 |
| [`model_deployment.py`](model_deployment.py) | 纯标准库 RetroChimera checkpoint 注册表、受 Host 防护的下载、绑定 inode 且写明摘要范围的归档校验、把发布目录与归档内容重新核对的安全原子解压，以及不含路径的 manifest 生成。 |
| [`benchmark_common.py`](benchmark_common.py) | 六个独立协议共用的严格 schema、有限数值、确定性哈希、原子 JSON 与中间产物工具。 |
| [`single_step_benchmark.py`](single_step_benchmark.py) | Scenario 1 的严格 class-unknown 公开输入准入、无序前体集合规范化、全 beam 诊断、中间结果生成和私有多参考 evaluator。 |
| [`multistep_benchmark.py`](multistep_benchmark.py) | Scenario 2 的预算核算、严格 molecule-OR/reaction-AND 库存闭合、路线去重与 target-level 评测。 |
| [`atom_mapping_benchmark.py`](atom_mapping_benchmark.py) | Scenario 3 的无映射输入准入、映射完整性检查、对称性等价对应评分与变键评测。 |
| [`forward_benchmark.py`](forward_benchmark.py) | Scenario 4 的反应物/试剂分离准入、完整 Top-K 产物诊断，以及 isomeric/connectivity 私有评测。 |
| [`condition_benchmark.py`](condition_benchmark.py) | Scenario 5 的闭集完整条件元组规范化与多参考 Top-K 评测，禁止边际预测笛卡尔积。 |
| [`yield_benchmark.py`](yield_benchmark.py) | Scenario 6 的原始收率/区间协议，覆盖随机与四个分子骨架切分、排序、校准、macro-OOD 和 worst-group 指标。 |
| [`scenario_benchmark_cli.py`](scenario_benchmark_cli.py) | Scenario 2–6 共用的离线 JSON `normalize`/`evaluate` 入口；规范化会写出带哈希的中间轨迹。 |
| [`reaction_model_deployment.py`](reaction_model_deployment.py) | AiZynthFinder、RXNMapper、ReactionT5v2 和 Parrot 的固定环境计划、上游 revision/发行包哈希、artifact tree 快照及无路径 manifest 生成。 |
| [`reaction_model_backends.py`](reaction_model_backends.py) | 隔离 AiZynthFinder 规划及 mapping/forward/yield/condition 推理的纯标准库 Host adapter，强制 manifest fingerprint 并返回结构化进程错误。 |
| [`reaction_model_worker.py`](reaction_model_worker.py) | 在外部环境运行 AiZynthFinder 路线搜索、RXNMapper、ReactionT5v2 正向/收率和 Parrot CLI 的 worker，不把重依赖引入 core。 |
| [`parrot_mar_inference.py`](parrot_mar_inference.py) | 对已准入并安全展开的 Parrot MAR checkpoint 执行纯推理，保留联合 beam，并拒绝该 USPTO 权重不支持的温度输出。 |
| [`reproducibility_bundle.py`](reproducibility_bundle.py) | 构建确定性的公开 ZIP，并拒绝本地路径、符号链接、超大文件、二进制和 operator 指定的敏感片段。 |
| [`syntheseus_worker.py`](syntheseus_worker.py) | 用于 RetroChimera 和受支持 Syntheseus 模型类别的隔离可选依赖 worker。它在处理请求前把文件描述符 1 重定向到 stderr（使原生模型输出无法破坏协议），剔除模型 metadata 中的文件系统路径，并只输出一个结构化 JSON 响应。 |
| [`MODEL_BACKENDS.md`](MODEL_BACKENDS.md) | 外部模型隔离安装、provenance manifest、使用方式、wire error、科学边界和离线 replay 验证的英文说明。 |
| [`MODEL_BACKENDS_zh.md`](MODEL_BACKENDS_zh.md) | 外部模型后端与可信度说明的中文版本。 |
| [`MODEL_TASKS.md`](MODEL_TASKS.md) | 六个可独立评测的模型任务、入选开源模型、备选项、许可证、部署证据和科学边界的工程审计。 |
| [`MODEL_TASKS_zh.md`](MODEL_TASKS_zh.md) | 模型任务工程审计的中文版。 |
| [`SCENARIO.md`](SCENARIO.md) | 六个模型化科学问题的英文 Scenario 规格；每项分别给出 Science Query、目标、输入、输出、技术、实现、指标和硬性约束，并说明狭义规划的必要子集。 |
| [`SCENARIO_zh.md`](SCENARIO_zh.md) | 按用户给定科学问题格式编写的六问题中文版，不再写成串行 pipeline。 |

这一层的定向回归测试位于 [`../../tests/test_retrosynthesis_scoring_regressions.py`](../../tests/test_retrosynthesis_scoring_regressions.py) 和 [`../../tests/test_harness_contract.py`](../../tests/test_harness_contract.py)。

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`examples/`](examples/) | 确定性的 aspirin 路线与注释 fixture、由它们生成的 HTML 与报告，以及重建两者的脚本。 |
| [`scenarios/`](scenarios/) | 六个独立科学场景的详细 Benchmark 规范，分别包含数据冻结、Ground Truth 隔离、阶段、评测、约束和失败案例。 |
