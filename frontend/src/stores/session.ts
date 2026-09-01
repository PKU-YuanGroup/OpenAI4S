import { field } from "./signal-field";

/** S.projects — app.js:120 */
export const projects = field(() => [] as unknown[]);
/** S.sessions — app.js:120 */
export const sessions = field(() => [] as unknown[]);
/** S.project — app.js:120 */
export const project = field(() => null as string | null);
/** S.currentId — app.js:120 */
export const currentId = field(() => null as string | null);
/** S.sandboxOrigin — app.js:120 */
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
