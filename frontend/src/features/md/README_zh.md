# frontend/src/features/md

[English](README.md)

Markdown + 高亮内核。先整体 `esc` 再标记替换；链接 scheme 白名单 `(https?:|mailto:|/|#)`；不用 marked / DOMPurify。`.tok-*` 类名不变。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`index.ts`](index.ts) | 汇总导出，把契约名 `renderMd` 挂到 `window`，并在 `document` 上绑定代码块复制监听。 |
| [`copy.ts`](copy.ts) | 代码块的复制按钮：一个委托的 `.cb-copy` 点击监听（app.js 原有，移植时丢了）。只有 `copyText` 确认写入才显示"已复制"；复制被拒时如实提示（功能内文案）并选中代码。 |
| [`copy.test.ts`](copy.test.ts) | 按钮文字与提示来自词典；点按钮任意位置都复制它所在的代码块，监听只绑定一次；"已复制"后复原；复制被拒时报告失败而不是打勾。 |
| [`esc.ts`](esc.ts) | `esc`（`&<>"`）和 `escQuote`（属性纪律）。 |
| [`esc.test.ts`](esc.test.ts) | 引号转义顺序；旧的 `&<>` 断言不破。 |
| [`highlight.ts`](highlight.ts) | mdHighlight 扫描器；`_OC_KW ∪ MD_KEYWORDS`；EDKW 从同表派生。 |
| [`highlight.test.ts`](highlight.test.ts) | `.tok-*` 类名、关键词并集、EDKW 派生。 |
| [`render.ts`](render.ts) | `renderMd` / `mdInline` / `mdCodeBlock`（复制按钮文字与提示走 `t()`）。F-21 把表格包进 `.md-table-wrap`。0.2.0 存下的单段 `/api/artifacts/<id>` 链接（契约 v1 网关一律 404）改写到 `/api/v1/artifacts/<id>`，其它 href 原样保留。引用块与列表嵌套到 32 层为止（更深的按平铺渲染），任何回答都不会让调用栈溢出；方括号内文字不含 `[`，因此一长串 `[` 是线性的。 |
| [`render.test.ts`](render.test.ts) | `tests/browser_smoke.mjs` 的 5 个 XSS 样本；scheme 白名单；旧产物链接改写；表格容器；12000 层嵌套引用或列表不会栈溢出；60000 个 `[` 线性完成；不成对的 `[` 保持为文字。 |
