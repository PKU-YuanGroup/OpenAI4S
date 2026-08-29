/**
 * F-20 boot: focus trap, team surface, ⌘K palette, upload/notes/mic,
 * layout density, column resizers.
 *
 * Assigns this lane's window names from the owning module (same pattern as
 * F-06 `bootWs()` → `onEvent` and F-07 → `t`).
 */

import { isReady } from "../../compat/stub";
import { cycleTheme } from "../theme/theme";
import "./chrome.css";
import { $ } from "./dom";
import { hostFn } from "./host";
import { applyLayout, readStoredLayout, setLayout } from "./layout";
import { bindMic, micDictate } from "./mic";
import {
  addModalEscapeBlocker,
  bindModalDismiss,
  closeModalEl,
  openModalEl,
  trapModalKeydown,
} from "./modal";
import { bindNotes, loadNotes } from "./notes";
import {
  bindPaletteButton,
  closePalette,
  handlePaletteHotkey,
  isPaletteOpen,
  openPalette,
} from "./palette";
import { initColResizers, restoreColWidths } from "./resizer";
import { bootTeam } from "./team";
import { bindUpload, uploadFiles } from "./upload";

export { API, ApiError, api, apiErrorText } from "./api";
export { applyLayout, readStoredLayout, setLayout } from "./layout";
export { micDictate } from "./mic";
export {
  FALLBACK_MODAL_SELECTORS,
  _focusables,
  _modalFocus,
  addModalEscapeBlocker,
  anyModalOpen,
  closeModalEl,
  openModalEl,
  resetModalTrap,
  trapModalKeydown,
} from "./modal";
export {
  PAL,
  closePalette,
  isPaletteOpen,
  openPalette,
  openPaletteArtifact,
  palSearch,
  parseArtifactQuery,
} from "./palette";
export { initColResizers, restoreColWidths } from "./resizer";
export {
  bootTeam,
  ensureTeamDom,
  fmtSize,
  loadAdmin,
  openAdmin,
  openTeamFilesPanel,
  probeTeamAuth,
} from "./team";
export { uploadFiles } from "./upload";
export { addNote, effProject, loadNotes } from "./notes";

type ChromeWindow = Record<string, unknown>;

function assignWindow(): void {
  const g = globalThis as unknown as { window?: ChromeWindow };
  const w = g.window;
  if (!w) return;
  w.openModalEl = openModalEl;
  w.closeModalEl = closeModalEl;
  w.trapModalKeydown = trapModalKeydown;
  w.openPalette = openPalette;
  w.closePalette = closePalette;
  w.applyLayout = applyLayout;
  w.setLayout = setLayout;
  w.uploadFiles = uploadFiles;
  w.micDictate = micDictate;
  w.loadNotes = loadNotes;
}

function installWorkbenchKeys(): void {
  if (typeof document === "undefined") return;
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" || e.key === "Tab") trapModalKeydown(e);
    handlePaletteHotkey(e);
    if ((e.metaKey || e.ctrlKey) && (e.key === "b" || e.key === "B")) {
      const setSidebar = hostFn("setSidebar");
      if (isReady(setSidebar)) {
        e.preventDefault();
        setSidebar(!document.body.classList.contains("sidebar-collapsed"));
      }
    }
    if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === "l" || e.key === "L")) {
      e.preventDefault();
      cycleTheme();
    }
  });
}

/** F-20 boot. Call after the shell has mounted so team ids can be found. */
export function bootChrome(): void {
  assignWindow();
  addModalEscapeBlocker(() => isPaletteOpen());
  applyLayout(readStoredLayout());
  restoreColWidths();
  initColResizers();
  bindPaletteButton();
  bindUpload();
  bindNotes();
  bindMic();
  bindModalDismiss($("#cust"), $("#cust-close"));
  bindModalDismiss($("#modal"), $("#modal-close"));
  const closeProjectModal = hostFn("closeProjectModal");
  const projClose = $("#proj-modal-close");
  const projCancel = $("#pm-cancel");
  const projModal = $("#proj-modal");
  const closeProj = (): void => {
    if (isReady(closeProjectModal)) closeProjectModal();
    else closeModalEl(projModal);
  };
  if (projClose) projClose.addEventListener("click", closeProj);
  if (projCancel) projCancel.addEventListener("click", closeProj);
  if (projModal) {
    projModal.addEventListener("click", (e) => {
      if (e.target === projModal) closeProj();
    });
  }
  installWorkbenchKeys();
  bootTeam();
}
