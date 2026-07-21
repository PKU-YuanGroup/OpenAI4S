# Web 分享查看器

由 relay 隧道的 ShareRouter 在分享公网 URL 上提供的独立只读查看器。它刻意与主单页应用
分开：没有 WebSocket、不做写操作、有自己的极简外壳。它拉取 `/api/meta` 与 `/api/view`，渲染
对话、Notebook 与产物，并提供一个「在本地运行」面板。它复用 `../scientific_renderers.js` 和
`../vendor/` 里自带的 3Dmol，并运行在严格的 `script-src 'self'` CSP 下，所以全部逻辑都在
`share.js`（无内联脚本），不可信内容一律用 `textContent` 放置，绝不用 `innerHTML`。

| 文件 | 作用 |
|---|---|
| `share.html` | 查看器外壳：静态标记、在本地运行面板，以及 `<script src="/static/share.js">` 和样式表链接。无内联脚本。 |
| `share.js` | 自包含的查看器逻辑：拉取 meta/view、一个安全的极简 Markdown 渲染器、单元/产物渲染、图片/CSV 预览，以及中英切换。 |
| `share.css` | 查看器的主题感知样式（浅色/深色）、在本地运行面板、单元与产物网格。 |
