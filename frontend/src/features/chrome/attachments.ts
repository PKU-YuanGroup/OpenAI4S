/**
 * M-10: the files a composer attached, as chips inside the composer card.
 *
 * An upload lands in the session as soon as it is chosen; the next Send waits
 * for it (`waitForPendingUploads`). Nothing on screen used to say so: a faint
 * "Inputs · 1" strip and a passing "Uploaded" hint, with no name, no size and
 * no way to take a wrong file back. Each file chosen now shows here, under a
 * line saying it goes with the next message, until that message is sent.
 *
 * × on a chip takes the file back. A file the upload created is removed from
 * the session (`DELETE /artifacts/{id}`); a file that went in as a new version
 * of an existing one only leaves the tray, because deleting it would take the
 * older versions with it. A file still uploading is removed once its upload
 * settles.
 *
 * Entries belong to the composer they were chosen in: the open session, or
 * the project's "no session yet" composer until the first upload or Send
 * creates its session (`bindAttachments`).
 */

import { effect } from "@preact/signals";
import { copyLookup, type CopyTable } from "../../i18n/copy";
import { currentId, project } from "../../stores/session";
import { effProject } from "../customize/host";
import { api, apiErrorText } from "./api";
import { $, el, hint, iconEl } from "./dom";
import { hostFn, isReady } from "./host";

const COPY: CopyTable = {
  zh: {
    "attach.label": "随下一条消息发送",
    "attach.labelTitle": "这些文件已放进本会话，智能体可以读取。点 × 撤回。",
    "attach.uploading": "上传中…",
    "attach.failed": "未上传",
    "attach.remove": "移除 {0}",
    "attach.removed": "已从本会话移除：{0}",
    "attach.removedChipOnly": "{0} 是已有文件的新版本，仍留在本会话中；旧版本可在版本历史中恢复",
    "attach.removeFailed": "无法移除 {0}：{1}",
  },
  en: {
    "attach.label": "Goes with your next message",
    "attach.labelTitle": "These files are already in this session, where the agent can read them. Use × to take one back.",
    "attach.uploading": "Uploading…",
    "attach.failed": "Not uploaded",
    "attach.remove": "Remove {0}",
    "attach.removed": "Removed from this session: {0}",
    "attach.removedChipOnly": "{0} went in as a new version of an existing file and stays in this session; the older version can be restored from version history",
    "attach.removeFailed": "Could not remove {0}: {1}",
  },
};

export const attachT = copyLookup(COPY);

export type AttachmentState = "uploading" | "ready" | "failed";

export interface ComposerAttachment {
  readonly key: number;
  /** The name the file is stored under once known (a "keep both" answer renames it). */
  name: string;
  size: number;
  state: AttachmentState;
  /** The destination session; null while the first session is being created. */
  frameId: string | null;
  projectId: string | null;
  artifactId: string;
  /** The upload created this Artifact, so × may remove it from the session. */
  created: boolean;
  /** × was pressed while the upload was still running. */
  discarded: boolean;
}

let seq = 0;
const tray: ComposerAttachment[] = [];

/** Every entry, for tests and the send path. */
export function composerAttachments(): readonly ComposerAttachment[] {
  return tray;
}

function openProject(): string | null {
  return effProject() || project.value || null;
}

/** The entries the composer on screen shows. */
export function visibleAttachments(): ComposerAttachment[] {
  const fid = currentId.value || null;
  const pid = openProject();
  return tray.filter((a) => (fid ? a.frameId === fid : !a.frameId && a.projectId === pid));
}

/** A file was chosen in the composer: show it at once, as uploading. */
export function addAttachment(
  file: { name: string; size?: number },
  frameId: string | null,
  projectId: string | null,
): ComposerAttachment {
  const entry: ComposerAttachment = {
    key: ++seq,
    name: file.name,
    size: typeof file.size === "number" && file.size >= 0 ? file.size : 0,
    state: "uploading",
    frameId,
    projectId,
    artifactId: "",
    created: false,
    discarded: false,
  };
  tray.push(entry);
  renderAttachments();
  return entry;
}

/** The first session a "no session yet" upload created is now its destination. */
export function bindAttachment(entry: ComposerAttachment, frameId: string): void {
  if (entry.frameId === frameId) return;
  entry.frameId = frameId;
  renderAttachments();
}

function drop(entry: ComposerAttachment): void {
  const at = tray.indexOf(entry);
  if (at >= 0) tray.splice(at, 1);
}

/** How one upload ended. A skipped file (cancelled at the same-name prompt) leaves the tray. */
export function settleAttachment(
  entry: ComposerAttachment,
  outcome: {
    ok: boolean;
    skipped?: boolean;
    frameId?: string | null;
    filename?: string;
    artifactId?: string;
    created?: boolean;
  },
): void {
  if (outcome.frameId) entry.frameId = outcome.frameId;
  if (outcome.skipped) {
    drop(entry);
    renderAttachments();
    return;
  }
  if (!outcome.ok) {
    entry.state = "failed";
    if (entry.discarded) drop(entry);
    renderAttachments();
    return;
  }
  entry.state = "ready";
  if (outcome.filename) entry.name = outcome.filename;
  entry.artifactId = outcome.artifactId || "";
  entry.created = !!outcome.created;
  if (entry.discarded) {
    void removeFromSession(entry);
    return;
  }
  renderAttachments();
}

/**
 * The message these entries went with was sent: they leave the tray. Returns
 * them so a refused message can put them back with its text.
 */
export function takeAttachments(frameId: string | null): ComposerAttachment[] {
  const taken = tray.filter((a) => a.frameId === frameId && a.state !== "uploading");
  taken.forEach(drop);
  renderAttachments();
  return taken;
}

/** A refused message went back into the composer: its files go back with it. */
export function restoreAttachments(list: readonly ComposerAttachment[]): void {
  for (const entry of list) if (!tray.includes(entry)) tray.push(entry);
  renderAttachments();
}

async function removeFromSession(entry: ComposerAttachment): Promise<void> {
  if (!entry.created || !entry.artifactId) {
    drop(entry);
    renderAttachments();
    if (entry.state === "ready") hint(attachT("attach.removedChipOnly", entry.name));
    return;
  }
  try {
    await api(`/artifacts/${encodeURIComponent(entry.artifactId)}`, { method: "DELETE" });
  } catch (error) {
    entry.discarded = false;
    renderAttachments();
    hint(attachT("attach.removeFailed", entry.name, apiErrorText(error)), true);
    return;
  }
  drop(entry);
  renderAttachments();
  if (entry.frameId && currentId.value === entry.frameId) {
    const loadArtifacts = hostFn("loadArtifacts");
    if (isReady(loadArtifacts)) loadArtifacts(entry.frameId);
  }
  hint(attachT("attach.removed", entry.name));
}

/** ×: take the file back. */
export function removeAttachment(entry: ComposerAttachment): Promise<void> {
  if (entry.state === "failed") {
    drop(entry);
    renderAttachments();
    return Promise.resolve();
  }
  entry.discarded = true;
  if (entry.state === "uploading") {
    // Removed from view now; the bytes are taken back once they land.
    renderAttachments();
    return Promise.resolve();
  }
  renderAttachments();
  return removeFromSession(entry);
}

/** `12.3 KB`: a chip's size. */
export function attachmentSize(n: number): string {
  if (n >= 1073741824) return (n / 1073741824).toFixed(1) + " GB";
  if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
  if (n >= 1024) return (n / 1024).toFixed(1) + " KB";
  return n + " B";
}

/** Paint `#composer-attachments` for the composer on screen. */
export function renderAttachments(): void {
  const host = $("#composer-attachments");
  if (!host) return;
  host.innerHTML = "";
  const shown = visibleAttachments().filter((a) => !a.discarded);
  if (!shown.length) {
    host.classList.add("hidden");
    return;
  }
  host.classList.remove("hidden");
  const label = el("span", "composer-attach-label");
  label.appendChild(iconEl("cloud-upload", 12));
  label.appendChild(el("span", null, attachT("attach.label")));
  label.title = attachT("attach.labelTitle");
  host.appendChild(label);
  for (const entry of shown) {
    const chip = el("span", "msg-ref-chip attach " + entry.state);
    chip.dataset.attachKey = String(entry.key);
    chip.appendChild(iconEl(entry.state === "failed" ? "alert-triangle" : "file-text", 11));
    chip.appendChild(el("span", "attach-name", entry.name));
    const meta =
      entry.state === "uploading"
        ? attachT("attach.uploading")
        : entry.state === "failed"
          ? attachT("attach.failed")
          : attachmentSize(entry.size);
    chip.appendChild(el("span", "attach-meta", meta));
    chip.title = entry.name + " · " + meta;
    const x = el("button", "attach-x") as HTMLButtonElement;
    x.type = "button";
    x.appendChild(iconEl("x", 11));
    x.setAttribute("aria-label", attachT("attach.remove", entry.name));
    x.title = attachT("attach.remove", entry.name);
    x.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      x.disabled = true;
      void removeAttachment(entry);
    };
    chip.appendChild(x);
    host.appendChild(chip);
  }
}

/** Repaint whenever the composer on screen changes owner. */
export function bootAttachments(): void {
  effect(() => renderAttachments());
}

/** Tests: forget every entry. */
export function _resetAttachments(): void {
  tray.length = 0;
}
