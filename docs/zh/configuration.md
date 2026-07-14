---
title: 配置
description: 模型、内核、网络、数据目录与 CLI 配置。
outline: deep
status: current
audience: [operators, contributors, users]
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# 配置

> 已依据仓库修订版 `a92e736` 于 2026-07-14 验证。Provider 的默认值只是便捷配置，并不保证服务可用。

配置从环境变量或仓库根目录下被 Git 忽略的 `.env` 中读取。非秘密设置通常有可用默认值；模型 API key 有意不提供默认值。Daemon 可以在没有 key 时启动，但在所选 provider 完成配置前，live provider call 会失败。**通常无需修改文件**——请在 UI 的 **Customize → Models** 中设置模型。若要通过环境变量配置，请将 `.env.example` 复制为 `.env`。

## 模型 Provider

一个 `OPENAI4S_LLM_PROVIDER` 用于选择 wire adapter；每种适配器都自带默认 `base_url` 和 `model`，因此通常只需设置密钥。统一规范化的 `host.llm` 背后支持四种 wire 格式：OpenAI 兼容的 `/chat/completions`、OpenAI `/responses`、Anthropic `/v1/messages` 和 Gemini `generateContent`。

| provider | wire | 默认模型 | 视觉能力 |
|---|---|---|:---:|
| `ark` | openai | `doubao-seed-2.0-pro`（另有 10 个通过 plan/v3 提供） | ✅ |
| `chatgpt` | openai | `gpt-5` | ✅ |
| `openai_responses` | responses | `gpt-5` | — |
| `claude` | anthropic | `claude-sonnet-4-5` | ✅ |
| `gemini` | gemini | `gemini-2.5-flash` | ✅ |

`ark` 是火山引擎的 plan/v3 网关——通过一个端点和一枚密钥提供 `doubao-seed-2.0-{pro,code,lite,mini}`、`glm-5.2`、`kimi-k2.7-code`、`kimi-k2.6`、`deepseek-v4-{pro,flash}`、`minimax-{m3,m2.7}`；这些模型都已预注册为可切换的模型配置。即使没有密钥，守护进程也会启动；在你完成设置前，UI 会显示 *“configure your API key”* 横幅。

`base_url` 和 `model` 按**显式值 → 特定 provider 的 OpenAI4S 变量 → 通用 OpenAI4S 变量 → provider 内置默认值**解析。`api_key` 依次检查显式值、特定 provider 的 OpenAI4S 变量和通用 OpenAI4S 变量，再尝试 provider 约定的 native 变量（例如 `ANTHROPIC_API_KEY`）；凭据没有默认值。`openai_responses` provider 使用无状态 Responses API wire，并在多轮对话间保留函数调用与 reasoning output items；当前适配器仅支持文本和工具。

## 内核环境（conda）

Gateway 当前在第一次运行 `serve` 时尝试在后台安装基础科学计算栈；这是应用启动行为，并非 core runtime 依赖。运维人员应预先构建并验证环境，不要依赖这个需要联网且 best-effort 的启动路径。对于更重的工具链，四份 conda 规格允许 agent 按任务选择环境——用 `openai4s setup` 创建（`--dry-run` 预览，`--only <name>` 只创建一个）。规格文件位于 [`envs/`](https://github.com/PKU-YuanGroup/OpenAI4S/tree/main/envs)：

- **`python`**（默认）——scanpy / anndata / leiden / UMAP / scikit-learn / RDKit / fair-esm / pandas / matplotlib。
- **`struct`**——torch + fair-esm + biotite。
- **`phylo`**——MAFFT / IQ-TREE / FastTree / trimAl / BioPython / ete3。
- **`r`**——tidyverse。

## 端口与数据

`OPENAI4S_HOST`（`127.0.0.1`）· `OPENAI4S_PORT`（`8760`）· `OPENAI4S_DATA_DIR`（`~/.openai4s`，包含 SQLite 数据库、Artifact、日志与 pidfile）。有关远程访问和 SSH 隧道，请参阅[安全架构](security.md)。

`OPENAI4S_SEED_DEMO`（`1`）——设为 `0` 可跳过首次启动时的 UniProt/RCSB 在线演示。它适用于 CI、隔离网络部署或刻意保持空白的工作台，不影响已有会话。

`OPENAI4S_NOTEBOOK_REPL`（`off`）——设为 `1` 可重新启用 Web UI 中 Notebook 内的开发者 REPL（在右侧面板运行任意内核代码）；默认关闭，因此 Notebook 是只读执行轨迹（参见[安全架构](security.md)）。

## 可选 Jupyter 适配器

守护进程与 KernelSpec 工具仍保持零依赖。仅当外部 Jupyter 客户端需要启动独立 OpenAI4S Python/R worker 时，才安装可选 wire stack：

```bash
python -m pip install 'ipykernel>=7,<8'
openai4s jupyter describe
openai4s jupyter install
```

`openai4s jupyter export <directory>` 只写出规格而不安装；`install --prefix <prefix>` 的目标为 `<prefix>/share/jupyter/kernels`。有关独立命名空间和 Host RPC 限制，请参阅[可选 Jupyter 兼容性](jupyter.md)。

## CLI

```bash
openai4s serve     # daemon + web UI (foreground)
openai4s status    # is it up?
openai4s stop      # stop the daemon
openai4s run "…"   # one Code-as-Action task in-process, no daemon
openai4s setup     # build the four conda kernel environments
openai4s jupyter describe               # inspect optional bridge availability
openai4s jupyter export ./kernel-specs  # pure-stdlib KernelSpec export
openai4s jupyter install                # install user KernelSpecs
```
