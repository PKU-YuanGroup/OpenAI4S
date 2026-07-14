# Web UI

[中文](README_zh.md)

This directory is the dependency-free browser client served at `/static/` by the stdlib gateway. There is no bundler or compilation step: in a source checkout, edits are visible after a browser reload; installed wheels serve their packaged copies. The UI consumes REST read/mutation endpoints and one WebSocket event stream, but it does not own canonical session state.

## Runtime responsibilities

- The dashboard and workspace shell expose projects, sessions, conversation, model selection, plans/reviews, approvals, artifacts, Notebook, Timeline, Context, Security, branches/recovery, Skills, and settings.
- `app.js` keeps browser projections and transient interaction state. Core Workbench projections use explicit sanitizers and do not retain provider wire payloads or raw tool arguments; some Settings and legacy `innerHTML` paths still require a complete escaping audit.
- WebSocket events update streaming prose, cells, activities, execution ownership, and workbench read models. REST is used for bounded reads and explicit mutations.
- Artifact rendering is selected on the server by [`../renderers.py`](../renderers.py). Sequence, alignment, genome, Molfile/SMILES, and LaTeX parsing helpers live in [`scientific_renderers.js`](scientific_renderers.js); table, image, PDF, HTML, and text presentation is mainly composed in `app.js`. HTML preview scripts may execute inside a sandboxed iframe without `allow-same-origin`; they do not execute in the main application origin, but the preview is not a script-free renderer.
- The frontend is deliberately hand-written HTML/CSS/JavaScript. Preserve stable DOM IDs and event names because offline static-contract and browser smoke tests depend on them.

## Direct files

| File | Responsibility |
| --- | --- |
| [`app.js`](app.js) | Main client application: localization/theme, same-origin API wrapper, WebSocket lifecycle, routing, dashboard/workspace state, streaming messages, execution controls, workbench projections, artifacts, Notebook/Timeline, models, plans, reviews, Skills, packages, and settings. |
| [`favicon.js`](favicon.js) | Animates the GIF favicon through WebCodecs when available, pauses in hidden tabs, and falls back to the static GIF. |
| [`favicon_anim_64.gif`](favicon_anim_64.gif) | Packaged animated/static favicon source. |
| [`index.html`](index.html) | Accessible DOM shell for dashboard, conversation workspace, composer, right dock, dialogs, and settings; loads theme before paint and references static scripts/styles. |
| [`scientific_renderers.js`](scientific_renderers.js) | UMD, dependency-free, data-only parsers and helpers for sequence/MSA, genome, Molfile/SMILES, LaTeX, and renderer-descriptor validation; also importable by Node contract tests. General table/image/PDF/HTML/text presentation remains in `app.js`. |
| [`style.css`](style.css) | Complete responsive visual system, light/dark tokens, fonts, dashboard/workspace layout, activity and artifact components, dialogs, accessibility, and mobile breakpoints. |

## Direct subdirectories

| Directory | Responsibility |
| --- | --- |
| `vendor/` | Vendored minified 3Dmol runtime and font assets. Treat these as upstream/byte-sensitive assets; they are excluded from formatting and are not documented file-by-file here. |

## Verification

Run the offline UI contracts from the repository root:

```bash
uv run pytest tests/test_webui_static_contract.py
node tests/scientific_renderers_smoke.cjs
```

For changes that affect interaction or streaming, also start `./start.sh` and exercise the real browser/WebSocket flow. See the [server package overview](../README.md) and [Web application guide](../../../docs/webapp.md).
