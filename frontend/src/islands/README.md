# frontend/src/islands

[中文说明](README_zh.md)

F-18 imperative islands. 3Dmol lazy script-tag inject (vendored copy only; the deleted-CDN comment is kept next to the tag), image annotator, dock Viewer / versions, app-origin Ketcher `/ketcher` iframe (embeddable headers, no sandbox), and inert PDF / initial HTML preview iframes. HTML can run scripts after a scoped grant on the verified alternate loopback origin. Does not edit `stores/` or `compat/window-exports.ts` above the lane-additions marker.

## Files

| File | Responsibility |
| --- | --- |
| [`annot.ts`](annot.ts) | Image annotator, pin status, composer chip (app.js:8965-8993, 9149-9429). |
| [`annot.test.ts`](annot.test.ts) | `annotationStatus` mapping; held pins; `openAnnotations`; the comment list reopens on the repainted chip after a removal. |
| [`dom.ts`](dom.ts) | `el` (the artifacts helper, re-exported) / `$` / lucide subset / `ghostIconBtn`. |
| [`frames.ts`](frames.ts) | Inert iframe policy, verified loopback origin and grant path checks; app-origin Ketcher + clipboard allow. |
| [`frames.test.ts`](frames.test.ts) | iframe sandbox attributes, the derived loopback origin, grant path checks, and app-origin Ketcher. |
| [`host.ts`](host.ts) | `isReady` window calls; `t()` fallback. |
| [`index.ts`](index.ts) | `bootIslands` / `installIslands`. Overwrites F-05 stubs. |
| [`install.test.ts`](install.test.ts) | Contract names pass `isReady` after install. |
| [`ketcher.ts`](ketcher.ts) | `openKetcher` iframe onto `/ketcher` (app.js:10834). |
| [`locator.ts`](locator.ts) | PDF / HTML locator comments (app.js:10835-10897). |
| [`mol.ts`](mol.ts) | `_molTeardown` + `molecule` lazy inject (app.js:9610-9673). Each view owns its bounded structure read and scene, so dock and fullscreen coexist. Closing a preview cancels its read and clears its model; a shared offscreen WebGL2 context is released only after its last viewer closes. Read failures show the reason, retry and exact-version download. |
| [`mol.test.ts`](mol.test.ts) | No static 3Dmol import; no live CDN URL; vendored src; only the latest attached structure per container creates a viewer. Independent dock/fullscreen lifetimes, scoped teardown, stable bridge selection despite delayed reads, theme updates for every live viewer, and a single shared script load. |
| [`viewer.ts`](viewer.ts) | Dock Viewer chrome, editor, version history and recorded source panels (app.js:9430-9609). The retrieval-source panel renders a withheld response digest as "hidden" (with the reason it is not the verifiable file digest) and names conflicting fields and normalized legacy aliases instead of silently resolving them; recorded source panels also show explicit unknown verification and unreported metadata. |
| [`viewer.test.ts`](viewer.test.ts) | Text-edit gates, metadata/source rendering, bilingual verification-state contracts, and the retrieval-source hidden-digest / alias / conflict contracts. |
