---
title: 文档治理
description: OpenAI4S 文档的事实优先级、状态词汇、语言政策与评审门槛。
status: current
audience:
  - contributors
  - operators
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# 文档治理

OpenAI4S 文档是一种工程接口。它必须区分当前代码保证的行为、仅仅尝试的行为，以及尚未接入的能力。

## 事实优先级

当资料互相矛盾时，按以下顺序判断：

1. 可执行协议与持久化行为；
2. 聚焦测试与契约测试；
3. 组合代码与公开 schema；
4. 当前 canonical 文档；
5. 历史计划、注释与营销文案。

出现一个 class、route 或 repository，并不足以证明功能端到端可用。产品可用性还要求它已被正确组合，必要时已经接入 UI 或客户端，并存在成功的验证路径。

## 状态词汇

| 标签 | 含义 |
|---|---|
| **Contract** | 调用方可以依赖的稳定不变量；修改时必须评审迁移方案与契约测试。 |
| **Implemented** | 已接入受支持的产品路径，并由相关测试覆盖。 |
| **Best-effort** | 有实际用途，但存在已记录的覆盖缺口、降级或非事务性故障模式。 |
| **Partial** | 某些层或控制已经可用，但端到端能力仍有明确限制。 |
| **Prototype** | 实验性集成，不得作为运维保证对外描述。 |
| **Planned** | 目标行为，不表示当前已经可用。本站不发布内部路线图。 |
| **Historical** | 为决策或迁移记录而保留，不是当前产品事实。 |

## 验证元数据

Canonical 页面包含：

- `status`
- `audience`
- `verified_commit`
- `last_verified`
- `owner`

网站根路径描述 `main`。项目出现稳定 release tag 后，再把稳定发行版文档发布到带版本的路径。

## 语言政策

英文是 canonical source。公开导航中的每个当前页面，都必须在 `/zh/` 下有简体中文对应页。内容变更只有在两条路径都更新后才算完整；否则 Pull Request 必须明确标记并跟踪临时翻译缺口。

历史源记录可以保留原始语言，但其当前状态与相关性必须提供双语摘要。

## 公开与私有内容

公开文档包括架构、限制、故障模式、安全边界和实现成熟度。凭据、特定主机访问资料、私有备份位置、事件联系人、内部机器别名和内部路线图不公开。

## Pull Request 门槛

文档变更必须：

1. 成功构建中英文网站；
2. 保留现有公开路径，或提供有意设计的 redirect/stub；
3. 校验内部链接和导航目标；
4. 确认 Mermaid 图在客户端无错误渲染；
5. 用中英文搜索词分别做冒烟测试；
6. 对可以自动生成的 inventory，避免手工维护动态计数；
7. 当能力在 Prototype、Partial 和 Implemented 之间变化时更新状态表；
8. 通过仓库 secret scan。

历史计划应放在明确标记的 History/ADR 区域，不得使用未经限定的“当前”措辞。
