# S field migration table

F-05 maps every `S.<name>` field from `openai4s/server/webui/app.js`
onto a `@preact/signals` field in `frontend/src/stores/`. One row per
field. The `window.S` Proxy in `frontend/src/compat/window-exports.ts`
get/set-maps each name onto `store.<name>.value`.

Origin:

- `declared` — `const S = { … }` at app.js:120–131
- `dynamic` — later assignment (`S._seqSeen` at 5176, `S._streamEpoch` at 5180, `S._artBust` at 5323, and siblings)

`identity` fields are stored by reference. Nested writes such as
`S._timelineView.searchQuery = …` and `S._timelineView.collapsedTurns.add(…)`
mutate the same object E2E asserts with `===`.

Do not edit the table by hand independently of `S_FIELD_META` in
`registry.ts` — `migration.test.ts` diffs the two plus `tests/webui-contract.md`.

## `session`

| Original | Store path | Origin | app.js | Identity |
| --- | --- | --- | --- | --- |
| `projects` | `session.projects` | declared | 120 | no |
| `sessions` | `session.sessions` | declared | 120 | no |
| `project` | `session.project` | declared | 120 | no |
| `currentId` | `session.currentId` | declared | 120 | no |
| `sandboxOrigin` | `session.sandboxOrigin` | declared | 120 | no |
| `_titleName` | `session._titleName` | declared | 120 | no |
| `annotations` | `session.annotations` | declared | 120 | no |
| `_annotDraft` | `session._annotDraft` | declared | 120 | no |
| `editingProject` | `session.editingProject` | dynamic | 6873 | no |
| `folders` | `session.folders` | dynamic | 7021 | no |
| `_foldersFor` | `session._foldersFor` | dynamic | 7021 | no |
| `_folderCollapsed` | `session._folderCollapsed` | dynamic | 7042 | yes |
| `_sessionScope` | `session._sessionScope` | dynamic | 6985 | no |
| `sessionPages` | `session.sessionPages` | dynamic | 6985 | no |
| `_sessionsLoadingMore` | `session._sessionsLoadingMore` | dynamic | 7006 | no |
| `sessionsHasMore` | `session.sessionsHasMore` | dynamic | 6997 | no |
| `_openGen` | `session._openGen` | dynamic | 7137 | no |
| `msgCursor` | `session.msgCursor` | dynamic | 7134 | no |
| `msgHasEarlier` | `session.msgHasEarlier` | dynamic | 7134 | no |
| `_msgEarlierLoading` | `session._msgEarlierLoading` | dynamic | 7134 | no |
| `feedback` | `session.feedback` | dynamic | 7163 | yes |
| `lastAnnotationReservation` | `session.lastAnnotationReservation` | dynamic | 8043 | no |

## `stream`

| Original | Store path | Origin | app.js | Identity |
| --- | --- | --- | --- | --- |
| `ws` | `stream.ws` | declared | 120 | no |
| `stream` | `stream.stream` | declared | 120 | yes |
| `running` | `stream.running` | declared | 120 | no |
| `planMode` | `stream.planMode` | declared | 120 | no |
| `exploreMode` | `stream.exploreMode` | declared | 120 | no |
| `planPending` | `stream.planPending` | declared | 120 | no |
| `planReady` | `stream.planReady` | declared | 120 | yes |
| `planStatus` | `stream.planStatus` | declared | 120 | no |
| `_seqSeen` | `stream._seqSeen` | dynamic | 5176 | yes |
| `_streamEpoch` | `stream._streamEpoch` | dynamic | 5180 | no |
| `_replayGap` | `stream._replayGap` | dynamic | 5197 | no |
| `stepEls` | `stream.stepEls` | dynamic | 5458 | yes |
| `reviewGate` | `stream.reviewGate` | dynamic | 5571 | yes |
| `turnTicket` | `stream.turnTicket` | dynamic | 5680 | no |
| `pendingRequestId` | `stream.pendingRequestId` | dynamic | 5687 | no |
| `pendingExecutionId` | `stream.pendingExecutionId` | dynamic | 5690 | no |
| `_resumeTimer` | `stream._resumeTimer` | dynamic | 5838 | no |
| `_resumeTok` | `stream._resumeTok` | dynamic | 5838 | no |
| `permCards` | `stream.permCards` | dynamic | 6490 | yes |

## `notebook`

| Original | Store path | Origin | app.js | Identity |
| --- | --- | --- | --- | --- |
| `cells` | `notebook.cells` | declared | 120 | yes |
| `kernels` | `notebook.kernels` | declared | 120 | yes |
| `liveCells` | `notebook.liveCells` | declared | 120 | yes |
| `_liveCell` | `notebook._liveCell` | declared | 120 | yes |
| `kernelFilter` | `notebook.kernelFilter` | declared | 120 | no |
| `variableInspector` | `notebook.variableInspector` | declared | 131 | yes |
| `pendingReplIdentity` | `notebook.pendingReplIdentity` | dynamic | 2993 | yes |
| `execSources` | `notebook.execSources` | dynamic | 7141 | yes |
| `lineage` | `notebook.lineage` | dynamic | 7148 | yes |
| `_lineageFor` | `notebook._lineageFor` | dynamic | 7148 | no |
| `_lineageReq` | `notebook._lineageReq` | dynamic | 8374 | no |
| `artifactWorkbench` | `notebook.artifactWorkbench` | dynamic | 8710 | no |
| `_executionLoadReq` | `notebook._executionLoadReq` | dynamic | 9747 | no |
| `_nbDirty` | `notebook._nbDirty` | dynamic | 9906 | no |
| `_nbReading` | `notebook._nbReading` | dynamic | 9906 | no |
| `_nbSched` | `notebook._nbSched` | dynamic | 9907 | no |
| `_replDraft` | `notebook._replDraft` | dynamic | 10430 | no |
| `_replDrafts` | `notebook._replDrafts` | dynamic | 10430 | yes |
| `_replLanguage` | `notebook._replLanguage` | dynamic | 10431 | no |

## `timeline`

| Original | Store path | Origin | app.js | Identity |
| --- | --- | --- | --- | --- |
| `actionTimeline` | `timeline.actionTimeline` | declared | 124 | yes |
| `executionQueue` | `timeline.executionQueue` | declared | 124 | yes |
| `executionIdentity` | `timeline.executionIdentity` | declared | 124 | yes |
| `recoveryState` | `timeline.recoveryState` | declared | 124 | yes |
| `actionTimelineSelectedGroupId` | `timeline.actionTimelineSelectedGroupId` | declared | 125 | no |
| `actionTimelineSelectedBranchId` | `timeline.actionTimelineSelectedBranchId` | declared | 125 | no |
| `recoveryActions` | `timeline.recoveryActions` | declared | 126 | yes |
| `branchState` | `timeline.branchState` | declared | 126 | yes |
| `branchUndo` | `timeline.branchUndo` | declared | 126 | yes |
| `contextState` | `timeline.contextState` | declared | 126 | yes |
| `securityState` | `timeline.securityState` | declared | 126 | yes |
| `computeTasks` | `timeline.computeTasks` | declared | 126 | yes |
| `delegationState` | `timeline.delegationState` | declared | 127 | yes |
| `workbenchErrors` | `timeline.workbenchErrors` | declared | 129 | yes |
| `_workbenchReq` | `timeline._workbenchReq` | declared | 129 | no |
| `_timelineHistoryReq` | `timeline._timelineHistoryReq` | declared | 129 | no |
| `_timelineHistoryLoading` | `timeline._timelineHistoryLoading` | declared | 129 | yes |
| `_timelineView` | `timeline._timelineView` | declared | 129 | yes |
| `_recoveryActionLoading` | `timeline._recoveryActionLoading` | declared | 130 | no |
| `_branchActionLoading` | `timeline._branchActionLoading` | declared | 130 | no |
| `_timelineRestoreFocusGroupId` | `timeline._timelineRestoreFocusGroupId` | declared | 130 | no |
| `_workbenchLoading` | `timeline._workbenchLoading` | dynamic | 3356 | no |
| `_workbenchTimer` | `timeline._workbenchTimer` | dynamic | 3387 | no |
| `_branchConversationTimer` | `timeline._branchConversationTimer` | dynamic | 3391 | no |
| `computeStatus` | `timeline.computeStatus` | dynamic | 7153 | yes |

## `artifacts`

| Original | Store path | Origin | app.js | Identity |
| --- | --- | --- | --- | --- |
| `artifacts` | `artifacts.artifacts` | declared | 120 | yes |
| `dockArtifact` | `artifacts.dockArtifact` | declared | 120 | yes |
| `filesScope` | `artifacts.filesScope` | declared | 120 | no |
| `projectArtifacts` | `artifacts.projectArtifacts` | declared | 120 | yes |
| `_projArtFor` | `artifacts._projArtFor` | declared | 120 | no |
| `rendererCatalog` | `artifacts.rendererCatalog` | declared | 121 | yes |
| `_rendererCatalogPromise` | `artifacts._rendererCatalogPromise` | declared | 121 | yes |
| `rendererDescriptors` | `artifacts.rendererDescriptors` | declared | 121 | yes |
| `_artBust` | `artifacts._artBust` | dynamic | 5323 | yes |
| `_tbl` | `artifacts._tbl` | dynamic | 5334 | yes |
| `_editing` | `artifacts._editing` | dynamic | 7158 | no |
| `_computeLostSeen` | `artifacts._computeLostSeen` | dynamic | 8244 | yes |
| `_artVer` | `artifacts._artVer` | dynamic | 8355 | yes |
| `_envSnapById` | `artifacts._envSnapById` | dynamic | 8376 | yes |
| `_artifactLoadReq` | `artifacts._artifactLoadReq` | dynamic | 8381 | no |
| `_thumbCache` | `artifacts._thumbCache` | dynamic | 8429 | yes |
| `_editorAC` | `artifacts._editorAC` | dynamic | 9484 | yes |
| `_molView` | `artifacts._molView` | dynamic | 9615 | yes |
| `_molViewer` | `artifacts._molViewer` | dynamic | 9613 | yes |

## `ui`

| Original | Store path | Origin | app.js | Identity |
| --- | --- | --- | --- | --- |
| `dock` | `ui.dock` | declared | 120 | yes |
| `openTabs` | `ui.openTabs` | declared | 120 | yes |
| `activeTab` | `ui.activeTab` | declared | 120 | no |
| `provMode` | `ui.provMode` | declared | 120 | no |
| `provSub` | `ui.provSub` | declared | 120 | no |
| `_menu` | `ui._menu` | declared | 120 | yes |
| `_dashPoll` | `ui._dashPoll` | dynamic | 6755 | no |
| `_modalMode` | `ui._modalMode` | dynamic | 6790 | no |
| `_jobPoll` | `ui._jobPoll` | dynamic | 11789 | no |
| `_messagesFollow` | `ui._messagesFollow` | dynamic | 12938 | no |

## `customize`

| Original | Store path | Origin | app.js | Identity |
| --- | --- | --- | --- | --- |
| `models` | `customize.models` | declared | 120 | yes |
| `defaultModel` | `customize.defaultModel` | declared | 120 | no |
| `skillsCatalog` | `customize.skillsCatalog` | declared | 120 | yes |
| `environmentStatus` | `customize.environmentStatus` | declared | 128 | yes |
| `standardProfileReadiness` | `customize.standardProfileReadiness` | declared | 128 | yes |
| `_environmentStatusPromise` | `customize._environmentStatusPromise` | declared | 128 | yes |
| `_environmentStatusRefreshFailed` | `customize._environmentStatusRefreshFailed` | declared | 128 | no |
| `defaultModelName` | `customize.defaultModelName` | dynamic | 8344 | no |

## Not on `S` (module-level)

| Original | Store path | Origin | app.js | Identity |
| --- | --- | --- | --- | --- |
| `_kc` | `notebook._kc` | module `const _kc` | 9954 | yes |

F-14 owns cache invalidation (`kernel_status` / `turnDone` / `nbSwitchEnv`).
The object is not exposed on the `S` Proxy.
