/**
 * F-16 boot. Assigns executed-code / provenance window names (F-06 bootWs
 * pattern), composes F-14 `renderNotebook` with the variable inspector, and
 * wraps F-17 Viewer chrome with a Provenance entry. Does not import
 * `compat/window-exports.ts`.
 */

import { isReady } from "../../compat/stub";
import { execSources } from "../../stores/notebook";
import { setShowProvenance } from "../notebook/cells";
import { renderNotebook as f14RenderNotebook } from "../notebook/Notebook";
import { setNotebookRenderImpl } from "../notebook/scroll";
import { forkFromCell } from "./branch";
import {
  buildExecutedCodeView,
  execSourcesState,
  paintExecutedCodeView,
  selectExecFrame,
  setPaintExecutionChrome,
  toggleExecutedCode,
} from "./exec";
import { paintVariableInspector, renderVariableInspector, setPaintInspector } from "./inspector";
import { decorateViewerWithProvenance, renderProvenanceInto, showProvenance } from "./provenance";
import type { ExecSourcesState } from "./types";

type Target = Record<string, unknown>;

export function paintExecutionChrome(): void {
  if (typeof document === "undefined") return;
  const nb = document.getElementById("dock-notebook");
  if (!nb) return;
  const st = execSources.value as ExecSourcesState | null;
  if (st && st.open) {
    paintExecutedCodeView(nb, st);
    return;
  }
  paintVariableInspector(nb);
}

/** F-14 render + inspector / executed-code overlay. app.js:10333-10479. */
export function renderNotebook(): void {
  f14RenderNotebook();
  paintExecutionChrome();
}

function hostTarget(target?: Target): Target | null {
  if (target) return target;
  const w = (globalThis as unknown as { window?: Target }).window;
  return w || null;
}

function wrapAfter(host: Target, name: string, after: () => void): void {
  const orig = host[name];
  if (!isReady(orig)) return;
  if ((orig as { __f16Wrapped?: boolean }).__f16Wrapped) return;
  const wrapped = (...args: unknown[]) => {
    const result = (orig as (...a: unknown[]) => unknown)(...args);
    after();
    return result;
  };
  Object.defineProperty(wrapped, "__f16Wrapped", { value: true });
  host[name] = wrapped;
}

export function installExecution(target?: Target): void {
  const host = hostTarget(target);
  if (host) {
    host.buildExecutedCodeView = buildExecutedCodeView;
    host.execSourcesState = execSourcesState;
    host.selectExecFrame = selectExecFrame;
    host.toggleExecutedCode = toggleExecutedCode;
    host.showProvenance = showProvenance;
    host.renderProvenanceInto = renderProvenanceInto;
    host.renderNotebook = renderNotebook;
    host.renderVariableInspector = renderVariableInspector;
    host.forkFromCell = forkFromCell;
    wrapAfter(host, "renderViewer", decorateViewerWithProvenance);
    wrapAfter(host, "setActiveTab", decorateViewerWithProvenance);
    wrapAfter(host, "openViewer", decorateViewerWithProvenance);
  }
  setShowProvenance(showProvenance);
  // Async loads and inspector language changes rebuild the dock the way
  // app.js `renderNotebook()` did (chips + executed-code / inspector).
  setPaintExecutionChrome(renderNotebook);
  setPaintInspector(renderNotebook);
  setNotebookRenderImpl(renderNotebook);
}

export function bootExecution(target?: Target): void {
  installExecution(target);
}
