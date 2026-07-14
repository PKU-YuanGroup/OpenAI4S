# Using Model Endpoint Skill（规划中 / 尚未接线）

本目录描述 planned endpoint-scoped inference kernel，并包含可信 provider shim/manifest。**当前 OpenAI4S 组合并未把本目录接到可执行 provider 路径：**`ComputeManager` 只发现 `remote-compute-*` provider 并支持 BYOC/SSH family；`host.endpoints.*` 当前只注册/探测 endpoint，不创建这里描述的 scoped inference kernel。

因此，[`SKILL.md`](SKILL.md) 是渐进 runbook/设计材料，provider 文件是 dormant 实现资产，不能证明 `compute_provider({'provider': ...})` 目前可用。在 discovery、lifecycle 与 routing 接通并测试前，不得把它表述为可用端到端能力。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 规划 recipe：从预加载 `BASE_URL`、endpoint-scoped proxy egress、可选 `INFER_API_KEY` 且无 submit/harvest 生命周期的内核调用注册 endpoint 原生 HTTP API。 |
| [`provider.json`](provider.json) | ID `infer` 的可信 provider manifest 设计：声明带 `httpx==0.28.1` 的 Python 3.11/pip helper 与 placeholder control egress target；当前不被 `ComputeManager` 发现。 |
| [`provider.py`](provider.py) | 可信但当前未接线的 `InferProvider`：清除 inference/NVIDIA-shaped secret；经 auth channel 接收 API key 并导出 canonical/安全校验 alias；要求 Cell 直接使用 HTTP；拒绝所有 create/exec/list/owner/terminate job-lifecycle operation。 |
| [`requirements.lock`](requirements.lock) | `httpx` 的 hash-pinned transitive helper dependency（`anyio`、`certifi`、`h11`、`httpcore`、`idna` 与 conditional `typing-extensions`）；未来 provider 接线并构建 helper 前不会自行安装。 |

## 直属子目录

无。
