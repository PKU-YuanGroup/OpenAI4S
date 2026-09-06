# frontend

[English](README.md)

工作台 UI。Preact 10 + `@preact/signals` + TypeScript（strict）+ Vite + Vitest。提交的构建产物落在 [`../openai4s/server/webui/dist/`](../openai4s/server/webui/dist/)，并且是默认 SPA 外壳。手写的 `app.js` 客户端是 `OPENAI4S_WEBUI=legacy` 逃生舱；不要在那里加功能。依赖只写在这份 `package.json` 里；仓库根上的 npm 包是 `openai4s-skills`，禁止往那里加前端依赖。

## 在架构中的位置

`npm run dev` 在 `http://127.0.0.1:5173/static/dist/` 提供本应用，并把 `/api`、`/ws` 与 `/static`（字体 + `style.css`）代理到 `8760` 上的 daemon。`npm run build` 以 `base: '/static/dist/'` 把产物写进 `openai4s/server/webui/dist/`；产物必须和源码同一 PR 提交。daemon 默认把 `dist/index.html` 当作 SPA 外壳发出。`OPENAI4S_WEBUI=legacy` 是逃生舱。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`index.html`](index.html) | SPA 外壳。head 加载 `/static/style.css`（与 legacy UI 同一份全局样式；F-21）、经典脚本（非 module）`/static/theme-bootstrap.js`（第一次绘制就带上 `data-theme`）、`/static/favicon.js`（10 fps 钳制）和 `/static/scientific_renderers.js`。应用入口是带 `src=` 的外链 `type="module"`，CSP `script-src 'self'` 不必放行内联脚本。 |
| [`package.json`](package.json) | 前端包：Preact 10、`@preact/signals`、Vite、Vitest、TypeScript。`private: true`。 |
| [`package-lock.json`](package-lock.json) | 锁文件，保证 `npm ci` 重建确定。CI 会重建 `webui/dist` 并 `git diff --exit-code`。 |
| [`PORTING_NOTES.md`](PORTING_NOTES.md) | 逐项把旧 `app.js` 行号映射到新模块。F-03 没有领域内核；F-04 对照 `_serve_index` / package-data / `_WHEEL_REQUIRED`。 |
| [`tsconfig.json`](tsconfig.json) | `src/` 的 strict TypeScript（`strict`、`noUncheckedIndexedAccess`、Preact `jsxImportSource`）。 |
| [`tsconfig.node.json`](tsconfig.node.json) | `vite.config.ts` 的 strict TypeScript。 |
| [`vite.config.ts`](vite.config.ts) | `base: '/static/dist/'`、禁用 `@vitejs/plugin-legacy`、`modulePreload.polyfill: false`、`assetsInlineLimit: 0`、outDir 为 `openai4s/server/webui/dist/`、`npm run dev` 把 `/static` 代理给字体和 `style.css`，以及拒绝内联 `<script>` 的构建后守卫。 |

`package.json` 还提供 `extract-i18n` / `extract-i18n:check`（F-07；实现在 `src/i18n/extract-i18n.mjs`）。

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`src/`](src/) | 应用源码。F-03 只放空壳（`main.tsx` / `App`）。后续工作项在各自车道里添加 `compat/`、`stores/`、`components/<area>/`、`features/<area>/`、`islands/`。 |

## 命令

```bash
cd frontend
npm ci
npm run dev          # Vite 监听 :5173，把 /api /ws /static 代理到 :8760
npm run build        # 类型检查 + 产出 openai4s/server/webui/dist/
npm test             # vitest run
npm run typecheck    # tsc --noEmit
```

## 约束

- 工作台 CSP 是 `script-src 'self' 'wasm-unsafe-eval'`（没有 `unsafe-eval`，也没有 `unsafe-inline`）。禁止运行时模板编译、禁止 Vite 内联脚本 polyfill、禁止 `@vitejs/plugin-legacy`。
- 不要把前端依赖加进仓库根的 `package.json`。
- 全局样式归 F-21。`src/stores/` 下的 store 文件归 F-05。`compat/window-exports.ts` 归 F-05。
