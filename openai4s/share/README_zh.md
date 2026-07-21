# share/

Web 分享的传输层。daemon 把一个会话发布成不可变的只读快照（在
[`../server/share_projection.py`](../server/share_projection.py) 构建），并通过 WSS 主动**出站**
连到你自建的 relay；访客经 `https://<share-id>.<domain>/` 访问。这里全部是纯标准库，且没有任何
代码触碰内核、dispatcher 或可写的 gateway 路由——relay 只把访客请求转给只读的 ShareRouter，仅此而已。

| 文件 | 作用 |
|---|---|
| `__init__.py` | 命名该包。 |
| `protocol.py` | daemon⇄relay 线协议：JSON 控制帧（类型白名单、限长）与二进制数据帧（6 字节头、分块），以及请求头允许名单。 |
| `ws_client.py` | 出站隧道用的极简标准库 WebSocket 客户端：经 TLS 校验的 `wss://`（TLS 1.2 下限、不降级）、仅接受 101 的握手、客户端掩码帧。 |
| `tunnel.py` | daemon 侧的 `TunnelClient`：一条会重连的 WSS 连接、按期望状态（重新）注册分享、基于 credit 的流控，并把 relay 转发来的请求派发给注入的只读处理器。 |
| `relay.py` | 无状态公网 relay（`openai4s relay serve`）：一个标准库 HTTP/WebSocket 服务，按 token 指纹认证发布者（takeover / conflict / compare-and-delete），按 host 标签路由访客请求，并强制仅 GET/HEAD、白名单、统一 404 的边界。 |
| `fetch.py` | 供 `openai4s share import <url>` 用的 SSRF 加固下载：非 loopback 仅 HTTPS、禁 URL credentials、逐跳重校验重定向、拒私网地址，以及流式大小上限。 |
