---
layout: home
title: OpenAI4S 文档
description: 面向 OpenAI4S 科学研究工作台的架构、贡献者与运维文档。
status: current
audience:
  - contributors
  - operators
  - users
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
hero:
  name: OpenAI4S
  text: 架构与运维
  tagline: 一份经代码验证的指南，涵盖混合控制平面、持久科学运行时，以及保障系统可运维性的边界。
  actions:
    - theme: brand
      text: 阅读架构文档
      link: /zh/architecture
    - theme: alt
      text: 部署与运维
      link: /zh/operations/
    - theme: alt
      text: 贡献代码
      link: /zh/contributing/codebase-map
features:
  - title: 架构契约
    details: 明确区分稳定的路由、完成、内核协议、所有权和持久化规则，以及尽力而为的实现细节。
  - title: 贡献者导航
    details: 在修改系统之前，先定位负责模块、扩展接缝、测试和兼容性边界。
  - title: 运维优先
    details: 将部署、数据布局、备份、安全态势、故障模式和恢复作为一等文档内容。
  - title: 如实标注状态
    details: 对已实现、部分实现、原型、计划中和历史能力进行明确标注。
---

## 本站记录什么

OpenAI4S 是一个本地优先、面向单用户的科学研究工作台。它以供应商原生 JSON 工具作为编排与权限控制平面，以持久的 Python/R Cell 作为科学执行平面。Python Cell 在运行过程中可以同步调用受审计的 Host 服务。

本站同等服务于两类受众：

- 贡献者：需要在不破坏协议、持久化、安全或兼容性契约的前提下修改引擎；以及
- 运维人员：需要安装、加固、备份、升级、诊断并恢复真实部署。

文档也包含产品概念和用户指南，但它们不能替代实现状态与故障边界文档。

::: warning 部署边界
OpenAI4S Workbench 不是面向公网的多租户服务。请让守护进程仅监听回环地址或可信私有网络。`openai4s.org/docs/` 上的文档站点是一个独立的静态部署。
:::

## 如何理解状态标签

<span class="status contract">契约（Contract）</span> 表示其他组件可以依赖的行为。<span class="status implemented">已实现（Implemented）</span> 表示目前已经接线并通过测试。<span class="status best-effort">尽力而为（Best-effort）</span> 表示实现可能不完整，或能够安全降级。<span class="status prototype">原型（Prototype）</span> 不构成运维保证。

完整的事实优先级与审阅规则参见[文档政策](./reference/documentation-policy.md)。
