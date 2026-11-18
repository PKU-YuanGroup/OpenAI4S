# frontend/src/features/autocomplete

[中文说明](README_zh.md)

F-12 composer (`@` / `#` / `/`) and right-dock editor autocomplete. Keyword lists come from F-08 `editorKeywords` in `features/md/highlight.ts` — this lane does not keep a private EDKW table. Window names (`ac`, `edacTeardown`) are assigned here, not left as F-05 stubs.

## Files

| File | Responsibility |
| --- | --- |
| [`detect.ts`](detect.ts) | `acDetectFrom` (`@/#/`; a `/` also straight after CJK text, flagged `afterCjk`) and `edacDetectFrom` (ASCII identifier ≥2). |
| [`detect.test.ts`](detect.test.ts) | Trigger parsing: boundary, empty query, mid-token, Han/IME, a `/` after CJK text (and not `@`/`#` or a URL). |
| [`copy.ts`](copy.ts) | `acT`: the `/` skill picker's count and "no match" lines (feature-local zh/en copy). |
| [`rank.ts`](rank.ts) | Composer filter + cap 8 for `@`/`#`; `rankSkillItems` for `/`: every match, name before word-start before description, Chinese research keywords and hyphen/space/underscore spellings, ties in catalog order with curated skills first. Editor keywords-first then buffer identifiers. |
| [`rank.test.ts`](rank.test.ts) | Ranking, identity-dedupe, F-08 keyword table, no private EDKW; the skill ranking (`/lit` puts literature-review first, Chinese keywords and description words find skills, an empty query keeps every skill). |
| [`composer.ts`](composer.ts) | Live `ac` controller and `#composer-ac` popup, hung above the composer card (its `bottom` and height measured on each render) so it never covers the line being typed. `@` requests one artifact-index page (filename query, limit 20, 150 ms debounce) and drops a response whose project, session, query, or update generation is stale; the popup stays capped at 8, is anchored on the token at the caret, and inserts `name#version`. While a search is pending (debounce or request), Enter, Tab, and the send button wait for the page and then complete the `@` token they were pressed on; the highlighted file is kept by artifact identity when the page brings a newer version; a request still open after 8 s shows the failure hint. Rows scroll inside `.ac-list`, so the hint stays visible below them. `/` shows up to 60 ranked skills with a count line, or a "no match" line (Escape closes it); a skill picked straight after CJK text is inserted with a space before it so `send()` reads it. Rows carry `listbox`/`option` roles and the full description as a tooltip. |
| [`catalog.ts`](catalog.ts) | `loadSkillsCatalog`: the one skills-catalog loader for `/` completions and `send()`'s `/skill` directive (the palette can adopt it). Concurrent callers share one request; a failed read rejects and is not stored, so the next caller retries. |
| [`composer.test.ts`](composer.test.ts) | Out-of-order file lists: the newest keystroke wins; the replacement starts at the token at the caret after the load; a caret that left the token closes the popup. `/` completions return after a failed catalog read; the shared catalog stores no failure and dedupes a concurrent load. The `/` picker lists more than eight rows with a count, says when nothing matched, finds skills by Chinese keyword and description, opens after Chinese text, and leaves "和/或" prose alone. |
| [`editor.ts`](editor.ts) | Per-editor controller, caret mirror, `execCommand('insertText')`. Editors are bound at their mount point (`bindEditorAutocomplete`); `watchEditAreas` binds those present once, with no document-wide observer. |
| [`index.ts`](index.ts) | `installAutocomplete` assigns window names; binds composer + `.edit-area`. |
| [`install.test.ts`](install.test.ts) | `ac` is the live object; `edacTeardown` passes `isReady`; no MutationObserver watches the document for editor textareas. |
