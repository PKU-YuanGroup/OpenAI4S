/**
 * Session-package import dialog and export feedback (TEAM-20, TEAM-13).
 *
 * Import ran from the home page and reported through `hint()`, which writes
 * into the workbench composer -- hidden on the home page -- so a refused
 * package looked like nothing happened. It now runs in a dialog of its own:
 * file, checking, the check's result, importing, then the session or the
 * reason it was refused. Exports fetch their bytes, so a refusal (409) is
 * shown instead of a browser download of an error page, and a finished
 * export says what was saved; the line clears itself.
 */

import { t } from "../../i18n";
import { copyLookup } from "../../i18n/copy";
import { publicText } from "../scrub/scrub";
import { API, ApiError, apiErrorText } from "./api";
import { hint } from "./chrome";
import { openConversation } from "./conversation";
import { $ } from "./dom";
import { loadProjects } from "./load";

export const MAX_PACKAGE_BYTES = 128 * 1024 * 1024;

export const packageT = copyLookup({
  en: {
    title: "Import session",
    file: "{0} · {1}",
    checking: "Checking the package…",
    checked: "Checked: {0} file(s) match the package's manifest (SHA-256 {1}…)",
    importing: "Importing…",
    imported: "Imported. Opening the read-only copy…",
    refused: "Not imported: {0}",
    next: "Nothing was changed. Choose an .openai4s-session.zip exported from OpenAI4S, or ask the sender to export it again.",
    close: "Close",
    exportedPackage: "Session package saved ({0}). Local paths are redacted and no API keys are included.",
    exportedPackageLeft:
      "Session package saved ({0}). No API keys are included, but messages and cell output still contain {1} path(s) of this computer (they are kept exactly as written so the package verifies); check before you share it.",
    exportedArtifacts: "Artifacts saved ({0}).",
  },
  zh: {
    title: "导入会话",
    file: "{0} · {1}",
    checking: "正在校验会话包…",
    checked: "校验通过：{0} 个文件与包内清单一致（SHA-256 {1}…）",
    importing: "正在导入…",
    imported: "已导入，正在打开只读副本…",
    refused: "未导入：{0}",
    next: "没有做任何改动。请选择由 OpenAI4S 导出的 .openai4s-session.zip，或请发送者重新导出。",
    close: "关闭",
    exportedPackage: "会话包已保存（{0}）。本地路径已脱敏，不含 API 密钥。",
    exportedPackageLeft:
      "会话包已保存（{0}），不含 API 密钥；但消息和单元输出里还留有 {1} 处本机路径（这些内容按原样保留，包才能通过校验），分享前请检查。",
    exportedArtifacts: "产物已保存（{0}）。",
  },
});

export function byteSize(n: number): string {
  if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
  if (n >= 1024) return (n / 1024).toFixed(1) + " KB";
  return n + " B";
}

/** A status line that clears itself, unless something else replaced it first. */
export function flash(text: string, err = false, ms = 8000): void {
  if (err) hint(text, true);
  else hint(text);
  setTimeout(() => {
    const shown = $("#composer-hint");
    if (shown && (shown.textContent || "").endsWith(text)) hint("");
  }, ms);
}

type ImportDialog = {
  step(text: string): void;
  fail(text: string): void;
  close(): void;
};

function importDialog(file: File): ImportDialog {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay share-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-labelledby", "pkg-import-title");
  const box = document.createElement("div");
  box.className = "share-box";
  overlay.appendChild(box);
  const head = document.createElement("h2");
  head.id = "pkg-import-title";
  head.className = "share-title";
  head.textContent = packageT("title");
  box.appendChild(head);
  const name = document.createElement("p");
  name.className = "share-scope";
  name.textContent = packageT("file", file.name || "session.zip", byteSize(file.size || 0));
  box.appendChild(name);
  const steps = document.createElement("ol");
  steps.className = "share-setup";
  steps.setAttribute("role", "status");
  steps.setAttribute("aria-live", "polite");
  box.appendChild(steps);
  const row = document.createElement("div");
  row.className = "share-actions";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "outline-btn";
  closeBtn.textContent = packageT("close");
  row.appendChild(closeBtn);
  box.appendChild(row);
  const close = () => overlay.remove();
  closeBtn.onclick = close;
  overlay.addEventListener("keydown", (event) => {
    if ((event as KeyboardEvent).key === "Escape") close();
  });
  document.body.appendChild(overlay);
  closeBtn.focus();
  return {
    step(text: string) {
      const li = document.createElement("li");
      li.textContent = text;
      steps.appendChild(li);
    },
    fail(text: string) {
      const error = document.createElement("p");
      error.className = "share-tunnel";
      error.setAttribute("role", "alert");
      error.textContent = packageT("refused", text);
      box.insertBefore(error, row);
      const next = document.createElement("p");
      next.className = "share-text";
      next.textContent = packageT("next");
      box.insertBefore(next, row);
      closeBtn.focus();
    },
    close,
  };
}

export async function importSessionPackage(file: File | null | undefined): Promise<void> {
  if (!file) return;
  const dialog = importDialog(file);
  if (file.size > MAX_PACKAGE_BYTES) {
    dialog.fail(t("sessionPackage.tooLarge"));
    return;
  }
  dialog.step(packageT("checking"));
  try {
    const checked = await fetch(API + "/sessions/verify", {
      method: "POST",
      headers: { "Content-Type": "application/vnd.openai4s.session+zip" },
      body: file,
    });
    const verdict = (await checked.json().catch(() => ({}))) as {
      ok?: boolean;
      problems?: string[];
      error?: string;
      files_verified?: unknown[];
      archive_sha256?: string;
    };
    if (!checked.ok || !verdict.ok) {
      const first = (verdict.problems || [])[0] || verdict.error || "";
      dialog.fail(t("sessionPackage.verifyFailed", publicText(first, 160)));
      return;
    }
    dialog.step(
      packageT("checked", (verdict.files_verified || []).length, String(verdict.archive_sha256 || "").slice(0, 12)),
    );
    dialog.step(packageT("importing"));
    const response = await fetch(API + "/sessions/import", {
      method: "POST",
      headers: { "Content-Type": "application/vnd.openai4s.session+zip" },
      body: file,
    });
    const result = (await response.json().catch(() => ({}))) as {
      root_frame_id?: string;
      project_id?: string;
    };
    if (!response.ok || !result.root_frame_id || !result.project_id) {
      throw new ApiError(result, response.status);
    }
    dialog.step(packageT("imported"));
    await loadProjects();
    await openConversation(result.root_frame_id, result.project_id);
    dialog.close();
    flash(t("sessionPackage.imported"));
  } catch (error) {
    dialog.fail(apiErrorText(error));
  }
}

/** Fetch an export and save it under `filename`; the bytes' size, or null after reporting why not. */
export async function saveExport(
  url: string,
  filename: string,
  onResponse?: (response: Response) => void,
): Promise<number | null> {
  try {
    const response = await fetch(url);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new ApiError(body, response.status);
    }
    onResponse?.(response);
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = href;
    // An empty name keeps the server's (Content-Disposition), as a plain link would.
    if (filename) link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(href), 2000);
    return blob.size;
  } catch (error) {
    flash(t("toast.exportFailed", apiErrorText(error)), true);
    return null;
  }
}

export async function exportSessionPackage(fid: string, label: string): Promise<void> {
  let left = 0;
  const size = await saveExport(
    `${API}/frames/${encodeURIComponent(fid)}/session/export`,
    label.replace(/[^\w一-龥-]+/g, "_") + ".openai4s-session.zip",
    // EXP-1: message text and cell output are kept byte-exact so the package
    // verifies; the server says how many local paths that left in.
    (response) => {
      left = Number(response.headers.get("X-OpenAI4S-Local-Paths-Left") || 0);
    },
  );
  if (size === null) return;
  flash(
    Number.isSafeInteger(left) && left > 0
      ? packageT("exportedPackageLeft", byteSize(size), left)
      : packageT("exportedPackage", byteSize(size)),
  );
}

export async function downloadSessionArtifacts(fid: string, filename: string): Promise<void> {
  const size = await saveExport(`${API}/frames/${encodeURIComponent(fid)}/artifacts.zip`, filename);
  if (size !== null) flash(packageT("exportedArtifacts", byteSize(size)));
}
