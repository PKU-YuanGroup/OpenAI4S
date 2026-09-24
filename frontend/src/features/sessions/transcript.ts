/** Restored messages, empty-session chips, and @-ref chips. app.js:7220-7409, 7766-7787. */

import { renderMd } from "../md/render";
import { failureMeta } from "../messages/failure";
import { rememberCandidateIdentity, setMessageReviewBadge } from "../messages/identity";
import { addMsgActions } from "../messages/list";
import { planModeRequestText, planSeed, planSeedMarker } from "../messages/planPrompt";
import { cancelledIdentity, stoppedMarker } from "../messages/stopped";
import { publicText } from "../scrub/scrub";
import { t } from "../../i18n";
import { artifacts } from "../../stores/artifacts";
import { currentId } from "../../stores/session";
import { $, el, grow } from "./dom";
import { iconEl } from "./icon";
import { callLane } from "./lane";
import type { ChatMessage } from "./messages";

// One action row (copy, 👍/👎, edit) for the first page, this older-page
// renderer and the live turn; it lives with the first-page renderer.
export { addMsgActions };

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
  const stopped = m.role !== "user" ? cancelledIdentity(m.cancelled) : null;
  if (stopped) {
    // Same marker as messages/list.ts and the live stream.
    const marker = el("div", "msg assistant turn-stopped");
    marker.dataset.turnStatus = "cancelled";
    marker.appendChild(stoppedMarker(stopped));
    marker.dataset.ts = String(new Date(m.created_at || "").getTime() || 0);
    (target || $("#messages"))?.appendChild(marker);
    return marker;
  }
  const seed = m.role === "user" ? planSeed(text) : null;
  if (seed) {
    // Same plan marker as messages/list.ts.
    const marker = el("div", "msg plan-seed");
    marker.appendChild(planSeedMarker(seed));
    marker.dataset.ts = String(new Date(m.created_at || "").getTime() || 0);
    (target || $("#messages"))?.appendChild(marker);
    return marker;
  }
  const w = el("div", "msg " + (m.role === "user" ? "user" : "assistant"));
  // Imported, like `failureMeta` below: no lane ever assigned the window
  // names `rememberCandidateIdentity` / `setMessageReviewBadge`, so an older
  // page's rows had neither a candidate identity nor a review badge.
  rememberCandidateIdentity(w, m);
  (w as HTMLElement & { _messageText?: string })._messageText = text;
  if (m.role === "user") {
    const b = el("div", "bubble");
    b.textContent = planModeRequestText(text);
    w.appendChild(b);
    renderMessageRefChips(w, m.artifact_refs);
  } else {
    const md = el("div", "md");
    md.innerHTML = renderMd(text);
    w.appendChild(md);
    // Imported, not `callLane("failureMeta", ...)`: no lane ever assigned that
    // window name, so the older-page renderer silently dropped the whole
    // failure row -- the support id, and now the Continue button too. This is
    // the same call `messages/list.ts` makes for a first-page row.
    if (m.failure && m.failure.request_id) w.appendChild(failureMeta(m.failure));
    const review = m.review_status || (m.metadata && m.metadata.review_status);
    const rec = review && typeof review === "object" ? (review as Record<string, unknown>) : null;
    const reviewStatus = rec ? rec.status || review : review;
    if (reviewStatus) {
      setMessageReviewBadge(w, String(reviewStatus), rec && rec.user_truth);
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
