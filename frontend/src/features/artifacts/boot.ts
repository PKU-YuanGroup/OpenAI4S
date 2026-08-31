import "./artifacts.css";
import { mountFilesPanel } from "../../components/artifacts/FilesPanel";
import { parseTable } from "../csv/csv";
import { setArtifactCreatedSideEffects, setLoadArtifactsImpl } from "../ws/handlers";
import { artifactCreatedSideEffects } from "./events";
import { loadArtifacts, setRenderConversationArtifactsImpl, setRenderFilesGridImpl } from "./load";
import { renderSheet } from "./sheet";
import {
  consumeArtifactDeepLink,
  dockClose,
  dockToggle,
  openArtifactFromHit,
  openViewer,
  renderConversationArtifacts,
  renderDockTabs,
  renderFilesGrid,
  renderViewer,
  setActiveTab,
} from "./ui";

type Target = Record<string, unknown>;

function hostTarget(target?: Target): Target | null {
  if (target) return target;
  const w = (globalThis as unknown as { window?: Target }).window;
  return w || null;
}

/**
 * Assign this lane's contract names onto `window` (F-06 `bootWs` pattern).
 * `parseTable` is F-08's kernel; smoke/E2E read it as a bare global so F-17
 * publishes it with `renderSheet`.
 */
export function installArtifacts(target?: Target): void {
  const host = hostTarget(target);
  if (host) {
    host.parseTable = parseTable;
    host.renderSheet = renderSheet;
    host.openViewer = openViewer;
    host.renderFilesGrid = renderFilesGrid;
    host.renderViewer = renderViewer;
    host.setActiveTab = setActiveTab;
    host.renderDockTabs = renderDockTabs;
    host.dockClose = dockClose;
    host.dockToggle = dockToggle;
    host.openArtifactFromHit = openArtifactFromHit;
  }
  setLoadArtifactsImpl(loadArtifacts);
  setArtifactCreatedSideEffects(artifactCreatedSideEffects);
  setRenderFilesGridImpl(renderFilesGrid);
  setRenderConversationArtifactsImpl(renderConversationArtifacts);
}

export function bootArtifacts(target?: Target): void {
  installArtifacts(target);
  if (typeof document !== "undefined") {
    bindFilesButton();
    mountFilesPanel();
    consumeArtifactDeepLink();
  }
}

function bindFilesButton(): void {
  const btn = document.getElementById("files-btn");
  if (!btn || btn.dataset.f17Bound === "1") return;
  btn.dataset.f17Bound = "1";
  btn.addEventListener("click", () => setActiveTab("files"));
}
