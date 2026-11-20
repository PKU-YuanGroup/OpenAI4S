import { h, render } from "preact";
import { LANG } from "../../i18n/runtime";
import { api } from "../timeline/api";
import { applyLabTabPreference, labTabChoice, LabPanel, noteLabPrepared } from "./LabPanel";
import "./lab.css";

export { labCopy, labTabHidden, setLabTabHidden } from "./LabPanel";

/**
 * Ask once whether the lab is set up, so its tab appears on its own when it
 * is. Skipped when the person already chose to show or hide the tab; a failed
 * read leaves the tab hidden, where Settings → General can still show it.
 */
export function bootLabTab(): void {
  if (labTabChoice()) return;
  void api("/mhs-lab/status").then(
    (status) => noteLabPrepared(!!status && (status as { available?: unknown }).available === true),
    () => undefined,
  );
}

let mounted = false;

/** Mount the Lab panel into `#dock-lab` once; it polls only while its pane is visible. */
export function mountLab(): void {
  if (mounted || typeof document === "undefined") return;
  const root = document.getElementById("dock-lab");
  if (!root) return;
  render(h(LabPanel, {}), root);
  mounted = true;
}

/** Label of the Lab dock tab (kept here so the generated i18n extract stays untouched). */
export function labTabLabel(): string {
  return LANG === "zh" ? "实验室" : "OpenMHS Lab";
}

// A tab the person hid stays hidden from the first paint of the dock.
applyLabTabPreference();
