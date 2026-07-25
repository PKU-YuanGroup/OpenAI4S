# Provider 中立的 LLM 层

[English](README.md)

这里的客户端只用标准库，会说四种 wire：OpenAI-compatible Chat Completions、OpenAI Responses、Anthropic Messages 和 Gemini `generateContent`。消息、原生工具调用、流式 delta、usage 计数和错误，都以同一套标准化形状返回。

## 在架构中的位置

[`../agent/`](../agent/) 的外层循环只经由这个包访问模型。它负责组装 provider 的 wire 请求，再把标准化后的 reply 数据交回去。它不挑选下一个动作，不执行工具，不启动内核，也不定义什么算完成；回复拿到之后，这些全部由 `AgentEngine` 路由决定。

能力元数据描述的是 OpenAI4S adapter 目前支持什么，不能当成供应商自家 SDK 全部能力的说明。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 包的 facade，也是当初从单模块拆成包却没弄坏任何调用方的原因。它对外导出配置、能力、registry、`LLMError` 和 `chat`。`_post_json` 和 `_post_sse` 是有意留在模块全局的：离线测试和其他集成正是替换这两个名字来拦截 wire 的。 |
| [`capabilities.py`](./capabilities.py) | 每个 provider 和模型被声明支持什么。provider 基线、部署级 override 和精确到模型的 override 会解析成一条带缓存的记录；`validate_model_request` 会直接拒掉那些模型根本没声明过的能力请求，而不是把它送到 wire 上等着失败。同一条记录还负责把各家的 usage 字段映射成统一的 token 计数，成本也由此估算。override 只存在于当前进程，这个模块不碰任何文件。 |
| [`catalog.py`](./catalog.py) | 模型 profile preset，线程安全，只存在于当前进程。它不关心底层是哪种 wire。 |
| [`client.py`](./client.py) | provider 中立的唯一入口。它把配置和 provider 定义合成出 base URL 与模型名，拦下发往纯文本 provider 的图片内容，然后把请求交给已注册的 wire adapter。如果解析出的模型没有声明工具调用能力，原生工具声明会被直接丢掉而不是发出去——这一轮退回 Code-as-Action 路径，而不是因为 schema 不受支持整轮失败。回来时再把 usage 标准化。 |
| [`messages.py`](./messages.py) | 会话历史的翻译，每种 wire 一个函数。OpenAI 把 `system` 当普通消息留在序列里；Anthropic、Gemini 和 Responses 则要求把它单独提出来，并把连续的工具结果并成一轮。原生调用、工具结果和 multipart 图片内容都从这里过一遍，原始参数不会丢。 |
| [`models.py`](./models.py) | 定义 `LLMError`：所有 transport 和 provider 抛出的唯一标准化错误。 |
| [`registry.py`](./registry.py) | 当前进程里有哪些 provider，以及每个 provider 是什么：wire、base URL、API key 的环境变量名、默认模型、能力绑定。注册要过校验（`base_url` 必须是绝对的 http(s) 地址，且不能把凭据写在里面），内置 provider 既不能被替换，也不能被删除。 |
| [`tooling.py`](./tooling.py) | 原生工具的契约集中在这里，好让任何 wire adapter 都不必去 import 工具 registry。声明先被规范成统一的 name/description/schema 形式，再渲染成各 wire 的工具 schema 和 tool choice。回传的调用会被标准化成共用形状；参数解不出来时，会以 `parse_error` 挂在这次调用上，而不是被丢掉。 |
| [`transport.py`](./transport.py) | 包里唯一开 socket 的地方：用 `urllib` 做 JSON POST 和 SSE 解码，不依赖任何 provider SDK。HTTP 错误和连接错误都以 `LLMError` 抛出。流里非空却不是合法 JSON 的事件会直接抛错，而不是跳过——因为被丢掉的那个事件可能正是一次工具调用；错误文本里的原始片段会被截断。 |
| [`resolve.py`](./resolve.py) | 「用哪个模型、哪个 key、哪个端点」的唯一答案，请求路径与 `openai4s doctor` 用的是同一份。进程配置与 store 里 Customize → Models 的设置分层叠加，因此诊断不会把一个真实 turn 能正常解析的配置报成坏的——daemon 是刻意不带 key 启动的。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`providers/`](./providers/) | wire adapter 本体：OpenAI-compatible Chat、Responses、Anthropic Messages 和 Gemini `generateContent`。 |

## 供应商扩展契约

- provider 定义与 wire adapter 分开注册；协议兼容时复用现成的 wire，不要再写一个。
- 每个原生调用在回到 Engine 之前，都要标准化成共用的 ID/name/raw-arguments/parsed-arguments/error 形状。
- secret 只留在 Host 配置和请求 header 里。provider key 绝不能进入科学 worker 的环境。
- adapter 一旦开始声明新的输入、工具、vision、streaming 或 usage 行为，就在同一次改动里更新能力校验和离线 mock 测试。
