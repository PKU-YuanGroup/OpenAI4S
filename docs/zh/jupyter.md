---
title: 可选 Jupyter 兼容层
description: 独立 Jupyter bridge 的行为及其不会跨越的边界。
outline: deep
status: current
audience: [contributors, operators, users]
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# 可选 Jupyter 兼容层

> 已于 2026-07-14 对仓库 revision `a92e736` 完成核验。

OpenAI4S 可以通过一个**可选的独立 adapter**，把既有 Python/R 科学 worker 暴露给普通 Jupyter client。该 adapter 不属于 daemon，也不会被 stdlib core 导入。

```text
Jupyter frontend
      |
      | Jupyter messaging / ZeroMQ（可选 ipykernel）
      v
OpenAI4S Jupyter bridge
      |
      | 既有 hardened JSON-lines 协议
      v
Python worker 或 R worker
```

Bridge 有意放在 `kernel/manager.py` 之上的单独适配层；它不改变 worker protocol、frame reader、Host-call transaction lock 或 R file-descriptor 纪律。

## 安装与检查

KernelSpec 的 description/export/install 只使用标准库，在尚未安装 Jupyter 时也能运行：

```bash
openai4s jupyter describe
openai4s jupyter describe --json
openai4s jupyter export ./jupyter-kernels
openai4s jupyter install
openai4s jupyter install --prefix "$VIRTUAL_ENV" --replace
```

安装后的名称是 `openai4s-python` 和 `openai4s-r`。若未给出 `--prefix`，`install` 使用 Jupyter 规定的 per-user data directory；`export` 将相同的标准 `kernel.json` 目录写到任意目标。目标已存在时默认 fail closed；只有显式 `--replace` 才会更新，且只更新 `kernel.json`，不会删除整个目录。

真正的 Jupyter wire execution 是可选能力：

```bash
python -m pip install 'ipykernel>=7,<8'
openai4s jupyter install --replace
jupyter kernelspec list
```

生成的 `argv` 内嵌安装 spec 时使用的 Python interpreter，并把 Jupyter 的 `{connection_file}` placeholder 交给 lazy bridge。未安装可选 `ipykernel` 时启动 spec 会给出可操作的错误信息；它不会让 OpenAI4S 的导入或 serve 依赖 Jupyter/ZeroMQ。

R spec 仍要求真实 `Rscript`（`openai4s setup --only r` 或 host R installation）。它是基于 Python 的 Jupyter wire adapter，包装既有 R worker，而不是 IRkernel。

## 已实现的 bridge 表面

- 标准 KernelSpec metadata；安装的 `ipykernel` 报告其实际 Jupyter protocol version，adapter 不硬编码版本；
- 在该 Jupyter kernel 进程生命周期内持久的独立 Python 或 R namespace；
- Cell execute reply、实时/最终 stdout、stderr 与 structured error；
- message-mode interrupt 转发到精确 child worker；
- child 的 graceful shutdown；
- 正常的 OpenAI4S child-environment allowlist 与 OS sandbox adapter。

## 重要边界

该兼容层有意小于 OpenAI4S Web runtime：

- 一个 Jupyter kernel 拥有**独立 namespace**，无法附着到既有 Web/CLI 会话或与之共享变量。
- 它没有 `HostDispatcher`，因此 `host.*` RPC（包括 `host.submit_output`）不可用。科学代码和普通文件 I/O 仍可运行，Host 编排服务不可用。
- Gateway Artifact 捕获、Action Ledger、provenance registration、checkpoint、recovery journal、权限和 Web execution queue 不会投影进这个独立进程。
- Rich display/comm widget、debugger、completion、inspection、history、stdin 和任意 `user_expressions` 尚未实现。可靠表面仅为 Cell execution 与 text/error stream。
- 安装 KernelSpec 不证明可选 wire dependency 或 R interpreter 已可用；请用 `openai4s jupyter describe` 检查，并实际启动所选 kernel 验证本地环境。

需要会话共享、Host RPC、Artifacts、lineage、recovery、权限或 `host.submit_output` completion 语义时，使用内置 Live Notebook。仅需与独立 Jupyter frontend 兼容时，使用可选 bridge。
