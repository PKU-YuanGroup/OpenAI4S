# Web share viewer

[中文说明](README_zh.md)

The standalone, read-only viewer served by the relay tunnel's ShareRouter at a
share's public URL. It is deliberately separate from the main single-page app:
no WebSocket, no writes, its own minimal shell. It fetches `/api/meta` and
`/api/view`, renders the conversation, Notebook and artifacts out of that one
payload, pulls artifact bytes from `/api/artifacts/<sha256>`, and offers a
"run locally" panel pointing at `/bundle`. It runs under a strict
`script-src 'self'` CSP, so all logic lives in `share.js` (no inline scripts)
and untrusted content is placed via `textContent`, never `innerHTML`.

It is self-contained rather than a reduced build of the main client. Nothing
here loads `../scientific_renderers.js` or the vendored 3Dmol from `../vendor/`;
the Markdown, image and CSV previews are its own, small and deliberately dull.
The ShareRouter's static allowlist does admit those two files, so a richer
viewer could reach for them without a server change — but that allowlist is a
statement about what the router may serve, not about what this page requests.

| File | Purpose |
|---|---|
| `share.html` | The viewer shell: static markup, the run-locally panel, and the `<script src="/static/share.js">` and stylesheet links. No inline script. |
| `share.js` | Self-contained viewer logic: fetches meta/view, a safe minimal Markdown renderer, cell/artifact rendering, image/CSV preview, and zh/en toggle. |
| `share.css` | Theme-aware styling for the viewer (light/dark via `prefers-color-scheme`), the run-locally panel, cells, and the artifact grid. |
