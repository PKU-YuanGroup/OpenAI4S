# frontend/src/features/chrome

[中文说明](README_zh.md)

F-20 workbench chrome: team mode, the modal focus trap, the ⌘K palette, upload / notes / mic, layout density, and column resizers. Team modals go through `openModalEl` / `closeModalEl` (the old IIFEs bypassed the trap). Palette Artifact hits follow M-03 (session first, then exact `version_id`).

## Files

| File | Responsibility |
| --- | --- |
| [`api.ts`](api.ts) | Same-origin JSON helper (`/api/v1`, `ApiError`). |
| [`chrome.css`](chrome.css) | Lane styles for palette / notes / team / resizer class names. |
| [`clipboard.test.ts`](clipboard.test.ts) | The async API is called as a method, a refusal falls back to a selection copy, and nothing available resolves false. |
| [`clipboard.ts`](clipboard.ts) | `copyText()`: the one clipboard write behind every Copy control; resolves `true` only for a confirmed write. |
| [`dom.ts`](dom.ts) | `$` / `el` / `icon` / `ago` / `hint` / `grow`. `icon` draws from the shared `icons/paths.ts` table. |
| [`host.ts`](host.ts) | `isReady` window-capability lookups. Does not import `window-exports`. |
| [`index.ts`](index.ts) | `bootChrome()`: window assignments, keydown, binds, `bootTeam`. Each step is isolated, so one that throws cannot leave the rest unbound. |
| [`layout.test.ts`](layout.test.ts) | `os-layout` persistence, compact/wide classes, column-width clamp, blocked site storage, and `bootChrome()` binding every later step after one throws. |
| [`layout.ts`](layout.ts) | `applyLayout` / `setLayout`. Key `os-layout`. |
| [`mic.ts`](mic.ts) | SpeechRecognition dictation onto `#composer`. |
| [`modal.test.ts`](modal.test.ts) | Trap stack, Tab cycle, Esc, focus restore, team fallback selectors, and a drag that ends on the scrim not closing. |
| [`modal.ts`](modal.ts) | Verbatim focus trap (stack / Tab / Esc / restore). The scrim closes only when the press started on it too. |
| [`notes.ts`](notes.ts) | Project notes in the Files dock. |
| [`palette.test.ts`](palette.test.ts) | M-03 Artifact hit, stub-safe `isReady`, out-of-order `PAL.gen`, a failed skills catalog retried rather than cached, a failed session open handled rather than left unhandled, Enter acting on the typed query while `/search` is in flight. |
| [`palette.ts`](palette.ts) | ⌘K palette. Artifact hits open session then exact version. A query's local commands show at once; Enter never picks from an earlier query's list. |
| [`resizer.ts`](resizer.ts) | Sidebar / dock column drag. Keys `os-side-w` / `os-dock-w`. The handle's tooltip is a static `data-i18n-title` label, repainted when the dictionaries load and on a language switch. |
| [`resizer.i18n.test.ts`](resizer.i18n.test.ts) | The resizer tooltip is never the bare key `resizer.drag`, and follows the dictionary load and a language switch. |
| [`team.test.ts`](team.test.ts) | Identity chip, admin panel, guest redirect, trap on team modals. |
| [`team.ts`](team.ts) | Team IIFEs. `/auth/me` probe; admin/files modals use the trap. |
| [`upload.test.ts`](upload.test.ts) | Selection-time destination, the four-clause batch match, single flight, retry supersession, 64-failure bound. |
| [`upload.ts`](upload.ts) | File input / paste / drop uploads, `UPLOAD_STATE`, the first-session single flight, and the send barrier's `waitForPendingUploads`. |
