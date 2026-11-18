import { DraftsPanel } from "./DraftsPanel";
import { ProjectFolderSection } from "./ProjectFolderSection";
import { useEffect } from "preact/hooks";
import { render } from "preact";
import { filesScope } from "../../stores/artifacts";
import { _openGen, currentId, project } from "../../stores/session";
import {
  browseFiles,
  currentFilesFingerprint,
  filesListingIsCurrent,
  setFilesKind,
  setFilesOrigin,
  setFilesQuery,
  setFilesShowHidden,
  setFilesSort,
} from "../../features/artifacts/files-index";
import { loadProjectArtifacts, setFilesScope } from "../../features/artifacts/load";
import { filesT } from "../../features/artifacts/copy";
import {
  filesHasMore,
  filesIndexError,
  filesIndexLoading,
  filesKind,
  filesOrigin,
  filesQuery,
  filesShowHidden,
  filesSort,
} from "../../features/artifacts/state";
import { renderFilesGrid } from "../../features/artifacts/ui";
import { FILES_PAGE_SIZE } from "../../features/artifacts/types";
import type { FilesOrigin } from "../../features/artifacts/types";
import "../../features/artifacts/artifacts.css";

let searchTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleSearch(value: string): void {
  setFilesQuery(value);
  if (searchTimer !== null) clearTimeout(searchTimer);
  const identity = currentFilesFingerprint();
  searchTimer = setTimeout(() => {
    searchTimer = null;
    if (identity !== currentFilesFingerprint()) return;
    void browseFiles({ reset: true }).then(() => renderFilesGrid());
  }, 200);
}

function applyOrigin(origin: FilesOrigin): void {
  setFilesOrigin(origin);
  void browseFiles({ reset: true }).then(() => renderFilesGrid());
}

/** ART-12: a human category, not a MIME substring. */
function applyKind(value: string): void {
  setFilesKind(value);
  void browseFiles({ reset: true }).then(() => renderFilesGrid());
}

function applySort(value: string): void {
  setFilesSort(value);
  void browseFiles({ reset: true }).then(() => renderFilesGrid());
}

function applyShowHidden(value: boolean): void {
  setFilesShowHidden(value);
  void browseFiles({ reset: true }).then(() => renderFilesGrid());
}

const KIND_KEYS = [
  ["", "files.filter.kind.all"],
  ["table", "files.filter.kind.table"],
  ["image", "files.filter.kind.image"],
  ["report", "files.filter.kind.report"],
  ["code", "files.filter.kind.code"],
  ["other", "files.filter.kind.other"],
] as const;

const SORT_KEYS = [
  ["newest", "files.sort.newest"],
  ["oldest", "files.sort.oldest"],
  ["name", "files.sort.name"],
] as const;

/**
 * Files dock chrome (M-03). Mounted into the shell's `#dock-files`.
 * Frozen ids: `#results-list`, `#results-count`, `#files-scope`.
 * The grid itself is painted imperatively by `renderFilesGrid` so a
 * signal-driven re-render of this chrome cannot wipe cards.
 */
export function FilesPanel() {
  const origin = filesOrigin.value;
  const scope = filesScope.value;
  const pid = project.value;
  const fid = currentId.value;
  const generation = _openGen.value;
  const currentListing = filesListingIsCurrent();

  useEffect(() => {
    if (searchTimer !== null) { clearTimeout(searchTimer); searchTimer = null; }
    if (scope === "project") {
      void loadProjectArtifacts().then(() => renderFilesGrid());
    } else {
      void browseFiles({ refresh: true }).then(() => renderFilesGrid());
    }
    return () => {
      if (searchTimer !== null) { clearTimeout(searchTimer); searchTimer = null; }
    };
  }, [pid, fid, generation, scope]);

  useEffect(() => {
    renderFilesGrid();
  });

  return (
    <>
      <div class="files-head">
        <span data-i18n="dock.files.heading">{filesT("dock.files.heading")}</span>
        <span id="results-count" class="count">
          0
        </span>
        <span class="seg files-scope" id="files-scope">
          <button
            class={scope === "frame" ? "seg-btn active" : "seg-btn"}
            data-scope="frame"
            data-i18n="dock.files.scope.frame"
            onClick={() => void setFilesScope("frame")}
          >
            {filesT("dock.files.scope.frame")}
          </button>
          <button
            class={scope === "project" ? "seg-btn active" : "seg-btn"}
            data-scope="project"
            data-i18n="dock.files.scope.project"
            onClick={() => void setFilesScope("project")}
          >
            {filesT("dock.files.scope.project")}
          </button>
        </span>
      </div>
      <DraftsPanel />
      <ProjectFolderSection />
      <div class="files-toolbar">
        <input
          class="files-search"
          type="search"
          placeholder={filesT("files.search.ph")}
          value={filesQuery.value}
          onInput={(e) => scheduleSearch((e.currentTarget as HTMLInputElement).value)}
        />
        <select
          class="files-filter-type"
          aria-label={filesT("files.filter.type")}
          value={filesKind.value}
          onChange={(e) => applyKind((e.currentTarget as HTMLSelectElement).value)}
        >
          {KIND_KEYS.map(([key, label]) => (
            <option key={key || "all"} value={key}>
              {filesT(label)}
            </option>
          ))}
        </select>
        <span class="files-origin" role="group" aria-label={filesT("files.filter.origin")}>
          {(["", "uploaded", "generated"] as const).map((key) => (
            <button
              key={key || "all"}
              class={origin === key ? "seg-btn active" : "seg-btn"}
              data-origin={key || "all"}
              onClick={() => applyOrigin(key)}
            >
              {filesT(
                key === ""
                  ? "files.filter.origin.all"
                  : key === "uploaded"
                    ? "files.filter.origin.uploaded"
                    : "files.filter.origin.generated",
              )}
            </button>
          ))}
        </span>
        <div class="files-toolbar-extra">
          {scope === "frame" ? (
            <select
              class="files-sort"
              aria-label={filesT("files.sort.label")}
              value={filesSort.value}
              onChange={(e) => applySort((e.currentTarget as HTMLSelectElement).value)}
            >
              {SORT_KEYS.map(([key, label]) => (
                <option key={key} value={key}>
                  {filesT(label)}
                </option>
              ))}
            </select>
          ) : null}
          <label class="files-show-hidden">
            <input
              type="checkbox"
              checked={filesShowHidden.value}
              onChange={(e) => applyShowHidden((e.currentTarget as HTMLInputElement).checked)}
            />
            {filesT("files.showHidden")}
          </label>
        </div>
      </div>
      {currentListing && filesIndexError.value ? <div class="files-index-note">{filesIndexError.value}</div> : null}
      <div id="results-list" class="files-grid" />
      {currentListing && filesHasMore.value ? (
        <button
          class="outline-btn small files-load-more"
          disabled={filesIndexLoading.value}
          onClick={() =>
            void browseFiles({ loadMore: true, limit: FILES_PAGE_SIZE }).then(() => renderFilesGrid())
          }
        >
          {filesT("files.loadMore")}
        </button>
      ) : null}
    </>
  );
}

/** Lane-local mount: paint into the shell `#dock-files` without editing Shell.tsx. */
export function mountFilesPanel(host?: HTMLElement | null): void {
  const node =
    host || (typeof document !== "undefined" ? document.getElementById("dock-files") : null);
  if (!node || node.dataset.m03Mounted === "1") return;
  node.dataset.m03Mounted = "1";
  render(<FilesPanel />, node);
}
