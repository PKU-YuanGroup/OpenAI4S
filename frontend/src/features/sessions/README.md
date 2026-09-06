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
| [`conversation.newsession.test.ts`](conversation.newsession.test.ts) | `newSession` releases the previous conversation (unsubscribe, notebook caches) before publishing the new id, and on the shared path resolves only after the conversation has opened. |
| [`actions.cancel.test.ts`](actions.cancel.test.ts) | A cancel ack is applied to "Stopping…" only when it names the execution this client is still running. |
| [`dashboard.ts`](dashboard.ts) | Home list, project search / load-more / retry, example CTA poll bound to view lifecycle, dash poll. |
| [`dom.ts`](dom.ts) | `$` / `el` / `ago` / `navURL` / composer helpers. |
| [`icon.ts`](icon.ts) | Line icons used by this lane's menus and rows. |
| [`index.ts`](index.ts) | Public re-exports; installs window names on import. |
| [`lane.ts`](lane.ts) | `isReady` wrapper for later-lane window names. |
| [`load.ts`](load.ts) | `loadSessions` cursor walk, `loadProjects` keyset pages (no `offset`), folders, `renderSessions`. |
| [`dashboard.projects.test.ts`](dashboard.projects.test.ts) | What the project card shows after a repaint that is not a full load, and what opening a session leaves in the store. The running badge is annotated from the frames the dashboard last fetched — including the 4s poll's, or a repaint paints "1 running" beside a Running card that poll just emptied — and leaving for the workspace reloads the unfiltered directory the header and switcher read, keeping the list it had when that background reload fails. |
| [`load.replace.test.ts`](load.replace.test.ts) | A Load-more clicked while a debounced search is still in flight is refused: before the gate it took a newer generation with the *old* query and cursor, so the search reply was discarded as stale and page two of the previous filter landed under the new box text. |
| [`load.projects.test.ts`](load.projects.test.ts) | Project-list query string has no `offset`; merge/dedupe; empty / retry / load-more view states. |
| [`messages.ts`](messages.ts) | `fetchRecentMessages` / `fetchOlderMessages` / `fetchAllMessages` / earlier bar. |
| [`paging.test.ts`](paging.test.ts) | Pagination constants, session sort, walk/dedupe, dashboard filters. |
| [`paging.ts`](paging.ts) | `MESSAGE_PAGE_SIZE=300`, `SESSION_MAX_PAGES=50`, sort/walk/filter. |
| [`projects.ts`](projects.ts) | Project menu/modal/research view, `sanitizeProjectLineage`. |
| [`transcript.ts`](transcript.ts) | `renderStored`, ref chips, empty-session starters, message actions. |
