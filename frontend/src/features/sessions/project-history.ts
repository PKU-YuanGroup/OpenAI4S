/** Local .openai4s history: browse immutable snapshots, never restore kernels. */
import { t } from "../../i18n";
import { _modalMode } from "../../stores/ui";
import { api, apiErrorText, API } from "./api";
import { binds } from "./binds";
import { projectHistoryCopy as copy } from "./copy";
import { $, closeModalEl, el } from "./dom";

type RecordValue = Record<string, unknown>;
type HistorySession = {
  session_id: string; title: string; revision: string; updated_at?: number | null;
  message_count?: number; cell_count?: number; file_count?: number; can_continue?: boolean;
};
type HistoryListing = {
  directory: string; status: string; last_saved_at: number | null; error?: string | null;
  sessions: HistorySession[]; truncated?: boolean;
};
type Snapshot = {
  session_id: string; title: string; revision: string; saved_at?: number | null; can_continue: boolean;
  branch_id?: string;
  revisions: Array<{ revision: string; saved_at?: number | null }>;
  messages: RecordValue[]; cells: RecordValue[]; files: RecordValue[]; settings: RecordValue;
  omissions?: Array<{ path: string; reason: string }>; truncated?: boolean;
};

const text = (value: unknown): string => typeof value === "string" ? value : value == null ? "" : JSON.stringify(value);
const count = (value: unknown): number => typeof value === "number" && Number.isFinite(value) ? Math.max(0, value) : 0;
const time = (value: unknown): string => typeof value === "number" && Number.isFinite(value) && !Number.isNaN(new Date(value).getTime()) ? new Date(value).toLocaleString() : "";
const record = (value: unknown): value is RecordValue => !!value && typeof value === "object" && !Array.isArray(value);

function button(label: string, action: () => void, cls = "outline-btn small"): HTMLButtonElement {
  const node = el("button", cls, label);
  node.type = "button";
  node.onclick = action;
  return node;
}

function listing(value: unknown): HistoryListing {
  if (!record(value) || typeof value.status !== "string" || !Array.isArray(value.sessions) ||
    value.sessions.some((item) => !record(item) || typeof item.session_id !== "string")) throw new Error(copy("invalid"));
  return value as unknown as HistoryListing;
}

function snapshot(value: unknown, sessionId: string): Snapshot {
  if (!record(value) || value.session_id !== sessionId || typeof value.revision !== "string" ||
    !Array.isArray(value.revisions) || !Array.isArray(value.messages) || !Array.isArray(value.cells) || !Array.isArray(value.files)) throw new Error(copy("invalid"));
  return value as unknown as Snapshot;
}

let modalVersion = 0;

export async function openProjectHistory(projectId: string, projectName: string): Promise<void> {
  const mode = `project-history:${++modalVersion}:${projectId}`;
  _modalMode.value = mode;
  const modal = $("#modal");
  const body = $("#modal-body");
  if (!modal || !body) return;
  const title = $("#modal-title");
  if (title) title.textContent = copy("title", projectName);
  const download = $("#modal-download");
  if (download) download.style.display = "none";
  body.innerHTML = "";
  const view = el("div", "project-history-view");
  body.appendChild(view);
  modal.classList.remove("hidden");
  const base = `/projects/${encodeURIComponent(projectId)}/history`;
  let request = 0;
  const active = (version: number) => _modalMode.value === mode && request === version && !modal.classList.contains("hidden");
  const status = el("div", "project-history-status");
  status.setAttribute("aria-live", "polite");
  view.appendChild(status);
  view.appendChild(el("p", "project-folder-note", copy("automatic")));
  const actions = el("div", "project-folder-actions");
  view.appendChild(actions);
  const content = el("div", "project-history-content");
  view.appendChild(content);

  const showError = (target: HTMLElement, error: unknown, retry: () => void, saving = false) => {
    target.innerHTML = "";
    const message = el("p", "project-folder-error", copy(saving ? "saveError" : "loadError", apiErrorText(error)));
    message.setAttribute("role", "alert");
    target.appendChild(message);
    target.appendChild(button(copy("retry"), retry));
  };

  const renderStatus = (data: HistoryListing) => {
    status.innerHTML = "";
    status.appendChild(el("div", "project-history-label", copy("directory")));
    status.appendChild(el("div", "project-folder-path", text(data.directory)));
    const known = ["saved", "saving", "error", "unavailable", "pending"].includes(data.status);
    status.appendChild(el("strong", `project-history-state ${known ? data.status : "unknown"}`, copy(known ? data.status : "unknown")));
    status.appendChild(el("div", "project-folder-note", time(data.last_saved_at) ? copy("lastSaved", time(data.last_saved_at)) : copy("neverSaved")));
    if (data.error) {
      const failure = el("p", "project-folder-error", text(data.error));
      failure.setAttribute("role", "alert");
      status.appendChild(failure);
    }
  };

  const detailUrl = (sessionId: string, revision?: string) => `${base}/${encodeURIComponent(sessionId)}${revision ? `?revision=${encodeURIComponent(revision)}` : ""}`;

  const continueSession = async (sessionId: string, revision: string, target: HTMLElement, control: HTMLButtonElement): Promise<void> => {
    const version = ++request;
    control.disabled = true;
    try {
      // Recheck current ownership/existence; an on-disk record alone grants no
      // executable conversation, and a saved boolean can become stale.
      const latest = snapshot(await api(detailUrl(sessionId, revision)), sessionId);
      if (!active(version)) return;
      if (latest.can_continue !== true) throw new Error(copy("cannotContinue"));
      await binds.openConversation(sessionId, projectId);
      if (active(version)) closeModalEl(modal);
    } catch (error) {
      if (active(version)) showError(target, error, () => { void showSnapshot(sessionId, revision); });
    } finally {
      if (active(version)) control.disabled = false;
    }
  };

  const renderSnapshot = (data: Snapshot) => {
    content.innerHTML = "";
    content.appendChild(button(copy("back"), () => { void showList(); }));
    content.appendChild(el("h3", "project-history-title", text(data.title) || copy("untitled")));
    const revisionBar = el("div", "project-folder-actions");
    const label = el("label", null, copy("revision"));
    const select = el("select", "project-history-revision");
    select.setAttribute("aria-label", copy("revision"));
    let revisions = data.revisions.filter((item) => record(item) && typeof item.revision === "string");
    if (!revisions.some((item) => item.revision === data.revision)) revisions = [{ revision: data.revision, saved_at: data.saved_at }, ...revisions];
    for (const item of revisions) {
      const option = el("option", null, `${time(item.saved_at) || item.revision} · ${item.revision.slice(0, 12)}`);
      option.value = item.revision;
      select.appendChild(option);
    }
    select.value = data.revision;
    select.onchange = () => { void showSnapshot(data.session_id, select.value); };
    label.appendChild(select);
    revisionBar.appendChild(label);
    content.appendChild(revisionBar);
    content.appendChild(el("div", "project-folder-note", copy("revisionId", data.revision)));
    if (typeof data.branch_id === "string" && data.branch_id) content.appendChild(el("div", "project-folder-note project-history-branch", copy("branch", data.branch_id)));
    if (time(data.saved_at)) content.appendChild(el("div", "project-folder-note", copy("savedAt", time(data.saved_at))));
    const continueBox = el("div", "project-history-continue");
    if (data.can_continue === true) {
      const resume = button(copy("continue"), () => { void continueSession(data.session_id, data.revision, continueBox, resume); }, "solid-btn small");
      continueBox.appendChild(resume);
      continueBox.appendChild(el("p", "project-folder-note", copy("continueHelp")));
    } else continueBox.appendChild(el("p", "project-folder-note", copy("archivedOnly")));
    content.appendChild(continueBox);
    if (data.truncated) content.appendChild(el("p", "project-folder-note", copy("limited")));
    if (data.omissions?.length) {
      const omitted = el("div", "project-history-omissions");
      omitted.setAttribute("role", "status");
      omitted.appendChild(el("strong", null, copy("omissions")));
      omitted.appendChild(el("p", "project-folder-note", copy("omissionsHelp")));
      for (const item of data.omissions) omitted.appendChild(el("div", "project-folder-path", `${text(item.path)} — ${text(item.reason)}`));
      content.appendChild(omitted);
    }
    const tabs = el("div", "project-research-tabs");
    tabs.setAttribute("role", "group");
    const panel = el("div", "project-history-panel");
    content.appendChild(tabs);
    content.appendChild(panel);
    const selectTab = (tab: string) => {
      for (const node of Array.from(tabs.children)) node.setAttribute("aria-pressed", String((node as HTMLElement).dataset.tab === tab));
      panel.innerHTML = "";
      if (tab === "messages") {
        if (!data.messages.length) panel.appendChild(el("p", "project-folder-note", copy("noMessages")));
        for (const message of data.messages) {
          const row = el("article", "project-history-message");
          const role = text(message.role);
          row.appendChild(el("strong", "project-history-label", copy(["user", "assistant", "system", "tool"].includes(role) ? role : "other")));
          if (time(message.created_at)) row.appendChild(el("span", "project-folder-note", time(message.created_at)));
          row.appendChild(el("pre", "project-history-text", text(message.content)));
          panel.appendChild(row);
        }
      } else if (tab === "cells") {
        if (!data.cells.length) panel.appendChild(el("p", "project-folder-note", copy("noCells")));
        for (const cell of data.cells) {
          const row = el("article", "project-history-message");
          row.appendChild(el("strong", "project-history-label", text(cell.language) || "python"));
          row.appendChild(el("pre", "project-history-text", text(cell.source)));
          for (const [field, label] of [["stdout", "stdout"], ["stderr", "stderr"], ["error", "cellError"]]) {
            if (!cell[field!]) continue;
            row.appendChild(el("strong", "project-history-label", copy(label!)));
            row.appendChild(el("pre", "project-history-text", text(cell[field!])));
          }
          panel.appendChild(row);
        }
      } else if (tab === "files") {
        if (!data.files.length) panel.appendChild(el("p", "project-folder-note", copy("noFiles")));
        for (const file of data.files) {
          const path = text(file.path);
          const row = el("article", "project-history-file");
          row.appendChild(el("strong", "project-folder-path", path));
          if (typeof file.size_bytes === "number") row.appendChild(el("div", "project-folder-note", copy("bytes", count(file.size_bytes).toLocaleString())));
          if (file.sha256) row.appendChild(el("div", "project-history-checksum", `${copy("checksum")}: ${text(file.sha256)}`));
          if (file.version_id) row.appendChild(el("div", "project-folder-note", text(file.version_id)));
          if (path && file.available !== false) {
            const link = el("a", "outline-btn small", copy("download"));
            link.href = `${API}${base}/${encodeURIComponent(data.session_id)}/file?revision=${encodeURIComponent(data.revision)}&path=${encodeURIComponent(path)}`;
            link.download = path.split("/").pop() || "download";
            row.appendChild(link);
          } else row.appendChild(el("p", "project-folder-note", copy("unavailableFile")));
          panel.appendChild(row);
        }
      } else {
        panel.appendChild(el("p", "project-folder-note", copy("settingsHelp")));
        for (const field of ["name", "description", "context"]) {
          panel.appendChild(el("h4", "project-history-label", copy(field)));
          panel.appendChild(el("pre", "project-history-text", text(data.settings?.[field])));
        }
      }
    };
    for (const tab of ["messages", "cells", "files", "settings"]) {
      const tabButton = button(copy(tab), () => selectTab(tab), "seg-btn");
      tabButton.dataset.tab = tab;
      tabs.appendChild(tabButton);
    }
    selectTab("messages");
  };

  const showSnapshot = async (sessionId: string, revision?: string): Promise<void> => {
    const version = ++request;
    content.innerHTML = "";
    content.appendChild(el("p", "project-folder-note", t("common.loading")));
    try {
      const data = snapshot(await api(detailUrl(sessionId, revision)), sessionId);
      if (!active(version)) return;
      renderSnapshot(data);
    } catch (error) {
      if (active(version)) {
        showError(content, error, () => { void showSnapshot(sessionId, revision); });
        content.appendChild(button(copy("back"), () => { void showList(); }));
      }
    }
  };

  const showList = async (save = false): Promise<void> => {
    const version = ++request;
    refresh.disabled = save;
    saveNow.disabled = save;
    content.innerHTML = "";
    content.appendChild(el("p", "project-folder-note", save ? copy("saving") : t("common.loading")));
    if (save) {
      status.innerHTML = "";
      status.appendChild(el("strong", "project-history-state saving", copy("saving")));
    }
    try {
      const data = listing(await api(base, save ? { method: "POST", body: "{}" } : undefined));
      if (!active(version)) return;
      renderStatus(data);
      content.innerHTML = "";
      content.appendChild(el("h3", "project-history-title", copy("sessions")));
      if (!data.sessions.length) content.appendChild(el("p", "project-folder-note", copy("empty")));
      for (const session of data.sessions) {
        const row = el("article", "project-history-session");
        row.appendChild(el("strong", "project-history-label", text(session.title) || copy("untitled")));
        if (time(session.updated_at)) row.appendChild(el("div", "project-folder-note", time(session.updated_at)));
        row.appendChild(el("div", "project-folder-note", copy("counts", count(session.message_count), count(session.cell_count), count(session.file_count))));
        row.appendChild(button(copy("open"), () => { void showSnapshot(session.session_id, session.revision); }));
        content.appendChild(row);
      }
      if (data.truncated) content.appendChild(el("p", "project-folder-note", copy("limited")));
    } catch (error) {
      if (active(version)) {
        if (save) {
          status.innerHTML = "";
          status.appendChild(el("strong", "project-history-state error", copy("error")));
        }
        showError(content, error, () => { void showList(save); }, save);
      }
    } finally {
      if (active(version)) { refresh.disabled = false; saveNow.disabled = false; }
    }
  };
  const refresh = button(copy("refresh"), () => { void showList(); });
  const saveNow = button(copy("saveNow"), () => { void showList(true); }, "solid-btn small");
  refresh.id = "project-history-refresh";
  saveNow.id = "project-history-save";
  actions.appendChild(refresh);
  actions.appendChild(saveNow);
  await showList();
}
