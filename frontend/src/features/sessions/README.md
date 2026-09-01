# frontend/src/features/sessions

[中文说明](README_zh.md)

F-13 dashboard / projects / sessions. Pagination and sort are pure functions. Window contract names (`fetchAllMessages`, `fetchOlderMessages`, `fetchRecentMessages`, `openConversation`, `renderMessageRefChips`, `renderComposerRefChips`) are assigned here. Capability checks use `isReady` from `compat/stub.ts` — this directory does not import `window-exports.ts`.

## Files

| File | Responsibility |
| --- | --- |
| [`actions.ts`](actions.ts) | Session menu, share dialog, import/export, title, cancel. app.js:7411-7793. |
| [`api.ts`](api.ts) | `API`, `ApiError`, `api()`, `apiErrorText`. app.js:84-119. |
| [`binds.ts`](binds.ts) | Late bindings so dashboard and conversation do not import each other. |
| [`boot.ts`](boot.ts) | Window exports, `setLoadSessionsImpl`, workbench click wiring. |
| [`chrome.test.ts`](chrome.test.ts) | Hint error prefix (`错误：` / `Error: `) without a new i18n key. |
| [`chrome.ts`](chrome.ts) | `hint`, disconnect banner, `openMenu` Esc/`role=menu`, keyboard activate. |
| [`conversation.ts`](conversation.ts) | `newSession`, `routeInitialView`. Re-exports `openConversation` (F-10) and `resumeWatch` (F-11) rather than keeping this lane's duplicates. |
| [`conversation.identity.test.ts`](conversation.identity.test.ts) | Those re-exports are the same function objects the owning lanes install. |
| [`dashboard.ts`](dashboard.ts) | Home list, example CTA poll bound to view lifecycle, dash poll. |
| [`dom.ts`](dom.ts) | `$` / `el` / `ago` / `navURL` / composer helpers. |
| [`icon.ts`](icon.ts) | Line icons used by this lane's menus and rows. |
| [`index.ts`](index.ts) | Public re-exports; installs window names on import. |
| [`lane.ts`](lane.ts) | `isReady` wrapper for later-lane window names. |
| [`load.ts`](load.ts) | `loadSessions` cursor walk, folders, `renderSessions`. |
| [`messages.ts`](messages.ts) | `fetchRecentMessages` / `fetchOlderMessages` / `fetchAllMessages` / earlier bar. |
| [`paging.test.ts`](paging.test.ts) | Pagination constants, session sort, walk/dedupe, dashboard filters. |
| [`paging.ts`](paging.ts) | `MESSAGE_PAGE_SIZE=300`, `SESSION_MAX_PAGES=50`, sort/walk/filter. |
| [`projects.ts`](projects.ts) | Project menu/modal/research view, `sanitizeProjectLineage`. |
| [`transcript.ts`](transcript.ts) | `renderStored`, ref chips, empty-session starters, message actions. |
