# Web share viewer

The standalone, read-only viewer served by the relay tunnel's ShareRouter at a
share's public URL. It is deliberately separate from the main single-page app:
no WebSocket, no writes, its own minimal shell. It fetches `/api/meta` and
`/api/view`, renders the conversation, Notebook, and artifacts, and offers a
"run locally" panel. It reuses `../scientific_renderers.js` and the vendored
3Dmol from `../vendor/`, and runs under a strict `script-src 'self'` CSP, so all
logic lives in `share.js` (no inline scripts) and untrusted content is placed via
`textContent`, never `innerHTML`.

| File | Purpose |
|---|---|
| `share.html` | The viewer shell: static markup, the run-locally panel, and the `<script src="/static/share.js">` and stylesheet links. No inline script. |
| `share.js` | Self-contained viewer logic: fetches meta/view, a safe minimal Markdown renderer, cell/artifact rendering, image/CSV preview, and zh/en toggle. |
| `share.css` | Theme-aware styling for the viewer (light/dark), the run-locally panel, cells, and the artifact grid. |
