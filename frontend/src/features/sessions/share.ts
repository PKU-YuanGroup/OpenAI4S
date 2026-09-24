/** Share dialog: create, copy, update or revoke a session's read-only link. app.js:7560-7697. */

import { t } from "../../i18n";
import { copyFailedText, copyText } from "../chrome/clipboard";
import { API, ApiError, apiErrorText } from "./api";
import { hint } from "./chrome";
import { shareCopy } from "./copy";
import { icon } from "./icon";
import type { SessionLike } from "./paging";

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
  const close = () => overlay.remove();
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
  if (state === "unconfigured") {
    box.appendChild(para(t("share.unconfigured")));
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

  const active = (shares.shares || []).find((s) => s.status === "ready" || s.status === "publishing");
  if (active) {
    const statusLine = document.createElement("div");
    statusLine.className = "share-status";
    statusLine.textContent = active.expires_at
      ? t("share.expiresAt") + " " + new Date(String(active.expires_at)).toLocaleString()
      : t("share.neverExpires");
    head.appendChild(statusLine);
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
    box.appendChild(scopeNote());
    const rows = document.createElement("div");
    rows.className = "share-rows";
    rows.appendChild(
      shareRow(
        t("share.update"),
        shareCopy("updateDesc"),
        mkBtn(t("share.update"), async () => {
          await shareCall("PUT", `${API}/shares/${encodeURIComponent(String(active.share_id))}`);
          hint(t("share.updated"));
          close();
        }),
      ),
    );
    rows.appendChild(
      shareRow(
        t("share.revoke"),
        shareCopy("revokeDesc"),
        mkBtn(
          t("share.revoke"),
          async () => {
            if (!confirm(t("share.revokeConfirm"))) return;
            await shareCall("DELETE", `${API}/shares/${encodeURIComponent(String(active.share_id))}`);
            hint(t("share.revoked"));
            close();
          },
          true,
        ),
      ),
    );
    box.appendChild(rows);
    finish(copyBtn);
  } else {
    box.appendChild(scopeNote());
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
