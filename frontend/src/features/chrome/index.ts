/**
 * F-20 boot: focus trap, team surface, ⌘K palette, upload/mic,
 * layout density, column resizers.
 *
 * Assigns this lane's window names from the owning module (same pattern as
 * F-06 `bootWs()` → `onEvent` and F-07 → `t`).
 */

import { isReady } from "../../compat/stub";
import { activeTab, dock } from "../../stores/ui";
import { dockClose, dockToggle, setActiveTab } from "../artifacts/ui";
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

/**
 * One boot step. A throw is reported and the remaining steps still bind: a
 * single failure here used to leave ⌘K, uploads, the modal traps and
 * everything after bootChrome() in main.tsx unbound.
 */
function bootStep(name: string, run: () => void): void {
  try {
    run();
  } catch (error) {
    console.error("bootChrome: " + name + " failed", error);
  }
}

/** F-20 boot. Call after the shell has mounted so team ids can be found. */
export function bootChrome(): void {
  bootStep("window names", assignWindow);
  bootStep("palette escape", () => addModalEscapeBlocker(() => isPaletteOpen()));
  bootStep("layout", () => applyLayout(readStoredLayout()));
  bootStep("column widths", restoreColWidths);
  bootStep("column resizers", initColResizers);
  bootStep("palette", bindPaletteButton);
  bootStep("upload", bindUpload);
  bootStep("mic", bindMic);
  bootStep("modal dismiss", () => {
    bindModalDismiss($("#cust"), $("#cust-close"));
    bindModalDismiss($("#modal"), $("#modal-close"));
  });
  bootStep("project modal", bindProjectModal);
  bootStep("keys", installWorkbenchKeys);
  bootStep("dock", bindDockChrome);
  bootStep("team", bootTeam);
}

function bindProjectModal(): void {
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
}

function bindDockChrome(): void {
  const tray = document.querySelector(".nb-tray");
  if (tray instanceof HTMLElement && tray.dataset.f23Bound !== "1") {
    tray.dataset.f23Bound = "1";
    tray.addEventListener("click", () => {
      const d = dock.value as { open?: boolean };
      if (d.open && activeTab.value === "notebook") dockClose();
      else setActiveTab("notebook");
    });
  }
  const toggle = document.getElementById("dock-toggle");
  if (toggle && toggle.dataset.f23Bound !== "1") {
    toggle.dataset.f23Bound = "1";
    toggle.addEventListener("click", () => dockToggle());
  }
  const collapse = document.getElementById("dock-collapse");
  if (collapse && collapse.dataset.f23Bound !== "1") {
    collapse.dataset.f23Bound = "1";
    collapse.addEventListener("click", () => dockClose());
  }
}
