# Workbench build output

[中文说明](README_zh.md)

Committed output of `frontend/` (`npm run build`). The gateway serves this tree at `/static/dist/`. It is also the default SPA shell at `/` and at workbench deep links; `OPENAI4S_WEBUI=legacy` is the escape hatch that serves `webui/index.html` instead. Every script is an external `src=` file so CSP `script-src 'self'` holds.

## Files

| File | Responsibility |
| --- | --- |
| `index.html` | Vite build output. Do not edit by hand; rebuild from `frontend/`. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| `assets/` | Hashed chunks emitted by Vite. |
