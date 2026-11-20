# frontend/src/features/chrome

[中文说明](README_zh.md)

F-20 workbench chrome: team mode, the modal focus trap, the ⌘K palette, upload / mic, layout density, and column resizers. Team modals go through `openModalEl` / `closeModalEl` (the old IIFEs bypassed the trap). Palette Artifact hits follow M-03 (session first, then exact `version_id`).

## Files

| File | Responsibility |
| --- | --- |
| [`api.ts`](api.ts) | Same-origin JSON helper (`/api/v1`, `ApiError`). |
| [`attachments.ts`](attachments.ts) | M-10 composer attachment tray (`#composer-attachments`): each chosen file as a chip with its name, size or state, and a × that takes it back (a file the upload created is deleted from the session; a new version of an existing file only leaves the tray), under a line saying the files go with the next message. Send takes the session's settled entries and a refused message puts them back. |
| [`attachments.test.ts`](attachments.test.ts) | Chips per state, per-composer visibility (a first upload's files follow its new session), × deleting only an Artifact the upload created, a refused delete keeping the chip, × while uploading, take/restore on send. |
| [`chrome.css`](chrome.css) | Lane styles for palette / team / resizer / composer attachment class names. |
| [`clipboard.test.ts`](clipboard.test.ts) | The async API is called as a method, a refusal falls back to a selection copy, and nothing available resolves false. |
| [`clipboard.ts`](clipboard.ts) | `copyText()`: the one clipboard write behind every Copy control; resolves `true` only for a confirmed write. |
| [`dom.ts`](dom.ts) | `$` / `el` / `icon` / `ago` / `localDateTime` / `localClock` / `hint` / `grow`. `icon` draws from the shared `icons/paths.ts` table. `ago` / `agoMs` say relative time in the UI language (刚刚 / 3 分钟前, just now / 3m). |
| [`host.ts`](host.ts) | `isReady` window-capability lookups. Does not import `window-exports`. |
| [`index.ts`](index.ts) | `bootChrome()`: window assignments, keydown, binds, `bootTeam`. Each step is isolated, so one that throws cannot leave the rest unbound. |
| [`layout.test.ts`](layout.test.ts) | `os-layout` persistence, compact/wide classes, column-width clamp, blocked site storage, and `bootChrome()` binding every later step after one throws while leaving `#files-btn` to artifacts. |
| [`layout.ts`](layout.ts) | `applyLayout` / `setLayout`. Key `os-layout`. |
| [`mic.ts`](mic.ts) | SpeechRecognition dictation onto `#composer`. |
| [`modal.test.ts`](modal.test.ts) | Trap stack, Tab cycle, Esc, focus restore, team fallback selectors, a drag that ends on the scrim not closing, and the deferred first focus leaving a control already focused inside the modal. |
| [`modal.ts`](modal.ts) | Verbatim focus trap (stack / Tab / Esc / restore). The first focus waits a frame for the content and is skipped when focus already moved inside the modal. The scrim closes only when the press started on it too. |
| [`palette.test.ts`](palette.test.ts) | M-03 Artifact hit, stub-safe `isReady`, out-of-order `PAL.gen`, a failed skills catalog retried rather than cached, a failed session open handled rather than left unhandled, Enter acting on the typed query while `/search` is in flight. |
| [`palette.ts`](palette.ts) | ⌘K palette. Artifact hits open session then exact version. A query's local commands show at once; Enter never picks from an earlier query's list. |
| [`resizer.ts`](resizer.ts) | Sidebar / dock column drag. Keys `os-side-w` / `os-dock-w`. The handle's tooltip is a static `data-i18n-title` label, repainted when the dictionaries load and on a language switch. |
| [`resizer.i18n.test.ts`](resizer.i18n.test.ts) | The resizer tooltip is never the bare key `resizer.drag`, and follows the dictionary load and a language switch. |
| [`team-account.ts`](team-account.ts) | The signed-in user's own account dialogs, opened from the user menu: Change password (`POST /auth/me/password`, current password required) and My model keys (`GET`/`PUT`/`DELETE /auth/me/llm-key`; a saved key is never shown again). Also `teamDialog`, the small team modal with a real close icon. |
| [`team-admin.ts`](team-admin.ts) | The Team admin panel's Users section: add a user (a generated password is shown once), and per row Disable / Enable / Reset password, each destructive one confirmed in place rather than by a native dialog; nothing on the admin's own row. Also the shared labels: `auditActionLabel`, `userLabel`. |
| [`team-chip.tsx`](team-chip.tsx) | `TeamChip`: the signed-in user (opens the user menu) and the Team files entry, rendered by the Shell in the dashboard header and the sidebar foot. Nothing in single-user mode with no data roots. |
| [`team-copy.ts`](team-copy.ts) | `teamT`: the team surface's zh/en copy (user menu, roles, account dialogs, the team-mode missing-session line). |
| [`team-identity.ts`](team-identity.ts) | The `teamUser` signal (who is signed in, from `/auth/me`; `null` in single-user mode), `teamFilesAvailable`, and `isTeamAdmin()` / `isTeamMember()`, so a view such as Settings → Compute can skip what a member would be refused. |
| [`team.test.ts`](team.test.ts) | The chip in both placements, the user menu per role, sign-out without a native confirm, nothing appended to `<body>` but the modals, admin panel, guest redirect, trap on team modals, audit times from epoch milliseconds, a late directory read not repainting over a newer one, admin section headings in the interface language. |
| [`team.ts`](team.ts) | `/auth/me` probe (fills `teamUser`), the user menu (`teamMenuItems`, `openUserMenu`, `signOut`), and the admin/files modals, which use the trap; the admin panel's sections show names beside ids and speak the interface language. Only the latest directory read renders, and it alone moves the Upload target. |
| [`upload.test.ts`](upload.test.ts) | Selection-time destination, the four-clause batch match, single flight, retry supersession, 64-failure bound; UP-3: every upload is first sent with `if_exists: "error"`, and keep both / replace / cancel re-send or skip without recording a failure. |
| [`upload.ts`](upload.ts) | File input / paste / drop uploads (each one shown in the attachment tray from selection), `UPLOAD_STATE`, the first-session single flight (with `reuseEmpty` it asks `POST /frames` for the project's newest unused session), and the send barrier's `waitForPendingUploads`. A taken name is refused first (`if_exists: "error"`) and the answer from `upload-conflict.ts` decides the re-send (UP-3); a cancelled file is `skipped`, not a failure. A multi-file selection uploads one file at a time, each waiting out a busy session (409 `trusted_capture_busy`), with per-file progress and one closing summary (UP-1/UP-2). |
| [`upload-conflict.ts`](upload-conflict.ts) | UP-3 same-name prompt: "keep both" (`name-2.ext`), "replace as a new version" or "cancel"; dialogs of concurrent uploads are queued one at a time, "do the same for the rest" answers a batch, Escape/scrim/any other close is a cancel; feature-local zh/en copy (`uploadT`). |
| [`upload-conflict.test.ts`](upload-conflict.test.ts) | Numbered names mirror the server, one dialog at a time in order, per-batch memory, and a prompt that cannot be shown cancels. |
