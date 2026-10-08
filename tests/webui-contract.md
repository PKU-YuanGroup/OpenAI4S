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
- `tests/browser_editor.mjs`
- `tests/browser_files.mjs`
- `tests/browser_judgment.mjs`
- `tests/browser_lab.mjs`
- `tests/browser_matrix.mjs`
- `tests/browser_navigation.mjs`
- `tests/browser_p1_controls.mjs`
- `tests/browser_provenance.mjs`
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
| `S` | browser_admission_fault.mjs, browser_files.mjs, browser_navigation.mjs, browser_p1_controls.mjs, browser_provenance.mjs, browser_smoke.mjs, browser_stage1_trusted_delivery.mjs | 232 |
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
| `hint` | browser_provenance.mjs | 1 |
| `loadAnnotations` | browser_admission_fault.mjs | 1 |
| `loadArtifacts` | browser_smoke.mjs | 1 |
| `loadEarlierActionTimeline` | browser_smoke.mjs | 1 |
| `loadWorkbenchState` | browser_p1_controls.mjs | 2 |
| `mergeDelegationChildEvent` | browser_p1_controls.mjs | 2 |
| `notebookExportLink` | browser_p1_controls.mjs | 1 |
| `onEvent` | browser_p1_controls.mjs, browser_smoke.mjs | 17 |
| `openAnnotations` | browser_admission_fault.mjs | 3 |
| `openConversation` | browser_admission_fault.mjs, browser_editor.mjs, browser_files.mjs, browser_lab.mjs, browser_matrix.mjs, browser_navigation.mjs, browser_p1_controls.mjs, browser_provenance.mjs, browser_smoke.mjs | 29 |
| `openCust` | browser_judgment.mjs, browser_p1_controls.mjs, browser_smoke.mjs | 5 |
| `openKetcher` | browser_sandbox_preview.mjs | 1 |
| `openPinPop` | browser_admission_fault.mjs | 1 |
| `openViewer` | browser_editor.mjs, browser_provenance.mjs, browser_smoke.mjs | 7 |
| `outstandingAdmissions` | browser_admission_fault.mjs | 16 |
| `parseTable` | browser_smoke.mjs | 1 |
| `reconcileLastAdmission` | browser_admission_fault.mjs | 2 |
| `rememberAdmission` | browser_admission_fault.mjs | 1 |
| `renderActionTimeline` | browser_p1_controls.mjs, browser_smoke.mjs | 11 |
| `renderAttachmentProblems` | browser_p1_controls.mjs | 1 |
| `renderComposerRefChips` | browser_p1_controls.mjs | 2 |
| `renderDelegationPanel` | browser_p1_controls.mjs | 2 |
| `renderMd` | browser_smoke.mjs, browser_stage0_acceptance.mjs | 2 |
| `renderMessageRefChips` | browser_p1_controls.mjs | 1 |
| `renderPins` | browser_admission_fault.mjs | 1 |
| `renderRefProblems` | browser_p1_controls.mjs | 1 |
| `renderSheet` | browser_smoke.mjs | 1 |
| `renderViewer` | browser_editor.mjs, browser_provenance.mjs | 4 |
| `sanitizeActionTimeline` | browser_smoke.mjs | 3 |
| `searchResultHttpUrl` | browser_smoke.mjs | 5 |
| `selectExecFrame` | browser_p1_controls.mjs | 2 |
| `send` | browser_admission_fault.mjs | 4 |
| `setActiveTab` | browser_p1_controls.mjs, browser_smoke.mjs, browser_stage0_acceptance.mjs | 9 |
| `showDashboard` | browser_navigation.mjs, browser_provenance.mjs | 2 |
| `steerDelegationChild` | browser_p1_controls.mjs | 1 |
| `t` | browser_editor.mjs, browser_p1_controls.mjs, browser_provenance.mjs, browser_smoke.mjs | 12 |
| `telemetryRow` | browser_matrix.mjs | 2 |
| `timelineOverviewTimeToX` | browser_smoke.mjs | 2 |
| `toggleActionTimelineTurn` | browser_smoke.mjs | 1 |
| `updateActionTimelineLedger` | browser_smoke.mjs | 1 |

Total names: 62

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
| `artifacts` | 5 | 1 | 0 | browser_admission_fault.mjs, browser_files.mjs, browser_p1_controls.mjs | `artifacts` |
| `branchState` | 4 | 0 | 0 | browser_p1_controls.mjs, browser_smoke.mjs | — |
| `currentId` | 26 | 0 | 0 | browser_files.mjs, browser_navigation.mjs, browser_p1_controls.mjs, browser_provenance.mjs, browser_smoke.mjs | — |
| `delegationState` | 5 | 6 | 0 | browser_p1_controls.mjs | `delegationState` |
| `dockArtifact` | 4 | 0 | 0 | browser_provenance.mjs, browser_smoke.mjs | — |
| `filesScope` | 1 | 0 | 0 | browser_files.mjs | — |
| `project` | 4 | 0 | 0 | browser_files.mjs, browser_navigation.mjs, browser_provenance.mjs | — |
| `provMode` | 1 | 0 | 0 | browser_stage1_trusted_delivery.mjs | — |
| `provSub` | 1 | 1 | 0 | browser_provenance.mjs | `provSub` |
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
| `#back-home` | browser_navigation.mjs | 1 |
| `#cancel-btn` | browser_lab.mjs, browser_p1_controls.mjs | 8 |
| `#composer` | browser_admission_fault.mjs, browser_lab.mjs, browser_matrix.mjs, browser_p1_controls.mjs | 11 |
| `#composer-ac` | browser_p1_controls.mjs | 1 |
| `#composer-ac .ac-list .ac-item` | browser_p1_controls.mjs | 1 |
| `#composer-ac > .ac-hint` | browser_p1_controls.mjs | 1 |
| `#composer-hint` | browser_navigation.mjs, browser_provenance.mjs | 2 |
| `#composer-refs` | browser_p1_controls.mjs | 1 |
| `#conv-title` | browser_smoke.mjs | 4 |
| `#cross-frame` | browser_sandbox_preview.mjs | 2 |
| `#cust .cust-row` | browser_p1_controls.mjs | 2 |
| `#cust .prof-row` | browser_p1_controls.mjs | 2 |
| `#cust .seg-btn` | browser_smoke.mjs | 1 |
| `#cust .toggle` | browser_p1_controls.mjs, browser_smoke.mjs | 2 |
| `#cust-close` | browser_p1_controls.mjs, browser_smoke.mjs | 6 |
| `#cust-content` | browser_p1_controls.mjs | 2 |
| `#cust-content .cust-h` | browser_p1_controls.mjs | 2 |
| `#cust-content .prof-row` | browser_p1_controls.mjs | 2 |
| `#cust-content[aria-busy="false"]` | browser_judgment.mjs, browser_p1_controls.mjs | 2 |
| `#cust:not(.hidden)` | browser_p1_controls.mjs, browser_smoke.mjs | 3 |
| `#customize-btn` | browser_p1_controls.mjs, browser_smoke.mjs | 2 |
| `#dash-import-session` | browser_smoke.mjs | 1 |
| `#dash-new-project` | browser_smoke.mjs | 3 |
| `#dash-new-project svg > *` | browser_smoke.mjs | 1 |
| `#dash-project-search` | browser_smoke.mjs | 1 |
| `#dash-projects` | browser_smoke.mjs | 1 |
| `#dash-projects .d-row` | browser_navigation.mjs, browser_provenance.mjs | 4 |
| `#dash-projects .d-row:not(.skeleton-row)` | browser_smoke.mjs | 1 |
| `#dash-sessions` | browser_smoke.mjs | 1 |
| `#dash-sessions .d-row:not(.skeleton-row), #dash-sessions .dash-empty` | browser_smoke.mjs | 1 |
| `#dashboard` | browser_navigation.mjs, browser_smoke.mjs | 3 |
| `#dashboard .lang-btn[data-lang="*"]` | browser_smoke.mjs | 2 |
| `#dashboard [data-i18n="dash.col.projects"]` | browser_smoke.mjs | 1 |
| `#dock-files` | browser_stage1_trusted_delivery.mjs | 1 |
| `#dock-files:not(.hidden)` | browser_stage1_trusted_delivery.mjs | 1 |
| `#dock-lab` | browser_lab.mjs, browser_matrix.mjs | 2 |
| `#dock-notebook` | browser_smoke.mjs | 1 |
| `#dock-notebook .nb-repl` | browser_stage0_acceptance.mjs | 1 |
| `#dock-notebook .nb-repl-input` | browser_stage0_acceptance.mjs | 1 |
| `#dock-notebook .nb-status` | browser_stage0_acceptance.mjs | 3 |
| `#dock-notebook .notebook-cell` | browser_smoke.mjs | 1 |
| `#dock-notebook:not(.hidden)` | browser_smoke.mjs, browser_stage0_acceptance.mjs | 2 |
| `#dock-tabs .dock-tab` | browser_editor.mjs, browser_lab.mjs, browser_matrix.mjs, browser_smoke.mjs | 12 |
| `#dock-timeline` | browser_smoke.mjs | 1 |
| `#dock-timeline .branch-row:not(.current)` | browser_p1_controls.mjs | 2 |
| `#dock-timeline .delegation-panel` | browser_p1_controls.mjs | 1 |
| `#dock-toggle` | browser_smoke.mjs | 1 |
| `#dock-viewer` | browser_editor.mjs, browser_provenance.mjs, browser_smoke.mjs, browser_stage1_trusted_delivery.mjs | 7 |
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
| `#files-btn` | browser_editor.mjs, browser_files.mjs, browser_navigation.mjs, browser_provenance.mjs, browser_stage1_trusted_delivery.mjs | 11 |
| `#heading` | browser_sandbox_preview.mjs | 4 |
| `#ketcher-save` | browser_sandbox_preview.mjs | 1 |
| `#ketcher-status` | browser_sandbox_preview.mjs | 2 |
| `#messages` | browser_lab.mjs, browser_p1_controls.mjs, browser_smoke.mjs | 9 |
| `#messages .empty-session` | browser_smoke.mjs | 1 |
| `#messages .msg.assistant .md a[href^="/api/v1/artifacts/"]` | browser_stage1_trusted_delivery.mjs | 1 |
| `#meta` | browser_team_mode.mjs | 1 |
| `#mobile-scrim:not(.hidden)` | browser_smoke.mjs | 2 |
| `#modal-body > iframe` | browser_sandbox_preview.mjs | 1 |
| `#navigate-app` | browser_sandbox_preview.mjs | 2 |
| `#new-session` | browser_navigation.mjs, browser_smoke.mjs | 3 |
| `#onboarding` | browser_auth.mjs | 1 |
| `#p` | browser_team_mode.mjs | 1 |
| `#proj-btn` | browser_navigation.mjs, browser_provenance.mjs | 2 |
| `#proj-menu .proj-item` | browser_navigation.mjs, browser_provenance.mjs | 2 |
| `#results-count` | browser_files.mjs | 2 |
| `#results-list .a-name` | browser_stage1_trusted_delivery.mjs | 2 |
| `#results-list .art` | browser_files.mjs | 2 |
| `#results-list .art[data-artifact-id="*"]` | browser_provenance.mjs | 1 |
| `#results-list .files-empty` | browser_files.mjs | 2 |
| `#revision` | browser_sandbox_preview.mjs | 1 |
| `#rightdock.collapsed` | browser_lab.mjs, browser_matrix.mjs, browser_smoke.mjs | 5 |
| `#rightdock:not(.collapsed)` | browser_smoke.mjs | 1 |
| `#send-btn` | browser_lab.mjs | 1 |
| `#session-list .folder-name` | browser_navigation.mjs | 1 |
| `#session-list .session` | browser_navigation.mjs, browser_smoke.mjs | 2 |
| `#session-menu-btn` | browser_provenance.mjs | 1 |
| `#session-more` | browser_navigation.mjs | 3 |
| `#settings-gear` | browser_smoke.mjs | 1 |
| `#sidebar` | browser_smoke.mjs | 1 |
| `#sidebar-collapse` | browser_smoke.mjs | 1 |
| `#sidebar-collapse svg > *` | browser_smoke.mjs | 1 |
| `#sidebar-reopen` | browser_smoke.mjs | 3 |
| `#stage0-completion-link-probe a` | browser_stage0_acceptance.mjs | 2 |
| `#tab-new` | browser_navigation.mjs | 1 |
| `#tabbar` | browser_smoke.mjs | 1 |
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
| `#ws-theme` | browser_smoke.mjs | 4 |
| `#ws-theme svg` | browser_smoke.mjs | 2 |
| `.annot-layer` | browser_admission_fault.mjs | 1 |
| `.annot-pin[data-annotation-status]` | browser_admission_fault.mjs | 1 |
| `.annot-pop .annot-btn.danger` | browser_admission_fault.mjs | 1 |
| `.annot-pop-status[data-annotation-status]` | browser_admission_fault.mjs | 1 |
| `.art[data-artifact-id="*"]` | browser_navigation.mjs | 7 |
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
| `.delegation-evidence` | browser_p1_controls.mjs | 1 |
| `.delegation-evidence-row .dlg-chip.completed` | browser_p1_controls.mjs | 1 |
| `.delegation-evidence-row .dlg-chip.warning` | browser_p1_controls.mjs | 1 |
| `.delegation-evidence-scope` | browser_p1_controls.mjs | 1 |
| `.dlg-chip` | browser_p1_controls.mjs | 2 |
| `.dlg-frame-ref` | browser_p1_controls.mjs | 1 |
| `.edit-acts .solid-btn` | browser_editor.mjs | 1 |
| `.edit-recovery button` | browser_editor.mjs | 6 |
| `.edit-status` | browser_editor.mjs | 2 |
| `.editor-draft` | browser_editor.mjs | 6 |
| `.editor-draft button` | browser_editor.mjs | 2 |
| `.editor-drafts` | browser_editor.mjs | 1 |
| `.files-empty` | browser_files.mjs | 3 |
| `.files-filter-type` | browser_files.mjs, browser_navigation.mjs, browser_provenance.mjs | 7 |
| `.files-index-note` | browser_files.mjs | 1 |
| `.files-load-more` | browser_files.mjs | 8 |
| `.files-origin [data-origin="*"]` | browser_files.mjs | 1 |
| `.files-origin [data-origin="all"]` | browser_navigation.mjs, browser_provenance.mjs | 2 |
| `.files-scope [data-scope="frame"]` | browser_files.mjs, browser_navigation.mjs, browser_provenance.mjs | 5 |
| `.files-scope [data-scope="project"]` | browser_files.mjs | 2 |
| `.files-search` | browser_files.mjs, browser_navigation.mjs, browser_provenance.mjs, browser_stage1_trusted_delivery.mjs | 7 |
| `.history-load-status` | browser_smoke.mjs | 4 |
| `.history-load-status button` | browser_smoke.mjs | 2 |
| `.history-load-status[data-history-state="partial"]` | browser_smoke.mjs | 2 |
| `.history-load-status[data-history-state="partial"], .history-load-status[data-history-state="error"]` | browser_smoke.mjs | 1 |
| `.lab-command` | browser_lab.mjs | 1 |
| `.lab-connection` | browser_lab.mjs | 3 |
| `.lab-metrics` | browser_lab.mjs | 1 |
| `.lab-metrics span` | browser_lab.mjs | 1 |
| `.lab-setup select` | browser_matrix.mjs | 1 |
| `.lab-stop` | browser_lab.mjs | 1 |
| `.msg-fork-btn[data-fork-message-id="*"]` | browser_p1_controls.mjs | 1 |
| `.msg-ref-chip` | browser_p1_controls.mjs | 2 |
| `.msg-ref-chip.unresolved` | browser_p1_controls.mjs | 1 |
| `.msg.user` | browser_p1_controls.mjs | 2 |
| `.msg.user .bubble` | browser_p1_controls.mjs | 4 |
| `.nb-exec-frame` | browser_p1_controls.mjs | 1 |
| `.nb-exec-note` | browser_p1_controls.mjs | 1 |
| `.nb-exec-title` | browser_p1_controls.mjs | 1 |
| `.nb-tray` | browser_lab.mjs, browser_matrix.mjs, browser_smoke.mjs | 4 |
| `.nb-variables-empty` | browser_smoke.mjs | 1 |
| `.nbc-artifact-error` | browser_smoke.mjs | 2 |
| `.nbc-artifact-error button` | browser_smoke.mjs | 1 |
| `.nbc-artifact-error:not([data-before-retry])` | browser_smoke.mjs | 1 |
| `.nbc-error` | browser_p1_controls.mjs | 1 |
| `.notebook-cell` | browser_p1_controls.mjs | 1 |
| `.notebook-cell.flash` | browser_provenance.mjs | 2 |
| `.notebook-cell[data-producing-cell="*"]` | browser_smoke.mjs | 2 |
| `.perm-allow` | browser_smoke.mjs | 1 |
| `.perm-card.resolved` | browser_smoke.mjs | 1 |
| `.perm-card:not(.resolved)` | browser_lab.mjs, browser_smoke.mjs | 2 |
| `.perm-scope .perm-seg` | browser_lab.mjs | 1 |
| `.prov-body` | browser_provenance.mjs, browser_stage1_trusted_delivery.mjs | 8 |
| `.prov-body .prov-card` | browser_provenance.mjs, browser_stage1_trusted_delivery.mjs | 2 |
| `.prov-body .prov-retry` | browser_provenance.mjs | 5 |
| `.prov-card` | browser_provenance.mjs, browser_stage1_trusted_delivery.mjs | 2 |
| `.prov-dlitem` | browser_p1_controls.mjs | 1 |
| `.prov-link` | browser_provenance.mjs, browser_stage1_trusted_delivery.mjs | 4 |
| `.prov-subtab` | browser_provenance.mjs, browser_smoke.mjs, browser_stage1_trusted_delivery.mjs | 6 |
| `.recovery-action-list` | browser_smoke.mjs | 1 |
| `.recovery-action-list button` | browser_smoke.mjs | 1 |
| `.ref-problems` | browser_p1_controls.mjs | 1 |
| `.renderer-note` | browser_smoke.mjs | 1 |
| `.s-child-tag` | browser_p1_controls.mjs | 1 |
| `.s-json` | browser_p1_controls.mjs | 2 |
| `.s-meta` | browser_p1_controls.mjs | 1 |
| `.s-out-tgl` | browser_p1_controls.mjs | 1 |
| `.session[data-frame-id="*"] .s-name` | browser_navigation.mjs | 2 |
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
| `[*]` | browser_smoke.mjs | 1 |
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
| `[data-f16-provenance="1"]` | browser_provenance.mjs, browser_smoke.mjs | 2 |
| `[data-f16-provenance="back"]` | browser_smoke.mjs | 1 |
| `[data-focus-key="branch-activate:*"]` | browser_lab.mjs | 2 |
| `[data-i18n="conv.jumpLastLabel"]` | browser_smoke.mjs | 1 |
| `[data-judgment-ack-cap]` | browser_judgment.mjs | 1 |
| `[data-judgment-ack]` | browser_judgment.mjs | 1 |
| `[data-judgment-cap="skill_suggest"]` | browser_judgment.mjs | 2 |
| `[data-judgment-cap="skill_suggest"] .toggle` | browser_judgment.mjs | 1 |
| `[data-judgment-cap="skill_suggest"][data-judgment-cap-on="on"]` | browser_judgment.mjs | 1 |
| `[data-judgment-clear-key]` | browser_judgment.mjs | 1 |
| `[data-judgment-disclosure]` | browser_judgment.mjs | 3 |
| `[data-judgment-key-state]` | browser_judgment.mjs | 2 |
| `[data-judgment-key]` | browser_judgment.mjs | 1 |
| `[data-judgment-master]` | browser_judgment.mjs | 1 |
| `[data-judgment-master] .toggle` | browser_judgment.mjs | 1 |
| `[data-judgment-save-key]` | browser_judgment.mjs | 1 |
| `[data-judgment-test-result]` | browser_judgment.mjs | 3 |
| `[data-judgment-test]` | browser_judgment.mjs | 2 |
| `[data-judgment]` | browser_judgment.mjs | 1 |
| `[data-read-error="sessions"]` | browser_navigation.mjs | 3 |
| `[data-read-error="sessions"] button` | browser_navigation.mjs | 1 |
| `[data-variable-inspector="python"]` | browser_smoke.mjs | 1 |
| `[title], [placeholder], [aria-label], input, textarea` | browser_smoke.mjs | 1 |
| `a[download="*"]` | browser_smoke.mjs | 1 |
| `body.sidebar-collapsed` | browser_smoke.mjs | 3 |
| `button.outline-btn` | browser_auth.mjs | 1 |
| `button.toggle` | browser_matrix.mjs | 2 |
| `details.delegation-evidence-list[data-details-key="delegation-evidence:c-checked"]` | browser_p1_controls.mjs | 2 |
| `img.nbc-fig` | browser_smoke.mjs | 3 |
| `navigation-evidence.txt` | browser_navigation.mjs | 2 |
| `script[src*="/static/dist/"]` | browser_smoke.mjs | 1 |
| `table.nbc-table` | browser_smoke.mjs | 1 |
| `textarea.edit-area` | browser_editor.mjs | 1 |

Total selectors: 293

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
    "hint",
    "loadAnnotations",
    "loadArtifacts",
    "loadEarlierActionTimeline",
    "loadWorkbenchState",
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
    "renderViewer",
    "sanitizeActionTimeline",
    "searchResultHttpUrl",
    "selectExecFrame",
    "send",
    "setActiveTab",
    "showDashboard",
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
    "filesScope",
    "project",
    "provMode",
    "provSub",
    "running",
    "workbenchErrors"
  ],
  "s_star_occurrences": 127,
  "selectors": [
    "#b",
    "#back-home",
    "#cancel-btn",
    "#composer",
    "#composer-ac",
    "#composer-ac .ac-list .ac-item",
    "#composer-ac > .ac-hint",
    "#composer-hint",
    "#composer-refs",
    "#conv-title",
    "#cross-frame",
    "#cust .cust-row",
    "#cust .prof-row",
    "#cust .seg-btn",
    "#cust .toggle",
    "#cust-close",
    "#cust-content",
    "#cust-content .cust-h",
    "#cust-content .prof-row",
    "#cust-content[aria-busy=\"false\"]",
    "#cust:not(.hidden)",
    "#customize-btn",
    "#dash-import-session",
    "#dash-new-project",
    "#dash-new-project svg > *",
    "#dash-project-search",
    "#dash-projects",
    "#dash-projects .d-row",
    "#dash-projects .d-row:not(.skeleton-row)",
    "#dash-sessions",
    "#dash-sessions .d-row:not(.skeleton-row), #dash-sessions .dash-empty",
    "#dashboard",
    "#dashboard .lang-btn[data-lang=\"*\"]",
    "#dashboard [data-i18n=\"dash.col.projects\"]",
    "#dock-files",
    "#dock-files:not(.hidden)",
    "#dock-lab",
    "#dock-notebook",
    "#dock-notebook .nb-repl",
    "#dock-notebook .nb-repl-input",
    "#dock-notebook .nb-status",
    "#dock-notebook .notebook-cell",
    "#dock-notebook:not(.hidden)",
    "#dock-tabs .dock-tab",
    "#dock-timeline",
    "#dock-timeline .branch-row:not(.current)",
    "#dock-timeline .delegation-panel",
    "#dock-toggle",
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
    "#new-session",
    "#onboarding",
    "#p",
    "#proj-btn",
    "#proj-menu .proj-item",
    "#results-count",
    "#results-list .a-name",
    "#results-list .art",
    "#results-list .art[data-artifact-id=\"*\"]",
    "#results-list .files-empty",
    "#revision",
    "#rightdock.collapsed",
    "#rightdock:not(.collapsed)",
    "#send-btn",
    "#session-list .folder-name",
    "#session-list .session",
    "#session-menu-btn",
    "#session-more",
    "#settings-gear",
    "#sidebar",
    "#sidebar-collapse",
    "#sidebar-collapse svg > *",
    "#sidebar-reopen",
    "#stage0-completion-link-probe a",
    "#tab-new",
    "#tabbar",
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
    "#ws-theme svg",
    ".annot-layer",
    ".annot-pin[data-annotation-status]",
    ".annot-pop .annot-btn.danger",
    ".annot-pop-status[data-annotation-status]",
    ".art[data-artifact-id=\"*\"]",
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
    ".delegation-evidence",
    ".delegation-evidence-row .dlg-chip.completed",
    ".delegation-evidence-row .dlg-chip.warning",
    ".delegation-evidence-scope",
    ".dlg-chip",
    ".dlg-frame-ref",
    ".edit-acts .solid-btn",
    ".edit-recovery button",
    ".edit-status",
    ".editor-draft",
    ".editor-draft button",
    ".editor-drafts",
    ".files-empty",
    ".files-filter-type",
    ".files-index-note",
    ".files-load-more",
    ".files-origin [data-origin=\"*\"]",
    ".files-origin [data-origin=\"all\"]",
    ".files-scope [data-scope=\"frame\"]",
    ".files-scope [data-scope=\"project\"]",
    ".files-search",
    ".history-load-status",
    ".history-load-status button",
    ".history-load-status[data-history-state=\"partial\"]",
    ".history-load-status[data-history-state=\"partial\"], .history-load-status[data-history-state=\"error\"]",
    ".lab-command",
    ".lab-connection",
    ".lab-metrics",
    ".lab-metrics span",
    ".lab-setup select",
    ".lab-stop",
    ".msg-fork-btn[data-fork-message-id=\"*\"]",
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
    ".nbc-artifact-error:not([data-before-retry])",
    ".nbc-error",
    ".notebook-cell",
    ".notebook-cell.flash",
    ".notebook-cell[data-producing-cell=\"*\"]",
    ".perm-allow",
    ".perm-card.resolved",
    ".perm-card:not(.resolved)",
    ".perm-scope .perm-seg",
    ".prov-body",
    ".prov-body .prov-card",
    ".prov-body .prov-retry",
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
    ".session[data-frame-id=\"*\"] .s-name",
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
    "[*]",
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
    "[data-focus-key=\"branch-activate:*\"]",
    "[data-i18n=\"conv.jumpLastLabel\"]",
    "[data-judgment-ack-cap]",
    "[data-judgment-ack]",
    "[data-judgment-cap=\"skill_suggest\"]",
    "[data-judgment-cap=\"skill_suggest\"] .toggle",
    "[data-judgment-cap=\"skill_suggest\"][data-judgment-cap-on=\"on\"]",
    "[data-judgment-clear-key]",
    "[data-judgment-disclosure]",
    "[data-judgment-key-state]",
    "[data-judgment-key]",
    "[data-judgment-master]",
    "[data-judgment-master] .toggle",
    "[data-judgment-save-key]",
    "[data-judgment-test-result]",
    "[data-judgment-test]",
    "[data-judgment]",
    "[data-read-error=\"sessions\"]",
    "[data-read-error=\"sessions\"] button",
    "[data-variable-inspector=\"python\"]",
    "[title], [placeholder], [aria-label], input, textarea",
    "a[download=\"*\"]",
    "body.sidebar-collapsed",
    "button.outline-btn",
    "button.toggle",
    "details.delegation-evidence-list[data-details-key=\"delegation-evidence:c-checked\"]",
    "img.nbc-fig",
    "navigation-evidence.txt",
    "script[src*=\"/static/dist/\"]",
    "table.nbc-table",
    "textarea.edit-area"
  ]
}
```
