# Web UI E2E compatibility contract

Machine inventory extracted from `tests/browser_*.mjs` by
`scripts/extract_webui_contract.mjs`. Do not edit by hand — regenerate.

F-01 does not transplant `app.js`. It freezes the three surfaces the
rewrite must keep green:

1. window globals → `frontend/src/compat/window-exports.ts` (F-05)
2. `S` field read/write, including nested writes after which tests call
   render functions synchronously → `window.S` Proxy (F-05); objects such
   as `_timelineView` keep reference identity
3. DOM id/class/attr selectors → class/id freeze (F-21)

Source files (sorted):
- `tests/browser_admission_fault.mjs`
- `tests/browser_auth.mjs`
- `tests/browser_matrix.mjs`
- `tests/browser_p1_controls.mjs`
- `tests/browser_sandbox_preview.mjs`
- `tests/browser_smoke.mjs`
- `tests/browser_stage0_acceptance.mjs`
- `tests/browser_stage1_trusted_delivery.mjs`
- `tests/browser_team_mode.mjs`

## 1. Bare window globals

Free identifiers inside `page.evaluate` / `waitForFunction` callbacks
on the main workbench page, minus locals, keywords, browser builtins,
and absence probes. `window.__*` test hooks are omitted. Existing
legacy-shell guards and iframe contexts are inventoried separately.
Sorted by name.

| Name | Files | Occurrences |
| --- | --- | --- |
| `ACTION_TIMELINE_OVERSCAN` | browser_smoke.mjs | 1 |
| `ACTION_TIMELINE_OVERVIEW_WIDTH` | browser_smoke.mjs | 2 |
| `ACTION_TIMELINE_PAGE_SIZE` | browser_smoke.mjs | 4 |
| `ACTION_TIMELINE_ROW_HEIGHT` | browser_smoke.mjs | 4 |
| `S` | browser_admission_fault.mjs, browser_p1_controls.mjs, browser_smoke.mjs, browser_stage1_trusted_delivery.mjs | 209 |
| `actionTimelineEntryKey` | browser_smoke.mjs | 1 |
| `actionTimelineOverviewVisualExtent` | browser_smoke.mjs | 1 |
| `actionTimelineSelectionOverlaps` | browser_smoke.mjs | 1 |
| `actionTimelineSpan` | browser_smoke.mjs | 2 |
| `admissionSettled` | browser_admission_fault.mjs | 6 |
| `annotationIsHeld` | browser_admission_fault.mjs | 5 |
| `annotationStatus` | browser_admission_fault.mjs | 3 |
| `buildExecutedCodeView` | browser_p1_controls.mjs | 1 |
| `buildStepCard` | browser_p1_controls.mjs | 2 |
| `commitActionTimelineOverviewSelection` | browser_smoke.mjs | 1 |
| `custTab` | browser_p1_controls.mjs | 1 |
| `execSourcesState` | browser_p1_controls.mjs | 2 |
| `fetchAllMessages` | browser_p1_controls.mjs | 1 |
| `fetchOlderMessages` | browser_p1_controls.mjs | 1 |
| `fetchRecentMessages` | browser_p1_controls.mjs | 1 |
| `forgetAdmission` | browser_admission_fault.mjs | 3 |
| `highlightTraceback` | browser_smoke.mjs | 1 |
| `loadAnnotations` | browser_admission_fault.mjs | 1 |
| `loadEarlierActionTimeline` | browser_smoke.mjs | 1 |
| `mergeDelegationChildEvent` | browser_p1_controls.mjs | 2 |
| `notebookExportLink` | browser_p1_controls.mjs | 1 |
| `onEvent` | browser_p1_controls.mjs, browser_smoke.mjs | 17 |
| `openAnnotations` | browser_admission_fault.mjs | 3 |
| `openConversation` | browser_admission_fault.mjs, browser_matrix.mjs, browser_smoke.mjs | 11 |
| `openCust` | browser_p1_controls.mjs, browser_smoke.mjs | 4 |
| `openKetcher` | browser_sandbox_preview.mjs | 1 |
| `openPinPop` | browser_admission_fault.mjs | 1 |
| `openViewer` | browser_smoke.mjs | 3 |
| `outstandingAdmissions` | browser_admission_fault.mjs | 16 |
| `parseTable` | browser_smoke.mjs | 1 |
| `reconcileLastAdmission` | browser_admission_fault.mjs | 2 |
| `rememberAdmission` | browser_admission_fault.mjs | 1 |
| `renderActionTimeline` | browser_smoke.mjs | 8 |
| `renderAttachmentProblems` | browser_p1_controls.mjs | 1 |
| `renderComposerRefChips` | browser_p1_controls.mjs | 2 |
| `renderDelegationPanel` | browser_p1_controls.mjs | 2 |
| `renderMd` | browser_smoke.mjs, browser_stage0_acceptance.mjs | 2 |
| `renderMessageRefChips` | browser_p1_controls.mjs | 1 |
| `renderPins` | browser_admission_fault.mjs | 1 |
| `renderRefProblems` | browser_p1_controls.mjs | 1 |
| `renderSheet` | browser_smoke.mjs | 1 |
| `sanitizeActionTimeline` | browser_smoke.mjs | 3 |
| `searchResultHttpUrl` | browser_smoke.mjs | 5 |
| `selectExecFrame` | browser_p1_controls.mjs | 2 |
| `send` | browser_admission_fault.mjs | 4 |
| `setActiveTab` | browser_smoke.mjs, browser_stage0_acceptance.mjs | 6 |
| `steerDelegationChild` | browser_p1_controls.mjs | 1 |
| `t` | browser_p1_controls.mjs, browser_smoke.mjs | 9 |
| `telemetryRow` | browser_matrix.mjs | 2 |
| `timelineOverviewTimeToX` | browser_smoke.mjs | 2 |
| `toggleActionTimelineTurn` | browser_smoke.mjs | 1 |
| `updateActionTimelineLedger` | browser_smoke.mjs | 1 |

Total names: 57

## 1a. Context-specific and optional globals

These uses keep their original harness checks. They are not required
on the default workbench window: legacy blocks run only behind their
existing shell guard, iframe calls run in that frame, and absence probes
explicitly handle missing names. An unguarded main-page use is still required.

| Context | Name | Files | Occurrences |
| --- | --- | --- | --- |
| iframe:editor | `ketcher` | browser_sandbox_preview.mjs | 5 |
| legacy:legacyUploadShell | `S` | browser_p1_controls.mjs | 4 |
| legacy:legacyUploadShell | `UPLOAD_STATE` | browser_p1_controls.mjs | 3 |
| legacy:legacyUploadShell | `grow` | browser_p1_controls.mjs | 2 |
| legacy:legacyUploadShell | `hint` | browser_p1_controls.mjs | 2 |
| legacy:legacyUploadShell | `renderComposerRefChips` | browser_p1_controls.mjs | 2 |
| legacy:legacyUploadShell | `send` | browser_p1_controls.mjs | 4 |
| legacy:legacyUploadShell | `turnDone` | browser_p1_controls.mjs | 2 |
| legacy:legacyUploadShell | `uploadFiles` | browser_p1_controls.mjs | 2 |
| optional probe | `UPLOAD_STATE` | browser_p1_controls.mjs | 3 |
| optional probe | `highlightTraceback` | browser_smoke.mjs | 1 |
| optional probe | `loadWorkbenchState` | browser_smoke.mjs | 1 |
| optional probe | `mergeDelegationChildEvent` | browser_p1_controls.mjs | 1 |
| optional probe | `onEvent` | browser_p1_controls.mjs | 1 |
| optional probe | `openAnnotations` | browser_admission_fault.mjs | 2 |
| optional probe | `openConversation` | browser_admission_fault.mjs, browser_matrix.mjs | 9 |
| optional probe | `renderActionTimeline` | browser_smoke.mjs | 1 |
| optional probe | `renderMd` | browser_smoke.mjs | 1 |
| optional probe | `searchResultHttpUrl` | browser_smoke.mjs | 1 |
| optional probe | `setActiveTab` | browser_stage0_acceptance.mjs | 2 |
| optional probe | `t` | browser_matrix.mjs | 2 |
| optional probe | `turnDone` | browser_p1_controls.mjs | 1 |

## 2. `S` field read/write surface

Top-level `S.<field>` accesses inside evaluate callbacks. Nested writes
(`S._timelineView.searchQuery = …`, `.collapsedTurns.add(...)`) are
listed under write paths so F-05 can keep object identity.

### 2a. Private `S._*` fields

| Field | Read | Write | Nested write | Files | Write paths |
| --- | --- | --- | --- | --- | --- |
| `_replayGap` | 2 | 0 | 0 | browser_smoke.mjs | — |
| `_timelineHistoryLoading` | 2 | 3 | 0 | browser_smoke.mjs | `_timelineHistoryLoading` |
| `_timelineHistoryReq` | 3 | 3 | 0 | browser_smoke.mjs | `_timelineHistoryReq` |
| `_timelineRestoreFocusGroupId` | 0 | 2 | 0 | browser_smoke.mjs | `_timelineRestoreFocusGroupId` |
| `_timelineView` | 96 | 0 | 4 | browser_smoke.mjs | `_timelineView.autoLoadArmed`, `_timelineView.collapsedTurns.add`, `_timelineView.searchNeedle`, `_timelineView.searchQuery` |
| `_workbenchLoading` | 3 | 2 | 0 | browser_smoke.mjs | `_workbenchLoading` |
| `_workbenchReq` | 3 | 2 | 0 | browser_smoke.mjs | `_workbenchReq` |
| `_workbenchTimer` | 2 | 0 | 0 | browser_smoke.mjs | — |

`S._*` identifier occurrences: 127

### 2b. Other `S.*` fields

| Field | Read | Write | Nested write | Files | Write paths |
| --- | --- | --- | --- | --- | --- |
| `actionTimeline` | 16 | 9 | 0 | browser_smoke.mjs | `actionTimeline` |
| `actionTimelineSelectedBranchId` | 2 | 5 | 0 | browser_smoke.mjs | `actionTimelineSelectedBranchId` |
| `actionTimelineSelectedGroupId` | 7 | 5 | 0 | browser_smoke.mjs | `actionTimelineSelectedGroupId` |
| `activeTab` | 2 | 0 | 0 | browser_smoke.mjs, browser_stage1_trusted_delivery.mjs | — |
| `annotations` | 1 | 1 | 0 | browser_admission_fault.mjs | `annotations` |
| `artifacts` | 3 | 1 | 0 | browser_admission_fault.mjs, browser_p1_controls.mjs | `artifacts` |
| `branchState` | 1 | 0 | 0 | browser_smoke.mjs | — |
| `currentId` | 18 | 0 | 0 | browser_p1_controls.mjs, browser_smoke.mjs | — |
| `delegationState` | 5 | 6 | 0 | browser_p1_controls.mjs | `delegationState` |
| `dockArtifact` | 1 | 0 | 0 | browser_smoke.mjs | — |
| `provMode` | 1 | 0 | 0 | browser_stage1_trusted_delivery.mjs | — |
| `running` | 0 | 2 | 0 | browser_p1_controls.mjs | `running` |
| `workbenchErrors` | 2 | 2 | 0 | browser_smoke.mjs | `workbenchErrors` |

### 2c. Context-specific `S` accesses

These fields belong to the existing guarded or framed checks above;
they do not require a signal on the default workbench `S` Proxy.

| Context | Field | Access | Files | Occurrences |
| --- | --- | --- | --- | --- |
| legacy:legacyUploadShell | `_sendPreparing` | read | browser_p1_controls.mjs | 1 |
| legacy:legacyUploadShell | `_sendPreparing` | write | browser_p1_controls.mjs | 1 |
| legacy:legacyUploadShell | `running` | read | browser_p1_controls.mjs | 2 |

## 3. DOM selector contract

CSS selectors passed to `locator`, `waitForSelector`, `querySelector`,
`querySelectorAll`, `closest`, `matches`, `getElementById`, and Playwright
`click` / `fill` / `textContent` when the argument is a CSS selector.
Template interpolations are normalized to `*`. Tag-only selectors
without id, class, or attribute (for example `script`) are omitted.
Sorted.

| Selector | Files | Occurrences |
| --- | --- | --- |
| `#b` | browser_team_mode.mjs | 1 |
| `#cancel-btn` | browser_p1_controls.mjs | 2 |
| `#composer` | browser_admission_fault.mjs, browser_matrix.mjs, browser_p1_controls.mjs | 9 |
| `#composer-refs` | browser_p1_controls.mjs | 1 |
| `#cross-frame` | browser_sandbox_preview.mjs | 2 |
| `#cust .cust-row` | browser_p1_controls.mjs | 2 |
| `#cust .prof-row` | browser_p1_controls.mjs | 2 |
| `#cust .toggle` | browser_p1_controls.mjs, browser_smoke.mjs | 2 |
| `#cust-close` | browser_p1_controls.mjs, browser_smoke.mjs | 5 |
| `#cust-content` | browser_p1_controls.mjs | 2 |
| `#cust-content .cust-h` | browser_p1_controls.mjs | 2 |
| `#cust-content .prof-row` | browser_p1_controls.mjs | 2 |
| `#cust-content[aria-busy="false"]` | browser_p1_controls.mjs | 1 |
| `#cust:not(.hidden)` | browser_p1_controls.mjs | 2 |
| `#customize-btn` | browser_p1_controls.mjs | 1 |
| `#dash-projects` | browser_smoke.mjs | 1 |
| `#dash-sessions` | browser_smoke.mjs | 1 |
| `#dashboard` | browser_smoke.mjs | 1 |
| `#dock-files` | browser_stage1_trusted_delivery.mjs | 1 |
| `#dock-files:not(.hidden)` | browser_stage1_trusted_delivery.mjs | 1 |
| `#dock-notebook` | browser_smoke.mjs | 1 |
| `#dock-notebook .nb-repl` | browser_stage0_acceptance.mjs | 1 |
| `#dock-notebook .nb-repl-input` | browser_stage0_acceptance.mjs | 1 |
| `#dock-notebook .nb-status` | browser_stage0_acceptance.mjs | 3 |
| `#dock-notebook:not(.hidden)` | browser_smoke.mjs, browser_stage0_acceptance.mjs | 2 |
| `#dock-tabs .dock-tab` | browser_smoke.mjs | 5 |
| `#dock-timeline` | browser_smoke.mjs | 1 |
| `#dock-viewer` | browser_smoke.mjs, browser_stage1_trusted_delivery.mjs | 5 |
| `#dock-viewer .renderer-noscript` | browser_sandbox_preview.mjs | 1 |
| `#dock-viewer .renderer-source` | browser_smoke.mjs | 4 |
| `#dock-viewer a[download]` | browser_smoke.mjs | 1 |
| `#dock-viewer button[aria-expanded="false"]` | browser_smoke.mjs | 2 |
| `#dock-viewer iframe` | browser_sandbox_preview.mjs | 1 |
| `#dock-viewer table.sheet` | browser_smoke.mjs | 4 |
| `#dock-viewer table.sheet tr` | browser_smoke.mjs | 1 |
| `#dock-viewer td img` | browser_smoke.mjs | 1 |
| `#dock-viewer th` | browser_smoke.mjs | 2 |
| `#dock-viewer tr` | browser_smoke.mjs | 1 |
| `#err` | browser_team_mode.mjs | 1 |
| `#figure` | browser_sandbox_preview.mjs | 1 |
| `#files-btn` | browser_stage1_trusted_delivery.mjs | 2 |
| `#heading` | browser_sandbox_preview.mjs | 4 |
| `#ketcher-save` | browser_sandbox_preview.mjs | 1 |
| `#ketcher-status` | browser_sandbox_preview.mjs | 2 |
| `#messages` | browser_p1_controls.mjs, browser_smoke.mjs | 7 |
| `#messages .empty-session` | browser_smoke.mjs | 1 |
| `#messages .msg.assistant .md a[href^="/api/v1/artifacts/"]` | browser_stage1_trusted_delivery.mjs | 1 |
| `#meta` | browser_team_mode.mjs | 1 |
| `#mobile-scrim:not(.hidden)` | browser_smoke.mjs | 2 |
| `#modal-body > iframe` | browser_sandbox_preview.mjs | 1 |
| `#navigate-app` | browser_sandbox_preview.mjs | 2 |
| `#onboarding` | browser_auth.mjs | 1 |
| `#p` | browser_team_mode.mjs | 1 |
| `#results-list .a-name` | browser_stage1_trusted_delivery.mjs | 2 |
| `#revision` | browser_sandbox_preview.mjs | 1 |
| `#rightdock.collapsed` | browser_smoke.mjs | 3 |
| `#rightdock:not(.collapsed)` | browser_smoke.mjs | 1 |
| `#sidebar-reopen` | browser_smoke.mjs | 3 |
| `#stage0-completion-link-probe a` | browser_stage0_acceptance.mjs | 2 |
| `#team-admin` | browser_team_mode.mjs | 2 |
| `#team-admin-body` | browser_team_mode.mjs | 1 |
| `#team-admin-close` | browser_team_mode.mjs | 1 |
| `#team-admin-modal:not(.hidden)` | browser_team_mode.mjs | 1 |
| `#team-admin:not(.hidden)` | browser_team_mode.mjs | 1 |
| `#team-user` | browser_team_mode.mjs | 1 |
| `#team-user:not(.hidden)` | browser_team_mode.mjs | 1 |
| `#u` | browser_team_mode.mjs | 1 |
| `#workspace` | browser_smoke.mjs | 1 |
| `#workspace:not(.hidden)` | browser_p1_controls.mjs, browser_smoke.mjs, browser_stage0_acceptance.mjs, browser_stage1_trusted_delivery.mjs | 8 |
| `#workspace:not(.hidden) .lang-btn.active[data-lang="en"]` | browser_smoke.mjs | 1 |
| `#workspace:not(.hidden) .lang-btn.active[data-lang="zh"]` | browser_smoke.mjs | 1 |
| `#workspace:not(.hidden) .lang-btn[data-lang="en"]` | browser_smoke.mjs | 1 |
| `#workspace:not(.hidden) .lang-btn[data-lang="zh"]` | browser_smoke.mjs | 1 |
| `#workspace:not(.hidden) [data-i18n="ws.nav.files"]` | browser_smoke.mjs | 1 |
| `#ws-theme` | browser_smoke.mjs | 2 |
| `.annot-layer` | browser_admission_fault.mjs | 1 |
| `.annot-pin[data-annotation-status]` | browser_admission_fault.mjs | 1 |
| `.annot-pop .annot-btn.danger` | browser_admission_fault.mjs | 1 |
| `.annot-pop-status[data-annotation-status]` | browser_admission_fault.mjs | 1 |
| `.branch-name` | browser_smoke.mjs | 3 |
| `.branch-panel` | browser_smoke.mjs | 1 |
| `.bubble` | browser_p1_controls.mjs | 2 |
| `.checkpoint-row button` | browser_smoke.mjs | 1 |
| `.ctx-item` | browser_stage1_trusted_delivery.mjs | 2 |
| `.cust-tab.active` | browser_p1_controls.mjs | 2 |
| `.cust-tab[data-tab="general"]` | browser_smoke.mjs | 2 |
| `.cust-tab[data-tab="models"]` | browser_p1_controls.mjs | 1 |
| `.cust-tab[data-tab="models"].active` | browser_smoke.mjs | 1 |
| `.cust-tab[data-tab="network"].active` | browser_smoke.mjs | 1 |
| `.cust-tab[data-tab="skills"]` | browser_p1_controls.mjs | 1 |
| `.delegation-child` | browser_p1_controls.mjs | 3 |
| `.delegation-child-controls button` | browser_p1_controls.mjs | 1 |
| `.dlg-chip` | browser_p1_controls.mjs | 2 |
| `.dlg-frame-ref` | browser_p1_controls.mjs | 1 |
| `.history-load-status` | browser_smoke.mjs | 4 |
| `.history-load-status button` | browser_smoke.mjs | 2 |
| `.history-load-status[data-history-state="partial"]` | browser_smoke.mjs | 2 |
| `.history-load-status[data-history-state="partial"], .history-load-status[data-history-state="error"]` | browser_smoke.mjs | 1 |
| `.msg-ref-chip` | browser_p1_controls.mjs | 2 |
| `.msg-ref-chip.unresolved` | browser_p1_controls.mjs | 1 |
| `.msg.user` | browser_p1_controls.mjs | 2 |
| `.msg.user .bubble` | browser_p1_controls.mjs | 4 |
| `.nb-exec-frame` | browser_p1_controls.mjs | 1 |
| `.nb-exec-note` | browser_p1_controls.mjs | 1 |
| `.nb-exec-title` | browser_p1_controls.mjs | 1 |
| `.nb-tray` | browser_smoke.mjs | 2 |
| `.nb-variables-empty` | browser_smoke.mjs | 1 |
| `.nbc-artifact-error` | browser_smoke.mjs | 1 |
| `.nbc-artifact-error button` | browser_smoke.mjs | 1 |
| `.nbc-error` | browser_p1_controls.mjs | 1 |
| `.notebook-cell` | browser_p1_controls.mjs | 1 |
| `.notebook-cell[data-producing-cell="*"]` | browser_smoke.mjs | 2 |
| `.perm-allow` | browser_smoke.mjs | 1 |
| `.perm-card.resolved` | browser_smoke.mjs | 1 |
| `.perm-card:not(.resolved)` | browser_smoke.mjs | 1 |
| `.prov-body` | browser_stage1_trusted_delivery.mjs | 1 |
| `.prov-body .prov-card` | browser_stage1_trusted_delivery.mjs | 1 |
| `.prov-card` | browser_stage1_trusted_delivery.mjs | 1 |
| `.prov-dlitem` | browser_p1_controls.mjs | 1 |
| `.prov-link` | browser_stage1_trusted_delivery.mjs | 1 |
| `.prov-subtab` | browser_smoke.mjs, browser_stage1_trusted_delivery.mjs | 3 |
| `.recovery-action-list` | browser_smoke.mjs | 1 |
| `.recovery-action-list button` | browser_smoke.mjs | 1 |
| `.ref-problems` | browser_p1_controls.mjs | 1 |
| `.renderer-note` | browser_smoke.mjs | 1 |
| `.s-child-tag` | browser_p1_controls.mjs | 1 |
| `.s-json` | browser_p1_controls.mjs | 2 |
| `.s-meta` | browser_p1_controls.mjs | 1 |
| `.s-out-tgl` | browser_p1_controls.mjs | 1 |
| `.team-admin-table` | browser_team_mode.mjs | 1 |
| `.timeline-inspector` | browser_smoke.mjs | 3 |
| `.timeline-inspector[data-group-id="ledger-a"]` | browser_smoke.mjs | 1 |
| `.timeline-inspector[data-group-id="ledger-a"] button` | browser_smoke.mjs | 1 |
| `.timeline-inspector[data-group-id="overview-middle"] button` | browser_smoke.mjs | 1 |
| `.timeline-inspector[data-group-id="overview-running"]` | browser_smoke.mjs | 2 |
| `.timeline-inspector[data-group-id="overview-running"] button` | browser_smoke.mjs | 1 |
| `.timeline-kind-icon svg` | browser_smoke.mjs | 1 |
| `.timeline-ledger` | browser_smoke.mjs | 2 |
| `.timeline-ledger-body` | browser_smoke.mjs | 2 |
| `.timeline-ledger-duration` | browser_smoke.mjs | 1 |
| `.timeline-ledger-row` | browser_smoke.mjs | 6 |
| `.timeline-ledger-row.search-match` | browser_smoke.mjs | 1 |
| `.timeline-ledger-row.selected[data-group-id="overview-running"]` | browser_smoke.mjs | 2 |
| `.timeline-ledger-row[data-group-id="*"]` | browser_smoke.mjs | 1 |
| `.timeline-ledger-row[data-group-id="*"] .timeline-row-button` | browser_smoke.mjs | 1 |
| `.timeline-ledger-row[data-group-id="ledger-a"]` | browser_smoke.mjs | 1 |
| `.timeline-ledger-row[data-group-id="ledger-a"] .timeline-turn-toggle` | browser_smoke.mjs | 1 |
| `.timeline-ledger-row[data-group-id="long-3500"]` | browser_smoke.mjs | 2 |
| `.timeline-ledger-row[data-group-id="long-3501"]` | browser_smoke.mjs | 3 |
| `.timeline-ledger-row[data-group-id="overview-middle"] .timeline-row-button` | browser_smoke.mjs | 1 |
| `.timeline-ledger-row[data-group-id]` | browser_smoke.mjs | 3 |
| `.timeline-ledger-scroll` | browser_smoke.mjs | 1 |
| `.timeline-ledger-tokens` | browser_smoke.mjs | 2 |
| `.timeline-ordinal-value` | browser_smoke.mjs | 1 |
| `.timeline-overview svg` | browser_smoke.mjs | 2 |
| `.timeline-overview-clear` | browser_smoke.mjs | 3 |
| `.timeline-overview-phase.queue` | browser_smoke.mjs | 2 |
| `.timeline-overview-tooltip` | browser_smoke.mjs | 5 |
| `.timeline-row-button` | browser_smoke.mjs | 1 |
| `.timeline-search-clear` | browser_smoke.mjs | 2 |
| `.timeline-search-input` | browser_smoke.mjs | 4 |
| `.timeline-search-scope` | browser_smoke.mjs | 2 |
| `.timeline-search-status` | browser_smoke.mjs | 1 |
| `.timeline-toolbar` | browser_smoke.mjs | 1 |
| `.timeline-turn-summary` | browser_smoke.mjs | 1 |
| `.timeline-turn-summary[data-turn-id="turn-alpha"]` | browser_smoke.mjs | 2 |
| `.timeline-turn-toggle` | browser_smoke.mjs | 1 |
| `.viewer-head .vh-acts button` | browser_stage1_trusted_delivery.mjs | 3 |
| `.viewer-head .vh-name` | browser_stage1_trusted_delivery.mjs | 2 |
| `.workbench-empty` | browser_smoke.mjs | 1 |
| `[` | browser_smoke.mjs | 1 |
| `[data-action="load-earlier-timeline"]` | browser_smoke.mjs | 4 |
| `[data-action="load-omitted-timeline"]` | browser_smoke.mjs | 2 |
| `[data-action="refresh-variables"]` | browser_smoke.mjs | 1 |
| `[data-diagnostic-check="model"]` | browser_smoke.mjs | 1 |
| `[data-diagnostic-check="unknown-fixture"] button, [data-diagnostic-check="unknown-fixture"] a` | browser_smoke.mjs | 1 |
| `[data-diagnostic-remedy]` | browser_smoke.mjs | 1 |
| `[data-diagnostic-setting="models"]` | browser_smoke.mjs | 1 |
| `[data-diagnostic-setting="network"]` | browser_smoke.mjs | 1 |
| `[data-diagnostics-error]` | browser_smoke.mjs | 1 |
| `[data-diagnostics-results="current"]` | browser_smoke.mjs | 2 |
| `[data-diagnostics-results="previous"]` | browser_smoke.mjs | 1 |
| `[data-diagnostics-results]` | browser_smoke.mjs | 1 |
| `[data-diagnostics-results] img, [data-diagnostics-results] script` | browser_smoke.mjs | 1 |
| `[data-diagnostics-results] time` | browser_smoke.mjs | 3 |
| `[data-diagnostics-run]` | browser_smoke.mjs | 7 |
| `[data-diagnostics-stale]` | browser_smoke.mjs | 1 |
| `[data-f16-provenance="1"]` | browser_smoke.mjs | 1 |
| `[data-f16-provenance="back"]` | browser_smoke.mjs | 1 |
| `[data-variable-inspector="python"]` | browser_smoke.mjs | 1 |
| `a[download="*"]` | browser_smoke.mjs | 1 |
| `body.sidebar-collapsed` | browser_smoke.mjs | 3 |
| `button.outline-btn` | browser_auth.mjs | 1 |
| `button.toggle` | browser_matrix.mjs | 2 |
| `img.nbc-fig` | browser_smoke.mjs | 3 |
| `table.nbc-table` | browser_smoke.mjs | 1 |

Total selectors: 196

```json
{
  "globals": [
    "ACTION_TIMELINE_OVERSCAN",
    "ACTION_TIMELINE_OVERVIEW_WIDTH",
    "ACTION_TIMELINE_PAGE_SIZE",
    "ACTION_TIMELINE_ROW_HEIGHT",
    "S",
    "actionTimelineEntryKey",
    "actionTimelineOverviewVisualExtent",
    "actionTimelineSelectionOverlaps",
    "actionTimelineSpan",
    "admissionSettled",
    "annotationIsHeld",
    "annotationStatus",
    "buildExecutedCodeView",
    "buildStepCard",
    "commitActionTimelineOverviewSelection",
    "custTab",
    "execSourcesState",
    "fetchAllMessages",
    "fetchOlderMessages",
    "fetchRecentMessages",
    "forgetAdmission",
    "highlightTraceback",
    "loadAnnotations",
    "loadEarlierActionTimeline",
    "mergeDelegationChildEvent",
    "notebookExportLink",
    "onEvent",
    "openAnnotations",
    "openConversation",
    "openCust",
    "openKetcher",
    "openPinPop",
    "openViewer",
    "outstandingAdmissions",
    "parseTable",
    "reconcileLastAdmission",
    "rememberAdmission",
    "renderActionTimeline",
    "renderAttachmentProblems",
    "renderComposerRefChips",
    "renderDelegationPanel",
    "renderMd",
    "renderMessageRefChips",
    "renderPins",
    "renderRefProblems",
    "renderSheet",
    "sanitizeActionTimeline",
    "searchResultHttpUrl",
    "selectExecFrame",
    "send",
    "setActiveTab",
    "steerDelegationChild",
    "t",
    "telemetryRow",
    "timelineOverviewTimeToX",
    "toggleActionTimelineTurn",
    "updateActionTimelineLedger"
  ],
  "s_private": [
    "_replayGap",
    "_timelineHistoryLoading",
    "_timelineHistoryReq",
    "_timelineRestoreFocusGroupId",
    "_timelineView",
    "_workbenchLoading",
    "_workbenchReq",
    "_workbenchTimer"
  ],
  "s_public": [
    "actionTimeline",
    "actionTimelineSelectedBranchId",
    "actionTimelineSelectedGroupId",
    "activeTab",
    "annotations",
    "artifacts",
    "branchState",
    "currentId",
    "delegationState",
    "dockArtifact",
    "provMode",
    "running",
    "workbenchErrors"
  ],
  "s_star_occurrences": 127,
  "selectors": [
    "#b",
    "#cancel-btn",
    "#composer",
    "#composer-refs",
    "#cross-frame",
    "#cust .cust-row",
    "#cust .prof-row",
    "#cust .toggle",
    "#cust-close",
    "#cust-content",
    "#cust-content .cust-h",
    "#cust-content .prof-row",
    "#cust-content[aria-busy=\"false\"]",
    "#cust:not(.hidden)",
    "#customize-btn",
    "#dash-projects",
    "#dash-sessions",
    "#dashboard",
    "#dock-files",
    "#dock-files:not(.hidden)",
    "#dock-notebook",
    "#dock-notebook .nb-repl",
    "#dock-notebook .nb-repl-input",
    "#dock-notebook .nb-status",
    "#dock-notebook:not(.hidden)",
    "#dock-tabs .dock-tab",
    "#dock-timeline",
    "#dock-viewer",
    "#dock-viewer .renderer-noscript",
    "#dock-viewer .renderer-source",
    "#dock-viewer a[download]",
    "#dock-viewer button[aria-expanded=\"false\"]",
    "#dock-viewer iframe",
    "#dock-viewer table.sheet",
    "#dock-viewer table.sheet tr",
    "#dock-viewer td img",
    "#dock-viewer th",
    "#dock-viewer tr",
    "#err",
    "#figure",
    "#files-btn",
    "#heading",
    "#ketcher-save",
    "#ketcher-status",
    "#messages",
    "#messages .empty-session",
    "#messages .msg.assistant .md a[href^=\"/api/v1/artifacts/\"]",
    "#meta",
    "#mobile-scrim:not(.hidden)",
    "#modal-body > iframe",
    "#navigate-app",
    "#onboarding",
    "#p",
    "#results-list .a-name",
    "#revision",
    "#rightdock.collapsed",
    "#rightdock:not(.collapsed)",
    "#sidebar-reopen",
    "#stage0-completion-link-probe a",
    "#team-admin",
    "#team-admin-body",
    "#team-admin-close",
    "#team-admin-modal:not(.hidden)",
    "#team-admin:not(.hidden)",
    "#team-user",
    "#team-user:not(.hidden)",
    "#u",
    "#workspace",
    "#workspace:not(.hidden)",
    "#workspace:not(.hidden) .lang-btn.active[data-lang=\"en\"]",
    "#workspace:not(.hidden) .lang-btn.active[data-lang=\"zh\"]",
    "#workspace:not(.hidden) .lang-btn[data-lang=\"en\"]",
    "#workspace:not(.hidden) .lang-btn[data-lang=\"zh\"]",
    "#workspace:not(.hidden) [data-i18n=\"ws.nav.files\"]",
    "#ws-theme",
    ".annot-layer",
    ".annot-pin[data-annotation-status]",
    ".annot-pop .annot-btn.danger",
    ".annot-pop-status[data-annotation-status]",
    ".branch-name",
    ".branch-panel",
    ".bubble",
    ".checkpoint-row button",
    ".ctx-item",
    ".cust-tab.active",
    ".cust-tab[data-tab=\"general\"]",
    ".cust-tab[data-tab=\"models\"]",
    ".cust-tab[data-tab=\"models\"].active",
    ".cust-tab[data-tab=\"network\"].active",
    ".cust-tab[data-tab=\"skills\"]",
    ".delegation-child",
    ".delegation-child-controls button",
    ".dlg-chip",
    ".dlg-frame-ref",
    ".history-load-status",
    ".history-load-status button",
    ".history-load-status[data-history-state=\"partial\"]",
    ".history-load-status[data-history-state=\"partial\"], .history-load-status[data-history-state=\"error\"]",
    ".msg-ref-chip",
    ".msg-ref-chip.unresolved",
    ".msg.user",
    ".msg.user .bubble",
    ".nb-exec-frame",
    ".nb-exec-note",
    ".nb-exec-title",
    ".nb-tray",
    ".nb-variables-empty",
    ".nbc-artifact-error",
    ".nbc-artifact-error button",
    ".nbc-error",
    ".notebook-cell",
    ".notebook-cell[data-producing-cell=\"*\"]",
    ".perm-allow",
    ".perm-card.resolved",
    ".perm-card:not(.resolved)",
    ".prov-body",
    ".prov-body .prov-card",
    ".prov-card",
    ".prov-dlitem",
    ".prov-link",
    ".prov-subtab",
    ".recovery-action-list",
    ".recovery-action-list button",
    ".ref-problems",
    ".renderer-note",
    ".s-child-tag",
    ".s-json",
    ".s-meta",
    ".s-out-tgl",
    ".team-admin-table",
    ".timeline-inspector",
    ".timeline-inspector[data-group-id=\"ledger-a\"]",
    ".timeline-inspector[data-group-id=\"ledger-a\"] button",
    ".timeline-inspector[data-group-id=\"overview-middle\"] button",
    ".timeline-inspector[data-group-id=\"overview-running\"]",
    ".timeline-inspector[data-group-id=\"overview-running\"] button",
    ".timeline-kind-icon svg",
    ".timeline-ledger",
    ".timeline-ledger-body",
    ".timeline-ledger-duration",
    ".timeline-ledger-row",
    ".timeline-ledger-row.search-match",
    ".timeline-ledger-row.selected[data-group-id=\"overview-running\"]",
    ".timeline-ledger-row[data-group-id=\"*\"]",
    ".timeline-ledger-row[data-group-id=\"*\"] .timeline-row-button",
    ".timeline-ledger-row[data-group-id=\"ledger-a\"]",
    ".timeline-ledger-row[data-group-id=\"ledger-a\"] .timeline-turn-toggle",
    ".timeline-ledger-row[data-group-id=\"long-3500\"]",
    ".timeline-ledger-row[data-group-id=\"long-3501\"]",
    ".timeline-ledger-row[data-group-id=\"overview-middle\"] .timeline-row-button",
    ".timeline-ledger-row[data-group-id]",
    ".timeline-ledger-scroll",
    ".timeline-ledger-tokens",
    ".timeline-ordinal-value",
    ".timeline-overview svg",
    ".timeline-overview-clear",
    ".timeline-overview-phase.queue",
    ".timeline-overview-tooltip",
    ".timeline-row-button",
    ".timeline-search-clear",
    ".timeline-search-input",
    ".timeline-search-scope",
    ".timeline-search-status",
    ".timeline-toolbar",
    ".timeline-turn-summary",
    ".timeline-turn-summary[data-turn-id=\"turn-alpha\"]",
    ".timeline-turn-toggle",
    ".viewer-head .vh-acts button",
    ".viewer-head .vh-name",
    ".workbench-empty",
    "[",
    "[data-action=\"load-earlier-timeline\"]",
    "[data-action=\"load-omitted-timeline\"]",
    "[data-action=\"refresh-variables\"]",
    "[data-diagnostic-check=\"model\"]",
    "[data-diagnostic-check=\"unknown-fixture\"] button, [data-diagnostic-check=\"unknown-fixture\"] a",
    "[data-diagnostic-remedy]",
    "[data-diagnostic-setting=\"models\"]",
    "[data-diagnostic-setting=\"network\"]",
    "[data-diagnostics-error]",
    "[data-diagnostics-results=\"current\"]",
    "[data-diagnostics-results=\"previous\"]",
    "[data-diagnostics-results]",
    "[data-diagnostics-results] img, [data-diagnostics-results] script",
    "[data-diagnostics-results] time",
    "[data-diagnostics-run]",
    "[data-diagnostics-stale]",
    "[data-f16-provenance=\"1\"]",
    "[data-f16-provenance=\"back\"]",
    "[data-variable-inspector=\"python\"]",
    "a[download=\"*\"]",
    "body.sidebar-collapsed",
    "button.outline-btn",
    "button.toggle",
    "img.nbc-fig",
    "table.nbc-table"
  ]
}
```
