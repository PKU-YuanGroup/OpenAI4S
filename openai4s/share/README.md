# share/

The web-share transport layer. The daemon publishes a session as an immutable,
read-only snapshot (built in [`../server/share_projection.py`](../server/share_projection.py))
and dials **out** over WSS to a relay you run; visitors reach it at
`https://<share-id>.<domain>/`. Everything here is pure standard library, and
none of it ever touches the kernel, dispatcher, or a writable gateway route — the
relay forwards visitor requests to the read-only ShareRouter and nothing else.

| File | Purpose |
|---|---|
| `__init__.py` | Names the package. |
| `protocol.py` | The daemon⇄relay wire protocol: JSON control frames (whitelisted types, size-bounded) and binary data frames (6-byte header, chunked), plus the request-header allowlist. |
| `ws_client.py` | A minimal stdlib WebSocket client used by the outbound tunnel: TLS-verified `wss://` (TLS 1.2 floor, no downgrade), a 101-only handshake, client-masked frames. |
| `tunnel.py` | The daemon-side `TunnelClient`: one reconnecting WSS connection, desired-state share (re)registration, credit-based flow control, and dispatch of relay-forwarded requests to the injected read-only handler. |
| `relay.py` | The stateless public relay (`openai4s relay serve`): a stdlib HTTP/WebSocket server that authenticates publishers by token fingerprint (takeover / conflict / compare-and-delete), routes visitor requests by host label, and enforces a GET/HEAD-only, allowlisted, uniform-404 boundary. |
| `fetch.py` | SSRF-hardened bundle download for `openai4s share import <url>`: HTTPS-only off loopback, no URL credentials, per-hop redirect re-validation, private-address rejection, and a streamed size cap. |
