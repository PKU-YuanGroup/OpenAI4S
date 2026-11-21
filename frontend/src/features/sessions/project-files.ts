/** Read-only source-folder selection and project file browsing. */
import { t } from "../../i18n";
import { _modalMode } from "../../stores/ui";
import { bytes } from "../artifacts/api";
import { renderSheet } from "../artifacts/sheet";
import { parseTable } from "../csv/csv";
import { API, ApiError, api, apiErrorText } from "./api";
import { projectFilesCopy as copy } from "./copy";
import { $, closeModalEl, el } from "./dom";

type FolderEntry = {
  name: string;
  path: string;
  kind?: "directory" | "file";
  size?: number | null;
  modified_ms?: number | null;
};
type FolderListing = {
  path: string;
  parent_path: string | null;
  folder_path?: string;
  entries: FolderEntry[];
  truncated: boolean;
};
type FilePreview = { path: string; name: string; content: string; size: number; truncated: boolean };

/**
 * ART-3: a project CSV/TSV is drawn as the Artifact viewer draws one -- the
 * same parser and sheet -- rather than as raw text. A preview cut at the size
 * cap ends mid-record, so its partial last line is left out.
 */
function previewRows(name: string, text: string, truncated: boolean): Record<string, unknown>[] | null {
  if (!/\.(csv|tsv)$/i.test(name)) return null;
  const complete = truncated ? text.slice(0, text.lastIndexOf("\n") + 1) : text;
  const rows = parseTable(complete, { filename: name });
  return rows && rows.length ? rows : null;
}

function button(label: string, onClick: () => void, cls = "outline-btn small"): HTMLButtonElement {
  const node = el("button", cls, label);
  node.type = "button";
  node.onclick = onClick;
  return node;
}

function showError(
  target: HTMLElement,
  error: unknown,
  retry: () => void,
  describe: (error: unknown) => string = apiErrorText,
): void {
  target.innerHTML = "";
  const message = el("div", "project-folder-error", copy("error", describe(error)));
  message.setAttribute("role", "alert");
  target.appendChild(message);
  target.appendChild(button(copy("retry"), retry));
}

/** Copy keys for the refusal codes of a typed or browsed project folder. */
const FOLDER_REFUSALS: Record<string, string> = {
  project_folder_outside_roots: "outsideRoots",
  project_folder_overlap: "overlapsData",
  project_folder_history: "historyFolder",
  local_project_folders_only: "localOnly",
  project_path_denied: "accessDenied",
};
/** `validate_folder_path`'s other refusals share the catch-all code; the status tells them apart. */
const CATCH_ALL_FOLDER_REFUSALS: Record<number, string> = {
  400: "notAFolder",
  403: "credentialFolder",
  404: "folderNotFound",
};

/**
 * A refused project folder -- on Save or in the folder picker -- in the
 * reader's language; the server's text is English. Only for those two: the
 * project files browser's catch-all 400s are about project-relative paths.
 */
export function projectFolderErrorText(error: unknown): string {
  const rec = error && typeof error === "object" ? (error as { code?: unknown; status?: unknown }) : null;
  const code = typeof rec?.code === "string" ? rec.code : "";
  const key = code === "project_folder" ? CATCH_ALL_FOLDER_REFUSALS[Number(rec?.status)] : FOLDER_REFUSALS[code];
  return key ? copy(key) : apiErrorText(error);
}

let pickerVersion = 0;
// Dot-folders (.cache, .conda…) are left out unless the picker asks for them.
let showHiddenFolders = false;

/** Reset also retires reads from a closed/reopened project editor. */
export function resetProjectFolderPicker(folderPath?: string | null): void {
  pickerVersion += 1;
  const picker = $("#pm-folder-picker");
  if (picker) {
    picker.innerHTML = "";
    picker.classList.add("hidden");
  }
  if (folderPath !== undefined) {
    showHiddenFolders = false;
    const field = $("#pm-folder") as HTMLInputElement | null;
    if (field) field.value = folderPath || "";
  }
}

export async function browseProjectFolder(path?: string, focusHiddenToggle = false): Promise<void> {
  const target = $("#pm-folder-picker");
  const modal = $("#proj-modal");
  if (!target || !modal) return;
  const version = ++pickerVersion;
  const requested = path ?? ($("#pm-folder") as HTMLInputElement | null)?.value.trim() ?? "";
  const active = () => version === pickerVersion && !modal.classList.contains("hidden");
  target.classList.remove("hidden");
  target.innerHTML = "";
  target.appendChild(el("div", "project-folder-note", t("common.loading")));
  try {
    const hidden = showHiddenFolders ? "&hidden=1" : "";
    const data = await api(`/local-folders?path=${encodeURIComponent(requested)}${hidden}`) as FolderListing;
    if (!active()) return;
    target.innerHTML = "";
    target.appendChild(el("div", "project-folder-path", data.path));
    const actions = el("div", "project-folder-actions");
    if (data.parent_path !== null) actions.appendChild(button(copy("up"), () => { void browseProjectFolder(data.parent_path!); }));
    actions.appendChild(button(copy("choose"), () => resetProjectFolderPicker(data.path), "solid-btn small"));
    actions.appendChild(button(copy("closePicker"), () => resetProjectFolderPicker()));
    target.appendChild(actions);
    const toggle = el("label", "project-folder-hidden-toggle");
    const checkbox = el("input");
    checkbox.type = "checkbox";
    checkbox.checked = showHiddenFolders;
    checkbox.onchange = () => {
      showHiddenFolders = checkbox.checked;
      void browseProjectFolder(data.path, true);
    };
    toggle.appendChild(checkbox);
    toggle.appendChild(el("span", null, copy("showHidden")));
    target.appendChild(toggle);
    // The listing is rebuilt; keep a keyboard user on the box they ticked.
    if (focusHiddenToggle) checkbox.focus();
    const list = el("div", "project-folder-list");
    for (const entry of data.entries) {
      list.appendChild(button(entry.name + "/", () => { void browseProjectFolder(entry.path); }, "project-folder-entry"));
    }
    if (!data.entries.length) list.appendChild(el("div", "project-folder-note", copy("noFolders")));
    target.appendChild(list);
    if (data.truncated) target.appendChild(el("div", "project-folder-note", copy("limited")));
  } catch (error) {
    if (active()) showError(target, error, () => { void browseProjectFolder(requested); }, projectFolderErrorText);
  }
}

/** A listed file's size and modification time, as much as the server knows. */
export function entryMeta(entry: Pick<FolderEntry, "size" | "modified_ms">): string {
  const parts: string[] = [];
  if (typeof entry.size === "number") parts.push(bytes(entry.size));
  if (typeof entry.modified_ms === "number") {
    parts.push(new Date(entry.modified_ms).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }));
  }
  return parts.join(" · ");
}

/**
 * ART-3 "import and view": read one project file whole and add it to the
 * session as an input, then open it in the Artifact viewer. The upload path
 * is the paperclip's own, so naming conflicts, a busy session and the first
 * session of a project behave as they do there.
 */
export async function importProjectFile(base: string, path: string): Promise<void> {
  const response = await fetch(`${API}${base}/file?path=${encodeURIComponent(path)}&raw=1`);
  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    throw new ApiError(body, response.status);
  }
  const name = path.slice(path.lastIndexOf("/") + 1);
  const file = new File([await response.blob()], name);
  const { uploadFiles } = await import("../chrome/upload");
  const [result] = await uploadFiles([file]);
  if (!result || result.skipped) return;
  if (!result.ok) throw result.error instanceof Error ? result.error : new Error(String(result.error || ""));
  const [{ loadArtifacts }, { presentViewer }, { artifacts }] = await Promise.all([
    import("../artifacts/load"),
    import("../artifacts/ui"),
    import("../../stores/artifacts"),
  ]);
  if (result.frameId) await loadArtifacts(result.frameId);
  const rows = (artifacts.value as Array<{ id: string; filename?: string | null }>) || [];
  const row = rows.find((candidate) => candidate.filename === result.filename);
  if (row) presentViewer(row);
}

let browserVersion = 0;

export async function openProjectFiles(
  projectId: string,
  projectName: string,
  startPath = "",
  startIsFile = false,
): Promise<void> {
  const mode = `project-files:${++browserVersion}:${projectId}`;
  _modalMode.value = mode;
  const modal = $("#modal");
  const modalBody = $("#modal-body");
  if (!modal || !modalBody) return;
  const title = $("#modal-title");
  if (title) title.textContent = copy("title", projectName);
  const download = $("#modal-download");
  if (download) download.style.display = "none";
  modalBody.innerHTML = "";
  modal.classList.remove("hidden");
  // UP-5: the shared modal body has no padding of its own (previews fill it
  // edge to edge), so this view brings its own: the path, note and input sat
  // flush against the border with their first character cut.
  const body = el("div", "project-files-pane");
  modalBody.appendChild(body);
  const sourcePath = el("div", "project-folder-path");
  body.appendChild(sourcePath);
  body.appendChild(el("p", "project-folder-note", copy("readOnly")));
  const toolbar = el("div", "project-folder-actions");
  const pathInput = el("input", "project-folder-input");
  pathInput.type = "text";
  pathInput.setAttribute("aria-label", copy("path"));
  pathInput.placeholder = copy("root");
  toolbar.appendChild(pathInput);
  body.appendChild(toolbar);
  const content = el("div", "project-files-content");
  content.setAttribute("aria-live", "polite");
  body.appendChild(content);
  let request = 0;
  let directory = "";
  let parentPath: string | null = null;
  const active = (version: number) => _modalMode.value === mode && request === version && !modal.classList.contains("hidden");
  const base = `/projects/${encodeURIComponent(projectId)}`;
  // ART-3: any project file opens in the Artifact viewer -- sheets, tables,
  // images, structures -- once it is an input of the session. The bytes are
  // read through the read-only project route and handed to the ordinary
  // upload; nothing is written to the project folder.
  const importButton = (path: string): HTMLButtonElement => {
    const node = button(copy("importView"), () => {
      const version = request;
      node.disabled = true;
      node.textContent = copy("importing");
      const status = el("p", "project-folder-note");
      status.setAttribute("role", "status");
      void importProjectFile(base, path).then(
        () => { if (active(version)) closeModalEl(modal); },
        (error: unknown) => {
          if (!active(version)) return;
          node.disabled = false;
          node.textContent = copy("importView");
          status.textContent = (error as { code?: string } | null)?.code === "project_file_too_large"
            ? copy("importTooLarge")
            : copy("importFailed", apiErrorText(error));
          node.after(status);
        },
      );
    }, "solid-btn small");
    return node;
  };
  const showFile = async (path: string): Promise<void> => {
    const version = ++request;
    content.innerHTML = "";
    content.appendChild(el("div", "project-folder-note", t("common.loading")));
    try {
      const data = await api(`${base}/file?path=${encodeURIComponent(path)}`) as FilePreview;
      if (!active(version)) return;
      content.innerHTML = "";
      content.appendChild(button(copy("back"), () => { void showDirectory(directory); }));
      content.appendChild(importButton(data.path));
      content.appendChild(el("div", "project-folder-path", data.path));
      content.appendChild(el("div", "project-folder-note", copy("bytes", data.size.toLocaleString())));
      content.appendChild(el("p", "project-folder-note", copy("previewHelp")));
      if (data.truncated) content.appendChild(el("p", "project-folder-note", copy("previewLimited")));
      const rows = previewRows(data.name || data.path, data.content, data.truncated);
      // textContent only: a project HTML file is data, never executable markup.
      if (rows) renderSheet(content, rows);
      else content.appendChild(el("pre", "project-file-preview", data.content));
    } catch (error) {
      if (!active(version)) return;
      if ((error as { code?: string } | null)?.code === "binary_preview") {
        // Not a failure and nothing a retry changes: this type has no text
        // preview. It used to be a red "cannot read this path" with Retry.
        content.innerHTML = "";
        content.appendChild(button(copy("back"), () => { void showDirectory(directory); }));
        content.appendChild(el("p", "project-folder-path", path));
        content.appendChild(el("p", "project-folder-note", copy("noInlinePreview")));
        content.appendChild(importButton(path));
        return;
      }
      showError(content, error, () => { void showFile(path); });
      content.appendChild(button(copy("back"), () => { void showDirectory(directory); }));
      content.appendChild(el("p", "project-folder-path", path));
      content.appendChild(el("p", "project-folder-note", copy("previewHelp")));
    }
  };
  const showDirectory = async (path: string): Promise<void> => {
    const version = ++request;
    content.innerHTML = "";
    content.appendChild(el("div", "project-folder-note", t("common.loading")));
    try {
      const data = await api(`${base}/files?path=${encodeURIComponent(path)}`) as FolderListing;
      if (!active(version)) return;
      directory = data.path;
      parentPath = data.parent_path;
      up.disabled = parentPath === null;
      pathInput.value = directory;
      sourcePath.textContent = data.folder_path || "";
      content.innerHTML = "";
      const list = el("div", "project-folder-list");
      for (const entry of data.entries) {
        const isFolder = entry.kind === "directory";
        const row = button(entry.name + (isFolder ? "/" : ""), () => {
          if (isFolder) void showDirectory(entry.path);
          else void showFile(entry.path);
        }, "project-folder-entry");
        row.title = entry.path;
        const meta = isFolder ? "" : entryMeta(entry);
        if (meta) row.appendChild(el("span", "project-folder-meta", meta));
        list.appendChild(row);
      }
      if (!data.entries.length) list.appendChild(el("div", "project-folder-note", copy("empty")));
      content.appendChild(list);
      if (data.truncated) content.appendChild(el("p", "project-folder-note", copy("limited")));
    } catch (error) {
      if (active(version)) showError(content, error, () => { void showDirectory(path); });
    }
  };
  toolbar.appendChild(button(copy("open"), () => { void showDirectory(pathInput.value.trim()); }));
  const up = button(copy("up"), () => { if (parentPath !== null) void showDirectory(parentPath); });
  up.disabled = true;
  toolbar.appendChild(up);
  toolbar.appendChild(button(copy("refresh"), () => { void showDirectory(directory); }));
  pathInput.onkeydown = (event) => {
    // Enter that commits an IME candidate must not navigate to a partial path.
    if (event.isComposing || event.keyCode === 229) return;
    if (event.key === "Enter") {
      event.preventDefault();
      void showDirectory(pathInput.value.trim());
    }
  };
  // The Files panel's project-folder section opens here at an entry (UP-5).
  if (startIsFile && startPath) {
    const slash = startPath.lastIndexOf("/");
    directory = slash > 0 ? startPath.slice(0, slash) : "";
    await showFile(startPath);
  } else await showDirectory(startPath);
}
