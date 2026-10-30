# frontend/src/features/artifacts

[English](README.md)

F-17 artifacts + Files（M-03）。版本缓存、Files 搜索/过滤/分页/深链、渲染器注册表接 `window.OpenAI4SScientificRenderers`（空值防御保留）、十个科学渲染器胶水。不改 `stores/`，也不改 `compat/window-exports.ts` 标记区以上的内容。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`api.ts`](api.ts) | 同源 `api()`、`ApiError`、`bytes`、`looksBinary` / `controlDense`、`el`/`icon`（`el` 与 islands 共用）、用 `isReady` 调 window。三个有界读取入口：严格 `fetchArtifactText(url, opts?)`（完整文件或拒绝）、严格 `readArtifactBytes(url, opts?)`（完整图片/PDF 字节或拒绝；预览使用 blob URL，绝不把产物 URL 直接交给元素）、前缀 `readArtifactPrefix(url, {maxBytes, …})`（仅文本缩略图，尊重 UTF-8 边界）；F1 常量、`ArtifactReadError.kind`（`too_large`/`timeout`/`aborted`）、HTTP 状态抛 `ApiError`、按容器归属的预览 AbortController（`beginPreviewRead`/`abortPreviewReads`/`abortDetachedPreviews`；重绘或关闭只取消该视图自己的读取并释放 blob URL，另一个仍挂载的预览保留自己的），以及冻结显示上限常量（共享截取 `previewSlice` 在 `cache.ts`）。 |
| [`read-budget.test.ts`](read-budget.test.ts) | F1/F2 读取契约：恰好等于上限时严格模式返回完整文件、伪造/超额/缺失 Content-Length、跨 chunk 越限即 cancel reader、HTTP 状态抛 ApiError、超时、外部中止、无 body 回退规则、前缀模式在 UTF-8 边界截断且不产生替换字符。 |
| [`api.test.ts`](api.test.ts) | `looksBinary`：单行序列仍是文本；base64 块、控制字符与转义转储仍判为二进制；只对前 1 MiB 做一次线性扫描。 |
| [`artifacts.css`](artifacts.css) | Files 工具条 / 过滤 / Load more / 过期 version 横幅。 |
| [`boot.test.ts`](boot.test.ts) | DOM 挂载顺序及初始路由先于深链的回归覆盖。 |
| [`boot.ts`](boot.ts) | `bootArtifacts` / `installArtifacts`。挂上 `parseTable` 与 `renderSheet`。 |
| [`cache.test.ts`](cache.test.ts) | 版本缓存、dock 原位 mutate、过期 `loadArtifacts`。 |
| [`cache.ts`](cache.ts) | `artifactCacheKey` / `syncArtifactVersion` / `artUrl`（精确 version 不走 latest）；`previewSlice(text, limit)` 是共享的 F3 显示截取（`{shown, truncated}`，不拆代理对），所有受限预览与旧表格兜底共用。 |
| [`catalog.test.ts`](catalog.test.ts) | UMD 空值防御、兼容渲染器 id、PDB CA 点。 |
| [`catalog.ts`](catalog.ts) | `scientificRenderers()` 空值防御、catalog 与 descriptor 拉取；目录失败不缓存为空成功，HTTP 200 但 `renderers` 不是数组同样算失败（空数组是真实目录并保持缓存）。`rendererCatalog.value` 保持 null、清在途 promise 并拒绝，由该目录算出的描述符一并失效；并发调用共享一次 `/renderers` 请求，成功目录保持缓存。兼容映射让 `.ipynb` 走 notebook 渲染器而不是通用文本路径。 |
| [`copy.ts`](copy.ts) | M-03 搜索/过滤/深链文案；区分核验未知与元数据未提供的数据集来源文案；provenance Environment 面板的无快照与包数量未知/不适用文案；读取失败/完整性状态文案；以及只读 Notebook 标签与省略提示（不改生成的 i18n 字典）。 |
| [`deeplink.test.ts`](deeplink.test.ts) | 查询串解析；精确 version；禁止静默降级 latest。 |
| [`deeplink.ts`](deeplink.ts) | `?artifact=&version_id=` 解析/序列化/解析 version。空 versions → not-found。 |
| [`events.test.ts`](events.test.ts) | `artifact_created` 版本同步 + 现场插图；跳过 stub 的 `nbRender`。 |
| [`events.ts`](events.ts) | `artifact_created` 余下 32 行（app.js:5314-5346）。 |
| [`files-index.test.ts`](files-index.test.ts) | cursor 生命周期（filter/project 丢弃）、禁止数组 route 回退、首屏 ≤50。 |
| [`files-index.ts`](files-index.ts) | 只走 B-06 `GET .../artifact-index`。filter fingerprint 丢弃旧 cursor。 |
| [`http-stub.ts`](http-stub.ts) | Vitest 用的 JSON `Response` 替身，带真实 body 流，使有界读取与浏览器一致。 |
| [`index.ts`](index.ts) | 对外 re-export。 |
| [`load.ts`](load.ts) | `loadArtifacts` / `loadProjectArtifacts` / `setFilesScope`。 |
| [`partial.ts`](partial.ts) | F3 `partial` 约定集中于此：`partialNotice`（持续可见的 `role="status"` 提示，`data-state="partial"`）、`downloadLink` 与 `previewDownload`（确切版本下载），由各渲染器、notebook、科学检索视图与旧版表格回退共用。 |
| [`preview.ts`](preview.ts) | 精确版本的 scoped grant 升级（按版本缓存 grant、改写说明文字），以及 grant 被拒后回退到惰性的 `/preview/<id-or-version>`。 |
| [`preview.test.ts`](preview.test.ts) | 生产渲染器 grant 生命周期、grant 复用与过期、源与路径拒绝、精确版本回退、非回环源保持惰性及已移除 iframe 回归测试。 |
| [`science-results.ts`](science-results.ts) | `readScienceSearchEnvelope(text)`：顶层键集合封闭（`query`/`endpoint`/`request_url`/`retrieved_at`/`source_checksum`/`response_sha256`/`accessions`/`result`），`result.results` 必须是对象数组且 `id`/`title`/`url`/`type`/`attributes` 类型受检，并要求检索身份标记，因此任意带 `result` 的无关 JSON 返回 null 并保持通用路径。视图含“检索记录”（默认）与“原始 JSON”两个标签页：每页 50 最多 500 条、显示“本文件保存 N 条记录”、`next_cursor` 只提示“还有后续页未包含在此文件中”（绝不称数据库总量）、可展开最多 8 个标量属性且一律 textContent、标题/标识/类型/属性与链接显示文本都经 publicText 脱敏、仅 http(s) 链接渲染为 `<a target="_blank" rel="noopener noreferrer">` 且显示文本经 publicText 脱敏，原始 JSON 页保留原文显示/展开/下载并绑定 A3 冻结的确切版本；同时保留 `scienceSourceStatus` 供版本历史来源面板使用，读取当前 `alias_conflicts` 键（并兼容旧的 `source_conflicts` 拼写），冲突同样会被点名。 |
| [`science-results.test.ts`](science-results.test.ts) | envelope 拒绝（坏 JSON、缺身份标记、字段类型错误、`result.results` 非数组/非对象、任何多余顶层键）、规范/旧键、next_cursor、外链协议/凭据脱敏与显示脱敏、标题/属性纯文本与 8 标量上限、空结果、50/500 分页与超限下载、原始 JSON 页（共享与回退）、版本隔离与零网络读取。 |
| [`notebook.ts`](notebook.ts) | 只读 nbformat-4 `.ipynb` 预览（F5–F7）：严格 envelope/字段类型校验（cells 数组、Cell 与输出字段类型）；最多 200 个 Cell、每批 50 个（“显示更多”）、每 Cell 20 输出、单文本块 65536 码元（`previewSlice`）；代码 Cell 用 `mdCodeBlock`（复制按钮即 `copy` 能力），Markdown 用 `renderMd(..., {resources:"none"})`，Raw 纯文本；PNG 在创建图像前逐项校验（Base64 估算→签名/IHDR/正尺寸→单张 2 MiB、累计 1600 万像素），最多 2 张并发解码；其他 MIME 只显示不支持提示；导出的 `[OpenAI4S artifacts]` 行保持纯文本；读取使用渲染根的 `previewReadSignal`，PNG URL 经 `previewObjectUrl` 登记，因此各关闭路径调用的 `abortPreviewReads` 即可中止读取、revoke 这些 URL 并清空其容器；侧栏与全屏相互独立。零执行、零 Host、零其他源请求。 |
| [`notebook.test.ts`](notebook.test.ts) | envelope/类型拒绝（坏 JSON、nbformat 3、错误字段类型、未知 cell/output 枚举）、Cell/输出/文本上限与分批“显示更多”、string 与 string-array 字段、代码复制块、Markdown 禁资源（data 与外链图片）、文本/错误输出、不支持富 MIME、PNG 校验与字节/像素上限及两并发解码队列、损坏文件原文、分类读取失败与重试、关闭中止/释放/清空。 |
| [`renderers.ts`](renderers.ts) | 兼容模式的信息行带“重试”，就地重新请求目录与描述符（无需整页刷新；仍失败则再次降级）。十一个科学胶水 + 用 `isReady` 接 image/pdf/html/3Dmol/notebook 孤岛。PDF iframe 由 F-18 补 `sandbox=""`。图片与 PDF 经 `readArtifactBytes` 读取（blob URL 归该视图所有）。标注视图在读取被拒绝时显示同样的分类失败卡。打开预览时也会拆掉仍在读取的分子结构。有界可取消读取；冻结显示上限统一经 `previewSlice` 截取；`partial` 预览带持续可见的 `role="status"` 提示（说明只显示了前 N 个字符）与确切版本下载（Markdown 在 `.md` 正文上下各一条提示；原始文本保留“展开完整原文”）；读取失败是第三态，显示分类失败卡。版本冻结（F4）：descriptor 入口一次性计算 `target = artifactMetadataTarget(a)` 与 `url = artUrl(target)`，所有渲染器与下载只用这两个值；没有版本标识的行显示“最新版本（无固定版本标识）”。 |
| [`renderers.test.ts`](renderers.test.ts) | 文本渲染器：每次渲染只读一次（无下载/文本循环）；序列按源码显示；JSON 只按控制字符判定二进制；body 已被替换时晚到的描述符不再执行渲染胶水。 侧栏与全屏的读取和 Notebook 图片会话彼此独立；关闭后的迟到描述符不能重启读取。 |
| [`sheet.test.ts`](sheet.test.ts) | `sheetShape` 键并集；5000×100 上限。 |
| [`sheet.ts`](sheet.ts) | `renderSheet` / `sheetShape`（app.js:8771-8802）。 |
| [`state.ts`](state.ts) | 车道局部 M-03 signal。不上升进 `stores/`。 |
| [`thumbs.ts`](thumbs.ts) | 由按版本、有上限的预览缓存生成磁贴缩略图、`parseMolPoints` / `molSvg`。全局 FIFO 前缀队列（最多 4 个在途、共享 15 秒期限），每个磁贴 DOM 元素登记为缓存键的消费者，`pruneThumbReads()` 只在全部消费者都离开文档时中止并释放图片 blob URL；同一版本缓存未命中时，仍有磁贴在显示则复用那份 blob，全部消费者离开后才释放；被截断的文本/分子磁贴标“部分预览”，被截断的数据磁贴只显示列名、不显示行数。图片磁贴按 8 MiB 主预览上限整文件读取（前缀无法解码），绝不把产物 URL 赋给 `img.src`；超过 256 KiB 磁贴预算的位图先缩成 480 px 副本，磁贴只保留该副本。 |
| [`thumbs.test.ts`](thumbs.test.ts) | 每个产物版本只读一次；错误页和失败的读取不缓存；LRU 上限。 |
| [`types.ts`](types.ts) | Artifact DTO、页大小 50/100、TEXT_EXT / MOL_EXT。 |
| [`ui.ts`](ui.ts) | Files 网格、dock 标签、`openViewer`、深链；`renderViewer` 交给 islands 的 Viewer。提供 `version_id` 时绝不静默 latest。 |
| [`ui.test.ts`](ui.test.ts) | 深链 apply / `openViewer` 精确 pin / stale 不打开 latest。 |
| [`editor.ts`](editor.ts) | 版本绑定草稿、有界 UTF-8 存储、校验读取与条件保存。 |
| [`editor-view.ts`](editor-view.ts) | 编辑控件及冲突／未知结果的只读恢复。 |
| [`editor.test.ts`](editor.test.ts) | 加载、版本、保存、核对和草稿容量合同。 |

- [`validation.ts`](validation.ts): 完整校验元数据导出必需版本与产物行，不静默丢弃畸形记录。
