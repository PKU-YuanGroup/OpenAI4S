# Workbench 哈希资源

[English](README.md)

`frontend/`（`npm run build`）提交进来的构建产物。Gateway 在 `/static/dist/` 提供这棵树。它也是 `/` 与工作台深链的默认 SPA 外壳；`OPENAI4S_WEBUI=legacy` 是改发 `webui/index.html` 的逃生舱。脚本全部是带 `src=` 的外链文件，CSP `script-src 'self'` 不需要放行内联脚本。

## 文件

| 文件 | 职责 |
| --- | --- |
| `en-DtuUOBlM.js` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `index-CTtIcZL8.js` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `index-Da_E9o_t.css` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
| `zh-gS0NfuQ7.js` | Vite 构建产物。不要手改；在 `frontend/` 里重新 build。 |
