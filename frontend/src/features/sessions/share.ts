/** Share dialog: create, copy, update or revoke a session's read-only link. app.js:7560-7697. */

import { t } from "../../i18n";
import { copyFailedText, copyText } from "../chrome/clipboard";
import { API, ApiError, apiErrorText } from "./api";
import { hint } from "./chrome";
import { shareCopy } from "./copy";
import { icon } from "./icon";
import type { SessionLike } from "./paging";
import { copyLookup } from "../../i18n/copy";

/** Tunnel state and snapshot lines (TEAM-16, TEAM-17). */
const shareStateCopy = copyLookup({
  en: {
    tunnelDown:
      "The relay is not connected, so this link does not open yet. It starts working once OpenAI4S reaches the relay.",
    lastError: "Last error: {0}",
    snapshot: "Snapshot {0} · {1}",
    counts: "{0} messages · {1} cells · {2} artifacts",
    excluded: "{0} file(s) left out",
    counting: "Counting what this link would publish…",
    willPublish: "This link would publish {0}.",
  },
  zh: {
    tunnelDown: "中转服务器（relay）未连接，这条链接暂时打不开；OpenAI4S 连上中转服务器后自动生效。",
    lastError: "最近的错误：{0}",
    snapshot: "快照 {0} · {1}",
    counts: "{0} 条消息 · {1} 个单元 · {2} 个产物",
    excluded: "排除了 {0} 个文件",
    counting: "正在统计这条链接会公开的内容…",
    willPublish: "这条链接将公开：{0}。",
  },
});

/** `expires_at` is epoch milliseconds; a numeric string must not be parsed as a date string. */
export function shareExpiry(value: unknown): Date | null {
  if (value == null || value === "") return null;
  const numeric = typeof value === "number" || /^\d+$/.test(String(value));
  const date = numeric ? new Date(Number(value)) : new Date(String(value));
  return Number.isNaN(date.getTime()) ? null : date;
}

export function tunnelDownText(lastError: unknown): string {
  const detail = typeof lastError === "string" ? lastError.trim().slice(0, 200) : "";
  return shareStateCopy("tunnelDown") + (detail ? " " + shareStateCopy("lastError", detail) : "");
}

/** "N messages · N cells · N artifacts · N file(s) left out", or "" without counts. */
export function countsText(counts: Record<string, unknown>): string {
  if (!["messages", "cells", "artifacts"].some((key) => typeof counts[key] === "number")) return "";
  let text = shareStateCopy(
    "counts",
    Number(counts.messages || 0),
    Number(counts.cells || 0),
    Number(counts.artifacts || 0),
  );
  const excluded = counts.excluded;
  const left =
    excluded && typeof excluded === "object"
      ? Object.values(excluded as Record<string, unknown>).reduce<number>((sum, n) => sum + (Number(n) || 0), 0)
      : 0;
  if (left) text += " · " + shareStateCopy("excluded", left);
  return text;
}

/** "Snapshot <time> · N messages · N cells · N artifacts · N file(s) left out". */
export function snapshotText(share: Record<string, unknown>): string {
  const counts = countsText((share.counts || {}) as Record<string, unknown>);
  if (!counts) return "";
  const at = shareExpiry(share.updated_at);
  return shareStateCopy("snapshot", at ? at.toLocaleString() : "", counts);
}

export async function openShareDialog(fid: string, frame: SessionLike = {}): Promise<void> {
  let status: Record<string, unknown>;
  let shares: { shares?: Array<Record<string, unknown>> };
  try {
    const pair = await Promise.all([
      fetch(`${API}/share/status`).then((r) => r.json()),
      fetch(`${API}/frames/${encodeURIComponent(fid)}/shares`).then((r) => r.json()),
    ]);
    status = pair[0] as Record<string, unknown>;
    shares = pair[1] as { shares?: Array<Record<string, unknown>> };
  } catch (error) {
    hint(t("nb.action.failed", apiErrorText(error)), true);
    return;
  }

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay share-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-labelledby", "share-title");
  const box = document.createElement("div");
  box.className = "share-box";
  overlay.appendChild(box);
  let poll: ReturnType<typeof setInterval> | undefined;
  const close = () => {
    if (poll !== undefined) clearInterval(poll);
    overlay.remove();
  };
  overlay.onclick = (e) => {
    if (e.target === overlay) close();
  };
  overlay.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  });
  const head = document.createElement("div");
  head.className = "share-head";
  const h = document.createElement("h2");
  h.id = "share-title";
  h.className = "share-title";
  h.textContent = t("share.title");
  head.appendChild(h);
  box.appendChild(head);
  // Appended last (finish) so the button focused on open stays the dialog's
  // own action; CSS pins it to the top-right corner.
  const closeX = document.createElement("button");
  closeX.type = "button";
  closeX.className = "share-close";
  closeX.title = t("share.close");
  closeX.setAttribute("aria-label", t("share.close"));
  closeX.innerHTML = icon("x", 15);
  closeX.onclick = close;

  const state = String(status.state || "");
  // No relay configured — the default install. "Sharing is off." with only a
  // Close button was a dead end (and the docs pointed at a settings tab that
  // does not exist), so both states say how to get from here to a link.
  if (state === "unconfigured" || (state === "disabled" && !status.configured)) {
    box.appendChild(para(shareCopy("setupIntro")));
    const steps = document.createElement("ol");
    steps.className = "share-setup";
    for (const key of ["setupEnv", "setupRestart", "setupEnable"] as const) {
      const li = document.createElement("li");
      li.textContent = shareCopy(key);
      steps.appendChild(li);
    }
    box.appendChild(steps);
    box.appendChild(para(shareCopy("setupDocs")));
    const closeBtn = mkBtn(t("share.close"), close);
    box.appendChild(actions(closeBtn));
    finish(closeBtn);
    return;
  }
  if (state === "disabled") {
    box.appendChild(para(t("share.disabled")));
    const closeBtn = mkBtn(t("share.close"), close);
    let enableBtn: HTMLButtonElement | null = null;
    if (status.configured) {
      enableBtn = mkBtn(
        t("share.enable"),
        async () => {
          await shareCall("PUT", `${API}/share/settings`, { enabled: true });
          close();
          void openShareDialog(fid, frame);
        },
        false,
        true,
      );
    }
    box.appendChild(enableBtn ? actions(closeBtn, enableBtn) : actions(closeBtn));
    finish(enableBtn || closeBtn);
    return;
  }

  // TEAM-16: a link the relay cannot serve looked exactly like a working one.
  // The tunnel's state is shown above the link, and re-read while the dialog
  // stays open, so a relay that comes back (or drops) is reflected.
  const tunnelNote = document.createElement("p");
  tunnelNote.className = "share-tunnel";
  tunnelNote.setAttribute("role", "status");
  let statusLine: HTMLDivElement | null = null;
  const paintTunnel = (current: Record<string, unknown>) => {
    const down = String(current.state || "") !== "connected";
    tunnelNote.hidden = !down;
    tunnelNote.textContent = down ? tunnelDownText(current.last_error) : "";
    if (statusLine) statusLine.className = down ? "share-status offline" : "share-status";
  };
  paintTunnel(status);
  poll = setInterval(() => {
    // Removed some other way than close(): stop reading.
    if (!overlay.isConnected) return clearInterval(poll);
    fetch(`${API}/share/status`)
      .then((r) => r.json())
      .then((next) => paintTunnel(next as Record<string, unknown>))
      .catch(() => undefined);
  }, 4000);

  const active = (shares.shares || []).find((s) => s.status === "ready" || s.status === "publishing");
  if (active) {
    statusLine = document.createElement("div");
    statusLine.className = "share-status";
    const expires = shareExpiry(active.expires_at);
    statusLine.textContent = expires
      ? t("share.expiresAt") + " " + expires.toLocaleString()
      : t("share.neverExpires");
    head.appendChild(statusLine);
    paintTunnel(status);
    box.appendChild(tunnelNote);
    const label = document.createElement("label");
    label.className = "share-label";
    label.htmlFor = "share-url";
    label.textContent = shareCopy("linkLabel");
    box.appendChild(label);
    const row = document.createElement("div");
    row.className = "share-url-row";
    const inp = document.createElement("input");
    inp.id = "share-url";
    inp.className = "share-url";
    inp.readOnly = true;
    inp.value = String(active.url || "");
    row.appendChild(inp);
    const copyBtn = mkBtn(
      t("share.copy"),
      () => {
        void copyText(String(active.url || "")).then((ok) => {
          if (ok) {
            hint(t("share.copied"));
            return;
          }
          // Nothing reached the clipboard: leave the link selected for a manual copy.
          inp.focus();
          inp.select();
          hint(copyFailedText(), true);
        });
      },
      false,
      true,
    );
    row.appendChild(copyBtn);
    box.appendChild(row);
    // TEAM-17: what this snapshot publishes, from the counts the server froze.
    const published = snapshotText(active);
    if (published) {
      const snap = para(published);
      snap.classList.add("share-snapshot");
      box.appendChild(snap);
    }
    box.appendChild(scopeNote());
    const rows = document.createElement("div");
    rows.className = "share-rows";
    rows.appendChild(
      shareRow(
        t("share.update"),
        shareCopy("updateDesc"),
        mkBtn(t("share.update"), async () => {
          const updated = await shareCall("PUT", `${API}/shares/${encodeURIComponent(String(active.share_id))}`);
          if (!updated) return;
          hint(t("share.updated"));
          // Stays open on the fresh snapshot, so its counts and the link are
          // right there to check and copy again.
          close();
          void openShareDialog(fid, frame);
        }),
      ),
    );
    const revokeRow = shareRow(
      t("share.revoke"),
      shareCopy("revokeDesc"),
      mkBtn(t("share.revoke"), () => confirmRevoke(), true),
    );
    rows.appendChild(revokeRow);
    box.appendChild(rows);
    finish(copyBtn);

    // In-dialog confirmation instead of the browser's native confirm().
    function confirmRevoke(): void {
      const ask = document.createElement("div");
      ask.className = "share-row share-confirm";
      ask.setAttribute("role", "alertdialog");
      const text = document.createElement("span");
      text.className = "share-row-desc";
      text.textContent = t("share.revokeConfirm");
      const cancel = mkBtn(t("share.close"), () => {
        ask.replaceWith(revokeRow);
      });
      const yes = mkBtn(
        t("share.revoke"),
        async () => {
          const done = await shareCall("DELETE", `${API}/shares/${encodeURIComponent(String(active!.share_id))}`);
          if (done === null) return;
          hint(t("share.revoked"));
          close();
        },
        true,
      );
      ask.appendChild(text);
      ask.appendChild(actions(cancel, yes));
      revokeRow.replaceWith(ask);
      cancel.focus();
    }
  } else {
    box.appendChild(tunnelNote);
    box.appendChild(scopeNote());
    // TEAM-17: what a link would publish, before it exists. The replay view
    // is the same projection a share publishes, built without publishing.
    const preview = para(shareStateCopy("counting"));
    preview.classList.add("share-snapshot");
    box.appendChild(preview);
    fetch(`${API}/sessions/${encodeURIComponent(fid)}/replay`)
      .then((r) => (r.ok ? r.json() : null))
      .then((view) => {
        const text = view ? countsText({ ...(view.counts || {}), excluded: view.excluded }) : "";
        if (text) preview.textContent = shareStateCopy("willPublish", text);
        else preview.remove();
      })
      .catch(() => preview.remove());
    const expRow = document.createElement("div");
    expRow.className = "share-expiry";
    const expLabel = document.createElement("span");
    expLabel.id = "share-expiry-label";
    expLabel.className = "share-label";
    expLabel.textContent = t("share.expiry").replace(/[:\uff1a]\s*$/, "");
    const seg = document.createElement("div");
    seg.className = "seg share-seg";
    seg.setAttribute("role", "radiogroup");
    seg.setAttribute("aria-labelledby", "share-expiry-label");
    let expiresIn = 604800;
    const radios: HTMLButtonElement[] = [];
    const paint = () =>
      radios.forEach((b) => {
        const on = Number(b.dataset.secs) === expiresIn;
        b.classList.toggle("active", on);
        b.setAttribute("aria-checked", on ? "true" : "false");
        b.tabIndex = on ? 0 : -1;
      });
    (
      [
        [0, t("share.expiry.never")],
        [86400, t("share.expiry.1d")],
        [604800, t("share.expiry.7d")],
        [2592000, t("share.expiry.30d")],
      ] as Array<[number, string]>
    ).forEach(([secs, label]) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "seg-btn";
      b.setAttribute("role", "radio");
      b.dataset.secs = String(secs);
      b.textContent = label;
      b.onclick = () => {
        expiresIn = secs;
        paint();
      };
      radios.push(b);
      seg.appendChild(b);
    });
    // Arrow keys move the choice, as in any radio group.
    seg.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const at = radios.findIndex((b) => Number(b.dataset.secs) === expiresIn);
      const next = radios[(at + (e.key === "ArrowRight" ? 1 : radios.length - 1)) % radios.length];
      if (!next) return;
      expiresIn = Number(next.dataset.secs);
      paint();
      next.focus();
    });
    paint();
    expRow.appendChild(expLabel);
    expRow.appendChild(seg);
    box.appendChild(expRow);
    const createBtn = mkBtn(
      t("share.create"),
      async () => {
        const body: { expires_in?: number } = {};
        if (expiresIn > 0) body.expires_in = expiresIn;
        const rec = await shareCall("POST", `${API}/frames/${encodeURIComponent(fid)}/shares`, body);
        close();
        if (rec && (rec as { url?: string }).url) void openShareDialog(fid, frame);
      },
      false,
      true,
    );
    box.appendChild(actions(mkBtn(t("share.close"), close), createBtn));
    finish(createBtn);
  }

  function finish(focus: HTMLElement | null): void {
    box.appendChild(closeX);
    document.body.appendChild(overlay);
    const target = focus || box.querySelector("button");
    if (target instanceof HTMLElement) target.focus();
  }
  function para(text: string): HTMLParagraphElement {
    const p = document.createElement("p");
    p.className = "share-text";
    p.textContent = text;
    return p;
  }
  function scopeNote(): HTMLParagraphElement {
    const p = document.createElement("p");
    p.className = "share-scope";
    p.textContent = t("share.scope");
    return p;
  }
  function actions(...buttons: HTMLButtonElement[]): HTMLDivElement {
    const row = document.createElement("div");
    row.className = "share-actions";
    buttons.forEach((b) => row.appendChild(b));
    return row;
  }
  function shareRow(title: string, desc: string, button: HTMLButtonElement): HTMLDivElement {
    const row = document.createElement("div");
    row.className = "share-row";
    const text = document.createElement("div");
    text.className = "share-row-text";
    const name = document.createElement("span");
    name.className = "share-row-title";
    name.textContent = title;
    const sub = document.createElement("span");
    sub.className = "share-row-desc";
    sub.textContent = desc;
    text.appendChild(name);
    text.appendChild(sub);
    row.appendChild(text);
    row.appendChild(button);
    return row;
  }
  function mkBtn(label: string, onClick: () => void, danger?: boolean, primary?: boolean): HTMLButtonElement {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.className = primary ? "solid-btn" : danger ? "outline-btn danger" : "outline-btn";
    b.onclick = onClick;
    return b;
  }
  async function shareCall(method: string, path: string, body?: unknown): Promise<unknown> {
    try {
      const r = await fetch(path, {
        method,
        headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new ApiError(j, r.status);
      return j;
    } catch (error) {
      hint(t("nb.action.failed", apiErrorText(error)), true);
      return null;
    }
  }
}
