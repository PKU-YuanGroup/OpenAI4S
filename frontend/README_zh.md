# frontend

[English](README.md)

下一版工作台 UI。Preact 10 + `@preact/signals` + TypeScript（strict）+ Vite + Vitest。提交的构建产物落在 [`../openai4s/server/webui/dist/`](../openai4s/server/webui/dist/)。手写的 `app.js` 客户端在 F-23 翻转默认之前一直作为逃生舱保留。依赖只写在这份 `package.json` 里；仓库根上的 npm 包是 `openai4s-skills`，禁止往那里加前端依赖。

## 在架构中的位置

`npm run dev` 在 `http://127.0.0.1:5173/static/dist/` 提供本应用，并把 `/api` 与 `/ws` 代理到 `8760` 上的 daemon。`npm run build` 以 `base: '/static/dist/'` 把空壳写进 `openai4s/server/webui/dist/`。真正让 daemon 端出这棵树的是 F-04（`OPENAI4S_WEBUI_NEXT=1`）。`app.js` 里的领域内核由后续 F 系列工作项移植；本目录只放工具链和一个挂载节点。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`index.html`](index.html) | SPA 外壳。唯一的脚本是带 `src=` 的外链 `type="module"`，CSP `script-src 'self'` 不必放行内联脚本。 |
| [`package.json`](package.json) | 前端包：Preact 10、`@preact/signals`、Vite、Vitest、TypeScript。`private: true`。 |
| [`package-lock.json`](package-lock.json) | 锁文件，保证 `npm ci` 重建确定（F-23 拿它对照 `webui/dist`）。 |
| [`PORTING_NOTES.md`](PORTING_NOTES.md) | 逐项把旧 `app.js` 行号映射到新模块。F-03 没有领域内核。 |
| [`tsconfig.json`](tsconfig.json) | `src/` 的 strict TypeScript（`strict`、`noUncheckedIndexedAccess`、Preact `jsxImportSource`）。 |
| [`tsconfig.node.json`](tsconfig.node.json) | `vite.config.ts` 的 strict TypeScript。 |
| [`vite.config.ts`](vite.config.ts) | `base: '/static/dist/'`、禁用 `@vitejs/plugin-legacy`、`modulePreload.polyfill: false`、`assetsInlineLimit: 0`、outDir 为 `openai4s/server/webui/dist/`，以及拒绝内联 `<script>` 的构建后守卫。 |

## 子目录

| 目录 | 职责 |
| --- | --- |
| [`src/`](src/) | 应用源码。F-03 只放空壳（`main.tsx` / `App`）。后续工作项在各自车道里添加 `compat/`、`stores/`、`components/<area>/`、`features/<area>/`、`islands/`。 |

## 命令

```bash
cd frontend
npm ci
npm run dev          # Vite 监听 :5173，把 /api 与 /ws 代理到 :8760
npm run build        # 类型检查 + 产出 openai4s/server/webui/dist/
npm test             # vitest run
npm run typecheck    # tsc --noEmit
```

## 约束

- 工作台 CSP 是 `script-src 'self' 'wasm-unsafe-eval'`（没有 `unsafe-eval`，也没有 `unsafe-inline`）。禁止运行时模板编译、禁止 Vite 内联脚本 polyfill、禁止 `@vitejs/plugin-legacy`。
- 不要把前端依赖加进仓库根的 `package.json`。
- 全局样式归 F-21。`src/stores/` 下的 store 文件归 F-05。`compat/window-exports.ts` 归 F-05。
