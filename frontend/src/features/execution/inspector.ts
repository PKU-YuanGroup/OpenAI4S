/**
 * Variable inspector. Port of app.js:10265-10332.
 *
 * Inspection never runs a Cell. sanitizeVariableInspection (F-15) fails
 * closed unless the payload is exact-scope for the active branch.
 */

import { variableInspector } from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { t } from "../../i18n/runtime";
import { runtimeSummary, shortRuntime } from "../notebook/kernel";
import { publicText } from "../scrub/scrub";
import { sanitizeVariableInspection } from "../timeline/sanitize";
import { api, apiErrorText } from "./api";

type InspectorState = {
  language: string;
  results: Record<string, unknown>;
  loading: string | null;
  error: string;
  request: number;
};

function el(tag: string, cls?: string | null, text?: string | null): HTMLElement {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

let paintExec: (() => void) | null = null;

export function setPaintInspector(fn: (() => void) | null): void {
  paintExec = fn;
}

function paint(): void {
  if (paintExec) paintExec();
}

function inspectorState(): InspectorState {
  return variableInspector.value as InspectorState;
}

/** app.js:10283-10288 */
export function variablePreviewText(value: unknown): string {
  if (typeof value === "string") return JSON.stringify(value);
  if (value === null) return "null";
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  return "";
}

export async function refreshVariableInspector(): Promise<void> {
  const inspector = inspectorState();
  const frameId = currentId.value;
  if (!inspector || !frameId || inspector.loading) return;
  const language = inspector.language === "r" ? "r" : "python";
  const request = (inspector.request = (inspector.request || 0) + 1);
  inspector.loading = language;
  inspector.error = "";
  paint();
  try {
    const payload = await api(
      `/frames/${encodeURIComponent(frameId)}/kernel/variables?language=${language}`,
    );
    if (frameId !== currentId.value || request !== inspectorState().request) return;
    inspector.results[language] = sanitizeVariableInspection(payload, frameId, language);
  } catch (error) {
    if (frameId === currentId.value && request === inspectorState().request)
      inspector.error = publicText(apiErrorText(error), 240);
  } finally {
    if (frameId === currentId.value && request === inspectorState().request) {
      inspector.loading = null;
      paint();
    }
  }
}

type VarRow = {
  name?: string;
  type?: string;
  preview?: unknown;
  length?: number;
  fingerprint?: string;
};

type Inspection = {
  available?: boolean;
  state?: string;
  reason?: string;
  generation_id?: string;
  state_revision?: number;
  variables?: VarRow[];
  truncated?: boolean;
};

export function renderVariableInspector(): HTMLElement {
  const inspector = inspectorState() || {
    language: "python",
    results: {},
    loading: null,
    error: "",
    request: 0,
  };
  const language = inspector.language === "r" ? "r" : "python";
  const data = ((inspector.results || {})[language] || null) as Inspection | null;
  const panel = el("section", "nb-variables");
  panel.setAttribute("data-variable-inspector", language);
  const head = el("div", "nb-variables-head");
  head.appendChild(el("span", "nb-variables-title", t("nb.variables.title")));
  const controls = el("div", "nb-variables-controls");
  const label = el("label", "nb-variables-language", t("nb.variables.language"));
  const select = el("select", "nb-variables-select") as HTMLSelectElement;
  (
    [
      ["python", "Python"],
      ["r", "R"],
    ] as const
  ).forEach(([value, text]) => {
    const option = el("option", null, text) as HTMLOptionElement;
    option.value = value;
    select.appendChild(option);
  });
  select.value = language;
  select.disabled = !!inspector.loading;
  select.onchange = () => {
    inspector.language = select.value === "r" ? "r" : "python";
    inspector.error = "";
    paint();
  };
  label.appendChild(select);
  controls.appendChild(label);
  const refresh = el(
    "button",
    "outline-btn small",
    inspector.loading ? t("nb.variables.loading") : t("nb.variables.refresh"),
  ) as HTMLButtonElement;
  refresh.setAttribute("data-action", "refresh-variables");
  refresh.disabled = !!inspector.loading || !currentId.value;
  refresh.onclick = () => {
    void refreshVariableInspector();
  };
  controls.appendChild(refresh);
  head.appendChild(controls);
  panel.appendChild(head);
  if (inspector.loading === language) {
    panel.appendChild(el("div", "nb-variables-empty", t("nb.variables.loading")));
    return panel;
  }
  if (inspector.error) {
    panel.appendChild(el("div", "timeline-error", t("nb.variables.error", inspector.error)));
    return panel;
  }
  if (!data) {
    panel.appendChild(el("div", "nb-variables-empty", t("nb.variables.notLoaded")));
    return panel;
  }
  const meta = el("div", "nb-variables-meta");
  if (data.generation_id)
    meta.appendChild(el("span", "timeline-pill", t("nb.variables.generation", shortRuntime(data.generation_id))));
  meta.appendChild(el("span", "timeline-pill", t("nb.variables.revision", data.state_revision)));
  const runtime = runtimeSummary();
  const runtimeGeneration = language === "r" ? runtime.r : runtime.python;
  const stale =
    Number(runtime.revision) > Number(data.state_revision) ||
    !!(runtimeGeneration && data.generation_id && runtimeGeneration !== data.generation_id);
  if (stale) meta.appendChild(el("span", "timeline-pill variable-stale", t("nb.variables.stale")));
  panel.appendChild(meta);
  if (!data.available) {
    const key = "nb.variables.state." + data.state;
    panel.appendChild(
      el("div", "nb-variables-empty", t(key) === key ? data.reason || t("nb.variables.state.failed") : t(key)),
    );
    return panel;
  }
  if (!(data.variables || []).length) {
    panel.appendChild(el("div", "nb-variables-empty", t("nb.variables.empty")));
    return panel;
  }
  const list = el("div", "nb-variable-list");
  (data.variables || []).forEach((variable) => {
    const row = el("div", "nb-variable-row");
    const identity = el("div", "nb-variable-identity");
    identity.appendChild(el("span", "nb-variable-name", variable.name || ""));
    identity.appendChild(el("span", "nb-variable-type", variable.type || ""));
    row.appendChild(identity);
    const details = el("div", "nb-variable-details");
    const preview = variablePreviewText(variable.preview);
    if (preview) details.appendChild(el("span", "nb-variable-preview", preview));
    if (variable.length != null)
      details.appendChild(el("span", "timeline-pill", t("nb.variables.length", variable.length)));
    if (variable.fingerprint)
      details.appendChild(
        el("span", "timeline-pill", t("nb.variables.fingerprint", shortRuntime(variable.fingerprint))),
      );
    row.appendChild(details);
    list.appendChild(row);
  });
  panel.appendChild(list);
  if (data.truncated)
    panel.appendChild(
      el("div", "nb-variables-truncated", t("nb.variables.truncated", (data.variables || []).length)),
    );
  return panel;
}

export function paintVariableInspector(nb: HTMLElement): void {
  const next = renderVariableInspector();
  const existing = nb.querySelector(".nb-variables");
  if (existing) existing.replaceWith(next);
  else nb.appendChild(next);
}
