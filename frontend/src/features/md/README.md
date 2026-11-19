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
| [`render.ts`](render.ts) | `renderMd` / `mdInline` / `mdCodeBlock` (its Copy label and title through `t()`). F-21 wraps tables in `.md-table-wrap`. A one-segment `/api/artifacts/<id>` link stored by 0.2.0 (a 404 on every contract-v1 gateway) is rewritten to `/api/v1/artifacts/<id>`; every other href is left as written. Blockquote and list nesting stop at 32 levels (deeper renders flat) so no answer can overflow the stack; bracket text excludes `[`, so a long run of `[` is linear. `resources: "none"` is the notebook-only mode (F7): no image node (an image becomes its escaped alt text) and no navigable link (a link becomes plain `text (url)`), in nested list/quote/table paths as well; `"default"`/omitted keeps the previous output byte for byte. Opt-in for Markdown Artifacts only (ART-2): `math: true` tokenizes `$…$`, `$$…$$`, `\(…\)` and `\[…\]` before escaping and emphasis (Pandoc's rule for a single `$`, so currency stays text; `\$` is a literal dollar; an unclosed block never swallows the document) into inert `.md-math` elements; `relativeImages: true` turns an image whose target is a relative path (such as `growth.png`) into `<img data-md-src>` with no `src`, for the viewer to resolve. |
| [`math.ts`](math.ts) | ART-2 lazy TeX typesetting: `typesetMath(root)` loads KaTeX plus its stylesheet and fonts as one separate chunk the first time a document has `.md-math` elements, then renders each with `trust: false`, bounded `maxExpand`/`maxSize` and `throwOnError: false`; a failed load or a refused formula keeps its source (`md-math-raw`). |
| [`math.test.ts`](math.test.ts) | No load without math; the inert options and per-element display mode; the source survives a failed load, a refused formula and a replaced view. |
| [`render.test.ts`](render.test.ts) | Five XSS samples from `tests/browser_smoke.mjs`; scheme whitelist; legacy Artifact-link rewrite; table wrap; 12,000 nested quotes or list levels render without overflowing the stack; a 60,000-`[` run is linear; an unbalanced `[` stays text; the notebook-only `resources:"none"` mode turns images into escaped alt text and links into plain `text (url)` in every nested path, and default rendering is unchanged. |
