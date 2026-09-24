# frontend/src/features/md

[中文说明](README_zh.md)

Markdown + highlight kernel. Whole-string `esc` then markup replacement; scheme whitelist `(https?:|mailto:|/|#)`; no marked / DOMPurify. `.tok-*` class names are unchanged.

## Files

| File | Responsibility |
| --- | --- |
| [`index.ts`](index.ts) | Re-exports, assigns the contract name `renderMd` onto `window`, and binds the code-block Copy listener on `document`. |
| [`copy.ts`](copy.ts) | The code-block Copy button: one delegated `.cb-copy` click listener (app.js had it; the port lost it). "Copied" only for a confirmed `copyText` write; a refused copy says so (feature-local copy) and selects the code. |
| [`copy.test.ts`](copy.test.ts) | The button's label and title come from the dictionary; a click anywhere on it copies its own block through one idempotent listener; Copied then back; a refused copy is reported, not ticked. |
| [`esc.ts`](esc.ts) | `esc` (`&<>"`) and `escQuote` (attribute discipline). |
| [`esc.test.ts`](esc.test.ts) | Quote-escape order; old `&<>` assertions still hold. |
| [`highlight.ts`](highlight.ts) | mdHighlight scanner; `_OC_KW ∪ MD_KEYWORDS`; EDKW derived from the same table. |
| [`highlight.test.ts`](highlight.test.ts) | `.tok-*` names, keyword union, EDKW derivation. |
| [`render.ts`](render.ts) | `renderMd` / `mdInline` / `mdCodeBlock` (its Copy label and title through `t()`). F-21 wraps tables in `.md-table-wrap`. A one-segment `/api/artifacts/<id>` link stored by 0.2.0 (a 404 on every contract-v1 gateway) is rewritten to `/api/v1/artifacts/<id>`; every other href is left as written. |
| [`render.test.ts`](render.test.ts) | Five XSS samples from `tests/browser_smoke.mjs`; scheme whitelist; legacy Artifact-link rewrite; table wrap. |
