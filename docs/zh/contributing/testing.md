---
title: 测试与验证
description: OpenAI4S 变更所需的测试层次和运行时检查。
status: current
audience:
  - contributors
  - operators
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# 测试与验证

默认测试套件是离线门槛：LLM 调用被 mock，用户数据被重定向到临时目录，live network、GPU、SSH、Docker、lab 和 browser marker 默认不运行。

## 本地门槛

```bash
uv run pytest
uv run pre-commit run --all-files
npm run docs:build
```

开发时运行聚焦测试，交付前再运行完整离线套件。

| 变更 | 最小聚焦门槛 |
|---|---|
| Agent 路由或 completion | `tests/test_agent_engine.py`、`tests/test_actions.py`、`tests/test_structured_finalize.py` |
| Kernel/Host 协议 | `tests/test_kernel.py`、相关 R/supervisor/sandbox 测试 |
| 权限/安全 | permission、Host contract、egress、sandbox 与 security 测试集 |
| Store/repository | 所属 repository 测试加 Store 兼容测试 |
| Gateway/会话行为 | Gateway、session service、coordinator 和 Web static-contract 测试 |
| Artifacts/recovery | artifact manager/repository、checkpoint、recovery 与 branch 测试 |
| Skills | discovery、versions、product surface 与对应 Skill 测试 |
| 打包 | release gates 与 artifact verification |
| 文档 | VitePress build、内部链接、locale parity、Mermaid 和搜索冒烟测试 |

## 超出单元测试的运行时验证

测试只是 kernel、WebSocket、artifact 和浏览器行为的最低门槛。改动这些表面时：

1. 用 `./start.sh` 启动真实工作台；
2. 尽可能运行无模型或 mock 场景；
3. 执行一个 Python Cell，相关时再执行一个 R Cell；
4. 检查 Notebook 事件、Artifact 捕获、取消和重连状态；
5. 对用户可见变更运行浏览器冒烟测试。

Jupyter adapter、远程计算、live LLM provider、SSH host、GPU 和真实浏览器都属于显式启用的环境。不得把它们加入默认离线门槛。

## 协议敏感型评审清单

- Kernel frame reader 是否仍由唯一组件持有？
- 每个原生工具声明是否仍得到一个规范结果？
- 取消是否只能命中精确 execution owner 和 kernel lease？
- 失败是否保留真实的 Action Ledger 和 execution attempt？
- 文件系统、SQLite、kernel 和 WebSocket 的提交点是否被分别描述，而非伪装成同一事务？
- Worker environment、日志、fixture 和生成文档中是否都没有 secret？
- 变更是否保持 Python core 零硬依赖？

Wheel 与 source archive 检查见[发布验证](../release-validation.md)。
