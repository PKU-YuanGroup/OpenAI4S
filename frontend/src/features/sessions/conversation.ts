/** openConversation, newSession, resumeWatch, routing. app.js:7087-7219, 2678-2706, 13231-13248. */

import { t } from "../../i18n";
import { _msgEarlierLoading, _openGen, currentId, project } from "../../stores/session";
import { apiErrorText } from "./api";
import { binds } from "./binds";
import { hint } from "./chrome";
import { showDashboard, showWorkspace } from "./dashboard";
import { $, FRAME_ROUTE, PROJECT_ROUTE } from "./dom";
import { callLane } from "./lane";
import { loadProjects, loadSessions, loadSessionsForScope, sessionListScope } from "./load";
import { beginNavigation, resetSessionDirectory } from "./navigation";
import { adoptCreatedFrame, createUploadSession } from "../chrome/upload";
import { openConversation, recoverConversation } from "../messages/open";
import { renderProjMenu } from "./projects";
import { resetNotebookCellCaches } from "../notebook/chrome";
import { unsub } from "../ws/connect";

/**
 * F-11 owns `resumeWatch` (send/ticket.ts). This lane carried a
 * character-for-character duplicate whose only difference was reaching
 * `openConversation` directly instead of through the lane call -- and
 * both copies were live, split by which module imported which. Same
 * shape as the `openConversation` pair above.
 */
export { resumeWatch } from "../send/ticket";

/**
 * Who owns the *view*. Not a list-read owner: session and folder rows are
 * scoped to their project (`load.ts: listScope`), and the project menu cancels
 * a pending open through `projects.ts: projectFilterVersion`.
 */
type ViewOwner = { generation: number; projectId: string | null };
const viewOwner = (): ViewOwner => ({ generation: _openGen.value, projectId: project.value });
const ownsView = (owner: ViewOwner): boolean =>
  _openGen.value === owner.generation && project.value === owner.projectId;

export async function newSession(projectId?: string): Promise<void> {
  // onclick passes a MouseEvent, and `window.newSession` is reachable from the
  // legacy shell too. Only an explicit string is a project override; a user
  // click creates the new conversation in the active project.
  const requestedProject = typeof projectId === "string" ? projectId : undefined;
  const targetProject =
    requestedProject === undefined ? project.value || null : requestedProject || null;
  const fresh = !!currentId.value;
  // A deliberate New click owns navigation before its POST. The previous
  // creation may already have published its id but still be opening; its
  // later open must not invalidate this newer intent. With no open frame,
  // keep the visit so Attach and the first Send still share their creation.
  if (fresh) beginNavigation();
  const owner = viewOwner();
  try {
    // Empty-project auto creation, Attach, and the first Send all share this
    // promise. They cannot create sibling frames and split bytes from text.
    // With a conversation already open there is nothing to share: uploads bind
    // to currentId directly, so the only thing the shared promise could do is
    // collapse two deliberate New-session clicks into one frame.
    const creation = createUploadSession(targetProject, { fresh });
    const frameId = await creation;
    if (currentId.value === frameId && (project.value || null) === targetProject) {
      // The shared creation adopted its own frame and is still opening it.
      // Callers such as openProject await this function for the conversation,
      // not for the id: resolving here left them racing an open that had not
      // happened yet.
      await creation.opened;
    } else {
      if (!ownsView(owner) || (project.value || null) !== targetProject) return;
      // Release the previous conversation the way openConversation would,
      // BEFORE the new id is published: openConversation derives "previous"
      // from currentId, and publishing first made it see the new frame as its
      // own predecessor -- the old subscription and the notebook caches were
      // then never released, and A's artifact events kept landing in B.
      const previous = currentId.value;
      if (previous && previous !== frameId) {
        unsub(previous);
        resetNotebookCellCaches(previous, frameId);
      }
      await adoptCreatedFrame(frameId, targetProject, { loadSessions, openConversation });
    }
    if (currentId.value === frameId) $("#composer")?.focus();
  } catch (e) {
    if (!ownsView(owner)) return;
    const error = t("folder.create.failed", apiErrorText(e));
    hint(error, true);
    if (fresh && currentId.value) {
      // The failed intent invalidated reads of the retained frame. Recover
      // them under a new owner, without resending creation or rolling back
      // the generation (which would admit the old outstanding responses).
      const retained = currentId.value;
      _msgEarlierLoading.value = false;
      // The sidebar scope may differ from the retained frame's project.
      // Recovery keeps its address and scope, and follows a newer directory
      // refresh immediately rather than waiting for a superseded socket.
      await Promise.allSettled([
        loadSessionsForScope(sessionListScope()),
        recoverConversation(retained, owner.generation),
        Promise.resolve(callLane("loadArtifacts", retained)),
      ]);
      if (ownsView(owner) && currentId.value === retained) hint(error, true);
    }
  }
}

/**
 * F-10 owns `openConversation`: this lane's copy painted its first page
 * with a synchronous forEach and a bare `messages.innerHTML = ""`, which
 * is the 640-message stall F-10 exists to remove. Both copies existed and
 * both were reachable -- `window.openConversation` was F-10's, while
 * `binds.openConversation` (dashboard, sidebar, project-open, routing) was
 * this one, so the live path never got the framed paint or
 * `cancelFramedRender`. F-10's `resetSessionScoped` is a strict superset
 * of the reset this copy did inline.
 */
export { openConversation };

/**
 * Show what the address names. Routing replaces the address it resolves
 * (`/projects/<pid>` becomes that project's first session) instead of pushing
 * a new entry: pushed, Back returned to `/projects/<pid>`, whose popstate
 * routed and pushed again, so Back could never leave a project deep link.
 */
export async function routeInitialView(): Promise<void> {
  const path = (typeof location !== "undefined" && location.pathname) || "/";
  const fm = path.match(FRAME_ROUTE);
  if (fm) {
    const pid = decodeURIComponent(fm[1] || "");
    const fid = decodeURIComponent(fm[2] || "");
    project.value = pid;
    resetSessionDirectory();
    const owner = viewOwner();
    showWorkspace();
    await loadProjects();
    if (!ownsView(owner)) return;
    await loadSessionsForScope(sessionListScope());
    if (!ownsView(owner)) return;
    renderProjMenu();
    await openConversation(fid, pid, { replaceUrl: true });
    return;
  }
  const pm = path.match(PROJECT_ROUTE);
  if (pm) {
    const pid = decodeURIComponent(pm[1] || "");
    const owner = viewOwner();
    const { openProject } = await import("./projects");
    if (ownsView(owner)) await openProject(pid, { replaceUrl: true });
    return;
  }
  showDashboard();
}

binds.openConversation = openConversation;
binds.newSession = newSession;
