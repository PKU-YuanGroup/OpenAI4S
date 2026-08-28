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
- `tests/browser_smoke.mjs`
- `tests/browser_stage0_acceptance.mjs`
- `tests/browser_stage1_trusted_delivery.mjs`
- `tests/browser_team_mode.mjs`

## 1. Bare window globals

Free identifiers inside `page.evaluate` / `waitForFunction` callbacks
(and sibling `.evaluate` on other Playwright pages), minus locals,
keywords, and browser builtins. `window.__*` test hooks are omitted.
Sorted by name.

| Name | Files | Occurrences |
| --- | --- | --- |
| `ACTION_TIMELINE_OVERSCAN` | browser_smoke.mjs | 1 |
| `ACTION_TIMELINE_OVERVIEW_WIDTH` | browser_smoke.mjs | 2 |
| `ACTION_TIMELINE_PAGE_SIZE` | browser_smoke.mjs | 4 |
| `ACTION_TIMELINE_ROW_HEIGHT` | browser_smoke.mjs | 4 |
| `S` | browser_admission_fault.mjs, browser_p1_controls.mjs, browser_smoke.mjs | 191 |
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
| `highlightTraceback` | browser_smoke.mjs | 2 |
| `loadAnnotations` | browser_admission_fault.mjs | 1 |
| `loadEarlierActionTimeline` | browser_smoke.mjs | 1 |
| `mergeDelegationChildEvent` | browser_p1_controls.mjs | 2 |
| `notebookExportLink` | browser_p1_controls.mjs | 1 |
| `onEvent` | browser_p1_controls.mjs, browser_smoke.mjs | 9 |
| `openAnnotations` | browser_admission_fault.mjs | 5 |
| `openConversation` | browser_admission_fault.mjs | 16 |
| `openCust` | browser_p1_controls.mjs | 1 |
| `openPinPop` | browser_admission_fault.mjs | 1 |
| `outstandingAdmissions` | browser_admission_fault.mjs | 16 |
| `parseTable` | browser_smoke.mjs | 1 |
| `reconcileLastAdmission` | browser_admission_fault.mjs | 2 |
| `rememberAdmission` | browser_admission_fault.mjs | 1 |
| `renderActionTimeline` | browser_smoke.mjs | 8 |
| `renderAttachmentProblems` | browser_p1_controls.mjs | 1 |
| `renderComposerRefChips` | browser_p1_controls.mjs | 2 |
| `renderDelegationPanel` | browser_p1_controls.mjs | 2 |
| `renderMd` | browser_smoke.mjs, browser_stage0_acceptance.mjs | 3 |
| `renderMessageRefChips` | browser_p1_controls.mjs | 1 |
| `renderPins` | browser_admission_fault.mjs | 1 |
| `renderRefProblems` | browser_p1_controls.mjs | 1 |
| `renderSheet` | browser_smoke.mjs | 1 |
| `sanitizeActionTimeline` | browser_smoke.mjs | 3 |
| `searchResultHttpUrl` | browser_smoke.mjs | 6 |
| `selectExecFrame` | browser_p1_controls.mjs | 2 |
| `send` | browser_admission_fault.mjs | 4 |
| `setActiveTab` | browser_smoke.mjs, browser_stage0_acceptance.mjs | 6 |
| `steerDelegationChild` | browser_p1_controls.mjs | 1 |
| `t` | browser_matrix.mjs, browser_p1_controls.mjs, browser_smoke.mjs | 10 |
| `telemetryRow` | browser_matrix.mjs | 2 |
| `timelineOverviewTimeToX` | browser_smoke.mjs | 2 |
| `toggleActionTimelineTurn` | browser_smoke.mjs | 1 |
| `updateActionTimelineLedger` | browser_smoke.mjs | 1 |

Total names: 55

## 2. `S` field read/write surface

Top-level `S.<field>` accesses inside evaluate callbacks. Nested writes
(`S._timelineView.searchQuery = …`, `.collapsedTurns.add(...)`) are
listed under write paths so F-05 can keep object identity.

### 2a. Private `S._*` fields

| Field | Read | Write | Nested write | Files | Write paths |
| --- | --- | --- | --- | --- | --- |
| `_timelineHistoryLoading` | 2 | 3 | 0 | browser_smoke.mjs | `_timelineHistoryLoading` |
| `_timelineHistoryReq` | 3 | 3 | 0 | browser_smoke.mjs | `_timelineHistoryReq` |
| `_timelineRestoreFocusGroupId` | 0 | 2 | 0 | browser_smoke.mjs | `_timelineRestoreFocusGroupId` |
| `_timelineView` | 96 | 0 | 4 | browser_smoke.mjs | `_timelineView.autoLoadArmed`, `_timelineView.collapsedTurns.add`, `_timelineView.searchNeedle`, `_timelineView.searchQuery` |
| `_workbenchLoading` | 1 | 2 | 0 | browser_smoke.mjs | `_workbenchLoading` |
| `_workbenchReq` | 2 | 2 | 0 | browser_smoke.mjs | `_workbenchReq` |
| `_workbenchTimer` | 2 | 0 | 0 | browser_smoke.mjs | — |

`S._*` identifier occurrences: 122

### 2b. Other `S.*` fields

| Field | Read | Write | Nested write | Files | Write paths |
| --- | --- | --- | --- | --- | --- |
| `actionTimeline` | 16 | 9 | 0 | browser_smoke.mjs | `actionTimeline` |
| `actionTimelineSelectedBranchId` | 2 | 5 | 0 | browser_smoke.mjs | `actionTimelineSelectedBranchId` |
| `actionTimelineSelectedGroupId` | 7 | 5 | 0 | browser_smoke.mjs | `actionTimelineSelectedGroupId` |
| `annotations` | 1 | 1 | 0 | browser_admission_fault.mjs | `annotations` |
| `artifacts` | 3 | 1 | 0 | browser_admission_fault.mjs, browser_p1_controls.mjs | `artifacts` |
| `currentId` | 14 | 0 | 0 | browser_p1_controls.mjs, browser_smoke.mjs | — |
| `delegationState` | 3 | 4 | 0 | browser_p1_controls.mjs | `delegationState` |
| `workbenchErrors` | 2 | 2 | 0 | browser_smoke.mjs | `workbenchErrors` |

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
| `#composer` | browser_admission_fault.mjs, browser_p1_controls.mjs | 2 |
| `#composer-refs` | browser_p1_controls.mjs | 1 |
| `#cust .cust-row` | browser_p1_controls.mjs | 2 |
| `#cust .prof-row` | browser_p1_controls.mjs | 2 |
| `#cust .toggle` | browser_p1_controls.mjs | 1 |
| `#cust:not(.hidden)` | browser_p1_controls.mjs | 1 |
| `#dash-projects` | browser_smoke.mjs | 1 |
| `#dash-sessions` | browser_smoke.mjs | 1 |
| `#dashboard` | browser_smoke.mjs | 1 |
| `#dock-files:not(.hidden)` | browser_stage1_trusted_delivery.mjs | 1 |
| `#dock-notebook` | browser_smoke.mjs | 1 |
| `#dock-notebook .nb-repl` | browser_stage0_acceptance.mjs | 1 |
| `#dock-notebook .nb-repl-input` | browser_stage0_acceptance.mjs | 1 |
| `#dock-notebook .nb-status` | browser_stage0_acceptance.mjs | 3 |
| `#dock-notebook:not(.hidden)` | browser_smoke.mjs, browser_stage0_acceptance.mjs | 2 |
| `#dock-tabs .dock-tab` | browser_smoke.mjs | 2 |
| `#err` | browser_team_mode.mjs | 1 |
| `#files-btn` | browser_stage1_trusted_delivery.mjs | 1 |
| `#messages` | browser_p1_controls.mjs, browser_smoke.mjs | 2 |
| `#messages .msg.assistant .md a[href^="/api/v1/artifacts/"]` | browser_stage1_trusted_delivery.mjs | 1 |
| `#meta` | browser_team_mode.mjs | 1 |
| `#p` | browser_team_mode.mjs | 1 |
| `#results-list .a-name` | browser_stage1_trusted_delivery.mjs | 1 |
| `#rightdock.collapsed` | browser_smoke.mjs | 1 |
| `#rightdock:not(.collapsed)` | browser_smoke.mjs | 1 |
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
| `#workspace:not(.hidden)` | browser_p1_controls.mjs, browser_smoke.mjs, browser_stage0_acceptance.mjs, browser_stage1_trusted_delivery.mjs | 7 |
| `.annot-layer` | browser_admission_fault.mjs | 1 |
| `.annot-pin[data-annotation-status]` | browser_admission_fault.mjs | 1 |
| `.annot-pop .annot-btn.danger` | browser_admission_fault.mjs | 1 |
| `.annot-pop-status[data-annotation-status]` | browser_admission_fault.mjs | 1 |
| `.branch-name` | browser_smoke.mjs | 2 |
| `.branch-panel` | browser_smoke.mjs | 1 |
| `.checkpoint-row button` | browser_smoke.mjs | 1 |
| `.ctx-item` | browser_stage1_trusted_delivery.mjs | 1 |
| `.delegation-child` | browser_p1_controls.mjs | 3 |
| `.delegation-child-controls button` | browser_p1_controls.mjs | 1 |
| `.dlg-chip` | browser_p1_controls.mjs | 2 |
| `.dlg-frame-ref` | browser_p1_controls.mjs | 1 |
| `.msg-ref-chip` | browser_p1_controls.mjs | 2 |
| `.msg-ref-chip.unresolved` | browser_p1_controls.mjs | 1 |
| `.nb-exec-frame` | browser_p1_controls.mjs | 1 |
| `.nb-exec-note` | browser_p1_controls.mjs | 1 |
| `.nb-exec-title` | browser_p1_controls.mjs | 1 |
| `.nb-tray` | browser_smoke.mjs | 1 |
| `.nb-variables-empty` | browser_smoke.mjs | 1 |
| `.nbc-error` | browser_p1_controls.mjs | 1 |
| `.notebook-cell` | browser_p1_controls.mjs | 1 |
| `.perm-allow` | browser_smoke.mjs | 1 |
| `.perm-card.resolved` | browser_smoke.mjs | 1 |
| `.perm-card:not(.resolved)` | browser_smoke.mjs | 1 |
| `.prov-body` | browser_stage1_trusted_delivery.mjs | 1 |
| `.prov-card` | browser_stage1_trusted_delivery.mjs | 1 |
| `.prov-dlitem` | browser_p1_controls.mjs | 1 |
| `.prov-link` | browser_stage1_trusted_delivery.mjs | 1 |
| `.prov-subtab` | browser_stage1_trusted_delivery.mjs | 1 |
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
| `.viewer-head .vh-acts button` | browser_stage1_trusted_delivery.mjs | 1 |
| `.viewer-head .vh-name` | browser_stage1_trusted_delivery.mjs | 1 |
| `[data-action="load-earlier-timeline"]` | browser_smoke.mjs | 4 |
| `[data-action="load-omitted-timeline"]` | browser_smoke.mjs | 2 |
| `[data-action="refresh-variables"]` | browser_smoke.mjs | 1 |
| `[data-variable-inspector="python"]` | browser_smoke.mjs | 1 |
| `button.toggle` | browser_matrix.mjs | 2 |

Total selectors: 119

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
    "openPinPop",
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
    "annotations",
    "artifacts",
    "currentId",
    "delegationState",
    "workbenchErrors"
  ],
  "s_star_occurrences": 122,
  "selectors": [
    "#b",
    "#composer",
    "#composer-refs",
    "#cust .cust-row",
    "#cust .prof-row",
    "#cust .toggle",
    "#cust:not(.hidden)",
    "#dash-projects",
    "#dash-sessions",
    "#dashboard",
    "#dock-files:not(.hidden)",
    "#dock-notebook",
    "#dock-notebook .nb-repl",
    "#dock-notebook .nb-repl-input",
    "#dock-notebook .nb-status",
    "#dock-notebook:not(.hidden)",
    "#dock-tabs .dock-tab",
    "#err",
    "#files-btn",
    "#messages",
    "#messages .msg.assistant .md a[href^=\"/api/v1/artifacts/\"]",
    "#meta",
    "#p",
    "#results-list .a-name",
    "#rightdock.collapsed",
    "#rightdock:not(.collapsed)",
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
    ".annot-layer",
    ".annot-pin[data-annotation-status]",
    ".annot-pop .annot-btn.danger",
    ".annot-pop-status[data-annotation-status]",
    ".branch-name",
    ".branch-panel",
    ".checkpoint-row button",
    ".ctx-item",
    ".delegation-child",
    ".delegation-child-controls button",
    ".dlg-chip",
    ".dlg-frame-ref",
    ".msg-ref-chip",
    ".msg-ref-chip.unresolved",
    ".nb-exec-frame",
    ".nb-exec-note",
    ".nb-exec-title",
    ".nb-tray",
    ".nb-variables-empty",
    ".nbc-error",
    ".notebook-cell",
    ".perm-allow",
    ".perm-card.resolved",
    ".perm-card:not(.resolved)",
    ".prov-body",
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
    "[data-action=\"load-earlier-timeline\"]",
    "[data-action=\"load-omitted-timeline\"]",
    "[data-action=\"refresh-variables\"]",
    "[data-variable-inspector=\"python\"]",
    "button.toggle"
  ]
}
```
