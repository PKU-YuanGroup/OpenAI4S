/** Window exports, F-06 loadSessions hook, and workbench event wiring. */

import { applyStaticI18n, setLang, t } from "../../i18n";
import { _titleName, currentId, editingProject } from "../../stores/session";
import { cycleTheme } from "../theme/theme";
import { setLoadSessionsImpl } from "../ws/handlers";
import {
  addToMessageMenu,
  cancelTurn,
  chooseSessionPackage,
  commitTitle,
  importSessionPackage,
  sessionMenu,
  sessionOptionsMenu,
} from "./actions";
import { hint, watchActivateKeys, watchDisconnect } from "./chrome";
import { newSession, routeInitialView } from "./conversation";
import { setScopedExecutionRequest } from "../notebook/kernel";
import { scopedExecutionRequest } from "../timeline/execution-request";
import { showDashboard } from "./dashboard";
import { $, down, grow, setSidebar, setTitle, syncMobileChrome, updateJumpPill } from "./dom";
import { paintIcons } from "./icon";
import { callLane, hostWindow } from "./lane";
import { loadSessions } from "./load";
import { fetchAllMessages, fetchOlderMessages, fetchRecentMessages } from "./messages";
import {
  closeProjectModal,
  deleteProject,
  openProjectModal,
  renderProjMenu,
  submitProjectModal,
} from "./projects";
import { renderComposerRefChips, renderMessageRefChips } from "./transcript";

export function installSessionExports(
  target: Record<string, unknown> | undefined = hostWindow() ||
    (globalThis as unknown as Record<string, unknown>),
): void {
  if (!target) return;
  target.fetchAllMessages = fetchAllMessages;
  target.fetchOlderMessages = fetchOlderMessages;
  target.fetchRecentMessages = fetchRecentMessages;
  // openConversation is assigned by features/messages (F-10): same reset
  // surface, but its first paint is framed rather than one forEach.
  target.renderMessageRefChips = renderMessageRefChips;
  target.renderComposerRefChips = renderComposerRefChips;
  target.hint = hint;
  target.loadSessions = loadSessions;
  target.showDashboard = showDashboard;
  // The command palette reaches both of these through `hostFn`, and neither
  // was ever assigned anywhere: `isReady(undefined)` is false, so "New session"
  // and "New project" closed the palette and did nothing at all. Installed here
  // rather than added to CONTRACT_GLOBAL_NAMES -- that list is diffed against
  // tests/webui-contract.md, and a stub there would still be un-ready.
  target.newSession = newSession;
  target.openProjectModal = openProjectModal;
  // Without this the Notebook's interrupt fell through to an UNSCOPED
  // POST /frames/<id>/kernel/interrupt carrying no execution_id or owner --
  // a shape app.js never sends, and one that can land on whichever execution
  // started after the one the user meant to stop.
  setScopedExecutionRequest(scopedExecutionRequest);
}

let bound = false;
let initialViewReady: Promise<void> | null = null;

export function bindWorkbench(): Promise<void> {
  if (typeof document === "undefined") return Promise.resolve();
  if (bound) return initialViewReady || Promise.resolve();
  bound = true;
  paintIcons();
  applyStaticI18n(document);
  watchActivateKeys(document);
  watchDisconnect();
  setLoadSessionsImpl(loadSessions);

  document.querySelectorAll(".lang-btn").forEach((b) => {
    (b as HTMLElement).onclick = () => {
      const lang = (b as HTMLElement).dataset.lang;
      if (lang === "zh" || lang === "en") void setLang(lang);
    };
  });

  const dashNew = $("#dash-new-project");
  if (dashNew) dashNew.onclick = () => openProjectModal();
  const dashImport = $("#dash-import-session");
  if (dashImport) dashImport.onclick = chooseSessionPackage;
  const pkg = $("#session-package-input") as HTMLInputElement | null;
  if (pkg) {
    pkg.onchange = async (event) => {
      const input = event.currentTarget as HTMLInputElement;
      const file = input.files && input.files[0];
      input.value = "";
      await importSessionPackage(file);
    };
  }
  const pmDelete = $("#pm-delete");
  if (pmDelete) {
    pmDelete.onclick = async () => {
      const id = editingProject.value;
      if (!id || !confirm(t("proj.delete.confirm"))) return;
      await deleteProject(String(id));
    };
  }
  const back = $("#back-home");
  if (back) back.onclick = showDashboard;
  const newBtn = $("#new-session");
  if (newBtn) newBtn.onclick = () => void newSession();
  const tabNew = $("#tab-new");
  if (tabNew) tabNew.onclick = () => void newSession();
  const tabClose = $("#tab-close");
  if (tabClose) {
    tabClose.onclick = (e) => {
      e.stopPropagation();
      showDashboard();
    };
  }
  const collapse = $("#sidebar-collapse");
  if (collapse) collapse.onclick = () => setSidebar(true);
  const reopen = $("#sidebar-reopen");
  if (reopen) reopen.onclick = () => setSidebar(false);
  const dt = $("#dash-theme");
  if (dt) dt.onclick = () => cycleTheme();
  const wt = $("#ws-theme");
  if (wt) wt.onclick = () => cycleTheme();
  const dashSettings = $("#dash-settings");
  if (dashSettings) dashSettings.onclick = () => callLane("openCust", "general");
  const customize = $("#customize-btn");
  if (customize) customize.onclick = () => callLane("openCust");
  const projBtn = $("#proj-btn");
  if (projBtn) {
    projBtn.onclick = () => {
      const menu = $("#proj-menu");
      if (!menu) return;
      menu.classList.toggle("hidden");
      if (!menu.classList.contains("hidden")) renderProjMenu();
    };
  }
  const ct = $("#conv-title") as HTMLInputElement | null;
  if (ct) {
    ct.addEventListener("keydown", (e) => {
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === "Enter") {
        e.preventDefault();
        ct.blur();
      } else if (e.key === "Escape") {
        setTitle(_titleName.value);
        ct.blur();
      }
    });
    ct.addEventListener("blur", () => {
      void commitTitle();
    });
  }
  const sessionMenuBtn = $("#session-menu-btn");
  if (sessionMenuBtn) {
    sessionMenuBtn.onclick = (e) => {
      if (currentId.value) sessionMenu(e.currentTarget as Element, currentId.value);
    };
  }
  const attach = $("#attach-btn");
  if (attach) attach.onclick = (e) => addToMessageMenu(e.currentTarget as Element);
  const sessionOpts = $("#session-options-btn");
  if (sessionOpts) {
    sessionOpts.onclick = (e) => {
      void sessionOptionsMenu(e.currentTarget as Element);
    };
  }
  const cancel = $("#cancel-btn");
  if (cancel) cancel.onclick = () => void cancelTurn();
  const jump = $("#jump-pill");
  if (jump) jump.onclick = () => down(true);
  const msgs = $("#messages");
  if (msgs) msgs.addEventListener("scroll", updateJumpPill);
  const composer = $("#composer");
  if (composer) {
    composer.addEventListener("input", () => {
      grow();
      renderComposerRefChips();
    });
  }
  const pmClose = $("#proj-modal-close");
  const pmCancel = $("#pm-cancel");
  if (pmClose) pmClose.onclick = closeProjectModal;
  if (pmCancel) pmCancel.onclick = closeProjectModal;
  const projModal = $("#proj-modal");
  if (projModal) {
    projModal.onclick = (e) => {
      if ((e.target as HTMLElement).id === "proj-modal") closeProjectModal();
    };
  }
  const pmCreate = $("#pm-create");
  if (pmCreate) pmCreate.onclick = () => void submitProjectModal();
  const gear = $("#settings-gear");
  if (gear) gear.onclick = () => callLane("openCust");

  window.addEventListener("popstate", () => {
    void routeInitialView().catch(showDashboard);
  });
  if (typeof window.matchMedia === "function") {
    const mq = window.matchMedia("(max-width: 900px)");
    const onMq = () => syncMobileChrome(true);
    if (typeof mq.addEventListener === "function") mq.addEventListener("change", onMq);
    else if (typeof (mq as { addListener?: (fn: () => void) => void }).addListener === "function") {
      (mq as { addListener: (fn: () => void) => void }).addListener(onMq);
    }
  }
  initialViewReady = routeInitialView().catch(() => {
    showDashboard();
  });
  return initialViewReady;
}
