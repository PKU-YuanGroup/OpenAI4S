# frontend/src/islands

[中文说明](README_zh.md)

F-18 imperative islands. 3Dmol lazy script-tag inject (vendored copy only; the deleted-CDN comment is kept next to the tag), image annotator, dock Viewer / versions, Ketcher `/ketcher` iframe (embeddable headers, no sandbox), PDF / html-preview iframe `sandbox=""`. Does not edit `stores/` or `compat/window-exports.ts` above the lane-additions marker.

## Files

| File | Responsibility |
| --- | --- |
| [`annot.ts`](annot.ts) | Image annotator, pin status, composer chip (app.js:8965-8993, 9149-9429). |
| [`annot.test.ts`](annot.test.ts) | `annotationStatus` mapping; held pins; `openAnnotations`. |
| [`dom.ts`](dom.ts) | `el` / `$` / lucide subset / `ghostIconBtn`. |
| [`frames.ts`](frames.ts) | PDF / html-preview empty sandbox; Ketcher src + clipboard allow. |
| [`frames.test.ts`](frames.test.ts) | iframe sandbox attributes; Ketcher has no sandbox. |
| [`host.ts`](host.ts) | `isReady` window calls; `t()` fallback. |
| [`index.ts`](index.ts) | `bootIslands` / `installIslands`. Overwrites F-05 stubs. |
| [`install.test.ts`](install.test.ts) | Contract names pass `isReady` after install. |
| [`ketcher.ts`](ketcher.ts) | `openKetcher` iframe onto `/ketcher` (app.js:10834). |
| [`locator.ts`](locator.ts) | PDF / HTML locator comments (app.js:10835-10897). |
| [`mol.ts`](mol.ts) | `_molTeardown` + `molecule` lazy inject (app.js:9610-9673). |
| [`mol.test.ts`](mol.test.ts) | No static 3Dmol import; no live CDN URL; vendored src. |
| [`viewer.ts`](viewer.ts) | Dock Viewer chrome, editor, versions (app.js:9430-9609). |
| [`viewer.test.ts`](viewer.test.ts) | `isTextEditable` extension / content-type gate. |
