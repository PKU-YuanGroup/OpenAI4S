# frontend/src/features/artifacts

[中文说明](README_zh.md)

F-17 artifacts + Files (M-03). Version cache, Files search/filter/pagination/deep link, renderer catalog onto `window.OpenAI4SScientificRenderers` (null defense kept), and the ten scientific renderer glues. Does not edit `stores/` or `compat/window-exports.ts` above the lane-additions marker.

## Files

| File | Responsibility |
| --- | --- |
| [`api.ts`](api.ts) | Same-origin `api()`, `ApiError`, `bytes`, `looksBinary` / `controlDense`, `el`/`icon` (`el` is shared with islands), `isReady` window calls. Two bounded read entries: strict `fetchArtifactText(url, opts?)` (complete file or rejection) and prefix `readArtifactPrefix(url, {maxBytes, …})` (thumbnails only, UTF-8 boundary respected); F1 constants, `ArtifactReadError.kind` (`too_large`/`timeout`/`aborted`), HTTP status as `ApiError`, per-render preview AbortController (`beginPreviewRead`/`abortPreviewReads`/`abortDetachedPreviews`), and the frozen display limits with `unicodeSafeSlice` / `truncateMarkdown`. |
| [`read-budget.test.ts`](read-budget.test.ts) | F1/F2 read contracts: strict complete-or-reject at the limit, forged/oversized/missing Content-Length, multi-chunk overflow cancels the reader, HTTP status as ApiError, timeout, external abort, no-body fallback rules, prefix truncation without a replacement character at the UTF-8 boundary. |
| [`api.test.ts`](api.test.ts) | `looksBinary`: one-line sequences stay text; base64 blobs, control characters and escape dumps do not; one linear pass over the first MiB. |
| [`artifacts.css`](artifacts.css) | Files toolbar / filter / Load more / stale-version banner. |
| [`boot.test.ts`](boot.test.ts) | DOM mount ordering and initial-route-before-deep-link regression coverage. |
| [`boot.ts`](boot.ts) | `bootArtifacts` / `installArtifacts`. Assigns `parseTable` + `renderSheet`. |
| [`cache.test.ts`](cache.test.ts) | Version cache, in-place dock mutate, stale `loadArtifacts`. |
| [`cache.ts`](cache.ts) | `artifactCacheKey` / `syncArtifactVersion` / `artUrl` (exact version never uses latest); `previewSlice(text, limit)` is the shared F3 display cut (`{shown, truncated}`, never splits a surrogate pair) used by every limited preview and by the legacy table fallback. |
| [`catalog.test.ts`](catalog.test.ts) | UMD null defense, compatibility renderer ids, PDB CA points. |
| [`catalog.ts`](catalog.ts) | `scientificRenderers()` empty-value defense, catalog + descriptor fetch; a failed catalog is never cached as empty, keeps `rendererCatalog.value` null, clears its in-flight promise and rejects with the original error, and descriptors resolved from it are dropped. Concurrent callers share one `/renderers` request; a successful catalog stays cached. Compatibility mapping keeps `.ipynb` on the notebook renderer rather than the generic text path. |
| [`copy.ts`](copy.ts) | M-03 search/filter/deep-link copy, the provenance Environment panel's no-snapshot and unknown/not-applicable package-count strings, read-failure / completeness states, and the read-only notebook labels and omission notices (does not rewrite generated i18n). |
| [`deeplink.test.ts`](deeplink.test.ts) | Query parse; exact version; no silent latest fallback. |
| [`deeplink.ts`](deeplink.ts) | `?artifact=&version_id=` parse/serialize/resolve. Empty versions → not-found. |
| [`events.test.ts`](events.test.ts) | `artifact_created` version sync + live figure; stub `nbRender` is skipped. |
| [`events.ts`](events.ts) | Remaining 32-line `artifact_created` body (app.js:5314-5346). |
| [`files-index.test.ts`](files-index.test.ts) | Cursor lifecycle (filter/project drop), no array-route fallback, first page ≤50. |
| [`files-index.ts`](files-index.ts) | B-06 `GET .../artifact-index` only. Filter fingerprint drops stale cursors. |
| [`http-stub.ts`](http-stub.ts) | JSON `Response` stand-in for Vitest, with a real body stream so bounded reads behave as in the browser. |
| [`index.ts`](index.ts) | Public re-exports. |
| [`load.ts`](load.ts) | `loadArtifacts` / `loadProjectArtifacts` / `setFilesScope`. |
| [`preview.ts`](preview.ts) | Scoped exact-version grant upgrade with a per-version grant cache, the retargeted caption, and the inert `/preview/<id-or-version>` fallback once a grant is refused. |
| [`preview.test.ts`](preview.test.ts) | Production renderer grant lifecycle, grant reuse and expiry, origin/path rejection, exact-version fallback, non-loopback inertness, and detached iframe regression tests. |
| [`science-results.ts`](science-results.ts) | Strict validation and bounded read-only view of a saved science-search envelope: old/new source fields, hidden/conflicting provenance markers, 50-record pages up to 500 with no remote fetch, sanitized http(s) record links, and the raw-JSON tab on the same exact version. |
| [`science-results.test.ts`](science-results.test.ts) | Envelope rejection (any JSON with a `result` property), canonical/legacy keys and conflicts, hidden digests, link sanitization, page caps, raw-JSON tab and no network read. |
| [`notebook.ts`](notebook.ts) | Read-only nbformat-4 `.ipynb` preview: strict envelope/shape validation, 200 cells / 20 outputs / 64 KiB text / 8 MiB file, PNG signature + IHDR + 2 MiB / 16 M-pixel budget with at most 2 decodes in flight, resource-free Markdown, unsupported rich MIME notices, no execution and no Host/external calls. |
| [`notebook.test.ts`](notebook.test.ts) | Envelope/type rejection, cell+output caps, 64 KiB UTF-8 truncation, PNG validation and byte/pixel limits, unsupported rich outputs, close cancels read and revokes image URLs. |
| [`renderers.ts`](renderers.ts) | Compatibility-mode info row carries a Retry that re-requests the catalog and descriptor in place (no page reload; a still-failing read degrades again). Eleven scientific glues + image/pdf/html/3Dmol/notebook islands via `isReady`. PDF iframe gets F-18 `sandbox=""`. Bounded, cancellable reads; the frozen display limits are cut once through `previewSlice`; a `partial` preview carries a persistent `role="status"` notice of how much is shown plus the exact-version download (Markdown gets one notice above and one below the `.md` body; raw source keeps "show complete text"); a failed read is the third state and shows the classified failure card. Version freeze (F4): the descriptor entry computes `target = artifactMetadataTarget(a)` and `url = artUrl(target)` once and every renderer/download uses only those; a row with no version identity says "latest version (no fixed version identifier)". |
| [`renderers.test.ts`](renderers.test.ts) | Text renderer: one read per render (no download/text cycle); sequences render as source; JSON is judged by control characters only; a late descriptor for a replaced body runs no glue. |
| [`sheet.test.ts`](sheet.test.ts) | `sheetShape` union keys; 5000×100 cap. |
| [`sheet.ts`](sheet.ts) | `renderSheet` / `sheetShape` (app.js:8771-8802). |
| [`state.ts`](state.ts) | Lane-local M-03 signals. Not promoted into `stores/`. |
| [`thumbs.ts`](thumbs.ts) | Tile thumbs from a bounded per-version preview cache, `parseMolPoints` / `molSvg`. Global FIFO prefix queue (at most four reads, shared 15 s deadline), every tile DOM element a consumer of its cache key, `pruneThumbReads()` aborts only reads whose consumers have all left; truncated text/molecule tiles say "partial preview", a truncated data tile shows columns only (never a row count). |
| [`thumbs.test.ts`](thumbs.test.ts) | One read per artifact version, error pages and failed reads never kept, LRU bound, read queue ≤4, leaving cancels unowned reads, partial text prefix. |
| [`types.ts`](types.ts) | Artifact DTO, page size 50/100, TEXT_EXT / MOL_EXT. |
| [`ui.ts`](ui.ts) | Files grid, dock tabs, `openViewer`, deep links; `renderViewer` delegates to the islands Viewer. Provided `version_id` never silent-latest. |
| [`ui.test.ts`](ui.test.ts) | Deep-link apply / `openViewer` exact pin / stale does not open latest. |
| [`editor.ts`](editor.ts) | Version-bound drafts, bounded UTF-8 storage, checksum reads and conditional saves. |
| [`editor-view.ts`](editor-view.ts) | Editor controls and read-only conflict/unknown-result recovery. |
| [`editor.test.ts`](editor.test.ts) | Load, version, save, reconciliation and draft capacity contracts. |

- [`validation.ts`](validation.ts): Validate every required metadata-export version and artifact row without dropping malformed records.
