# 可选逆合成模型后端

[English](MODEL_BACKENDS.md)

本文说明逆合成规划 Skill 的可选外部模型边界。OpenAI4S 一侧继续保持标准库优先；重型模型包、checkpoint、CUDA 库和模型专属依赖留在独立的 Python 或 conda 环境中，通过一次版本化 JSON 请求和一次 JSON 响应与 OpenAI4S 通信。

首个实现支持 RetroChimera 以及 Syntheseus 暴露的模型 wrapper 做单步逆合成推理。它不替代 AiZynthFinder 的多步规划，也不会把模型分数解释成实验成功概率。

## 适用范围

外部后端主要用于三类场景：

- 生成额外的单步前体候选；
- 比较具有不同归纳偏置的模型是否给出一致建议；
- 在候选进入路线审阅之前记录模型和 checkpoint provenance。

多步 Syntheseus 搜索、正向模型校验、模型共识排序和交互式子树重规划是后续独立功能，不会隐藏在第一版 adapter 里。

## 架构

```text
OpenAI4S retrosynthesis Skill
        |
        | stdin 上的一次版本化 JSON 请求
        v
隔离的 syntheseus_worker.py 进程
        |
        | 可选依赖导入与模型推理
        v
RetroChimera 或 Syntheseus 模型环境
        |
        | stdout 上的一次版本化 JSON 响应
        v
schema 校验、provenance 检查与 Harness replay
```

stdout 只允许输出一个 JSON 对象。worker 在处理请求前会把文件描述符 1 重定向到 stderr，并保留一个私有副本用于写响应，因此直接写 stdout 的原生库（PyTorch、DGL、CUDA、RDKit 都会这样做）不会破坏协议。仅重绑 `sys.stdout` 是不够的，因为这些写入根本不经过它。该副本会在任何 fork 出的子进程中关闭，因此不 exec 直接 fork 的模型不会在自身退出后仍占住 Host 的管道。

这里有三条限制，应当明说而非暗示。当没有可用的 stderr 供 stdout 迁移时，重定向会放弃，worker 随后在未受保护的 stdout 上作答——保护程度不比从前更好，但至少是可见的。重定向发生在 worker 内部，因此无法覆盖解释器到达该处之前写出的字节：继承的 `PYTHONPATH` 上某个 `sitecustomize` 打印的启动横幅仍会破坏响应，`openai4s/kernel/worker.py` 也有同样的限制。另外，由于模型 stdout 现在落到 stderr，而 Host 会把 stderr 引用进 `nonzero_exit` 消息，该消息在抛出前会先做路径清洗。

Host 不使用 `shell=True`，限制请求和响应大小，设置超时，核对响应中的 `request_id`，并拒绝未知响应字段。

## 支持的模型类别

| 类别 | Worker 接受的模型名 | 主要用途 | 依赖说明 |
| --- | --- | --- | --- |
| RetroChimera ensemble | `RetroChimera` | 推荐作为第一个外部 second-opinion 模型 | 安装独立的 `retrochimera` 包及 Syntheseus 接口依赖。 |
| RetroChimera 组件 | `RetroChimeraEdit`, `RetroChimeraDeNovo` | 判断图编辑和序列生成组件是否一致 | 应使用同一 checkpoint family，并在 manifest 中准确记录组件名。 |
| 模板与图模型 | `GLN`, `Graph2Edits`, `LocalRetro`, `MEGAN`, `MHNreact` | 引入结构不同的候选生成机制 | 每个 wrapper 可能需要对应的 Syntheseus 可选依赖组。 |
| 序列与检索模型 | `Chemformer`, `RootAligned`, `RetroKNN` | 引入序列对齐或检索式候选 | 只安装实际使用的依赖组和 checkpoint。 |

Adapter 将 `num_results` 明确限制在 10 以内。低排名预测不会被展示成同等可靠的候选；下游必须保留原始 rank 和 score type，不能把所有模型分数静默转换成同一种概率。

## 可信度与下载策略

这里的“隔离”指的是依赖边界，而非安全边界。该 worker 只是一个普通子进程：它继承调用方的环境，不在任何 OS sandbox 下运行，自身也没有出网管控。它把 PyTorch、CUDA 和模型专属依赖挡在 OpenAI4S core 进程之外，但并不约束模型代码本身。应当把 checkpoint 及其 wrapper 视为你主动选择运行的代码。

默认禁止自动下载 checkpoint。除非显式设置 `allow_model_download=True`，否则在没有 `model_dir` 的情况下调用 `single_step(...)` 会在启动外部进程前直接失败。

更稳妥的生产流程是：

1. 通过经过批准的流程获取 checkpoint；
2. 审查 checkpoint 和训练数据许可证；
3. 计算 SHA-256；
4. 创建不含本地路径的公开 model manifest；
5. 将本地 checkpoint 目录和 manifest 一起传给 adapter。

本地 `model_dir` 只发送给隔离 worker，不会复制进规范化结果、dashboard、Harness tape 或 model manifest，从而避免把工作站路径泄漏到公开 Artifact。

模型返回的 metadata 在离开 worker 前会经过同样的过滤：名为 `*path*` 或 `*directory*` 的 key 会被丢弃；剩余的字符串（无论是值还是 key），只要**以**绝对路径、家目录相对路径、UNC 共享或 `file://` URL 开头，就替换为 `<redacted-path>`。错误消息的清洗更激进，会替换字符串中任意位置的路径——因为 checkpoint 缺失时抛出的异常文本会带上调用方的 `model_dir`。

有两条边界应当明说而非暗示。metadata 的值只在字符串开头匹配，因此 wrapper 自由文本注释里夹在句中的路径不会被遮蔽：不加锚定的匹配无法把 `kcal/mol` 或 `F/C=C/F` 中的键方向斜杠与目录区分开，为了抓一次散文提及而破坏化学数据是更差的取舍。另外，清洗发生在 worker 内部，因此对 worker 启动之前写出的字节无能为力。

## 安装

应创建独立环境，不要把模型包加入 OpenAI4S core 环境。开发该 adapter 时使用的参考版本可以这样安装：

```bash
conda create -n openai4s-retro python=3.11 -y
conda activate openai4s-retro
pip install syntheseus==0.7.2 retrochimera==1.2.0
```

USPTO-50K checkpoint 使用可选的 Graphium 架构。加载该变体前，应以
`retrochimera[graphium]==1.2.0` 取代普通包；Pistachio 与 USPTO-FULL 路径
不需要这个 extra。

其他 Syntheseus wrapper 有各自的模型依赖。应根据选定模型遵循上游安装说明，而不是默认安装所有模型家族。

Adapter 不会把 `syntheseus`、`retrochimera`、PyTorch 或 CUDA 加进 `pyproject.toml`。Worker 会报告运行时安装的包版本；缺少或不兼容的依赖会返回结构化 backend error。

### 可复现的 RetroChimera checkpoint 部署

`model_deployment.py` 登记公开的 Pistachio、USPTO-FULL 和 USPTO-50K RetroChimera 归档，以及上游字节数、MD5、DOI 记录和 MIT 许可证。列出注册表不需要联网：

```bash
python -m skills.retrosynthesis_planning.model_deployment list
```

除非调用方显式授权，否则禁止下载；下载通过 OpenAI4S `host.web_download` 执行，因此每次重定向都会经过出网允许名单和 SSRF 防护。请在 OpenAI4S Python cell 中运行，并把目标放在 session workspace 内：

```python
from pathlib import Path

from retrosynthesis_planning.model_deployment import (
    checkpoint_spec,
    download_checkpoint,
)

workspace = Path.cwd().resolve()
archive = workspace / "models" / "retrochimera" / "retrochimera_uspto50k.zip"
spec = checkpoint_spec("uspto50k")
download_checkpoint(
    spec,
    archive,
    allow_network=True,
    web_download=host.web_download,
)
```

`host` 是 cell 中已经注入的 singleton，并不是可导入的模块。显式传入该
capability 也让 helper 易于测试，并阻止独立脚本悄悄自行联网。

`host.web_download` 会在强制执行字节上限并计算 SHA-256 的同时，把响应流式写入原子临时文件；它不会在 daemon 内存中聚合数 GB 的 checkpoint。操作者也可以改用部署环境批准的流式下载器，然后在解压前运行下面的离线 `verify` 命令。独立模块本身不会直接联网。

较小的 USPTO-50K 归档适合安装冒烟测试，但不能替代覆盖更广的主 checkpoint。上游把 Pistachio 描述为发布的主力且最强 checkpoint。只有通过校验后才安装归档：

```bash
CHECKPOINT_ROOT="$PWD/models/retrochimera"

python -m skills.retrosynthesis_planning.model_deployment verify \
  uspto50k "$CHECKPOINT_ROOT/retrochimera_uspto50k.zip"

python -m skills.retrosynthesis_planning.model_deployment extract \
  uspto50k \
  "$CHECKPOINT_ROOT/retrochimera_uspto50k.zip" \
  "$CHECKPOINT_ROOT/uspto50k" \
  --manifest "$CHECKPOINT_ROOT/uspto50k/model-manifest.json"
```

请从下载 Cell 使用的同一个 session workspace 根目录运行该命令块。因此，
`$PWD/models/retrochimera` 是下载、校验、解压、manifest 创建和推理共用的唯一
可写 checkpoint 根目录。

该命令最多只把经审阅大小的归档复制到私有快照，同时校验字节数和 MD5 并计算 SHA-256，随后只解压这份已验证快照。它拒绝非普通文件及超大源、ZIP 中的绝对路径、路径穿越、反斜杠、Windows 盘符相对路径/备用数据流/设备名和符号链接，并限制 member 数量及展开大小。请求的 manifest 必须位于新模型目录内；它会在私有 staging 中写好，因此 manifest 与解压文件通过一次原子目录发布同时可见。命令会拒绝在解压开始时已经存在的模型目录。调用方必须串行化针对同一目标的解压：初始存在性检查与最终 POSIX 目录 rename 并不是跨进程锁，否则竞态创建的空目录仍可能被替换。生成的 manifest 不含路径，可以直接传给 `SyntheseusBackend`。

当 workspace 文件系统支持硬链接时，下载与独立 manifest 写入会把发布对象绑定到
已验证 inode。对于 exFAT 或部分 SMB 挂载等拒绝硬链接的文件系统，它们仍使用私有
staging、发布前后字节校验和原子 rename，但调用方也必须串行化针对同一目标的写入。

## Model manifest

Model manifest 是公开 provenance，不是环境配置文件。它不能包含本地 checkpoint 路径、凭据、私有数据集位置或内部实验名称。

```json
{
  "schema_version": 1,
  "provider": "Microsoft Research",
  "model": "RetroChimera",
  "model_version": "1.2.0",
  "checkpoint_id": "reviewed-uspto50k-checkpoint",
  "checkpoint_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "training_dataset": "USPTO-50K",
  "code_license": "MIT",
  "checkpoint_license": "MIT",
  "source_url": "https://doi.org/10.6084/m9.figshare.30601718.v1",
  "metadata": {
    "reviewed_by": "replace-with-public-review-role"
  }
}
```

只有在 checkpoint SHA-256 存在、训练数据集已明确、代码与 checkpoint 许可证不是 `unknown`、`unspecified` 或 `review-required`，且 digest 没有被明确限定为仅覆盖源归档时，`provenance_status` 才会是 `complete`。部署 helper 会记录 `checkpoint_sha256_scope: source_archive` 和 `runtime_integrity: unverified`：这个 digest 证明安装的是哪份经审阅 ZIP，并不能证明执行推理时可变的解压目录仍是相同字节，因此其状态保持 `incomplete`。仅在 manifest 中写入 `runtime_integrity: verified` 不能升级该状态；这需要真正的 host 侧目录验证器。系统会基于 canonical JSON 计算 manifest fingerprint，因此即使人类可读 checkpoint ID 不变，manifest 的修改仍然可见。worker 会原样回显 manifest——清洗只作用于模型返回的 metadata，绝不作用于操作者自己的文档，因为一旦过滤，公布的 fingerprint 就无法从被审阅的文件复算出来。`SyntheseusBackend` 会把回传的 fingerprint 与自己发出的 manifest 比对，不一致时抛出 `manifest_mismatch`，因此 worker 无法悄悄替换一份没有人批准的 provenance 记录。

## 使用方法

```python
from pathlib import Path

from retrosynthesis_planning.external_backends import SyntheseusBackend

workspace = Path.cwd().resolve()
model_dir = workspace / "models" / "retrochimera" / "uspto50k"
manifest = model_dir / "model-manifest.json"
cache_dir = workspace / "models" / "syntheseus-cache"
cache_dir.mkdir(parents=True, exist_ok=True)

backend = SyntheseusBackend(
    model="RetroChimera",
    model_dir=model_dir,
    manifest=manifest,
    python_command=(
        "conda",
        "run",
        "--no-capture-output",
        "-n",
        "openai4s-retro",
        "python",
    ),
    timeout_seconds=600,
    env={
        "WANDB_MODE": "offline",
        "SYNTHESEUS_CACHE_DIR": str(cache_dir),
    },
)
```

`env` 只把列出的值加入继承的 worker 环境。它用于模型 cache 和离线模式控制，不用于凭据；秘密仍应进入正常的 credential broker。

`--no-capture-output`是必需的，不是可选项：缺少它时 `conda run` 不会转发 stdin，
worker 读到空请求，于是每次调用都会返回 `invalid_json` 错误响应而不是结果。

```python

capabilities = backend.capabilities()
result = backend.single_step(
    "CC(=O)Oc1ccccc1C(=O)O",
    num_results=5,
)
```

结果会保留：

- 模型名与运行时包版本；
- 按顺序排列的反应物候选和 reaction SMILES；
- 可用时的原始 score 字段及 score type；
- 可表示为 JSON、且已剔除文件系统路径的模型 metadata；
- 公开 model manifest 及其 fingerprint；
- checkpoint provenance 不完整时的 warning；
- 防止把模型分数描述成产率或成功概率的科学免责声明。

## Wire contract

Wire schema 与具体模型包独立版本化。当前 worker 支持 `capabilities` 和 `single_step` 两种操作。

成功的单步响应包含 `target_smiles`、`model`、有序 `predictions`、`model_manifest`、`runtime`、`warnings` 和 `elapsed_seconds`。失败请求包含结构化 `error`，其中有 `code`、`message` 和 `retryable`。

预期错误码包括：

- 禁止自动下载且未提供模型目录时返回 `checkpoint_required`；
- 缺少选定可选包时返回 `dependency_missing`；
- 安装包未导出预期 class 时返回 `dependency_incompatible`；
- 请求超出版本化 contract 时返回 `unsupported_model` 或 `unsupported_operation`；
- 捕获到模型侧失败时返回 `inference_failed`；
- Host 侧可能抛出 `timeout`、`nonzero_exit`、`invalid_json` 和 `response_too_large`。

结构化模型错误属于合法 backend response，可以在 ensemble 中作为一个失败 provider 处理。进程崩溃、stdout 非法或 request ID 不匹配属于协议失败，会在 Host 侧抛出异常。

## Harness 与验证

默认 PR suite 不下载模型权重。`harness/evals/retrosynthesis_backend_cases.json` 保存公开安全的合成响应 tape，`harness/evals/retrosynthesis_backends.py` 会把它们送入真实 worker 结果使用的同一个生产 response normalizer。

Replay 报告包含：

- case accuracy；
- 预期成功状态和 error code 是否一致；
- prediction 数量；
- 成功 case 的 complete provenance 比例；
- 带 score 的 prediction 覆盖率；
- 每个规范化响应的 canonical SHA-256。

运行定向契约：

```bash
uv run pytest tests/test_harness_contract.py
uv run python -m harness.cli run --tier pr --offline
```

未来可以增加显式 opt-in 的 model canary 来加载少量经过审核的 checkpoint，但它必须标记 external/GPU，不能成为默认离线 PR suite 的要求。

## 科学解释边界

RetroChimera 和其他学习式逆合成模型可能产生化学上不合理或分布外的候选。多个模型一致只能说明计算结果具有一定一致性，不能证明反应可行。不同模型家族的高 raw score 也不能自动视为经过统一校准。

在候选升级成可执行路线之前，应结合确定性结构检查、reaction-center 审阅、可用时的 forward 或 round-trip 校验、来源可追溯的反应先例、库存核验、安全审查和独立化学专家决策。

因此 adapter 返回的是候选与 provenance。它不会生成虚构产率，不会隐藏模型分歧，也不会把预测标记成实验验证结果。

## 后续计划

后续兼容层包括：

- 规范化 multi-backend candidate bundle 和 reciprocal-rank consensus；
- forward-model round-trip 与立体化学感知校验；
- 不同路线之间的 weakest-step 和 shared-failure 分析；
- PaRoutes 风格离线路线 benchmark 与 opt-in model canary；
- 展示 model vote、reaction center、evidence grade 和 review action 的交互式 route DAG；
- 将 multi-step Syntheseus search 作为独立能力，并记录 inventory 与 search manifest。

这些改动应继续拆成独立 PR，让外部进程边界与 provenance contract 先接受审阅，再允许模型输出影响路线排序或 workbench UI。
