# Workbench hashed assets

[中文说明](README_zh.md)

Committed output of `frontend/` (`npm run build`). The gateway serves this tree at `/static/dist/`. `OPENAI4S_WEBUI_NEXT=1` makes `dist/index.html` the SPA shell at `/` and at workbench deep links. Every script is an external `src=` file so CSP `script-src 'self'` holds.

## Files

| File | Responsibility |
| --- | --- |
| `index-BwbKMGnO.js` | Vite build output. Do not edit by hand; rebuild from `frontend/`. |
