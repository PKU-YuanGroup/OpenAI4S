# Remote Compute NVIDIA Provider Skill

一份 NVIDIA NIM 的渐进披露 runbook，底下压着一条 Host 能识别的**可信 compute-provider 边界**。[`provider.py`](provider.py) 不是 `kernel.py` 那样的 sidecar，而是由受限的 compute-provider helper 加载的 provider 实现代码；[`provider.json`](provider.json) 声明这个 helper 能拿到的那一小片环境与出网范围。

这些文件在这里，`byoc:nvidia` 才能在兼容的 OpenAI4S 组合里被发现。但这不代表任务真的跑得起来：hosted 模式需要有效的 NVIDIA API key 和网络；self-hosted 模式需要 Docker、装了 Container Toolkit 的 NVIDIA GPU、一个拉得下来的 NIM 镜像，通常还需要 NGC credential。提交任务仍然要过审批，并且会消耗真实资源。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 这份 runbook：hosted 与 self-hosted NIM 怎么选、输入怎么准备和 stage、`host.compute` 的 create → submit → 轮询 `.result()` → harvest 全流程、哪个 key 用在哪里、任务环境长什么样、跑砸了怎么办，以及结果在采信之前怎么验。 |
| [`provider.json`](provider.json) | provider ID `nvidia` 的可信 manifest。它只声明两个 secret 输入，`NGC_API_KEY` 和 `NVIDIA_API_KEY`，此外没有别的；helper 环境是一个裸的 Python 3.11；出网范围钉死在 NVIDIA 的 control、registry 与 blob 主机上；最多 8 个任务并发。 |
| [`provider.py`](provider.py) | 可信的实现。credential 从 helper 的认证通道进来；Docker 会先查一遍，缺 `docker` CLI 时直接报出清晰的错误，而不是操作到一半抛一个光秃秃的 `FileNotFoundError`。创建 handle 就是创建一个打好 label 的容器：self-hosted 形态从 `nvcr.io` 拉一个跑在 GPU 上的 NIM 容器，hosted 形态则起一个精简的 keepalive 容器。endpoint URL 和 hosted key 只在 `docker exec` 时注入，因此任务脚本里永远不必写死自己跑在哪种形态下。归属靠 Docker label 记录，list 与 owner 读取因此能精确到具体安装；terminate 幂等；docker 的 stderr 会被映射成结构化的错误类别；要脱敏的 secret 前缀和 `nvapi-`/`nvcf-` token 形状都声明在类上。 |
