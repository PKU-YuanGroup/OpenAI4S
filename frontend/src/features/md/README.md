# frontend/src/features/md

[中文说明](README_zh.md)

Markdown + highlight kernel. Whole-string `esc` then markup replacement; scheme whitelist `(https?:|mailto:|/|#)`; no marked / DOMPurify. `.tok-*` class names are unchanged.

## Files

| File | Responsibility |
| --- | --- |
| [`index.ts`](index.ts) | Re-exports, and assigns the contract name `renderMd` onto `window`. |
| [`esc.ts`](esc.ts) | `esc` (`&<>"`) and `escQuote` (attribute discipline). |
| [`esc.test.ts`](esc.test.ts) | Quote-escape order; old `&<>` assertions still hold. |
| [`highlight.ts`](highlight.ts) | mdHighlight scanner; `_OC_KW ∪ MD_KEYWORDS`; EDKW derived from the same table. |
| [`highlight.test.ts`](highlight.test.ts) | `.tok-*` names, keyword union, EDKW derivation. |
| [`render.ts`](render.ts) | `renderMd` / `mdInline` / `mdCodeBlock`. F-21 wraps tables in `.md-table-wrap`. |
| [`render.test.ts`](render.test.ts) | Five XSS samples from `tests/browser_smoke.mjs`; scheme whitelist; table wrap. |
