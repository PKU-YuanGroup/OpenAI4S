/** Restored messages, empty-session chips, and @-ref chips. app.js:7220-7409, 7766-7787. */

import { renderMd } from "../md/render";
import { publicText } from "../scrub/scrub";
import { t } from "../../i18n";
import { artifacts } from "../../stores/artifacts";
import { currentId, feedback as feedbackSignal } from "../../stores/session";
import { api } from "./api";
import { hint } from "./chrome";
import { $, el, grow } from "./dom";
import { iconEl } from "./icon";
import { callLane } from "./lane";
import type { ChatMessage } from "./messages";

export function starters(): Array<{ t: string; p: string }> {
  return [
    { t: t("starter.litReview.title"), p: t("starter.litReview.prompt") },
    { t: t("starter.dataAnalysis.title"), p: t("starter.dataAnalysis.prompt") },
    { t: t("starter.proteinModel.title"), p: t("starter.proteinModel.prompt") },
    { t: t("starter.phylo.title"), p: t("starter.phylo.prompt") },
  ];
}

export function renderEmptySession(): void {
  const m = $("#messages");
  if (!m) return;
  const wrap = el("div", "empty-session");
  wrap.appendChild(el("div", "es-title", t("empty.title")));
  wrap.appendChild(el("div", "es-sub", t("empty.sub")));
  const chips = el("div", "es-chips");
  starters().forEach((s) => {
    const chip = el("button", "es-chip");
    chip.type = "button";
    chip.appendChild(el("div", "es-chip-t", s.t));
    chip.appendChild(el("div", "es-chip-p", s.p));
    chip.onclick = () => {
      const c = $("#composer") as HTMLTextAreaElement | null;
      if (!c) return;
      c.value = s.p;
      grow();
      c.focus();
    };
    chips.appendChild(chip);
  });
  wrap.appendChild(chips);
  m.appendChild(wrap);
}

export function renderStored(m: ChatMessage, target?: ParentNode | null): HTMLElement | null {
  const text = Array.isArray(m.content)
    ? (m.content as Array<{ text?: string }>).map((b) => (b && b.text) || "").join("")
    : String((m.content as string) || "");
  if (!text.trim()) return null;
  const w = el("div", "msg " + (m.role === "user" ? "user" : "assistant"));
  callLane("rememberCandidateIdentity", w, m);
  (w as HTMLElement & { _messageText?: string })._messageText = text;
  if (m.role === "user") {
    const b = el("div", "bubble");
    b.textContent = text;
    w.appendChild(b);
    renderMessageRefChips(w, m.artifact_refs);
  } else {
    const md = el("div", "md");
    md.innerHTML = renderMd(text);
    w.appendChild(md);
    if (m.failure && m.failure.request_id) {
      const meta = callLane("failureMeta", m.failure);
      if (meta instanceof Node) w.appendChild(meta);
    }
    const review = m.review_status || (m.metadata && m.metadata.review_status);
    const rec = review && typeof review === "object" ? (review as Record<string, unknown>) : null;
    const reviewStatus = rec ? rec.status || review : review;
    if (reviewStatus) {
      callLane("setMessageReviewBadge", w, reviewStatus, rec && rec.user_truth);
      if (reviewStatus !== "candidate" && w.dataset) w.dataset.candidateResolved = "true";
    }
  }
  w.dataset.ts = String(new Date(m.created_at || "").getTime() || 0);
  (target || $("#messages"))?.appendChild(w);
  if (m.role !== "user") addMsgActions(w, text);
  return w;
}

export function insertMessageByTime(node: HTMLElement | null): void {
  const host = $("#messages");
  if (!host || !node) return;
  const ts = Number(node.dataset.ts || 0);
  const kids = host.children;
  for (let i = 0; i < kids.length; i++) {
    const kid = kids[i] as HTMLElement;
    if (kid.id === "msgs-earlier") continue;
    const kidTs = Number(kid.dataset && kid.dataset.ts);
    if (Number.isFinite(kidTs) && kidTs > ts) {
      host.insertBefore(node, kid);
      return;
    }
  }
  host.appendChild(node);
}

export function renderMessageRefChips(host: HTMLElement, refs: unknown): void {
  if (!Array.isArray(refs) || !refs.length) return;
  const row = el("div", "msg-refs");
  refs.slice(0, 8).forEach((raw) => {
    const r = raw as Record<string, unknown>;
    const name = String((r && r.display_name) || "");
    if (!name) return;
    const chip = el("span", "msg-ref-chip");
    chip.appendChild(iconEl("file-text", 11));
    chip.appendChild(el("span", null, publicText(name, 60)));
    const parts = [String(r.version_id || "")];
    if (r.sha256) parts.push("sha256:" + String(r.sha256).slice(0, 12));
    if (r.materialized_target) parts.push("↗ " + String(r.source_session || "").slice(0, 12));
    chip.title = parts.filter(Boolean).join(" · ");
    const pool = (artifacts.value || []) as Array<Record<string, unknown>>;
    const full = pool.find((x) => (x.artifact_id || x.id) === r.artifact_id);
    if (full) {
      chip.classList.add("clickable");
      chip.onclick = () => {
        callLane("openViewer", full);
      };
    }
    row.appendChild(chip);
  });
  if (row.children.length) host.appendChild(row);
}

export function renderComposerRefChips(): void {
  const host = $("#composer-refs");
  if (!host) return;
  host.innerHTML = "";
  const composer = $("#composer") as HTMLTextAreaElement | null;
  const text = (composer && composer.value) || "";
  const found: Array<{ name: string; version: string }> = [];
  const seen = new Set<string>();
  const re = /(?:^|\s)@([^\s@#]+)(?:#(v-[A-Za-z0-9_-]+))?/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const key = (m[1] || "") + "#" + (m[2] || "");
    if (seen.has(key)) continue;
    seen.add(key);
    found.push({ name: m[1] || "", version: m[2] || "" });
    if (found.length >= 8) break;
  }
  if (!found.length) {
    host.classList.add("hidden");
    return;
  }
  const pool = [...((artifacts.value || []) as Array<Record<string, unknown>>)];
  found.forEach((ref) => {
    const match = pool.find(
      (a) =>
        a &&
        a.filename === ref.name &&
        (!ref.version || String(a.version_id || "") === ref.version),
    );
    const chip = el("span", "msg-ref-chip" + (match ? "" : " unresolved"));
    chip.appendChild(iconEl(match ? "file-text" : "alert-triangle", 11));
    chip.appendChild(el("span", null, publicText(ref.name, 60)));
    if (!match) {
      chip.title = t("refs.unresolvedChip");
      host.appendChild(chip);
      return;
    }
    const parts = [String(ref.version || match.version_id || "")];
    if (match.checksum) parts.push("sha256:" + String(match.checksum).slice(0, 12));
    const elsewhere =
      match.root_frame_id && currentId.value && match.root_frame_id !== currentId.value;
    if (elsewhere) parts.push("\u2197 " + String(match.root_frame_id).slice(0, 12));
    chip.title = parts.filter(Boolean).join(" \u00b7 ");
    if (elsewhere) chip.classList.add("elsewhere");
    chip.classList.add("clickable");
    chip.onclick = () => {
      callLane("openViewer", match);
    };
    host.appendChild(chip);
  });
  host.classList.remove("hidden");
}

function fbKey(text: string): string {
  let h = 0;
  const s = (text || "").slice(0, 400);
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return "m" + (h >>> 0).toString(36);
}

function feedbackBag(): Record<string, unknown> {
  const cur = feedbackSignal.value;
  if (cur && typeof cur === "object") return cur as Record<string, unknown>;
  const next = Object.create(null) as Record<string, unknown>;
  feedbackSignal.value = next;
  return next;
}

function sendFeedback(key: string, rating: string | null): void {
  if (!currentId.value) return;
  const bag = feedbackBag();
  if (rating) bag[key] = rating;
  else delete bag[key];
  api("/frames/" + currentId.value + "/feedback", {
    method: "POST",
    body: JSON.stringify({ key, rating }),
  }).catch(() => {});
  hint(
    rating === "up"
      ? t("toast.feedbackUp")
      : rating === "down"
        ? t("toast.feedbackDown")
        : t("toast.feedbackCancelled"),
  );
}

export function addMsgActions(wrap: HTMLElement, text: string): void {
  if (!wrap || wrap.querySelector(".msg-actions")) return;
  const row = el("div", "msg-actions");
  const copy = el("button");
  copy.type = "button";
  copy.title = t("msgAction.copy");
  copy.appendChild(iconEl("copy", 16));
  copy.onclick = () => {
    try {
      if (navigator.clipboard) navigator.clipboard.writeText(text || "");
    } catch {
      /* ignore */
    }
    copy.innerHTML = "";
    copy.appendChild(iconEl("check", 16));
    setTimeout(() => {
      copy.innerHTML = "";
      copy.appendChild(iconEl("copy", 16));
    }, 1200);
  };
  const key = fbKey(text);
  const cur = feedbackBag()[key] || null;
  const tup = el("button", cur === "up" ? "on" : null);
  tup.type = "button";
  tup.title = t("msgAction.thumbsUp");
  tup.appendChild(iconEl("thumbs-up", 16));
  const tdn = el("button", cur === "down" ? "on" : null);
  tdn.type = "button";
  tdn.title = t("msgAction.thumbsDown");
  tdn.appendChild(iconEl("thumbs-down", 16));
  tup.onclick = () => {
    const on = !tup.classList.contains("on");
    tup.classList.toggle("on", on);
    tdn.classList.remove("on");
    sendFeedback(key, on ? "up" : null);
  };
  tdn.onclick = () => {
    const on = !tdn.classList.contains("on");
    tdn.classList.toggle("on", on);
    tup.classList.remove("on");
    sendFeedback(key, on ? "down" : null);
  };
  const edit = el("button");
  edit.type = "button";
  edit.title = t("common.edit");
  edit.appendChild(iconEl("pencil", 16));
  edit.onclick = () => {
    const c = $("#composer") as HTMLTextAreaElement | null;
    if (!c) return;
    c.value = text || "";
    grow();
    c.focus();
  };
  row.appendChild(copy);
  row.appendChild(tup);
  row.appendChild(tdn);
  row.appendChild(edit);
  wrap.appendChild(row);
}
