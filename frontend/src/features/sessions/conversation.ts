/** openConversation, newSession, resumeWatch, routing. app.js:7087-7219, 2678-2706, 13231-13248. */

import { t } from "../../i18n";
import { currentId, project } from "../../stores/session";
import { apiErrorText } from "./api";
import { binds } from "./binds";
import { hint } from "./chrome";
import { showDashboard, showWorkspace } from "./dashboard";
import { $ } from "./dom";
import { loadProjects, loadSessions } from "./load";
import { createUploadSession } from "../chrome/upload";
import { openConversation } from "../messages/open";
import { renderProjMenu } from "./projects";
import { sub } from "../ws/connect";

/**
 * F-11 owns `resumeWatch` (send/ticket.ts). This lane carried a
 * character-for-character duplicate whose only difference was reaching
 * `openConversation` directly instead of through the lane call -- and
 * both copies were live, split by which module imported which. Same
 * shape as the `openConversation` pair above.
 */
export { resumeWatch } from "../send/ticket";

export async function newSession(projectId?: string): Promise<void> {
  // onclick passes a MouseEvent, and `window.newSession` is reachable from the
  // legacy shell too. Only an explicit string is a project override; a user
  // click creates the new conversation in the active project.
  const requestedProject = typeof projectId === "string" ? projectId : undefined;
  const targetProject =
    requestedProject === undefined ? project.value || null : requestedProject || null;
  try {
    // Empty-project auto creation, Attach, and the first Send all share this
    // promise. They cannot create sibling frames and split bytes from text.
    // With a conversation already open there is nothing to share: uploads bind
    // to currentId directly, so the only thing the shared promise could do is
    // collapse two deliberate New-session clicks into one frame.
    const frameId = await createUploadSession(targetProject, { fresh: !!currentId.value });
    if ((project.value || null) !== targetProject) return;
    if (currentId.value !== frameId) {
      // Publish the destination before awaiting the sidebar refresh, so a file
      // selected in that interval binds to this exact new conversation.
      currentId.value = frameId;
      sub(frameId);
      await loadSessions();
      if (currentId.value !== frameId || (project.value || null) !== targetProject) return;
      await openConversation(frameId, targetProject);
    }
    if (currentId.value === frameId) $("#composer")?.focus();
  } catch (e) {
    hint(t("folder.create.failed", apiErrorText(e)), true);
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

export async function routeInitialView(): Promise<void> {
  const path = (typeof location !== "undefined" && location.pathname) || "/";
  const fm = path.match(/^\/projects\/([^/]+)\/frames\/([^/]+)/);
  if (fm) {
    const pid = decodeURIComponent(fm[1] || "");
    const fid = decodeURIComponent(fm[2] || "");
    await loadProjects();
    project.value = pid;
    showWorkspace();
    await loadSessions();
    renderProjMenu();
    await openConversation(fid, pid);
    return;
  }
  const pm = path.match(/^\/projects\/([^/]+)\/?$/);
  if (pm) {
    const pid = decodeURIComponent(pm[1] || "");
    const { openProject } = await import("./projects");
    await openProject(pid);
    return;
  }
  showDashboard();
}

binds.openConversation = openConversation;
binds.newSession = newSession;
