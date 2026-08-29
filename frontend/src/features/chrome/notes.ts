/**
 * Project notes in the Files dock. Port of app.js:10909-10914.
 */

import { t } from "../../i18n/runtime";
import { currentId, project, sessions } from "../../stores/session";
import { api } from "./api";
import { $, ago, el, iconEl } from "./dom";
import { hostFn, isReady } from "./host";

type NoteRow = {
  note_id?: string;
  id?: string;
  content?: string;
  text?: string;
  updated_at?: string;
  created_at?: string;
};

type SessionRow = { id?: string; project_id?: string | null };

/** app.js:10910 */
export function effProject(): string | null {
  if (project.value) return project.value;
  const list = sessions.value as SessionRow[];
  const f = list.find((x) => x.id === currentId.value);
  return (f && f.project_id) || null;
}

export function renderNotes(notes: NoteRow[]): void {
  const list = $("#notes-list");
  if (!list) return;
  list.innerHTML = "";
  if (!notes.length) {
    list.appendChild(el("div", "files-empty", t("notes.empty")));
    return;
  }
  notes.forEach((n) => {
    const d = el("div", "note");
    d.appendChild(el("div", null, n.content || n.text || ""));
    d.appendChild(el("div", "nt-time", ago(n.updated_at || n.created_at)));
    const del = el("span", "nt-del");
    del.appendChild(iconEl("trash-2", 14));
    del.onclick = async () => {
      try {
        await api(`/notes/${n.note_id || n.id}`, { method: "DELETE" });
      } catch {
        /* ignore */
      }
      void loadNotes();
    };
    d.appendChild(del);
    list.appendChild(d);
  });
}

export async function loadNotes(): Promise<void> {
  const pid = effProject();
  const list = $("#notes-list");
  if (!list) return;
  if (!pid) {
    list.innerHTML = '<div class="files-empty">' + t("notes.emptyNoProject") + "</div>";
    return;
  }
  try {
    const d = (await api(`/projects/${pid}/notes`)) as { notes?: NoteRow[] } | NoteRow[] | null;
    const notes = (d && !Array.isArray(d) && d.notes) || (Array.isArray(d) ? d : []);
    renderNotes(notes);
  } catch {
    list.innerHTML = "";
  }
}

export async function addNote(): Promise<void> {
  const pid = effProject();
  const inp = $("#note-input") as HTMLTextAreaElement | null;
  if (!inp) return;
  const c = inp.value.trim();
  if (!pid || !c) return;
  try {
    await api(`/projects/${pid}/notes`, {
      method: "POST",
      body: JSON.stringify({ content: c }),
    });
    inp.value = "";
    await loadNotes();
  } catch {
    /* ignore */
  }
}

export function bindNotes(): void {
  const save = $("#note-save");
  if (save) save.addEventListener("click", () => void addNote());
  const filesBtn = $("#files-btn");
  if (filesBtn) {
    filesBtn.addEventListener("click", () => {
      void loadNotes();
      const block = $("#notes-block");
      if (block) block.classList.remove("hidden");
      const dockTab = hostFn("dockTab");
      const setActiveTab = hostFn("setActiveTab");
      if (isReady(dockTab)) dockTab("files");
      else if (isReady(setActiveTab)) setActiveTab("files");
    });
  }
}
