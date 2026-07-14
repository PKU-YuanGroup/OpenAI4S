# Remote Compute NVIDIA Provider Skill

本目录组合渐进披露 NVIDIA NIM runbook 与 Host 可识别的**可信 compute-provider 边界**。不同于 `kernel.py` sidecar，[`provider.py`](provider.py) 是由受限 compute-provider helper 加载的 provider 实现代码；[`provider.json`](provider.json) 声明窄环境/egress surface。

文件存在会让兼容 OpenAI4S 组合能够发现 `byoc:nvidia`，但不能证明实际可运行。Hosted mode 需要有效 NVIDIA API key 与网络；self-hosted mode 需要 Docker、NVIDIA GPU/Container Toolkit、可访问 NIM image，通常还需 NGC credential。Job submit 仍经 permission gate，并会消耗真实资源。

## 直属文件

| 文件 | 职责 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | 选择 hosted/self-hosted NIM、准备/stage 输入、`host.compute` create/submit/notification/harvest、auth/environment 规则、recovery 与结果验证的渐进 runbook。 |
| [`provider.json`](provider.json) | 可信 provider manifest：注册 ID `nvidia`；只声明 `NGC_API_KEY`/`NVIDIA_API_KEY` 为 secret input；指定 Python 3.11 helper、NVIDIA control/registry/blob egress 与最大并发 8。 |
| [`provider.py`](provider.py) | 可信 provider 实现：通过 helper auth channel 接收 credential；检查 Docker；创建带 label 的 hosted keepalive 或 self-hosted GPU NIM container；执行时才注入 endpoint/key；适配 `docker exec`；列出/读取精确 installation ownership；幂等 terminate；映射 error；声明 secret/token scrubbing。 |

## 直属子目录

无。
