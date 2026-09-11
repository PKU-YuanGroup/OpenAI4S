import { field } from "./signal-field";

/** S.projects — app.js:120 */
export const projects = field(() => [] as unknown[]);
/** S.sessions — app.js:120 */
export const sessions = field(() => [] as unknown[]);
/** S.project — app.js:120 */
export const project = field(() => null as string | null);
/** S.currentId — app.js:120 */
export const currentId = field(() => null as string | null);
/**
 * S.sandboxOrigin — app.js:120. Declared for field parity with the frozen
 * shell and nothing else: the preview origin is derived from `location` at
 * render time (features/artifacts/preview.ts), so this has no reader.
 */
export const sandboxOrigin = field(() => "");
/** S._titleName — app.js:120 */
export const _titleName = field(() => "");
/** S.annotations — app.js:120 */
export const annotations = field(() => [] as unknown[]);
/** S._annotDraft — app.js:120 */
export const _annotDraft = field(() => null as unknown);
/** S.editingProject — app.js:6873 */
export const editingProject = field(() => null as unknown);
/** S.folders — app.js:7021 */
export const folders = field(() => [] as unknown[]);
/** S._foldersFor — app.js:7021 */
export const _foldersFor = field(() => null as string | null);
/** S._folderCollapsed — app.js:7042; mutated in place */
export const _folderCollapsed = field(() => Object.create(null) as Record<string, unknown>);
/** S._sessionScope — app.js:6985 */
export const _sessionScope = field(() => "");
/** S.sessionPages — app.js:6985 */
export const sessionPages = field(() => 1);
/** S._sessionsLoadingMore — app.js:7006 */
export const _sessionsLoadingMore = field(() => false);
/** S.sessionsHasMore — app.js:6997 */
export const sessionsHasMore = field(() => false);
/** Dashboard project-list search box. Not part of the frozen S field set. */
export const projectsQuery = field(() => "");
/** Opaque keyset cursor for GET /projects. Not part of the frozen S field set. */
export const projectsNextCursor = field(() => null as string | null);
export const projectsHasMore = field(() => false);
export const projectsTotal = field(() => 0);
export const _projectsLoadingMore = field(() => false);
export const projectsLoadError = field(() => false);
/** S._openGen — app.js:7137 */
export const _openGen = field(() => 0);
/** S.msgCursor — app.js:7134 */
export const msgCursor = field(() => null as unknown);
/** S.msgHasEarlier — app.js:7134 */
export const msgHasEarlier = field(() => false);
/** S._msgEarlierLoading — app.js:7134 */
export const _msgEarlierLoading = field(() => false);
/** S.feedback — app.js:7163; mutated in place */
export const feedback = field(() => Object.create(null) as Record<string, unknown>);
/** S.lastAnnotationReservation — app.js:8043 */
export const lastAnnotationReservation = field(() => null as unknown);

/** Read results belong to both a conversation and an opening generation. */
export type HistoryLoadResult = {
  messagesLoaded: boolean;
  stepsLoaded: boolean;
  runStateLoaded: boolean;
  superseded: boolean;
};
export type HistoryLoadState = HistoryLoadResult & {
  fid: string;
  generation: number;
  status: "loading" | "loaded" | "partial" | "error";
  errors: Record<string, string>;
  deferred: boolean;
};
export const historyLoad = field(() => null as HistoryLoadState | null);
export const historyMutation = field(() => 0);
export const historyUnconfirmed = field(() => 0);
const historyPendingSubmissions = field(() => new Set<object>());
export function resetHistorySubmissions(): void {
  historyPendingSubmissions.value = new Set();
  historyUnconfirmed.value = 0;
}
export function beginHistorySubmission(): () => void {
  const pending = historyPendingSubmissions.value;
  const token = {};
  pending.add(token);
  historyUnconfirmed.value = pending.size;
  noteHistoryMutation();
  return () => {
    // Navigation can replace this set. A late admission must not settle a
    // submission belonging to the new visit, even when its frame id matches.
    if (historyPendingSubmissions.value === pending && pending.delete(token)) {
      historyUnconfirmed.value = pending.size;
      noteHistoryMutation();
    }
  };
}
export const historyContent = field(() => null as {
  fid: string;
  messages: Array<Record<string, unknown>>;
  steps: Array<Record<string, unknown>>;
} | null);
export function noteHistoryMutation(): void {
  historyMutation.value += 1;
}

export const sessionSignals = {
  projects,
  sessions,
  project,
  currentId,
  sandboxOrigin,
  _titleName,
  annotations,
  _annotDraft,
  editingProject,
  folders,
  _foldersFor,
  _folderCollapsed,
  _sessionScope,
  sessionPages,
  _sessionsLoadingMore,
  sessionsHasMore,
  _openGen,
  msgCursor,
  msgHasEarlier,
  _msgEarlierLoading,
  feedback,
  lastAnnotationReservation,
};
