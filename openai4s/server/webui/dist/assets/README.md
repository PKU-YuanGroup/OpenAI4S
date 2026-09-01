# Workbench hashed assets

[中文说明](README_zh.md)

Committed output of `frontend/` (`npm run build`). The gateway serves this tree at `/static/dist/`. It is also the default SPA shell at `/` and at workbench deep links; `OPENAI4S_WEBUI=legacy` is the escape hatch that serves `webui/index.html` instead. Every script is an external `src=` file so CSP `script-src 'self'` holds.

## Files

| File | Responsibility |
| --- | --- |
| `en-CPQbE2IT.js` | Vite build output. Do not edit by hand; rebuild from `frontend/`. |
| `index-BkE1ajtx.js` | Vite build output. Do not edit by hand; rebuild from `frontend/`. |
| `index-Da_E9o_t.css` | Vite build output. Do not edit by hand; rebuild from `frontend/`. |
| `zh-cawl9S-h.js` | Vite build output. Do not edit by hand; rebuild from `frontend/`. |
