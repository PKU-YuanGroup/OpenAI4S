# Web UI

[中文说明](README_zh.md)

The browser client lives here, and the stdlib gateway serves it at `/static/`. There is no npm, no bundler and no compile step: in a source checkout an edit shows up on the next browser reload, while an installed wheel serves its packaged copy. `index.html` itself loads only `favicon.js`, `scientific_renderers.js` and `app.js`, but it is not the only script source. One third-party library reaches the page: when you open a molecule artifact, `app.js` injects 3Dmol at runtime from the vendored copy under `vendor/`, and if that script fails to load it retries from `https://3Dmol.org/build/3Dmol-min.js`, a real outbound request from an application that otherwise stays on loopback. Should the CDN copy fail too, the artifact is shown as plain text. The client reads and mutates over REST and follows one WebSocket event stream. It holds projections of session state, never the canonical copy.

## Runtime responsibilities

- Two shells cover the product: a dashboard and a conversation workspace. Between them they expose projects, sessions, conversation, model selection, plans/reviews, approvals, artifacts, Notebook, Timeline, Context, Security, branches/recovery, Skills, and settings.
- `app.js` holds the browser-side projections and the transient interaction state. Core Workbench projections go through explicit sanitizers and retain neither provider wire payloads nor raw tool arguments. Some Settings paths and older `innerHTML` code have not had a complete escaping audit yet.
- WebSocket events drive streaming prose, cells, activities, execution ownership, and the workbench read models. REST covers bounded reads and explicit mutations.
- The renderer for an artifact is chosen on the server, by [`../renderers.py`](../renderers.py). Sequence, alignment, genome, Molfile/SMILES and LaTeX parsing live in [`scientific_renderers.js`](scientific_renderers.js); table, image, PDF, HTML and text presentation is composed mostly in `app.js`. Scripts inside an HTML preview may execute: they run in a sandboxed iframe without `allow-same-origin`, so they never reach the main application origin, but the preview is not a script-free renderer.
- The frontend is hand-written HTML/CSS/JavaScript on purpose. Keep DOM IDs and event names stable, because the offline static-contract test and the browser smoke test key off them.

## Files

| File | Responsibility |
| --- | --- |
| [`app.js`](app.js) | Everything the client does, in one file: localization and theme, the same-origin API wrapper, the WebSocket lifecycle, routing, dashboard and workspace state, streaming messages, execution controls, workbench projections, artifacts, Notebook and Timeline, models, plans, reviews, Skills, packages, and settings. |
| [`favicon.js`](favicon.js) | Animates the GIF favicon through WebCodecs when available, pauses in hidden tabs, and falls back to the static GIF. |
| [`favicon_anim_64.gif`](favicon_anim_64.gif) | The packaged favicon source: the frames the animation decodes, and the static fallback icon. |
| [`index.html`](index.html) | The accessible DOM shell for dashboard, conversation workspace, composer, right dock, dialogs, and settings. It applies the theme before first paint and references the static scripts and styles. |
| [`scientific_renderers.js`](scientific_renderers.js) | Dependency-free parsers and helpers for sequence/MSA, genome, Molfile/SMILES and LaTeX, plus renderer-descriptor validation. They return plain data and never HTML, so `app.js` builds the DOM from the records; a small UMD wrapper lets the Node contract test import the same file. General table/image/PDF/HTML/text presentation stays in `app.js`. |
| [`style.css`](style.css) | The whole visual system: light/dark tokens, fonts, dashboard and workspace layout, activity and artifact components, dialogs, accessibility, and mobile breakpoints. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| `vendor/` | Vendored minified 3Dmol runtime and font assets. 3Dmol is the one piece of third-party JavaScript in the client, and `app.js` injects it only when a molecule artifact is opened. If the vendored file does not load, that injection falls back to the `3Dmol.org` CDN before it gives up and renders plain text ([`app.js`](app.js), the `3Dmol-min.js` script tags). Treat these as upstream, byte-sensitive assets; they are excluded from formatting and are not documented file-by-file here. |

## Verification

Run the offline UI contracts from the repository root:

```bash
uv run pytest tests/test_webui_static_contract.py
node tests/scientific_renderers_smoke.cjs
```

Anything that changes interaction or streaming also needs a real browser: start `./start.sh` and drive the actual WebSocket flow. See the [server package overview](../README.md) and the [Web application guide](../../../docs/webapp.md).
