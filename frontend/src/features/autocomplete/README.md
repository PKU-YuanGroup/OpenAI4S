# frontend/src/features/autocomplete

[中文说明](README_zh.md)

F-12 composer (`@` / `#` / `/`) and right-dock editor autocomplete. Keyword lists come from F-08 `editorKeywords` in `features/md/highlight.ts` — this lane does not keep a private EDKW table. Window names (`ac`, `edacTeardown`) are assigned here, not left as F-05 stubs.

## Files

| File | Responsibility |
| --- | --- |
| [`detect.ts`](detect.ts) | `acDetectFrom` (`@/#/`) and `edacDetectFrom` (ASCII identifier ≥2). |
| [`detect.test.ts`](detect.test.ts) | Trigger parsing: boundary, empty query, mid-token, Han/IME. |
| [`rank.ts`](rank.ts) | Composer filter + cap 8; editor keywords-first then buffer identifiers. |
| [`rank.test.ts`](rank.test.ts) | Ranking, identity-dedupe, F-08 keyword table, no private EDKW. |
| [`composer.ts`](composer.ts) | Live `ac` controller, project-file cache, `#composer-ac` popup. |
| [`editor.ts`](editor.ts) | Per-editor controller, caret mirror, `execCommand('insertText')`. |
| [`index.ts`](index.ts) | `installAutocomplete` assigns window names; binds composer + `.edit-area`. |
| [`install.test.ts`](install.test.ts) | `ac` is the live object; `edacTeardown` passes `isReady`. |
