# Workbench 哈希资源

[English](README.md)

`frontend/`（`npm run build`）提交进来的构建产物。F-04 接上 `OPENAI4S_WEBUI_NEXT=1` 之后，Gateway 会在 `/static/dist/` 提供这棵树。脚本全部是带 `src=` 的外链文件，CSP `script-src 'self'` 不需要放行内联脚本。

## 文件

| 文件 | 职责 |
| --- | --- |
| `index-BwbKMGnO.js` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
