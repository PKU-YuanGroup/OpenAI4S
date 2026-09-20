# `openai4s/judgment/`

[English](README.md)

厂商中立的**实验性语义判断层**。它把现在散落在关键词规则和「让 LLM 返回 JSON
判决」里的离散判断，变成有类型、带概率、可记录、可评测、可回放的步骤。第一个
接入的厂商是 TypeSafe Jev。整层**默认关闭**；W0 只定义类型、后端端口、开关和
披露文本。这里还没有 HTTP 客户端、dispatcher 挂钩、工具或网关路由。

## 在架构中的位置

Host 侧的调用方（后续的 `JudgmentService`）面对的是 `JudgmentBackend`。
W0 提供的 `NullBackend` 永远抛 `BackendError("disabled")`。
生效开关按 env → Store → 默认关闭解析；走 UI 路径时还要求当前版本的
数据披露确认。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 对外导出：题型/答案类型、后端端口、开关解析、披露辅助函数。 |
| [`types.py`](types.py) | `Noul` / `Choice` / `Score`、`Answer`、`JudgmentResult`、`BackendReply` 的 frozen dataclass，以及 `to_api()` / `to_dict()`。 |
| [`port.py`](port.py) | `JudgmentBackend` Protocol、`BackendError` 错误码，以及 `NullBackend`。 |
| [`flags.py`](flags.py) | `resolve(cfg, store)` 以及 `JUDGMENT_FLAG_PRECEDENCE` 里的六条优先级。Store 访问只读。 |
| [`disclosure.py`](disclosure.py) | `DISCLOSURE_VERSION`、按能力列出的披露文案、固定事实、`is_acknowledged`。 |
| [`settings.py`](settings.py) | 网关和 doctor 用的设置面：`status` / `update` / `probe_connection`，TypeSafe key 走 SecretBroker（不写入 env、不回显），以及「`api.typesafe.ai` 是否已授权」的 egress **报告**。不新增 egress 分组。 |
