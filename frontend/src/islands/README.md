# frontend/src/islands

[中文说明](README_zh.md)

F-18 imperative islands. 3Dmol lazy script-tag inject (vendored copy only; the deleted-CDN comment is kept next to the tag), image annotator, dock Viewer / versions, app-origin Ketcher `/ketcher` iframe (embeddable headers, no sandbox), an unsandboxed PDF iframe that only loads a first-party `application/pdf` blob, and the inert initial HTML preview iframe. HTML can run scripts after a scoped grant on the verified alternate loopback origin. Does not edit `stores/` or `compat/window-exports.ts` above the lane-additions marker.

## Files

| File | Responsibility |
| --- | --- |
| [`annot.ts`](annot.ts) | Image annotator, pin status, composer chip (app.js:8965-8993, 9149-9429). |
| [`annot.test.ts`](annot.test.ts) | `annotationStatus` mapping; held pins; `openAnnotations`; the comment list reopens on the repainted chip after a removal. |
| [`dom.ts`](dom.ts) | `el` (the artifacts helper, re-exported) / `$` / lucide subset / `ghostIconBtn`. |
| [`frames.ts`](frames.ts) | Inert HTML iframe policy, the unsandboxed PDF-blob frame (`applyPdfFrame`: Chromium refuses its PDF viewer in any sandboxed frame), verified loopback origin and grant path checks; app-origin Ketcher + clipboard allow. |
| [`frames.test.ts`](frames.test.ts) | iframe sandbox attributes, the derived loopback origin, grant path checks, and app-origin Ketcher. |
| [`host.ts`](host.ts) | `isReady` window calls; `t()` fallback. |
| [`index.ts`](index.ts) | `bootIslands` / `installIslands`. Overwrites F-05 stubs. |
| [`install.test.ts`](install.test.ts) | Contract names pass `isReady` after install. |
| [`ketcher.ts`](ketcher.ts) | `openKetcher` iframe onto `/ketcher` (app.js:10834). |
| [`locator.ts`](locator.ts) | PDF / HTML locator comments (app.js:10835-10897). |
| [`mol.ts`](mol.ts) | `_molTeardown` + `molecule` lazy inject (app.js:9610-9673). Each view owns its bounded structure read and scene, so dock and fullscreen coexist. Closing a preview cancels its read and clears its model; a shared offscreen WebGL2 context is released only after its last viewer closes. Read failures show the reason, retry and exact-version download. Controls: style and colour (default, by chain, by B-factor, with a legend; a scheme the file cannot support is disabled and says why; ligands keep element colours), reset view (back to the first fit's orientation and zoom), click a residue to toggle its label, clear labels, and a facts line with the PDB ID and title (parsed from the text already read), chains and atom count. |
| [`mol.test.ts`](mol.test.ts) | No static 3Dmol import; no live CDN URL; vendored src; only the latest attached structure per container creates a viewer. Independent dock/fullscreen lifetimes, scoped teardown, stable bridge selection despite delayed reads, theme updates for every live viewer, and a single shared script load. ART-6 controls: the facts line from the single read, reset to the first fit then refit, click-to-label toggling and clearing, chain/B-factor colouring with legend and surface, disabled schemes for a small molecule, no labels or saved view without those 3Dmol APIs, and every control's zh/en copy. |
| [`mol-copy.ts`](mol-copy.ts) | ART-6 `molT` copy table (zh/en): colour schemes, reset view, labels, the facts line and the mouse hint (Ctrl+drag pans in the vendored 3Dmol, not Shift). The original tag and style keys stay in the F-07 dictionaries. |
| [`mol-facts.ts`](mol-facts.ts) | Pure helpers for the structure viewer: PDB HEADER/TITLE parsing (stops at the first coordinate record), polymer chains and B-factor range, residue label/key/anchor, the chain palette and blue-white-red B-factor colours, and the 3Dmol style for each representation and scheme. No DOM, no 3Dmol. |
| [`mol-facts.test.ts`](mol-facts.test.ts) | Header parsing on real 2NYD and AlphaFold records (continuations, CRLF, short lines, placeholder IDs, length cap), facts and scheme availability, residue labels/keys/anchors, colour mapping, and that the default scheme keeps the original styles. |
| [`viewer.ts`](viewer.ts) | Dock Viewer chrome, editor, version history and recorded source panels (app.js:9430-9609). The retrieval-source panel renders a withheld response digest as "hidden" (with the reason it is not the verifiable file digest) and names conflicting fields and normalized legacy aliases instead of silently resolving them; recorded source panels also show explicit unknown verification and unreported metadata. Version comparison is offered only when the Stage 9 workbench (the only server of diffs) is on (ART-3). |
| [`viewer.test.ts`](viewer.test.ts) | Text-edit gates, metadata/source rendering, bilingual verification-state contracts, and the retrieval-source hidden-digest / alias / conflict contracts. |
