# Web UI

[中文说明](README_zh.md)

The browser client lives here, and the stdlib gateway serves it at `/static/`. There is no npm, no bundler and no compile step: in a source checkout an edit shows up on the next browser reload, while an installed wheel serves its packaged copy. `index.html` itself loads only `favicon.js`, `scientific_renderers.js` and `app.js`, but it is not the only script source. One third-party library reaches the page: when you open a molecule artifact, `app.js` injects 3Dmol at runtime from the vendored copy under `vendor/`, and from nowhere else. That injection used to retry from `https://3Dmol.org/build/3Dmol-min.js` — a real outbound request, made silently, executing third-party script in the page that holds the session cookie. It was removed; if the vendored file does not load, the artifact is shown as plain text, which is the same outcome the CDN path reached whenever it failed anyway. The client reads and mutates over REST and follows one WebSocket event stream. It holds projections of session state, never the canonical copy.

## Runtime responsibilities

- Two shells cover the product: a dashboard and a conversation workspace. Between them they expose projects, sessions, conversation, model selection, plans/reviews, approvals, artifacts, Notebook (with its per-language `.ipynb`, bundle and Markdown exports), Timeline, Context, Security, branches/recovery, Skills, and settings.
- `app.js` holds the browser-side projections and the transient interaction state. Core Workbench projections go through explicit sanitizers and retain neither provider wire payloads nor raw tool arguments. Some Settings paths and older `innerHTML` code have not had a complete escaping audit yet.
- WebSocket events drive streaming prose, cells, activities, execution ownership, and the workbench read models. REST covers bounded reads and explicit mutations. The subscription sends `since_seq` and the stream `epoch`, so a reconnect resumes inside `replay_begin`/`replay_end` instead of refetching — and a new epoch discards every cursor the tab holds rather than numbering a stream this daemon never produced. A turn is tracked by its `execution_id`, never by the session it runs in: frames outlive turns and two turns overlap.
- The client keeps the small amount of state a lost response would otherwise destroy. A pinned-comment admission id is minted here, from the platform CSPRNG, and written to `localStorage` *before* the message is sent, so a tab whose 202 never arrived can ask what became of those comments instead of resending them or silently dropping them. Long lists are paged rather than capped: the session list follows the server's opaque keyset cursor behind a load-more control, and an older message page is inserted in time order rather than appended, because the column already holds activity steps older than the newest page.
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
| `share/` | The standalone read-only share viewer (`share.html`/`share.js`/`share.css`), served by the relay tunnel's ShareRouter — separate from the main single-page app, and self-contained rather than a reduced build of it. The shell loads `share.js` and `share.css` and nothing else: no WebSocket, no shared state with `app.js`, and its own small Markdown and CSV renderers instead of `scientific_renderers.js`. The router's asset allowlist does admit `scientific_renderers.js` and the vendored 3Dmol so a richer viewer could reach for them, but nothing in the current shell requests either. |
| `vendor/` | Vendored minified 3Dmol runtime and font assets. 3Dmol is the one piece of third-party JavaScript in the client, and `app.js` injects it only when a molecule artifact is opened. If the vendored file does not load, the artifact is rendered as plain text — there is no CDN fallback ([`app.js`](app.js), the `3Dmol-min.js` script tag). Treat these as upstream, byte-sensitive assets; they are excluded from formatting and are not documented file-by-file here. |

## Verification

Run the offline UI contracts from the repository root:

```bash
uv run pytest tests/test_webui_static_contract.py
node tests/scientific_renderers_smoke.cjs
```

Anything that changes interaction or streaming also needs a real browser: start `./start.sh` and drive the actual WebSocket flow. See the [server package overview](../README.md) and the [Web application guide](../../../docs/webapp.md).
