import { useEffect, useState } from "preact/hooks";
import { api, apiErrorText } from "../../features/artifacts/api";
import { filesT } from "../../features/artifacts/copy";
import { effProject } from "../../features/customize/host";
import { openProjectFiles } from "../../features/sessions/project-files";
import { projects } from "../../stores/session";

type FolderEntry = { name: string; path: string; kind: string; size?: number | null };
type Listing = { pid: string; entries: FolderEntry[]; truncated: boolean; error: string | null };
type ProjectRow = { id?: string; project_id?: string; name?: string | null; folder_path?: string | null };

/** How many top-level entries the panel lists before "browse all". */
export const PROJECT_FOLDER_PREVIEW = 12;

export function projectFolderOf(pid: string | null): { name: string; folder: string } | null {
  if (!pid) return null;
  const row = ((projects.value as ProjectRow[]) || []).find((p) => (p.project_id || p.id) === pid);
  if (!row || !row.folder_path) return null;
  return { name: String(row.name || pid), folder: String(row.folder_path) };
}

/**
 * UP-5: the bound project folder in the Files panel, read-only. It used to be
 * reachable only from the project menu's "项目文件" modal. Entries open that
 * same modal at the folder or file, which is where navigation and previews
 * already live; nothing here writes to the folder.
 */
export function ProjectFolderSection() {
  const pid = effProject();
  const bound = projectFolderOf(pid);
  const [listing, setListing] = useState<Listing | null>(null);

  useEffect(() => {
    if (!pid || !bound) return undefined;
    let live = true;
    api(`/projects/${encodeURIComponent(pid)}/files?path=`)
      .then((data) => {
        if (!live) return;
        const entries = ((data as { entries?: FolderEntry[] }).entries || []).filter(
          (entry) => entry && typeof entry.path === "string",
        );
        setListing({
          pid,
          entries: entries.slice(0, PROJECT_FOLDER_PREVIEW),
          truncated: entries.length > PROJECT_FOLDER_PREVIEW || !!(data as { truncated?: boolean }).truncated,
          error: null,
        });
      })
      .catch((error: unknown) => {
        if (live) setListing({ pid, entries: [], truncated: false, error: apiErrorText(error) });
      });
    return () => {
      live = false;
    };
  }, [pid, bound?.folder]);

  if (!pid || !bound) return null;
  const shown = listing && listing.pid === pid ? listing : null;
  return (
    <details class="files-project-folder" open>
      <summary>
        <span class="files-project-folder-title">{filesT("files.projectFolder.title")}</span>
        <span class="files-project-folder-path" title={bound.folder}>
          {bound.folder}
        </span>
      </summary>
      <p class="files-project-folder-note">{filesT("files.projectFolder.note")}</p>
      {shown && shown.error ? <div class="files-project-folder-note">{shown.error}</div> : null}
      {shown && !shown.error && !shown.entries.length ? (
        <div class="files-project-folder-note">{filesT("files.projectFolder.empty")}</div>
      ) : null}
      <div class="files-project-folder-list">
        {(shown ? shown.entries : []).map((entry) => {
          const folder = entry.kind === "directory";
          return (
            <button
              key={entry.path}
              type="button"
              class="files-project-folder-entry"
              title={entry.path}
              onClick={() => void openProjectFiles(pid, bound.name, entry.path, !folder)}
            >
              {entry.name + (folder ? "/" : "")}
            </button>
          );
        })}
      </div>
      <button type="button" class="outline-btn small" onClick={() => void openProjectFiles(pid, bound.name)}>
        {filesT(shown && shown.truncated ? "files.projectFolder.browseMore" : "files.projectFolder.browse")}
      </button>
    </details>
  );
}
