# frontend/src/features/autocomplete

[中文说明](README_zh.md)

F-12 composer (`@` / `#` / `/`) and right-dock editor autocomplete. Keyword lists come from F-08 `editorKeywords` in `features/md/highlight.ts` — this lane does not keep a private EDKW table. Window names (`ac`, `edacTeardown`) are assigned here, not left as F-05 stubs.

## Files

| File | Responsibility |
| --- | --- |
| [`detect.ts`](detect.ts) | `acDetectFrom` (`@/#/`) and `edacDetectFrom` (ASCII identifier ≥2). |
| [`detect.test.ts`](detect.test.ts) | Trigger parsing: boundary, empty query, mid-token, Han/IME. |
| [`rank.ts`](rank.ts) | Composer filter + cap 8; editor keywords-first then buffer identifiers. An `@` file row inserts the bare filename and carries its version as `mention`. |
| [`rank.test.ts`](rank.test.ts) | Ranking, identity-dedupe, F-08 keyword table, no private EDKW; a file row's version is pinned beside the name, never in it. |
| [`mentions.ts`](mentions.ts) | The `@` pins a draft carries (ART-16): a pick writes `@name` and keeps `{filename, version_id}` here; `send()` posts them as `mentions`. A pin counts only while its `@name` token is in the text (the server applies the same rule); a name picked more often than it now appears keeps its latest picks. `stripRefVersions` shows a text-form `@name#v-<id>` as `@name`. |
| [`mentions.test.ts`](mentions.test.ts) | Token boundaries (punctuation, CJK, spaced and regex-character names, the text form), pin selection after deletes and re-picks, the bound, clear/restore, token blanking, and the display strip. |
| [`composer.ts`](composer.ts) | Live `ac` controller and `#composer-ac` popup. `@` requests one artifact-index page (filename query, limit 20, 150 ms debounce) and drops a response whose project, session, query, or update generation is stale; the popup stays capped at 8, is anchored on the token at the caret, and inserts `@name` while pinning the row's version in `mentions.ts`. While a search is pending (debounce or request), Enter, Tab, and the send button wait for the page and then complete the `@` token they were pressed on; the highlighted file is kept by artifact identity when the page brings a newer version; a request still open after 8 s shows the failure hint. Rows scroll inside `.ac-list`, so the hint stays visible below them. |
| [`catalog.ts`](catalog.ts) | `loadSkillsCatalog`: the one skills-catalog loader for `/` completions and `send()`'s `/skill` directive (the palette can adopt it). Concurrent callers share one request; a failed read rejects and is not stored, so the next caller retries. |
| [`composer.test.ts`](composer.test.ts) | Out-of-order file lists: the newest keystroke wins; the replacement starts at the token at the caret after the load; a caret that left the token closes the popup. `/` completions return after a failed catalog read; the shared catalog stores no failure and dedupes a concurrent load. |
| [`editor.ts`](editor.ts) | Per-editor controller, caret mirror, `execCommand('insertText')`. Editors are bound at their mount point (`bindEditorAutocomplete`); `watchEditAreas` binds those present once, with no document-wide observer. |
| [`index.ts`](index.ts) | `installAutocomplete` assigns window names; binds composer + `.edit-area`. |
| [`install.test.ts`](install.test.ts) | `ac` is the live object; `edacTeardown` passes `isReady`; no MutationObserver watches the document for editor textareas. |
