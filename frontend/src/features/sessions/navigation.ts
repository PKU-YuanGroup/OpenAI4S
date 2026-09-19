/**
 * The view generation and the synchronous directory reset that goes with it.
 *
 * Deliberately *not* a list-read owner. `_openGen` says who owns the view:
 * every conversation open and every trip Home bumps it, and neither makes
 * project P's session rows wrong. List reads are scoped to their project
 * instead (`load.ts: listScope`), and the project menu cancels a pending
 * project open through its own counter (`projects.ts: projectFilterVersion`)
 * rather than by retiring the open conversation's reads.
 */
import { _foldersFor, _msgEarlierLoading, _openGen, _sessionScope, _sessionsLoadingMore, folders, foldersLoadError, foldersLoading, project, sessionPages, sessions, sessionsHasMore, sessionsLoadError, sessionsLoading } from "../../stores/session";

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
  // A paging request superseded by this navigation skips its own `finally`,
  // so it can no longer clear this latch. Every caller that keeps a
  // conversation on screen resets it again through `openConversation`; this is
  // what covers the callers that do not.
  _msgEarlierLoading.value = false;
  return _openGen.value;
}
