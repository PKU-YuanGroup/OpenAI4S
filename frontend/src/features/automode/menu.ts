/**
 * The read-only Auto Mode block at the foot of the session options menu.
 *
 * Three stacked lines -- Availability, Saved selection, Run -- whose headings
 * never change with the preset, then the budget block and the Audit entry.
 * It has no editor: no preset or sub-mode picker, no clear, no budget control.
 * The menu's existing "Auto review" row is untouched and still patches
 * `review-settings`; this block is not a second switch.
 *
 * While the block is on screen it repaints from `autoModeStatus()`; a hint,
 * a reconnect or a reopen only ever schedules another GET.
 */

import { autoModeT } from "./copy";
import { openAutoModeAudits } from "./audits";
import {
  availabilityText,
  budgetModel,
  runDetailRows,
  runText,
  selectionDetail,
  selectionText,
} from "./present";
import { autoModeStatus, onAutoModeStatus, refreshAutoModeStatus, type AutoModeStatus } from "./status";
import { closeMenu, repositionMenu, type MenuItem } from "../sessions/chrome";
import { el } from "../sessions/dom";
import type { AutoModeView } from "./types";

let mounted: HTMLElement | null = null;
let stopListening: (() => void) | null = null;

/** Whether the block is on screen, so a hint is worth a GET. */
export function autoModeStatusVisible(): boolean {
  if (!mounted) return false;
  if (mounted.isConnected === false) {
    unmount();
    return false;
  }
  return true;
}

function unmount(): void {
  if (stopListening) stopListening();
  stopListening = null;
  mounted = null;
}

function line(kind: string, heading: string, value: string, extra?: HTMLElement | null): HTMLElement {
  const row = el("div", "am-line");
  row.dataset.line = kind;
  row.appendChild(el("span", "am-h", heading));
  row.appendChild(el("span", "am-v", value));
  if (extra) row.appendChild(extra);
  return row;
}

function keyValues(rows: Array<[string, string]>, className: string): HTMLElement {
  const list = el("dl", className);
  for (const [key, value] of rows) {
    list.appendChild(el("dt", null, key));
    list.appendChild(el("dd", null, value));
  }
  return list;
}

function details(summary: string, body: HTMLElement, className: string): HTMLElement {
  const box = el("details", className);
  box.appendChild(el("summary", null, summary));
  box.appendChild(body);
  // Opening or closing changes the menu's height under its anchor.
  box.ontoggle = () => repositionMenu();
  return box;
}

function button(className: string, label: string, onClick: () => void): HTMLButtonElement {
  const node = el("button", "ctx-item " + className, label);
  node.type = "button";
  node.setAttribute("role", "menuitem");
  node.onclick = (event) => {
    event.stopPropagation();
    onClick();
  };
  return node;
}

/**
 * The ceilings and meters. All thirteen rows sit behind one disclosure so the
 * menu stays short; what needs attention -- a meter near or at its ceiling,
 * the exhausted list, a tripped circuit -- is repeated below it, always shown.
 */
function budgetBlock(view: AutoModeView): HTMLElement {
  const model = budgetModel(view);
  const block = el("div", "am-budget" + (model.warn ? " am-budget-warn" : ""));
  const body = el("div", "am-budget-body");
  for (const row of model.rows) {
    const meter = el("div", "am-meter" + (row.flag ? " am-meter-" + row.flag : ""));
    meter.dataset.field = row.field;
    if (row.flag) meter.dataset.flag = row.flag;
    meter.appendChild(el("span", "am-ml", row.label));
    meter.appendChild(el("span", "am-mc", row.ceiling ?? ""));
    if (row.meter !== null) {
      meter.appendChild(el("span", "am-mv", row.meter));
      meter.appendChild(el("span", "am-ma", row.authority ?? ""));
    }
    body.appendChild(meter);
  }
  if (model.noUsage) body.appendChild(el("div", "am-note", autoModeT("autoMode.budget.noUsage")));
  block.appendChild(details(model.summary, body, "am-budget-table"));
  const alerts = el("div", "am-alerts");
  for (const row of model.rows) {
    if ((row.flag !== "near" && row.flag !== "at") || row.meter === null) continue;
    const alert = el("div", "am-alert am-meter-" + row.flag);
    alert.dataset.field = row.field;
    alert.dataset.flag = row.flag;
    alert.appendChild(el("span", "am-ml", row.label));
    alert.appendChild(el("span", "am-mv", row.meter));
    alerts.appendChild(alert);
  }
  if (model.exhausted) alerts.appendChild(el("div", "am-warn", model.exhausted));
  if (model.circuit) alerts.appendChild(el("div", "am-warn", model.circuit));
  if (alerts.firstChild) block.appendChild(alerts);
  return block;
}

function readyBody(view: AutoModeView): HTMLElement[] {
  const detail = selectionDetail(view);
  const runExtra = view.run ? runDetailRows(view.run) : [];
  const nodes: HTMLElement[] = [
    line("availability", autoModeT("autoMode.h.availability"), availabilityText(view)),
    line(
      "selection",
      autoModeT("autoMode.h.selection"),
      selectionText(view),
      detail ? el("span", "am-sub", detail) : null,
    ),
    line(
      "run",
      autoModeT("autoMode.h.run"),
      runText(view),
      runExtra.length ? details(autoModeT("autoMode.details"), keyValues(runExtra, "am-kv"), "am-detail") : null,
    ),
  ];
  // The Audit entry belongs to the run line, above the budget block.
  const actions = el("div", "am-actions");
  actions.appendChild(
    button("am-audit", autoModeT("autoMode.audit.open"), () => {
      closeMenu();
      void openAutoModeAudits();
    }),
  );
  nodes.push(actions, budgetBlock(view));
  return nodes;
}

function placeholderBody(value: string, retry: boolean): HTMLElement[] {
  const nodes = [
    line("availability", autoModeT("autoMode.h.availability"), value),
    line("selection", autoModeT("autoMode.h.selection"), value),
    line("run", autoModeT("autoMode.h.run"), value),
  ];
  if (retry) {
    const actions = el("div", "am-actions");
    actions.appendChild(button("am-retry", autoModeT("autoMode.retry"), () => void refreshAutoModeStatus()));
    nodes.push(actions);
  }
  return nodes;
}

function bodyFor(status: AutoModeStatus): HTMLElement[] {
  if (status.phase === "ready") return readyBody(status.view);
  if (status.phase === "failed") {
    return placeholderBody(
      autoModeT(status.failure === "not_found" ? "autoMode.notFound" : "autoMode.unavailable"),
      true,
    );
  }
  return placeholderBody(autoModeT("autoMode.loading"), false);
}

/** Paint `status` into the block. Exported for the language-switch repaint and tests. */
export function paintAutoModeBlock(node: HTMLElement, status: AutoModeStatus = autoModeStatus()): void {
  node.innerHTML = "";
  node.dataset.phase = status.phase;
  node.setAttribute("aria-busy", status.phase === "loading" ? "true" : "false");
  node.appendChild(el("div", "am-title", autoModeT("autoMode.block")));
  for (const child of bodyFor(status)) node.appendChild(child);
}

/** Repaint the block in place if it is on screen. */
export function repaintAutoModeBlock(): void {
  if (!autoModeStatusVisible() || !mounted) return;
  paintAutoModeBlock(mounted);
  repositionMenu();
}

/**
 * The menu item: a fresh block bound to the status store. Call
 * `refreshAutoModeStatus()` before awaiting anything else, so the read runs
 * beside the menu's own `review-settings` GET.
 */
export function autoModeMenuItem(): MenuItem {
  unmount();
  const node = el("div", "am-status");
  node.setAttribute("role", "group");
  node.setAttribute("aria-label", autoModeT("autoMode.block"));
  node.setAttribute("aria-live", "polite");
  paintAutoModeBlock(node);
  mounted = node;
  stopListening = onAutoModeStatus(repaintAutoModeBlock);
  return { node };
}

/** Test seam. */
export function resetAutoModeMenu(): void {
  unmount();
}
