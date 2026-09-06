import { signal, type Signal } from "@preact/signals";
import { artifactsSignals } from "./artifacts";
import { customizeSignals } from "./customize";
import { notebookSignals } from "./notebook";
import { sessionSignals } from "./session";
import { streamSignals } from "./stream";
import { timelineSignals } from "./timeline";
import { uiSignals } from "./ui";

export type StoreId =
  | "session"
  | "stream"
  | "notebook"
  | "timeline"
  | "artifacts"
  | "ui"
  | "customize";

export type SFieldOrigin = "declared" | "dynamic";

export type SFieldMeta = {
  name: string;
  store: StoreId;
  origin: SFieldOrigin;
  originLine: number;
  identity: boolean;
};

/**
 * One row per `S.<name>` field. Origin line is the `const S = {…}`
 * declaration (app.js:120-131) or the dynamic mount (5176/5180/5323/…).
 * `identity: true` means tests or the live client mutate the object in
 * place — the Proxy must return the same reference on get.
 */
export const S_FIELD_META: readonly SFieldMeta[] = [
  { name: "projects", store: "session", origin: "declared", originLine: 120, identity: false },
  { name: "sessions", store: "session", origin: "declared", originLine: 120, identity: false },
  { name: "project", store: "session", origin: "declared", originLine: 120, identity: false },
  { name: "currentId", store: "session", origin: "declared", originLine: 120, identity: false },
  { name: "sandboxOrigin", store: "session", origin: "declared", originLine: 120, identity: false },
  { name: "_titleName", store: "session", origin: "declared", originLine: 120, identity: false },
  { name: "annotations", store: "session", origin: "declared", originLine: 120, identity: false },
  { name: "_annotDraft", store: "session", origin: "declared", originLine: 120, identity: false },
  { name: "editingProject", store: "session", origin: "dynamic", originLine: 6873, identity: false },
  { name: "folders", store: "session", origin: "dynamic", originLine: 7021, identity: false },
  { name: "_foldersFor", store: "session", origin: "dynamic", originLine: 7021, identity: false },
  { name: "_folderCollapsed", store: "session", origin: "dynamic", originLine: 7042, identity: true },
  { name: "_sessionScope", store: "session", origin: "dynamic", originLine: 6985, identity: false },
  { name: "sessionPages", store: "session", origin: "dynamic", originLine: 6985, identity: false },
  { name: "_sessionsLoadingMore", store: "session", origin: "dynamic", originLine: 7006, identity: false },
  { name: "sessionsHasMore", store: "session", origin: "dynamic", originLine: 6997, identity: false },
  { name: "_openGen", store: "session", origin: "dynamic", originLine: 7137, identity: false },
  { name: "msgCursor", store: "session", origin: "dynamic", originLine: 7134, identity: false },
  { name: "msgHasEarlier", store: "session", origin: "dynamic", originLine: 7134, identity: false },
  { name: "_msgEarlierLoading", store: "session", origin: "dynamic", originLine: 7134, identity: false },
  { name: "feedback", store: "session", origin: "dynamic", originLine: 7163, identity: true },
  { name: "lastAnnotationReservation", store: "session", origin: "dynamic", originLine: 8043, identity: false },
  { name: "ws", store: "stream", origin: "declared", originLine: 120, identity: false },
  { name: "stream", store: "stream", origin: "declared", originLine: 120, identity: true },
  { name: "running", store: "stream", origin: "declared", originLine: 120, identity: false },
  { name: "planMode", store: "stream", origin: "declared", originLine: 120, identity: false },
  { name: "exploreMode", store: "stream", origin: "declared", originLine: 120, identity: false },
  { name: "planPending", store: "stream", origin: "declared", originLine: 120, identity: false },
  { name: "planReady", store: "stream", origin: "declared", originLine: 120, identity: true },
  { name: "planStatus", store: "stream", origin: "declared", originLine: 120, identity: false },
  { name: "_seqSeen", store: "stream", origin: "dynamic", originLine: 5176, identity: true },
  { name: "_streamEpoch", store: "stream", origin: "dynamic", originLine: 5180, identity: false },
  { name: "_replayGap", store: "stream", origin: "dynamic", originLine: 5197, identity: false },
  { name: "stepEls", store: "stream", origin: "dynamic", originLine: 5458, identity: true },
  { name: "reviewGate", store: "stream", origin: "dynamic", originLine: 5571, identity: true },
  { name: "turnTicket", store: "stream", origin: "dynamic", originLine: 5680, identity: false },
  { name: "pendingRequestId", store: "stream", origin: "dynamic", originLine: 5687, identity: false },
  { name: "pendingExecutionId", store: "stream", origin: "dynamic", originLine: 5690, identity: false },
  { name: "_resumeTimer", store: "stream", origin: "dynamic", originLine: 5838, identity: false },
  { name: "_resumeTok", store: "stream", origin: "dynamic", originLine: 5838, identity: false },
  { name: "permCards", store: "stream", origin: "dynamic", originLine: 6490, identity: true },
  { name: "cells", store: "notebook", origin: "declared", originLine: 120, identity: true },
  { name: "kernels", store: "notebook", origin: "declared", originLine: 120, identity: true },
  { name: "liveCells", store: "notebook", origin: "declared", originLine: 120, identity: true },
  { name: "_liveCell", store: "notebook", origin: "declared", originLine: 120, identity: true },
  { name: "kernelFilter", store: "notebook", origin: "declared", originLine: 120, identity: false },
  { name: "variableInspector", store: "notebook", origin: "declared", originLine: 131, identity: true },
  { name: "pendingReplIdentity", store: "notebook", origin: "dynamic", originLine: 2993, identity: true },
  { name: "execSources", store: "notebook", origin: "dynamic", originLine: 7141, identity: true },
  { name: "lineage", store: "notebook", origin: "dynamic", originLine: 7148, identity: true },
  { name: "_lineageFor", store: "notebook", origin: "dynamic", originLine: 7148, identity: false },
  { name: "_lineageReq", store: "notebook", origin: "dynamic", originLine: 8374, identity: false },
  { name: "artifactWorkbench", store: "notebook", origin: "dynamic", originLine: 8710, identity: false },
  { name: "_executionLoadReq", store: "notebook", origin: "dynamic", originLine: 9747, identity: false },
  { name: "_nbDirty", store: "notebook", origin: "dynamic", originLine: 9906, identity: false },
  { name: "_nbReading", store: "notebook", origin: "dynamic", originLine: 9906, identity: false },
  { name: "_nbSched", store: "notebook", origin: "dynamic", originLine: 9907, identity: false },
  { name: "_replDraft", store: "notebook", origin: "dynamic", originLine: 10430, identity: false },
  { name: "_replDrafts", store: "notebook", origin: "dynamic", originLine: 10430, identity: true },
  { name: "_replLanguage", store: "notebook", origin: "dynamic", originLine: 10431, identity: false },
  { name: "actionTimeline", store: "timeline", origin: "declared", originLine: 124, identity: true },
  { name: "executionQueue", store: "timeline", origin: "declared", originLine: 124, identity: true },
  { name: "executionIdentity", store: "timeline", origin: "declared", originLine: 124, identity: true },
  { name: "recoveryState", store: "timeline", origin: "declared", originLine: 124, identity: true },
  { name: "actionTimelineSelectedGroupId", store: "timeline", origin: "declared", originLine: 125, identity: false },
  { name: "actionTimelineSelectedBranchId", store: "timeline", origin: "declared", originLine: 125, identity: false },
  { name: "recoveryActions", store: "timeline", origin: "declared", originLine: 126, identity: true },
  { name: "branchState", store: "timeline", origin: "declared", originLine: 126, identity: true },
  { name: "branchUndo", store: "timeline", origin: "declared", originLine: 126, identity: true },
  { name: "contextState", store: "timeline", origin: "declared", originLine: 126, identity: true },
  { name: "securityState", store: "timeline", origin: "declared", originLine: 126, identity: true },
  { name: "computeTasks", store: "timeline", origin: "declared", originLine: 126, identity: true },
  { name: "delegationState", store: "timeline", origin: "declared", originLine: 127, identity: true },
  { name: "workbenchErrors", store: "timeline", origin: "declared", originLine: 129, identity: true },
  { name: "_workbenchReq", store: "timeline", origin: "declared", originLine: 129, identity: false },
  { name: "_timelineHistoryReq", store: "timeline", origin: "declared", originLine: 129, identity: false },
  { name: "_timelineHistoryLoading", store: "timeline", origin: "declared", originLine: 129, identity: true },
  { name: "_timelineView", store: "timeline", origin: "declared", originLine: 129, identity: true },
  { name: "_recoveryActionLoading", store: "timeline", origin: "declared", originLine: 130, identity: false },
  { name: "_branchActionLoading", store: "timeline", origin: "declared", originLine: 130, identity: false },
  { name: "_timelineRestoreFocusGroupId", store: "timeline", origin: "declared", originLine: 130, identity: false },
  { name: "_workbenchLoading", store: "timeline", origin: "dynamic", originLine: 3356, identity: false },
  { name: "_workbenchTimer", store: "timeline", origin: "dynamic", originLine: 3387, identity: false },
  { name: "_branchConversationTimer", store: "timeline", origin: "dynamic", originLine: 3391, identity: false },
  { name: "computeStatus", store: "timeline", origin: "dynamic", originLine: 7153, identity: true },
  { name: "artifacts", store: "artifacts", origin: "declared", originLine: 120, identity: true },
  { name: "dockArtifact", store: "artifacts", origin: "declared", originLine: 120, identity: true },
  { name: "filesScope", store: "artifacts", origin: "declared", originLine: 120, identity: false },
  { name: "projectArtifacts", store: "artifacts", origin: "declared", originLine: 120, identity: true },
  { name: "_projArtFor", store: "artifacts", origin: "declared", originLine: 120, identity: false },
  { name: "rendererCatalog", store: "artifacts", origin: "declared", originLine: 121, identity: true },
  { name: "_rendererCatalogPromise", store: "artifacts", origin: "declared", originLine: 121, identity: true },
  { name: "rendererDescriptors", store: "artifacts", origin: "declared", originLine: 121, identity: true },
  { name: "_artBust", store: "artifacts", origin: "dynamic", originLine: 5323, identity: true },
  { name: "_tbl", store: "artifacts", origin: "dynamic", originLine: 5334, identity: true },
  { name: "_editing", store: "artifacts", origin: "dynamic", originLine: 7158, identity: false },
  { name: "_computeLostSeen", store: "artifacts", origin: "dynamic", originLine: 8244, identity: true },
  { name: "_artVer", store: "artifacts", origin: "dynamic", originLine: 8355, identity: true },
  { name: "_envSnapById", store: "artifacts", origin: "dynamic", originLine: 8376, identity: true },
  { name: "_artifactLoadReq", store: "artifacts", origin: "dynamic", originLine: 8381, identity: false },
  { name: "_thumbCache", store: "artifacts", origin: "dynamic", originLine: 8429, identity: true },
  { name: "_editorAC", store: "artifacts", origin: "dynamic", originLine: 9484, identity: true },
  { name: "_molView", store: "artifacts", origin: "dynamic", originLine: 9615, identity: true },
  { name: "_molViewer", store: "artifacts", origin: "dynamic", originLine: 9613, identity: true },
  { name: "dock", store: "ui", origin: "declared", originLine: 120, identity: true },
  { name: "openTabs", store: "ui", origin: "declared", originLine: 120, identity: true },
  { name: "activeTab", store: "ui", origin: "declared", originLine: 120, identity: false },
  { name: "provMode", store: "ui", origin: "declared", originLine: 120, identity: false },
  { name: "provSub", store: "ui", origin: "declared", originLine: 120, identity: false },
  { name: "_menu", store: "ui", origin: "declared", originLine: 120, identity: true },
  { name: "_dashPoll", store: "ui", origin: "dynamic", originLine: 6755, identity: false },
  { name: "_modalMode", store: "ui", origin: "dynamic", originLine: 6790, identity: false },
  { name: "_jobPoll", store: "ui", origin: "dynamic", originLine: 11789, identity: false },
  { name: "_messagesFollow", store: "ui", origin: "dynamic", originLine: 12938, identity: false },
  { name: "models", store: "customize", origin: "declared", originLine: 120, identity: true },
  { name: "defaultModel", store: "customize", origin: "declared", originLine: 120, identity: false },
  { name: "skillsCatalog", store: "customize", origin: "declared", originLine: 120, identity: true },
  { name: "environmentStatus", store: "customize", origin: "declared", originLine: 128, identity: true },
  { name: "standardProfileReadiness", store: "customize", origin: "declared", originLine: 128, identity: true },
  { name: "_environmentStatusPromise", store: "customize", origin: "declared", originLine: 128, identity: true },
  { name: "_environmentStatusRefreshFailed", store: "customize", origin: "declared", originLine: 128, identity: false },
  { name: "defaultModelName", store: "customize", origin: "dynamic", originLine: 8344, identity: false },
];

/** Fields whose object identity E2E asserts (`===` after write / nested mutate). */
export const IDENTITY_S_FIELDS = ["_timelineView", "actionTimeline", "executionQueue"] as const;

export const sSignals = {
  ...sessionSignals,
  ...streamSignals,
  ...notebookSignals,
  ...timelineSignals,
  ...artifactsSignals,
  ...uiSignals,
  ...customizeSignals,
} as Record<string, Signal<unknown>>;

function signalFor(
  signals: Record<string, Signal<unknown>>,
  name: string,
): Signal<unknown> {
  const existing = signals[name];
  if (existing) return existing;
  const created = signal(undefined as unknown);
  signals[name] = created;
  return created;
}

/**
 * `window.S` Proxy: get returns `signal.value`, set writes `signal.value`.
 * Nested writes (`S._timelineView.searchQuery = …`) hit the stored object
 * itself, so identity-sensitive fields must be stored by reference.
 */
export function createSProxy(
  signals: Record<string, Signal<unknown>> = sSignals,
): object {
  return new Proxy(
    {},
    {
      get(_target, prop) {
        if (prop === Symbol.toStringTag) return "Object";
        if (typeof prop !== "string") return undefined;
        const sig = signals[prop];
        return sig ? sig.value : undefined;
      },
      set(_target, prop, value) {
        if (typeof prop !== "string") return false;
        signalFor(signals, prop).value = value;
        return true;
      },
      has(_target, prop) {
        return typeof prop === "string" && Object.prototype.hasOwnProperty.call(signals, prop);
      },
      ownKeys() {
        return Reflect.ownKeys(signals);
      },
      getOwnPropertyDescriptor(_target, prop) {
        if (typeof prop !== "string" || !Object.prototype.hasOwnProperty.call(signals, prop)) {
          return undefined;
        }
        return {
          configurable: true,
          enumerable: true,
          writable: true,
          value: signals[prop]!.value,
        };
      },
      defineProperty() {
        return false;
      },
      deleteProperty(_target, prop) {
        if (typeof prop !== "string") return false;
        const sig = signals[prop];
        if (!sig) return true;
        sig.value = undefined;
        return true;
      },
    },
  );
}
