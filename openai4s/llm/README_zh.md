# Provider 中立的 LLM 层

[English](README.md)

这里的客户端只用标准库，会说四种 wire：OpenAI-compatible Chat Completions、OpenAI Responses、Anthropic Messages 和 Gemini `generateContent`。消息、原生工具调用、流式 delta、usage 计数和错误，都以同一套标准化形状返回。

## 在架构中的位置

[`../agent/`](../agent/) 的外层循环只经由这个包访问模型。它负责组装 provider 的 wire 请求，再把标准化后的 reply 数据交回去。它不挑选下一个动作，不执行工具，不启动内核，也不定义什么算完成；回复拿到之后，这些全部由 `AgentEngine` 路由决定。

能力元数据描述的是 OpenAI4S adapter 目前支持什么，不能当成供应商自家 SDK 全部能力的说明。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`__init__.py`](./__init__.py) | 包的 facade，也是当初从单模块拆成包却没弄坏任何调用方的原因。它对外导出配置、能力、registry、`LLMError` 和 `chat`。`_post_json` 和 `_post_sse` 是有意留在模块全局的：离线测试和其他集成正是替换这两个名字来拦截 wire 的。它们会把一份调用上下文——这次在跟哪个 provider 说话、用户是否已经按下停止——经 `bind_call_context` 往下传，而后者只绑定目标真正接受的关键字参数，所以那些集成注入进来的四参数 transport 依旧照常工作，而不是让一次本该成功的调用抛 `TypeError`。 |
| [`capabilities.py`](./capabilities.py) | 每个 provider 和模型被声明支持什么。provider 基线、部署级 override 和精确到模型的 override 会解析成一条带缓存的记录；`validate_model_request` 会直接拒掉那些模型根本没声明过的能力请求，而不是把它送到 wire 上等着失败。同一条记录还负责把各家的 usage 字段映射成统一的 token 计数，成本也由此估算。override 只存在于当前进程，这个模块不碰任何文件。 |
| [`catalog.py`](./catalog.py) | 模型 profile preset，线程安全，只存在于当前进程。它不关心底层是哪种 wire。 |
| [`client.py`](./client.py) | provider 中立的唯一入口。它把配置和 provider 定义合成出 base URL 与模型名，拦下发往纯文本 provider 的图片内容，然后把请求交给已注册的 wire adapter。如果解析出的模型没有声明工具调用能力，原生工具声明会被直接丢掉而不是发出去——这一轮退回 Code-as-Action 路径，而不是因为 schema 不受支持整轮失败。缺 API key 只对远端端点是致命的；本地端点的「鉴权」本来就是外面连不到它。`supports_vision_for` 回答的是一次调用真正会用到的 provider／端点／模型三元组，因为按 provider 给的答案说的是那个 provider 的*默认*模型：一个钉在纯文本模型上、provider 本身却支持 vision 的会话，会先通过预检，再被下面那道 guard 拒掉——本该优雅退回纯文本，结果整轮失败。回来时再把 usage 标准化。 |
| [`messages.py`](./messages.py) | 会话历史的翻译，每种 wire 一个函数。OpenAI 把 `system` 当普通消息留在序列里；Anthropic、Gemini 和 Responses 则要求把它单独提出来，并把连续的工具结果并成一轮。原生调用、工具结果和 multipart 图片内容都从这里过一遍，原始参数不会丢。 |
| [`models.py`](./models.py) | 定义 `LLMError`：所有 transport 和 provider 抛出的唯一标准化错误；以及它下面的 `TransportError`——一次证据完整的 HTTP 失败：状态码、provider 的 error code、header、request id、`Retry-After`，还有那个不论状态码看起来多可重试都能一票否决重试的 `output_committed`。以前每个失败都被压成一句 f-string，于是不去解析英文就分不出 429 和 401，什么都重试不了。它继承 `LLMError`，因此已有的 `except LLMError` 不用改，想要细节的调用方自己取。`llm_failure_code` 只把精确、封闭的结构化供应商信号映射为本地公开错误码；它既不解析供应商文案，也不把供应商原始 code 对外发布。`status_is_retryable` 与 `parse_retry_after`（秒数或 HTTP-date，绝不返回负值）也一并放在这里。 |
| [`registry.py`](./registry.py) | 当前进程里有哪些 provider，以及每个 provider 是什么：wire、base URL、API key 的环境变量名、默认模型、能力绑定。注册要过校验（`base_url` 必须是绝对的 http(s) 地址，且不能把凭据写在里面），内置 provider 既不能被替换，也不能被删除。 |
| [`tooling.py`](./tooling.py) | 原生工具的契约集中在这里，好让任何 wire adapter 都不必去 import 工具 registry。声明先被规范成统一的 name/description/schema 形式，再渲染成各 wire 的工具 schema 和 tool choice。回传的调用会被标准化成共用形状；参数解不出来时，会以 `parse_error` 挂在这次调用上，而不是被丢掉。 |
| [`transport.py`](./transport.py) | 包里唯一开 socket 的地方：用 `urllib` 做 JSON POST 和 SSE 解码，不依赖任何 provider SDK。HTTP 错误和连接错误都以 `TransportError` 抛出——正是这一点才让重试成为可能；即使结构化错误藏在 HTTP 200 的 SSE 事件里，也会保留同一条类型化路径。重试策略刻意收得很窄：只有什么都没提交出去的请求才可以重放（整份响应的 POST 可以，已经把事件交给调用方的流不行），并且只共享一份有上限的尝试预算；服务器给了 `Retry-After` 就压过算出来的延时；取消是在等待*过程中*轮询的，而不是只在等待前后各看一眼；再加一个总预算，免得一个五分钟的 `Retry-After` 把一整轮悄无声息地挂在那里。突发保护错误会使用带正值下限、较慢的指数 jitter；流式重试耗尽后不会再开启一份新的非流式重试预算。流里非空却不是合法 JSON 的事件会直接抛错，而不是跳过——因为被丢掉的那个事件可能正是一次工具调用；错误文本里的原始片段会被截断。 |
| [`resolve.py`](./resolve.py) | 「用哪个模型、哪个 key、哪个端点」的唯一答案，请求路径与 `openai4s doctor` 用的是同一份。进程配置先叠上 store 里 Customize → Models 的设置，再叠上会话自己钉住的模型，因此诊断不会把一个真实 turn 能正常解析的配置报成坏的——daemon 是刻意不带 key 启动的。一旦 provider 被覆盖，上一个 provider 那份具体的 base URL、模型和 key 会被清掉而不是继承下来，否则请求就会带着错的凭据发到错的端点。`is_loopback_endpoint` 也在这里：Ollama、LM Studio、vLLM 和 llama.cpp 都在 loopback 上说 OpenAI-compatible wire，它们的「鉴权」就是外面根本连不到，所以要求它们给出 key 等于把一套能用的安装报成故障——而且只认字面上的 loopback 地址，因为一个今天恰好解析到 127.0.0.1 的主机名，并不构成稳定的授权依据。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`providers/`](./providers/) | wire adapter 本体：OpenAI-compatible Chat、Responses、Anthropic Messages 和 Gemini `generateContent`。 |

## 供应商扩展契约

- provider 定义与 wire adapter 分开注册；协议兼容时复用现成的 wire，不要再写一个。
- 每个原生调用在回到 Engine 之前，都要标准化成共用的 ID/name/raw-arguments/parsed-arguments/error 形状。
- secret 只留在 Host 配置和请求 header 里。provider key 绝不能进入科学 worker 的环境。
- adapter 一旦开始声明新的输入、工具、vision、streaming 或 usage 行为，就在同一次改动里更新能力校验和离线 mock 测试。
