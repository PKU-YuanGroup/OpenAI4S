/**
 * F-18 imperative islands. Importing this module and calling `bootIslands`
 * overwrites the F-05 stubs (same pattern as F-06 `bootWs` / F-17
 * `bootArtifacts`). Do not import `compat/window-exports.ts`.
 */

import { isReady } from "../compat/stub";
import { setRenderViewerImpl } from "../features/artifacts/ui";
import {
  annotationId,
  annotationIsHeld,
  annotationsFor,
  annotationStatus,
  closeAnnotDraft,
  closeAnnotPop,
  loadAnnotations,
  openAnnotations,
  openPinPop,
  refreshAllStages,
  renderAnnotatableImage,
  renderPins,
  updateAnnotBadge,
} from "./annot";
import { openKetcher } from "./ketcher";
import { renderLocatorComments } from "./locator";
import { molTeardown, molecule } from "./mol";
import { isTextEditable, openArtifact, renderViewer, showVersions } from "./viewer";

export {
  ARTIFACT_IFRAME_SANDBOX,
  KETCHER_ALLOW,
  KETCHER_PATH,
  applyArtifactIframeSandbox,
  applyKetcherFrame,
  htmlPreviewSrc,
  ketcherFrameSrc,
} from "./frames";
export {
  annotationId,
  annotationIsHeld,
  annotationStatus,
  annotationsFor,
  loadAnnotations,
  openAnnotations,
  openPinPop,
  refreshAllStages,
  renderAnnotatableImage,
  renderPins,
  updateAnnotBadge,
} from "./annot";
export { openKetcher } from "./ketcher";
export { renderLocatorComments } from "./locator";
export { MOL_VENDOR_SRC, molTeardown, molecule } from "./mol";
export { isTextEditable, openArtifact, renderViewer, showVersions } from "./viewer";

type Target = Record<string, unknown>;

function hostTarget(target?: Target): Target | null {
  if (target) return target;
  const w = (globalThis as unknown as { window?: Target }).window;
  return w || (globalThis as unknown as Target);
}

export function installIslands(target?: Target): void {
  const host = hostTarget(target);
  if (!host) return;
  host.molecule = molecule;
  host._molTeardown = molTeardown;
  host.renderAnnotatableImage = renderAnnotatableImage;
  host.renderPins = renderPins;
  host.openPinPop = openPinPop;
  host.annotationStatus = annotationStatus;
  host.annotationIsHeld = annotationIsHeld;
  host.annotationId = annotationId;
  host.annotationsFor = annotationsFor;
  host.openAnnotations = openAnnotations;
  host.loadAnnotations = loadAnnotations;
  host.updateAnnotBadge = updateAnnotBadge;
  // openConversation closes the previous conversation's annotation draft and
  // view popover through `callLane`; with neither installed the switch left
  // both behind.
  host.closeAnnotDraft = closeAnnotDraft;
  host.closeAnnotPop = closeAnnotPop;
  host.refreshAllStages = refreshAllStages;
  host.openKetcher = openKetcher;
  host.renderLocatorComments = renderLocatorComments;
  host.openArtifact = openArtifact;
  host.showVersions = showVersions;
  host.isTextEditable = isTextEditable;
  host.renderViewer = renderViewer;
}

export function bootIslands(target?: Target): void {
  installIslands(target);
  setRenderViewerImpl(renderViewer);
}

export function islandsReady(target?: Target): boolean {
  const host = hostTarget(target);
  if (!host) return false;
  return isReady(host.molecule) && isReady(host.renderAnnotatableImage) && isReady(host.openKetcher);
}
