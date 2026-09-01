/** openConversation, newSession, resumeWatch, routing. app.js:7087-7219, 2678-2706, 13231-13248. */

import { t } from "../../i18n";
import { _projArtFor, _tbl } from "../../stores/artifacts";
import { defaultModelName } from "../../stores/customize";
import {
  _lineageFor,
  _liveCell,
} from "../../stores/notebook";
import {
  _msgEarlierLoading,
  _openGen,
  _titleName,
  project,
} from "../../stores/session";
import {
  _resumeTimer,
  _resumeTok,
} from "../../stores/stream";
import {
  _branchActionLoading,
  _branchConversationTimer,
  _recoveryActionLoading,
  _timelineHistoryLoading,
  _timelineHistoryReq,
  _timelineRestoreFocusGroupId,
  _workbenchLoading,
  _workbenchReq,
  _workbenchTimer,
} from "../../stores/timeline";
import { api, apiErrorText } from "./api";
import { binds } from "./binds";
import { hint } from "./chrome";
import { showDashboard, showWorkspace } from "./dashboard";
import {
  $,
} from "./dom";
import { loadProjects, loadSessions } from "./load";
import { openConversation } from "../messages/open";
import { renderProjMenu } from "./projects";


/**
 * F-11 owns `resumeWatch` (send/ticket.ts). This lane carried a
 * character-for-character duplicate whose only difference was reaching
 * `openConversation` directly instead of through the lane call -- and
 * both copies were live, split by which module imported which. Same
 * shape as the `openConversation` pair above.
 */
export { resumeWatch } from "../send/ticket";

export async function newSession(): Promise<void> {
  try {
    const f = (await api("/frames", {
      method: "POST",
      body: JSON.stringify({
        project_id: project.value || undefined,
        model: defaultModelName.value,
      }),
    })) as { id: string };
    await loadSessions();
    await openConversation(f.id, project.value);
    $("#composer")?.focus();
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
