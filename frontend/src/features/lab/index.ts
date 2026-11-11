import { h, render } from "preact";
import { LANG } from "../../i18n/runtime";
import { LabPanel } from "./LabPanel";
import "./lab.css";

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
