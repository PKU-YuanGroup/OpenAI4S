/**
 * M-10: the files a user message was sent with, as chips under its bubble.
 *
 * The composer shows each upload as a chip "going with your next message"
 * (`chrome/attachments.ts`). Once the message is sent the same names stay
 * under its bubble: in the sending tab at once, in another tab from the
 * `user_message` event, and after a reload from the stored row's
 * `attachments` (`{artifact_id, name}` each).
 */

import { isReady } from "../../compat/stub";
import { copyLookup, type CopyTable } from "../../i18n/copy";
import { artifacts } from "../../stores/artifacts";
import { publicText } from "../scrub/scrub";
import { hint } from "../sessions/chrome";
import { iconEl } from "../sessions/icon";
import { el } from "./dom";

const COPY: CopyTable = {
  zh: {
    "sent.chip": "随这条消息发送：{0}",
    "sent.gone": "{0} 已不在本会话中",
  },
  en: {
    "sent.chip": "Sent with this message: {0}",
    "sent.gone": "{0} is no longer in this session",
  },
};

const sentT = copyLookup(COPY);

export type SentAttachment = { artifact_id: string; name: string };

/** The usable `{artifact_id, name}` entries of a message's `attachments`. */
export function sentAttachments(raw: unknown): SentAttachment[] {
  if (!Array.isArray(raw)) return [];
  const out: SentAttachment[] = [];
  for (const item of raw) {
    const r = (item || {}) as Record<string, unknown>;
    const id = typeof r.artifact_id === "string" ? r.artifact_id : "";
    const name = typeof r.name === "string" ? r.name : "";
    if (id && name) out.push({ artifact_id: id, name });
  }
  return out;
}

function openViewer(artifact: unknown): void {
  const fn = (globalThis as Record<string, unknown>).openViewer;
  if (isReady(fn)) (fn as (a: unknown) => void)(artifact);
}

/**
 * Chips for the files under a user row, in the row's `.msg-refs` line (the
 * one its `@` references use). A click opens the file while it is still in
 * the session's file list, and says so when it is not.
 */
export function renderSentAttachments(host: HTMLElement, raw: unknown): void {
  const files = sentAttachments(raw);
  if (!files.length) return;
  let row = host.querySelector(":scope > .msg-refs") as HTMLElement | null;
  if (!row) {
    row = el("div", "msg-refs");
    // Where a reload puts it: before the pins box and the queued badge.
    host.insertBefore(row, host.querySelector(":scope > .annot-attach, :scope > .msg-queued"));
  }
  for (const file of files) {
    const chip = el("span", "msg-ref-chip sent clickable");
    chip.dataset.artifactId = file.artifact_id;
    chip.appendChild(iconEl("cloud-upload", 11));
    chip.appendChild(el("span", null, publicText(file.name, 60)));
    chip.title = sentT("sent.chip", file.name);
    // Looked up on click: a reopened session draws its messages before its
    // file list has loaded.
    chip.onclick = () => {
      const pool = (artifacts.value || []) as Array<Record<string, unknown>>;
      const full = pool.find((x) => (x.artifact_id || x.id) === file.artifact_id);
      if (full) openViewer(full);
      else hint(sentT("sent.gone", file.name));
    };
    row.appendChild(chip);
  }
}
