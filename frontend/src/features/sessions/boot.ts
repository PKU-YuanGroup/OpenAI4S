/** Window exports, F-06 loadSessions hook, and workbench event wiring. */

import { LANG, applyStaticI18n, i18nReady, onLanguageChange, setLang, t, type Lang } from "../../i18n";
import { _titleName, currentId, editingProject } from "../../stores/session";
import { activeTab } from "../../stores/ui";
import { renderDockTabs } from "../artifacts/ui";
import { cycleTheme, refreshThemeToggle } from "../theme/theme";
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
import { loadDashboard, repaintDashboard, showDashboard } from "./dashboard";
import { $, grow, setSidebar, setTitle, syncMobileChrome } from "./dom";
import { bindMessageScroll } from "../messages/scroll";
import { bindModalDismiss } from "../chrome/modal";
import { paintIcons } from "./icon";
import { callLane, hostWindow } from "./lane";
import { loadSessions, renderSessions, syncCurrentTitle } from "./load";
import { renderEmptySession } from "../messages/list";
import {
  fetchAllMessages,
  fetchOlderMessages,
  fetchRecentMessages,
  paintEarlierControl,
} from "./messages";
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
  // Same hole, two more names this lane owns: `openConversation` paints the
  // "Load earlier messages" bar through `callLane("paintEarlierControl")`
  // (so a conversation longer than one page had no way to reach its history
  // in this shell), and the chrome's Cmd/Ctrl+B handler reaches the sidebar
  // through `hostFn("setSidebar")`.
  target.paintEarlierControl = paintEarlierControl;
  target.setSidebar = setSidebar;
  // Without this the Notebook's interrupt fell through to an UNSCOPED
  // POST /frames/<id>/kernel/interrupt carrying no execution_id or owner --
  // a shape app.js never sends, and one that can land on whichever execution
  // started after the one the user meant to stop.
  setScopedExecutionRequest(scopedExecutionRequest);
}

let bound = false;
let initialViewReady: Promise<void> | null = null;

/**
 * How long the first view waits for the locale chunks. They are a fraction of
 * the main bundle this page has already loaded, so this is only reached by a
 * stalled request -- and a workbench that never shows its projects is worse
 * than one that shows keys in its lists.
 */
export const I18N_ROUTE_WAIT_MS = 8000;

/**
 * Resolves when the dictionaries load, fail to load, or the wait runs out --
 * with `true` only in the last case, when the first view renders without them.
 */
function dictionariesSettled(): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    const done = (timedOut: boolean) => {
      clearTimeout(timer);
      resolve(timedOut);
    };
    const timer = setTimeout(() => done(true), I18N_ROUTE_WAIT_MS);
    i18nReady().then(
      () => done(false),
      () => done(false),
    );
  });
}

/**
 * The dictionaries landed after the first view gave up waiting for them.
 * The boot repaint reaches static labels only, so the lists that first view
 * rendered through `t()` kept their bare keys -- "dash.meta.sessions" on the
 * dashboard, "date.bucket.today" and the empty session's starters in the
 * workspace -- until something else happened to re-render them. Once only:
 * a later language switch is not this path.
 */
function repaintListsRenderedWithoutDictionaries(): void {
  const dash = $("#dashboard");
  if (dash && !dash.classList.contains("hidden")) void loadDashboard();
  renderSessions();
  repaintEmptySession();
}

/** Only an empty session has this node, and it is all that session shows. */
function repaintEmptySession(): void {
  const host = $("#messages");
  const empty = host?.querySelector(":scope > .empty-session");
  if (host && empty) {
    empty.remove();
    renderEmptySession(host);
  }
}

/**
 * app.js `rerenderI18n`: a language switch repaints the views built through
 * `t()` from what they already hold, without new reads -- the dashboard lists,
 * the project menu, the sidebar, an unnamed session's title, the empty
 * session, the dock tabs and the dock pane on screen. Only a switch: the first
 * dictionary load keeps the language, routing waits for it, and a first view
 * that could not wait is repainted by `repaintListsRenderedWithoutDictionaries`.
 */
function languageSwitchRepaint(): (lang: Lang) => void {
  let painted = LANG;
  return (lang) => {
    if (lang === painted) return;
    painted = lang;
    const dash = $("#dashboard");
    if (dash && !dash.classList.contains("hidden")) repaintDashboard();
    renderProjMenu();
    renderSessions();
    syncCurrentTitle();
    repaintEmptySession();
    renderDockTabs();
    if (activeTab.value === "timeline") callLane("renderActionTimeline");
    else if (activeTab.value === "notebook") callLane("renderNotebook");
  };
}

export function bindWorkbench(): Promise<void> {
  if (typeof document === "undefined") return Promise.resolve();
  if (bound) return initialViewReady || Promise.resolve();
  bound = true;
  paintIcons();
  // installTheme() ran before the Shell existed, so the theme buttons still
  // carry the markup's "moon"; show the glyph for the theme actually applied.
  refreshThemeToggle();
  // app.js re-ran it on every language change (and so for the toggle's
  // aria-label, which no data-i18n attribute covers); that includes the
  // first dictionary load.
  onLanguageChange(refreshThemeToggle);
  onLanguageChange(languageSwitchRepaint());
  applyStaticI18n(document);
  watchActivateKeys(document);
  watchDisconnect();
  setLoadSessionsImpl(async () => { await loadSessions(); });

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
  // The Shell has rendered #messages and #jump-pill by now.
  bindMessageScroll();
  const composer = $("#composer");
  if (composer) {
    composer.addEventListener("input", () => {
      grow();
      renderComposerRefChips();
    });
  }
  const pmCancel = $("#pm-cancel");
  if (pmCancel) pmCancel.onclick = closeProjectModal;
  // Chrome's dismissal, closing through closeProjectModal: a text selection
  // released over the scrim used to close the dialog and drop what was typed.
  bindModalDismiss($("#proj-modal"), $("#proj-modal-close"), closeProjectModal);
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
  // The first data-driven view waits for the dictionaries. The dashboard
  // lists, the session sidebar and an opened session render through t(), and
  // nothing re-renders them when the locale chunks land: routing first left
  // "dash.meta.sessions" / "dash.sessions.empty" on screen whenever the chunk
  // was slower than the API. The handlers above are bound already; only this
  // render waits.
  let routedWithoutDictionaries = false;
  initialViewReady = dictionariesSettled()
    .then((timedOut) => {
      routedWithoutDictionaries = timedOut;
      return routeInitialView();
    })
    .catch(() => {
      showDashboard();
    })
    .finally(() => {
      // The Shell's deep-link indicator (dashboard.css retires it as soon as a
      // view is shown); the first route has settled either way.
      const pending = $("#route-loading");
      if (pending) pending.hidden = true;
      // After the route, so the repaint cannot run ahead of the lists it fixes.
      if (routedWithoutDictionaries) {
        void i18nReady().then(repaintListsRenderedWithoutDictionaries, () => undefined);
      }
    });
  return initialViewReady;
}
