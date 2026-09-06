# frontend/src/features/artifacts

[English](README.md)

F-17 artifacts + Files（M-03）。版本缓存、Files 搜索/过滤/分页/深链、渲染器注册表接 `window.OpenAI4SScientificRenderers`（空值防御保留）、十个科学渲染器胶水。不改 `stores/`，也不改 `compat/window-exports.ts` 标记区以上的内容。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`api.ts`](api.ts) | 同源 `api()`、`ApiError`、`bytes`、`looksBinary`、`el`/`icon`、用 `isReady` 调 window。 |
| [`artifacts.css`](artifacts.css) | Files 工具条 / 过滤 / Load more / 过期 version 横幅。 |
| [`boot.test.ts`](boot.test.ts) | DOM 挂载顺序及初始路由先于深链的回归覆盖。 |
| [`boot.ts`](boot.ts) | `bootArtifacts` / `installArtifacts`。挂上 `parseTable` 与 `renderSheet`。 |
| [`cache.test.ts`](cache.test.ts) | 版本缓存、dock 原位 mutate、过期 `loadArtifacts`。 |
| [`cache.ts`](cache.ts) | `artifactCacheKey` / `syncArtifactVersion` / `artUrl`（精确 version 不走 latest）。 |
| [`catalog.test.ts`](catalog.test.ts) | UMD 空值防御、兼容渲染器 id、PDB CA 点。 |
| [`catalog.ts`](catalog.ts) | `scientificRenderers()` 空值防御、catalog 与 descriptor 拉取。 |
| [`copy.ts`](copy.ts) | M-03 搜索/过滤/深链文案（不改生成的 i18n 字典）。 |
| [`deeplink.test.ts`](deeplink.test.ts) | 查询串解析；精确 version；禁止静默降级 latest。 |
| [`deeplink.ts`](deeplink.ts) | `?artifact=&version_id=` 解析/序列化/解析 version。空 versions → not-found。 |
| [`events.test.ts`](events.test.ts) | `artifact_created` 版本同步 + 现场插图；跳过 stub 的 `nbRender`。 |
| [`events.ts`](events.ts) | `artifact_created` 余下 32 行（app.js:5314-5346）。 |
| [`files-index.test.ts`](files-index.test.ts) | cursor 生命周期（filter/project 丢弃）、禁止数组 route 回退、首屏 ≤50。 |
| [`files-index.ts`](files-index.ts) | 只走 B-06 `GET .../artifact-index`。filter fingerprint 丢弃旧 cursor。 |
| [`http-stub.ts`](http-stub.ts) | Vitest 用的 JSON `Response` 替身。 |
| [`index.ts`](index.ts) | 对外 re-export。 |
| [`load.ts`](load.ts) | `loadArtifacts` / `loadProjectArtifacts` / `setFilesScope`。 |
| [`renderers.ts`](renderers.ts) | 十个科学胶水 + 用 `isReady` 接 image/pdf/html/3Dmol 孤岛。PDF iframe 由 F-18 补 `sandbox=""`。 |
| [`sheet.test.ts`](sheet.test.ts) | `sheetShape` 键并集；5000×100 上限。 |
| [`sheet.ts`](sheet.ts) | `renderSheet` / `sheetShape`（app.js:8771-8802）。 |
| [`state.ts`](state.ts) | 车道局部 M-03 signal。不上升进 `stores/`。 |
| [`thumbs.ts`](thumbs.ts) | 磁贴缩略图、`parseMolPoints` / `molSvg`。 |
| [`types.ts`](types.ts) | Artifact DTO、页大小 50/100、TEXT_EXT / MOL_EXT。 |
| [`ui.ts`](ui.ts) | Files 网格、Viewer、`openViewer`、⌘K/深链命中。提供 `version_id` 时绝不静默 latest。 |
| [`ui.test.ts`](ui.test.ts) | 深链 apply / `openViewer` 精确 pin / stale 不打开 latest。 |
