/** Navigation identity is independent of each directory request's generation. */
import { _msgEarlierLoading, _openGen, _foldersFor, _sessionScope, _sessionsLoadingMore, folders, foldersLoadError, foldersLoading, project, sessionPages, sessions, sessionsHasMore, sessionsLoadError, sessionsLoading } from "../../stores/session";

export type Navigation = { projectId: string | null; generation: number };
export function navigation(): Navigation {
  return { projectId: project.value, generation: _openGen.value };
}
export function ownsNavigation(owner: Navigation): boolean {
  return project.value === owner.projectId && _openGen.value === owner.generation;
}
export function resetSessionDirectory(): void {
  _sessionScope.value = project.value || "";
  sessions.value = [];
  sessionPages.value = 1;
  sessionsHasMore.value = false;
  folders.value = [];
  _foldersFor.value = null;
  sessionsLoadError.value = false;
  foldersLoadError.value = false;
}
export function beginNavigation(): number {
  _openGen.value++;
  sessionsLoading.value = false;
  foldersLoading.value = false;
  _sessionsLoadingMore.value = false;
  // The superseded paging request can no longer clear this itself. Callers
  // that follow with openConversation reset it again; selectProject keeps the
  // open frame and would otherwise leave "Load earlier" disabled for good.
  _msgEarlierLoading.value = false;
  return _openGen.value;
}
export function beginProjectNavigation(id: string): Navigation {
  beginNavigation();
  project.value = id;
  resetSessionDirectory();
  return navigation();
}
