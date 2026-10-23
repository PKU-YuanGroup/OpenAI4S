# frontend/src/features/artifacts

[English](README.md)

F-17 artifacts + Files（M-03）。版本缓存、Files 搜索/过滤/分页/深链、渲染器注册表接 `window.OpenAI4SScientificRenderers`（空值防御保留）、十个科学渲染器胶水。不改 `stores/`，也不改 `compat/window-exports.ts` 标记区以上的内容。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`api.ts`](api.ts) | 同源 `api()`、`ApiError`、`bytes`、`looksBinary` / `controlDense`、`el`/`icon`（`el` 与 islands 共用）、用 `isReady` 调 window。有界 `readArtifactText()`/`fetchArtifactText()`（主预览 8 MiB、流式累计、超时/取消分类、`ArtifactReadError`）与冻结显示上限、`unicodeSafeSlice` / `truncateMarkdown`。 |
| [`read-budget.test.ts`](read-budget.test.ts) | 流式字节预算：缺失/伪造长度、跨 chunk 超额即取消 reader、超时、取消、先查 HTTP 状态、无流式 body 拒绝预览、仅在允许时保留部分前缀。 |
| [`api.test.ts`](api.test.ts) | `looksBinary`：单行序列仍是文本；base64 块、控制字符与转义转储仍判为二进制；只对前 1 MiB 做一次线性扫描。 |
| [`artifacts.css`](artifacts.css) | Files 工具条 / 过滤 / Load more / 过期 version 横幅。 |
| [`boot.test.ts`](boot.test.ts) | DOM 挂载顺序及初始路由先于深链的回归覆盖。 |
| [`boot.ts`](boot.ts) | `bootArtifacts` / `installArtifacts`。挂上 `parseTable` 与 `renderSheet`。 |
| [`cache.test.ts`](cache.test.ts) | 版本缓存、dock 原位 mutate、过期 `loadArtifacts`。 |
| [`cache.ts`](cache.ts) | `artifactCacheKey` / `syncArtifactVersion` / `artUrl`（精确 version 不走 latest）；`artifactPreviewTarget` / `artifactPreviewUrl` 把一次预览与它的下载冻结到同一版本。 |
| [`catalog.test.ts`](catalog.test.ts) | UMD 空值防御、兼容渲染器 id、PDB CA 点。 |
| [`catalog.ts`](catalog.ts) | `scientificRenderers()` 空值防御、catalog 与 descriptor 拉取；目录失败不缓存为空成功、清在途 promise 与描述符缓存，可重试恢复。兼容映射让 `.ipynb` 走 notebook 渲染器而不是通用文本路径。 |
| [`copy.ts`](copy.ts) | M-03 搜索/过滤/深链文案、provenance Environment 面板的无快照与包数量未知/不适用文案、读取失败/完整性状态文案，以及只读 Notebook 标签与省略提示（不改生成的 i18n 字典）。 |
| [`deeplink.test.ts`](deeplink.test.ts) | 查询串解析；精确 version；禁止静默降级 latest。 |
| [`deeplink.ts`](deeplink.ts) | `?artifact=&version_id=` 解析/序列化/解析 version。空 versions → not-found。 |
| [`events.test.ts`](events.test.ts) | `artifact_created` 版本同步 + 现场插图；跳过 stub 的 `nbRender`。 |
| [`events.ts`](events.ts) | `artifact_created` 余下 32 行（app.js:5314-5346）。 |
| [`files-index.test.ts`](files-index.test.ts) | cursor 生命周期（filter/project 丢弃）、禁止数组 route 回退、首屏 ≤50。 |
| [`files-index.ts`](files-index.ts) | 只走 B-06 `GET .../artifact-index`。filter fingerprint 丢弃旧 cursor。 |
| [`http-stub.ts`](http-stub.ts) | Vitest 用的 JSON `Response` 替身，带真实 body 流，使有界读取与浏览器一致。 |
| [`index.ts`](index.ts) | 对外 re-export。 |
| [`load.ts`](load.ts) | `loadArtifacts` / `loadProjectArtifacts` / `setFilesScope`。 |
| [`preview.ts`](preview.ts) | 精确版本的 scoped grant 升级（按版本缓存 grant、改写说明文字），以及 grant 被拒后回退到惰性的 `/preview/<id-or-version>`。 |
| [`preview.test.ts`](preview.test.ts) | 生产渲染器 grant 生命周期、grant 复用与过期、源与路径拒绝、精确版本回退、非回环源保持惰性及已移除 iframe 回归测试。 |
| [`notebook.ts`](notebook.ts) | 只读 nbformat-4 `.ipynb` 预览：严格 envelope/字段类型校验、200 Cell / 每 Cell 20 输出 / 单文本 64 KiB / 整文件 8 MiB、PNG 签名与 IHDR 检查 + 单张 2 MiB / 累计 1600 万像素 + 最多 2 张并发解码、Markdown 禁资源模式、HTML/SVG/widget 等只提示、零执行零 Host 零外联。 |
| [`notebook.test.ts`](notebook.test.ts) | envelope/类型拒绝、Cell 与输出上限、64 KiB UTF-8 截断、PNG 校验与字节/像素上限、不支持富输出、关闭取消读取并释放图像 URL。 |
| [`renderers.ts`](renderers.ts) | 十一个科学胶水 + 用 `isReady` 接 image/pdf/html/3Dmol/notebook 孤岛。PDF iframe 由 F-18 补 `sandbox=""`。有界可取消读取；显示上限保持冻结数值；每个受限预览明确“完整／部分预览（附精确版本下载）／无法预览”。 |
| [`renderers.test.ts`](renderers.test.ts) | 文本渲染器：每次渲染只读一次（无下载/文本循环）；序列按源码显示；JSON 只按控制字符判定二进制；body 已被替换时晚到的描述符不再执行渲染胶水。 |
| [`sheet.test.ts`](sheet.test.ts) | `sheetShape` 键并集；5000×100 上限。 |
| [`sheet.ts`](sheet.ts) | `renderSheet` / `sheetShape`（app.js:8771-8802）。 |
| [`state.ts`](state.ts) | 车道局部 M-03 signal。不上升进 `stores/`。 |
| [`thumbs.ts`](thumbs.ts) | 由按版本、有上限的预览缓存生成磁贴缩略图、`parseMolPoints` / `molSvg`。4 并发 256 KiB 读取、15 秒期限、共享读取引用计数，离开 Files 时取消无人使用的读取。 |
| [`thumbs.test.ts`](thumbs.test.ts) | 每个产物版本只读一次；错误页和失败的读取不缓存；LRU 上限。 |
| [`types.ts`](types.ts) | Artifact DTO、页大小 50/100、TEXT_EXT / MOL_EXT。 |
| [`ui.ts`](ui.ts) | Files 网格、dock 标签、`openViewer`、深链；`renderViewer` 交给 islands 的 Viewer。提供 `version_id` 时绝不静默 latest。 |
| [`ui.test.ts`](ui.test.ts) | 深链 apply / `openViewer` 精确 pin / stale 不打开 latest。 |
| [`editor.ts`](editor.ts) | 版本绑定草稿、有界 UTF-8 存储、校验读取与条件保存。 |
| [`editor-view.ts`](editor-view.ts) | 编辑控件及冲突／未知结果的只读恢复。 |
| [`editor.test.ts`](editor.test.ts) | 加载、版本、保存、核对和草稿容量合同。 |

- [`validation.ts`](validation.ts): 完整校验元数据导出必需版本与产物行，不静默丢弃畸形记录。
