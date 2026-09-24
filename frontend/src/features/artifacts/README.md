# frontend/src/features/artifacts

[中文说明](README_zh.md)

F-17 artifacts + Files (M-03). Version cache, Files search/filter/pagination/deep link, renderer catalog onto `window.OpenAI4SScientificRenderers` (null defense kept), and the ten scientific renderer glues. Does not edit `stores/` or `compat/window-exports.ts` above the lane-additions marker.

## Files

| File | Responsibility |
| --- | --- |
| [`api.ts`](api.ts) | Same-origin `api()`, `ApiError`, `bytes`, `looksBinary` / `controlDense`, `el`/`icon` (`el` is shared with islands), `isReady` window calls. |
| [`api.test.ts`](api.test.ts) | `looksBinary`: one-line sequences stay text; base64 blobs, control characters and escape dumps do not; one linear pass over the first MiB. |
| [`artifacts.css`](artifacts.css) | Files toolbar / filter / Load more / stale-version banner. |
| [`boot.test.ts`](boot.test.ts) | DOM mount ordering and initial-route-before-deep-link regression coverage. |
| [`boot.ts`](boot.ts) | `bootArtifacts` / `installArtifacts`. Assigns `parseTable` + `renderSheet`. |
| [`cache.test.ts`](cache.test.ts) | Version cache, in-place dock mutate, stale `loadArtifacts`. |
| [`cache.ts`](cache.ts) | `artifactCacheKey` / `syncArtifactVersion` / `artUrl` (exact version never uses latest). |
| [`catalog.test.ts`](catalog.test.ts) | UMD null defense, compatibility renderer ids, PDB CA points. |
| [`catalog.ts`](catalog.ts) | `scientificRenderers()` empty-value defense, catalog + descriptor fetch. |
| [`copy.ts`](copy.ts) | M-03 search/filter/deep-link copy, plus the provenance Environment panel's no-snapshot and unknown/not-applicable package-count strings (does not rewrite generated i18n). |
| [`deeplink.test.ts`](deeplink.test.ts) | Query parse; exact version; no silent latest fallback. |
| [`deeplink.ts`](deeplink.ts) | `?artifact=&version_id=` parse/serialize/resolve. Empty versions → not-found. |
| [`events.test.ts`](events.test.ts) | `artifact_created` version sync + live figure; stub `nbRender` is skipped. |
| [`events.ts`](events.ts) | Remaining 32-line `artifact_created` body (app.js:5314-5346). |
| [`files-index.test.ts`](files-index.test.ts) | Cursor lifecycle (filter/project drop), no array-route fallback, first page ≤50. |
| [`files-index.ts`](files-index.ts) | B-06 `GET .../artifact-index` only. Filter fingerprint drops stale cursors. |
| [`http-stub.ts`](http-stub.ts) | JSON `Response` stand-in for Vitest. |
| [`index.ts`](index.ts) | Public re-exports. |
| [`load.ts`](load.ts) | `loadArtifacts` / `loadProjectArtifacts` / `setFilesScope`. |
| [`preview.ts`](preview.ts) | Scoped exact-version grant upgrade with a per-version grant cache, the retargeted caption, and the inert `/preview/<id-or-version>` fallback once a grant is refused. |
| [`preview.test.ts`](preview.test.ts) | Production renderer grant lifecycle, grant reuse and expiry, origin/path rejection, exact-version fallback, non-loopback inertness, and detached iframe regression tests. |
| [`renderers.ts`](renderers.ts) | Ten scientific glues + image/pdf/html/3Dmol islands via `isReady`. PDF iframe gets F-18 `sandbox=""`. |
| [`renderers.test.ts`](renderers.test.ts) | Text renderer: one read per render (no download/text cycle); sequences render as source; JSON is judged by control characters only; a late descriptor for a replaced body runs no glue. |
| [`sheet.test.ts`](sheet.test.ts) | `sheetShape` union keys; 5000×100 cap. |
| [`sheet.ts`](sheet.ts) | `renderSheet` / `sheetShape` (app.js:8771-8802). |
| [`state.ts`](state.ts) | Lane-local M-03 signals. Not promoted into `stores/`. |
| [`thumbs.ts`](thumbs.ts) | Tile thumbs from a bounded per-version preview cache, `parseMolPoints` / `molSvg`. |
| [`thumbs.test.ts`](thumbs.test.ts) | One read per artifact version, error pages and failed reads never kept, LRU bound. |
| [`types.ts`](types.ts) | Artifact DTO, page size 50/100, TEXT_EXT / MOL_EXT. |
| [`ui.ts`](ui.ts) | Files grid, Viewer, `openViewer`, ⌘K/deep-link hit. Provided `version_id` never silent-latest. |
| [`ui.test.ts`](ui.test.ts) | Deep-link apply / `openViewer` exact pin / stale does not open latest. |
| [`editor.ts`](editor.ts) | Version-bound drafts, bounded UTF-8 storage, checksum reads and conditional saves. |
| [`editor-view.ts`](editor-view.ts) | Editor controls and read-only conflict/unknown-result recovery. |
| [`editor.test.ts`](editor.test.ts) | Load, version, save, reconciliation and draft capacity contracts. |

- [`validation.ts`](validation.ts): Validate every required metadata-export version and artifact row without dropping malformed records.
