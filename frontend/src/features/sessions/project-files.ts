/** Read-only source-folder selection and project file browsing. */
import { t } from "../../i18n";
import { _modalMode } from "../../stores/ui";
import { api, apiErrorText } from "./api";
import { projectFilesCopy as copy } from "./copy";
import { $, el } from "./dom";

type FolderEntry = { name: string; path: string; kind?: "directory" | "file"; size?: number };
type FolderListing = {
  path: string;
  parent_path: string | null;
  folder_path?: string;
  entries: FolderEntry[];
  truncated: boolean;
};
type FilePreview = { path: string; name: string; content: string; size: number; truncated: boolean };

function button(label: string, onClick: () => void, cls = "outline-btn small"): HTMLButtonElement {
  const node = el("button", cls, label);
  node.type = "button";
  node.onclick = onClick;
  return node;
}

function showError(target: HTMLElement, error: unknown, retry: () => void): void {
  target.innerHTML = "";
  const message = el("div", "project-folder-error", copy("error", apiErrorText(error)));
  message.setAttribute("role", "alert");
  target.appendChild(message);
  target.appendChild(button(copy("retry"), retry));
}

let pickerVersion = 0;

/** Reset also retires reads from a closed/reopened project editor. */
export function resetProjectFolderPicker(folderPath?: string | null): void {
  pickerVersion += 1;
  const picker = $("#pm-folder-picker");
  if (picker) {
    picker.innerHTML = "";
    picker.classList.add("hidden");
  }
  if (folderPath !== undefined) {
    const field = $("#pm-folder") as HTMLInputElement | null;
    if (field) field.value = folderPath || "";
  }
}

export async function browseProjectFolder(path?: string): Promise<void> {
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
    const data = await api(`/local-folders?path=${encodeURIComponent(requested)}`) as FolderListing;
    if (!active()) return;
    target.innerHTML = "";
    target.appendChild(el("div", "project-folder-path", data.path));
    const actions = el("div", "project-folder-actions");
    if (data.parent_path !== null) actions.appendChild(button(copy("up"), () => { void browseProjectFolder(data.parent_path!); }));
    actions.appendChild(button(copy("choose"), () => resetProjectFolderPicker(data.path), "solid-btn small"));
    actions.appendChild(button(copy("closePicker"), () => resetProjectFolderPicker()));
    target.appendChild(actions);
    const list = el("div", "project-folder-list");
    for (const entry of data.entries) {
      list.appendChild(button(entry.name + "/", () => { void browseProjectFolder(entry.path); }, "project-folder-entry"));
    }
    if (!data.entries.length) list.appendChild(el("div", "project-folder-note", copy("noFolders")));
    target.appendChild(list);
    if (data.truncated) target.appendChild(el("div", "project-folder-note", copy("limited")));
  } catch (error) {
    if (active()) showError(target, error, () => { void browseProjectFolder(requested); });
  }
}

let browserVersion = 0;

export async function openProjectFiles(projectId: string, projectName: string): Promise<void> {
  const mode = `project-files:${++browserVersion}:${projectId}`;
  _modalMode.value = mode;
  const modal = $("#modal");
  const body = $("#modal-body");
  if (!modal || !body) return;
  const title = $("#modal-title");
  if (title) title.textContent = copy("title", projectName);
  const download = $("#modal-download");
  if (download) download.style.display = "none";
  body.innerHTML = "";
  modal.classList.remove("hidden");
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
  const showFile = async (path: string): Promise<void> => {
    const version = ++request;
    content.innerHTML = "";
    content.appendChild(el("div", "project-folder-note", t("common.loading")));
    try {
      const data = await api(`${base}/file?path=${encodeURIComponent(path)}`) as FilePreview;
      if (!active(version)) return;
      content.innerHTML = "";
      content.appendChild(button(copy("back"), () => { void showDirectory(directory); }));
      content.appendChild(el("div", "project-folder-path", data.path));
      content.appendChild(el("div", "project-folder-note", copy("bytes", data.size.toLocaleString())));
      content.appendChild(el("p", "project-folder-note", copy("previewHelp")));
      if (data.truncated) content.appendChild(el("p", "project-folder-note", copy("previewLimited")));
      // textContent only: a project HTML file is data, never executable markup.
      content.appendChild(el("pre", "project-file-preview", data.content));
    } catch (error) {
      if (active(version)) {
        showError(content, error, () => { void showFile(path); });
        content.appendChild(button(copy("back"), () => { void showDirectory(directory); }));
        content.appendChild(el("p", "project-folder-path", path));
        content.appendChild(el("p", "project-folder-note", copy("previewHelp")));
      }
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
  await showDirectory("");
}
