# Harness Provider

[English](README.md)

场景运行时用到的假 provider 都放在这里。每个假 provider 顶替一处外部平台边界：只按场景声明好的内容作答，绝不连出去访问真实服务，并把收到的调用记录下来供事后检查。正因为如此，场景断言的才是编排行为本身，而不是某个服务当时通不通。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](__init__.py) | 导出 scripted LLM 与它的结构化错误类型。 |
| [`scripted_llm.py`](scripted_llm.py) | 以一队脚本化 step 为底的模型 callable。它按顺序返回声明好的规范化响应（顺手补齐 `reasoning`、`usage`、`finish_reason`、`raw` 默认值），在脚本声明了错误的地方抛出 `ScriptedProviderError`，并报告还剩多少 step；每次传入的消息列表都会被深拷贝进 `calls`，场景事后可以照原样检查 prompt。脚本用完还继续调用会抛 `AssertionError`，而不是把最后一条回复重放一遍。 |
| [`.gitkeep`](.gitkeep) | 把目录留在 git 里，下一个假 provider 才有地方落。 |

provider 回放的脚本，就是由 [`../schema.py`](../schema.py) 校验的 `provider_script` 字段，驱动它的是 [`../runner.py`](../runner.py)。将来的 compute、endpoint 或 lab fake 也可以放进来，前提是默认离线。
