# frontend/src/features/sessions

[中文说明](README_zh.md)

F-13 dashboard / projects / sessions. Pagination and sort are pure functions. Window contract names (`fetchAllMessages`, `fetchOlderMessages`, `fetchRecentMessages`, `openConversation`, `renderMessageRefChips`, `renderComposerRefChips`) are assigned here. Capability checks use `isReady` from `compat/stub.ts` — this directory does not import `window-exports.ts`.

## Files

| File | Responsibility |
| --- | --- |
| [`actions.ts`](actions.ts) | Session menu, import/export, title, cancel. app.js:7411-7793. |
| [`api.ts`](api.ts) | `API`, `ApiError`, `api()`, `apiErrorText`. app.js:84-119. |
| [`binds.ts`](binds.ts) | Late bindings so dashboard and conversation do not import each other. |
| [`boot.ts`](boot.ts) | Window exports, `setLoadSessionsImpl`, workbench click wiring. Binds the Shell at once, but routes to the first view only once the locale chunks have loaded (or failed, or `I18N_ROUTE_WAIT_MS` ran out): the dashboard lists, sidebar and an opened session render through `t()` and are not repainted when the dictionaries land. If the wait runs out, those lists (dashboard, sidebar, empty session) are re-rendered once when the dictionaries do arrive. |
| [`boot.i18n-gate.test.ts`](boot.i18n-gate.test.ts) | Handlers are bound before the dictionaries load, the first route waits for them, a failed or stalled locale chunk still routes, and dictionaries that arrive after the wait repaint the lists (only then). |
| [`chrome.test.ts`](chrome.test.ts) | Hint error prefix (`错误：` / `Error: `) without a new i18n key. |
| [`chrome.ts`](chrome.ts) | `hint`, disconnect banner, `openMenu` Esc/`role=menu`, keyboard activate. |
| [`conversation.ts`](conversation.ts) | `newSession`, `routeInitialView`. Re-exports `openConversation` (F-10) and `resumeWatch` (F-11) rather than keeping this lane's duplicates. |
| [`conversation.identity.test.ts`](conversation.identity.test.ts) | Those re-exports are the same function objects the owning lanes install. |
| [`conversation.newsession.test.ts`](conversation.newsession.test.ts) | `newSession` releases the previous conversation (unsubscribe, notebook caches) before publishing the new id, and on the shared path resolves only after the conversation has opened. |
| [`actions.directory.test.ts`](actions.directory.test.ts) | Session-menu actions keep the sidebar directory truthful: a folder made by "New folder and move" is listed rather than answered from the cached folders, and deleting the open session never reopens it when the list refresh that follows fails. |
| [`actions.cancel.test.ts`](actions.cancel.test.ts) | A cancel ack is applied to "Stopping…" only when it names the execution this client is still running. |
| [`dashboard.ts`](dashboard.ts) | Home list, project search / load-more / retry, example CTA poll bound to view lifecycle, dash poll. |
| [`dashboard.sessions.test.ts`](dashboard.sessions.test.ts) | The dashboard's session lists and the example CTA: its status poll runs once however many repaints raced its first read, and never after the dashboard was left. |
| [`dom.ts`](dom.ts) | `$` / `el` / `ago` / `navURL` / composer helpers; `FRAME_ROUTE` / `PROJECT_ROUTE` / `routesToWorkspace`, shared by `routeInitialView` and the Shell's first paint. `setTitle` takes `#conv-title` over from its static `data-i18n-val` label. |
| [`icon.ts`](icon.ts) | `icon` / `iconEl` / `paintIcons` for this lane's menus, rows and `[data-icon]` markup. Paths come from the shared `icons/paths.ts` table. |
| [`index.ts`](index.ts) | Public re-exports; installs window names on import. |
| [`lane.ts`](lane.ts) | `isReady` wrapper for later-lane window names. |
| [`load.ts`](load.ts) | `loadSessions` cursor walk, `loadProjects` keyset pages (no `offset`), folders, `renderSessions`. |
| [`dashboard.projects.test.ts`](dashboard.projects.test.ts) | What the project card shows after a repaint that is not a full load, and what opening a session leaves in the store. The running badge is annotated from the frames the dashboard last fetched — including the 4s poll's, or a repaint paints "1 running" beside a Running card that poll just emptied — and leaving for the workspace reloads the unfiltered directory the header and switcher read, keeping the list it had when that background reload fails. |
| [`load.replace.test.ts`](load.replace.test.ts) | A Load-more clicked while a debounced search is still in flight is refused: before the gate it took a newer generation with the *old* query and cursor, so the search reply was discarded as stale and page two of the previous filter landed under the new box text. |
| [`load.projects.test.ts`](load.projects.test.ts) | Project-list query string has no `offset`; merge/dedupe; empty / retry / load-more view states. |
| [`messages.ts`](messages.ts) | `fetchRecentMessages` / `fetchOlderMessages` / `fetchAllMessages` / earlier bar. |
| [`paging.test.ts`](paging.test.ts) | Pagination constants, session sort, walk/dedupe, dashboard filters. |
| [`paging.ts`](paging.ts) | `MESSAGE_PAGE_SIZE=300`, `SESSION_MAX_PAGES=50`, sort/walk/filter. |
| [`navigation.ts`](navigation.ts) | The view generation and the synchronous directory reset. Deliberately not a list-read owner: list reads are scoped to their project. |
| [`copy.ts`](copy.ts) | Bilingual directory-read failure and retry copy. |
| [`load.navigation.test.ts`](load.navigation.test.ts) | Out-of-order sessions/folders/pages, project-scoped read ownership, read errors and auto-open ownership. |
| [`actions.export.test.ts`](actions.export.test.ts) | Markdown export requires successful validated reads and freezes the session title. |
| [`projects.navigation.test.ts`](projects.navigation.test.ts) | A superseded project navigation cannot replace the current session or its session/folder lists; menu filtering cancels a pending project open, including repeated A→B→A filters, and the open then hands the view back — it reloads the conversation whose reads its entry retired, or opens the menu's project when the workspace was revealed with none; the current navigation transfers ownership to its conversation. List reads are scoped to their project, not to the view generation: a same-project refresh survives a conversation open or a trip Home that overtakes it. |
| [`share.test.ts`](share.test.ts) | Copy says "copied" only for a write the clipboard confirmed; a refused write is reported and the link is left selected for a manual copy. |
| [`share.ts`](share.ts) | Share dialog: create, copy, update or revoke a session's read-only link. app.js:7560-7697. |
| [`projects.ts`](projects.ts) | Project menu/modal/research view, `sanitizeProjectLineage`. `renderProjMenu` takes `#proj-current` over from its static `data-i18n` label. |
| [`static-i18n-ownership.test.ts`](static-i18n-ownership.test.ts) | Once code has written the session title or the current project's name, neither the late locale-chunk repaint nor a language switch puts "Session" / "Project" back; the title input commits on blur, so that repaint renamed the session on the server. |
| [`transcript.ts`](transcript.ts) | Composer @-ref chips. Re-exports the stored-row names (`renderStored`, `addMsgActions`, `insertMessageByTime`, `renderEmptySession`, `renderMessageRefChips`) from `messages/list.ts`, their one implementation. |
