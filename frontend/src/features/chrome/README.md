# frontend/src/features/chrome

[中文说明](README_zh.md)

F-20 workbench chrome: team mode, the modal focus trap, the ⌘K palette, upload / notes / mic, layout density, and column resizers. Team modals go through `openModalEl` / `closeModalEl` (the old IIFEs bypassed the trap). Palette Artifact hits follow M-03 (session first, then exact `version_id`).

## Files

| File | Responsibility |
| --- | --- |
| [`api.ts`](api.ts) | Same-origin JSON helper (`/api/v1`, `ApiError`). |
| [`chrome.css`](chrome.css) | Lane styles for palette / notes / team / resizer class names. |
| [`dom.ts`](dom.ts) | `$` / `el` / `icon` / `ago` / `hint` / `grow`. |
| [`host.ts`](host.ts) | `isReady` window-capability lookups. Does not import `window-exports`. |
| [`index.ts`](index.ts) | `bootChrome()`: window assignments, keydown, binds, `bootTeam`. |
| [`layout.test.ts`](layout.test.ts) | `os-layout` persistence, compact/wide classes, column-width clamp. |
| [`layout.ts`](layout.ts) | `applyLayout` / `setLayout`. Key `os-layout`. |
| [`mic.ts`](mic.ts) | SpeechRecognition dictation onto `#composer`. |
| [`modal.test.ts`](modal.test.ts) | Trap stack, Tab cycle, Esc, focus restore, team fallback selectors. |
| [`modal.ts`](modal.ts) | Verbatim focus trap (stack / Tab / Esc / restore). |
| [`notes.ts`](notes.ts) | Project notes in the Files dock. |
| [`palette.test.ts`](palette.test.ts) | M-03 Artifact hit, stub-safe `isReady`, out-of-order `PAL.gen`. |
| [`palette.ts`](palette.ts) | ⌘K palette. Artifact hits open session then exact version. |
| [`resizer.ts`](resizer.ts) | Sidebar / dock column drag. Keys `os-side-w` / `os-dock-w`. |
| [`team.test.ts`](team.test.ts) | Identity chip, admin panel, guest redirect, trap on team modals. |
| [`team.ts`](team.ts) | Team IIFEs. `/auth/me` probe; admin/files modals use the trap. |
| [`upload.ts`](upload.ts) | File input / paste / drop uploads. |
