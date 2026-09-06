import { useEffect } from "preact/hooks";
import { render } from "preact";
import { filesScope } from "../../stores/artifacts";
import { project } from "../../stores/session";
import { browseFiles, setFilesContentType, setFilesOrigin, setFilesQuery } from "../../features/artifacts/files-index";
import { loadProjectArtifacts, setFilesScope } from "../../features/artifacts/load";
import { filesT } from "../../features/artifacts/copy";
import {
  filesContentType,
  filesHasMore,
  filesIndexError,
  filesIndexLoading,
  filesOrigin,
  filesQuery,
} from "../../features/artifacts/state";
import { renderFilesGrid } from "../../features/artifacts/ui";
import { FILES_PAGE_SIZE } from "../../features/artifacts/types";
import type { FilesOrigin } from "../../features/artifacts/types";
import "../../features/artifacts/artifacts.css";

let searchTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleSearch(value: string): void {
  setFilesQuery(value);
  if (searchTimer !== null) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    searchTimer = null;
    void browseFiles({ reset: true }).then(() => renderFilesGrid());
  }, 200);
}

function applyOrigin(origin: FilesOrigin): void {
  setFilesOrigin(origin);
  void browseFiles({ reset: true }).then(() => renderFilesGrid());
}

function applyType(value: string): void {
  setFilesContentType(value);
  void browseFiles({ reset: true }).then(() => renderFilesGrid());
}

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

  useEffect(() => {
    if (scope === "project") {
      void loadProjectArtifacts().then(() => renderFilesGrid());
    }
  }, [pid, scope]);

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
      <div class="files-toolbar">
        <input
          class="files-search"
          type="search"
          placeholder={filesT("files.search.ph")}
          value={filesQuery.value}
          onInput={(e) => scheduleSearch((e.currentTarget as HTMLInputElement).value)}
        />
        <input
          class="files-filter-type"
          type="search"
          placeholder={filesT("files.filter.type.ph")}
          aria-label={filesT("files.filter.type")}
          value={filesContentType.value}
          onChange={(e) => applyType((e.currentTarget as HTMLInputElement).value)}
        />
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
      </div>
      {filesIndexError.value ? <div class="files-index-note">{filesIndexError.value}</div> : null}
      <div id="results-list" class="files-grid" />
      {filesHasMore.value ? (
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
