# frontend/src/features/md

[English](README.md)

Markdown + 高亮内核。先整体 `esc` 再标记替换；链接 scheme 白名单 `(https?:|mailto:|/|#)`；不用 marked / DOMPurify。`.tok-*` 类名不变。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`esc.ts`](esc.ts) | `esc`（`&<>"`）和 `escQuote`（属性纪律）。 |
| [`esc.test.ts`](esc.test.ts) | 引号转义顺序；旧的 `&<>` 断言不破。 |
| [`highlight.ts`](highlight.ts) | mdHighlight 扫描器；`_OC_KW ∪ MD_KEYWORDS`；EDKW 从同表派生。 |
| [`highlight.test.ts`](highlight.test.ts) | `.tok-*` 类名、关键词并集、EDKW 派生。 |
| [`render.ts`](render.ts) | `renderMd` / `mdInline` / `mdCodeBlock`。 |
| [`render.test.ts`](render.test.ts) | `tests/browser_smoke.mjs` 的 5 个 XSS 样本；scheme 白名单。 |
