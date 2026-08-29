# Workbench 哈希资源

[English](README.md)

`frontend/`（`npm run build`）提交进来的构建产物。Gateway 在 `/static/dist/` 提供这棵树。`OPENAI4S_WEBUI_NEXT=1` 时 `dist/index.html` 成为 `/` 与工作台深链的 SPA 外壳。脚本全部是带 `src=` 的外链文件，CSP `script-src 'self'` 不需要放行内联脚本。

## 文件

| 文件 | 职责 |
| --- | --- |
| `index-BwbKMGnO.js` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
