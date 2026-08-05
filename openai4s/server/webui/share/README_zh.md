# Web 分享查看器

[English](README.md)

由 relay 隧道的 ShareRouter 在分享公网 URL 上提供的独立只读查看器。它刻意与主单页应用
分开：没有 WebSocket、不做写操作、有自己的极简外壳。它拉取 `/api/meta` 与 `/api/view`，
就用这一份 payload 渲染对话、Notebook 与产物，产物字节从 `/api/artifacts/<sha256>` 取，
并提供一个指向 `/bundle` 的「在本地运行」面板。它运行在严格的 `script-src 'self'` CSP 下，
所以全部逻辑都在 `share.js`（无内联脚本），不可信内容一律用 `textContent` 放置，
绝不用 `innerHTML`。

它是自成一体的，不是主客户端的裁剪版。这里没有任何代码加载 `../scientific_renderers.js`
或 `../vendor/` 里自带的 3Dmol；Markdown、图片与 CSV 预览都是它自带的，小而刻意平淡。
ShareRouter 的静态白名单确实放行了那两个文件，好让更完整的查看器无需改服务端就能取用——
但那份白名单说的是路由器**可以**提供什么，而不是本页面会请求什么。

| 文件 | 作用 |
|---|---|
| `share.html` | 查看器外壳：静态标记、在本地运行面板，以及 `<script src="/static/share.js">` 和样式表链接。无内联脚本。 |
| `share.js` | 自包含的查看器逻辑：拉取 meta/view、一个安全的极简 Markdown 渲染器、单元/产物渲染、图片/CSV 预览，以及中英切换。 |
| `share.css` | 查看器的主题感知样式（通过 `prefers-color-scheme` 支持浅色/深色）、在本地运行面板、单元与产物网格。 |
