# frontend/src/islands

[English](README.md)

F-18 命令式孤岛。3Dmol 懒注入 script 标签（只取自带副本；删-CDN 安全注释留在标签旁）、图片标注器、dock Viewer / 版本、Ketcher `/ketcher` iframe（embeddable 头，不加 sandbox）、PDF / html-preview iframe `sandbox=""`。不改 `stores/`，也不改 `compat/window-exports.ts` 标记区以上的内容。

## 文件

| 文件 | 职责 |
| --- | --- |
| [`annot.ts`](annot.ts) | 图片标注器、pin 状态、作曲框 chip（app.js:8965-8993, 9149-9429）。 |
| [`annot.test.ts`](annot.test.ts) | `annotationStatus` 映射；held pin；`openAnnotations`。 |
| [`dom.ts`](dom.ts) | `el` / `$` / lucide 子集 / `ghostIconBtn`。 |
| [`frames.ts`](frames.ts) | PDF / html-preview 空 sandbox；Ketcher src + clipboard allow。 |
| [`frames.test.ts`](frames.test.ts) | iframe sandbox 属性；Ketcher 不加 sandbox。 |
| [`host.ts`](host.ts) | 用 `isReady` 调 window；`t()` 回退。 |
| [`index.ts`](index.ts) | `bootIslands` / `installIslands`。覆盖 F-05 占位。 |
| [`install.test.ts`](install.test.ts) | 安装后契约名字通过 `isReady`。 |
| [`ketcher.ts`](ketcher.ts) | `openKetcher` iframe 打开 `/ketcher`（app.js:10834）。 |
| [`locator.ts`](locator.ts) | PDF / HTML locator 评论（app.js:10835-10897）。 |
| [`mol.ts`](mol.ts) | `_molTeardown` + `molecule` 懒注入（app.js:9610-9673）。 |
| [`mol.test.ts`](mol.test.ts) | 无 3Dmol 静态 import；无活 CDN URL；自带 src。 |
| [`viewer.ts`](viewer.ts) | Dock Viewer 铬、编辑器、版本（app.js:9430-9609）。 |
| [`viewer.test.ts`](viewer.test.ts) | `isTextEditable` 扩展名 / content-type 门。 |
