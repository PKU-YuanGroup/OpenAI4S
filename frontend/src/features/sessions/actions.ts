/** Session title, menus, share dialog, import/export. app.js:7411-7793. */

import { t } from "../../i18n";
import { artifacts } from "../../stores/artifacts";
import { defaultModelName, models } from "../../stores/customize";
import { _openGen, _titleName, currentId, folders, project, sessions } from "../../stores/session";
import { exploreMode, planMode, running } from "../../stores/stream";
import { API, ApiError, api, apiErrorText } from "./api";
import { hint, openMenu, type MenuItem } from "./chrome";
import { openConversation, resumeWatch } from "./conversation";
import { $, clearConversationChrome, enableComposer, setTitle } from "./dom";
import { callLane } from "./lane";
import { assignFolder, loadProjects, loadSessions } from "./load";
import { fetchAllMessages, fetchRecentMessages } from "./messages";
import { publicText } from "../scrub/scrub";
import type { SessionLike } from "./paging";

export async function commitTitle(): Promise<void> {
  if (!currentId.value) return;
  const name = (($("#conv-title") as HTMLInputElement | null)?.value || "").trim();
  if (!name || name === _titleName.value) {
    setTitle(_titleName.value);
    return;
  }
  try {
    await api("/frames/" + currentId.value, { method: "PATCH", body: JSON.stringify({ name }) });
    _titleName.value = name;
    setTitle(name);
    void loadSessions();
  } catch (e) {
    setTitle(_titleName.value);
    hint(t("toast.renameFailed", apiErrorText(e)), true);
  }
}

export function addToMessageMenu(anchor: Element): void {
  openMenu(anchor, [
    { label: t("composer.menu.attachFiles"), icon: "plus", onClick: () => $("#file-input")?.click() },
    {
      label: t("composer.menu.yourFiles"),
      icon: "files",
      onClick: () => {
        callLane("setActiveTab", "files");
      },
    },
    { label: t("composer.menu.requestReview"), icon: "eye-context", onClick: () => void requestReview() },
    { label: t("composer.menu.saveAsSkill"), icon: "book", onClick: () => void saveCurrentAsSkill() },
    { sep: true },
    { label: t("composer.menu.contextUsage"), icon: "circle-dot", onClick: () => void showContextUsage() },
  ]);
}

export async function showContextUsage(): Promise<void> {
  if (!currentId.value) return;
  let frame: Record<string, unknown>;
  let steps: Array<Record<string, unknown>> = [];
  try {
    const data = await Promise.all([
      api(`/frames/${currentId.value}`),
      api(`/frames/${currentId.value}/steps`).catch(() => ({ steps: [] })),
    ]);
    frame = data[0] as Record<string, unknown>;
    steps = ((data[1] as { steps?: Array<Record<string, unknown>> }).steps || []) as Array<
      Record<string, unknown>
    >;
  } catch (e) {
    hint((e as Error).message, true);
    return;
  }
  const input = Number(frame.input_tokens || 0);
  const output = Number(frame.output_tokens || 0);
  const reviewer = steps
    .filter((s) => s.kind === "review")
    .reduce((sum, s) => {
      const outputRec = s.output as { usage?: { input_tokens?: number; output_tokens?: number } } | undefined;
      const usage = outputRec && outputRec.usage;
      return (
        sum + Number((usage && ((usage.input_tokens || 0) + (usage.output_tokens || 0))) || 0)
      );
    }, 0);
  const title = $("#modal-title");
  if (title) title.textContent = t("composer.menu.contextUsage");
  const dl = $("#modal-download");
  if (dl) dl.style.display = "none";
  const body = $("#modal-body");
  if (!body) return;
  body.innerHTML = "";
  const card = document.createElement("div");
  card.className = "prov-card";
  const h = document.createElement("div");
  h.className = "prov-h";
  h.textContent = `${(input + output).toLocaleString()} tokens`;
  card.appendChild(h);
  const meta = document.createElement("div");
  meta.className = "prov-meta";
  meta.textContent = `Input ${input.toLocaleString()} · Output ${output.toLocaleString()} · Reviewer ${reviewer.toLocaleString()}`;
  card.appendChild(meta);
  body.appendChild(card);
  $("#modal")?.classList.remove("hidden");
}

export async function saveCurrentAsSkill(): Promise<void> {
  if (!currentId.value) {
    callLane("skillEditor", null);
    return;
  }
  let messages: Array<{ role?: string; content?: unknown }> = [];
  try {
    const data = await fetchRecentMessages(currentId.value, 500);
    messages = data.messages || [];
  } catch {
    messages = [];
  }
  const latestUser = [...messages].reverse().find((m) => m.role === "user");
  const latestAssistant = [...messages].reverse().find((m) => m.role === "assistant");
  const title =
    (_titleName.value || "research-workflow")
      .toLowerCase()
      .replace(/[^a-z0-9一-龥]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 48) || "research-workflow";
  const request = String((latestUser && latestUser.content) || "").trim();
  const result = String((latestAssistant && latestAssistant.content) || "").trim();
  callLane("skillEditor", null, {
    name: title,
    description: request.replace(/\s+/g, " ").slice(0, 180),
    body: `# Purpose\n\n${request || "Describe when this workflow should be used."}\n\n# Procedure\n\n1. Reproduce the evidence-gathering and analysis workflow.\n2. Preserve data provenance, code, and generated artifacts.\n3. State uncertainty and do not overclaim beyond the evidence.\n\n# Example outcome\n\n${result.slice(0, 6000)}`,
  });
}

export async function requestReview(): Promise<void> {
  if (!currentId.value || running.value) return;
  running.value = true;
  enableComposer(false);
  $("#cancel-btn")?.classList.remove("hidden");
  hint("Reviewing", false, true);
  try {
    await api(`/frames/${currentId.value}/review`, { method: "POST", body: "{}" });
    resumeWatch(currentId.value, _openGen.value);
  } catch (e) {
    callLane("turnDone", "failed");
    hint((e as Error).message, true);
  }
}

export async function sessionOptionsMenu(anchor: Element): Promise<void> {
  if (!currentId.value) return;
  let review: { auto_review?: boolean; reviewer_model?: string; delegation_enabled?: boolean } = {
    auto_review: false,
    reviewer_model: "",
    delegation_enabled: true,
  };
  try {
    review = (await api(`/frames/${currentId.value}/review-settings`)) as typeof review;
  } catch {
    /* defaults */
  }
  const checked = (on: boolean) => (on ? "✓  " : "");
  openMenu(anchor, [
    {
      label: checked(review.delegation_enabled !== false) + t("composer.option.delegation"),
      icon: "users",
      onClick: async () => {
        const on = review.delegation_enabled === false;
        try {
          await api(`/frames/${currentId.value}/review-settings`, {
            method: "PATCH",
            body: JSON.stringify({ delegation_enabled: on }),
          });
          hint(t("composer.option.delegation") + ` · ${on ? "On" : "Off"}`);
        } catch (e) {
          hint((e as Error).message, true);
        }
      },
    },
    {
      label: checked(!!planMode.value) + t("composer.planMode"),
      icon: "grid",
      onClick: () => $("#plan-toggle")?.click(),
    },
    {
      label: checked(!!exploreMode.value) + t("composer.exploreMode"),
      icon: "compass",
      onClick: () => $("#explore-toggle")?.click(),
    },
    { sep: true },
    {
      label: checked(!!review.auto_review) + t("composer.option.autoReview"),
      icon: "eye-context",
      onClick: async () => {
        try {
          await api(`/frames/${currentId.value}/review-settings`, {
            method: "PATCH",
            body: JSON.stringify({ auto_review: !review.auto_review }),
          });
          hint(t("composer.option.autoReview") + ` · ${!review.auto_review ? "On" : "Off"}`);
        } catch (e) {
          hint((e as Error).message, true);
        }
      },
    },
    {
      label: t("composer.option.reviewerModel") + (review.reviewer_model ? ` · ${review.reviewer_model}` : ""),
      icon: "sliders",
      onClick: () => reviewerModelMenu(anchor, review.reviewer_model),
    },
    { label: t("composer.option.memory"), icon: "book", onClick: () => callLane("openCust", "memory") },
    { label: t("composer.option.specialist"), icon: "users", onClick: () => callLane("openCust", "specialists") },
    { label: t("composer.option.compute"), icon: "terminal", onClick: () => callLane("openCust", "compute") },
  ]);
}

function reviewerModelMenu(anchor: Element, current?: string): void {
  const choices = [{ id: "", name: t("composer.option.sameModel") }].concat(
    ((models.value || []) as Array<{ id: string; name?: string }>).map((m) => ({
      id: m.id,
      name: m.name || m.id,
    })),
  );
  openMenu(
    anchor,
    choices.map((model) => ({
      label: (model.id === (current || "") ? "✓  " : "") + model.name,
      icon: "circle-dot",
      onClick: async () => {
        try {
          await api(`/frames/${currentId.value}/review-settings`, {
            method: "PATCH",
            body: JSON.stringify({ reviewer_model: model.id }),
          });
          hint(t("composer.option.reviewerModel") + ` · ${model.name}`);
        } catch (e) {
          hint((e as Error).message, true);
        }
      },
    })),
  );
}

export function sessionMenu(anchor: Element, fid: string): void {
  const frame = (sessions.value as SessionLike[]).find((x) => x.id === fid) || {};
  const items: MenuItem[] = [{ label: t("folder.menu.rename"), icon: "pencil", onClick: () => void renameFrame(fid) }];
  if (frame.running || (fid === currentId.value && running.value)) {
    items.push({
      label: t("sessionMenu.cancel"),
      icon: "stop",
      onClick: async () => {
        try {
          const result = (await callLane(
            "scopedExecutionRequest",
            fid,
            "cancel",
            "session menu cancel",
          )) as { ok?: boolean } | undefined;
          if (result && result.ok && fid === currentId.value) callLane("turnDone", "cancelled");
        } catch (error) {
          hint(t("nb.action.failed", apiErrorText(error)), true);
        }
        void loadSessions();
      },
    });
  }
  items.push(
    { label: t("sessionMenu.exportMarkdown"), icon: "download", onClick: () => void exportSession(fid) },
    { label: t("share.menu"), icon: "share", onClick: () => void openShareDialog(fid, frame) },
    { label: t("sessionPackage.export"), icon: "archive", onClick: () => exportSessionPackage(fid, frame) },
    {
      label: t("sessionMenu.downloadArtifacts"),
      icon: "files",
      onClick: () =>
        downloadArtifactBundle(
          `${API}/frames/${encodeURIComponent(fid)}/artifacts.zip`,
          `${frame.name || frame.task_summary || "session"}-artifacts.zip`,
        ),
    },
    {
      label: t("sessionMenu.viewNotebook"),
      icon: "notebook",
      onClick: async () => {
        if (fid !== currentId.value) await openConversation(fid, frame.project_id);
        callLane("setActiveTab", "notebook");
      },
    },
    {
      label: t("compute.menu.runLocation"),
      icon: "server",
      onClick: () => {
        callLane("openRunLocationDialog", fid);
      },
    },
    { sep: true },
    { label: t("sessionMenu.duplicate"), icon: "copy", onClick: () => void duplicateSession(fid) },
    { label: t("sessionMenu.moveToFolder"), icon: "folder", onClick: () => moveToFolderAt(anchor, fid) },
    {
      label: t("common.delete"),
      icon: "trash-2",
      danger: true,
      onClick: () => {
        if (confirm(t("confirm.deleteSession"))) void deleteSession(fid);
      },
    },
  );
  openMenu(anchor, items);
}

export function exportSessionPackage(fid: string, frame: SessionLike = {}): void {
  const label = frame.name || frame.task_summary || "session";
  downloadArtifactBundle(
    `${API}/frames/${encodeURIComponent(fid)}/session/export`,
    label.replace(/[^\w一-龥-]+/g, "_") + ".openai4s-session.zip",
  );
}

export async function openShareDialog(fid: string, frame: SessionLike = {}): Promise<void> {
  let status: Record<string, unknown> = {};
  let shares: { shares?: Array<Record<string, unknown>> } = { shares: [] };
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
  overlay.className = "modal-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.style.cssText =
    "position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:1000";
  const box = document.createElement("div");
  box.style.cssText =
    "background:var(--panel,#fff);color:var(--ink,#111);max-width:520px;width:90%;border-radius:12px;padding:20px;box-shadow:0 10px 40px rgba(0,0,0,.3)";
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
  const h = document.createElement("h3");
  h.textContent = t("share.title");
  h.style.marginTop = "0";
  box.appendChild(h);

  const state = String(status.state || "");
  if (state === "unconfigured") {
    box.appendChild(Object.assign(document.createElement("p"), { textContent: t("share.unconfigured") }));
    box.appendChild(mkBtn(t("share.close"), close));
    document.body.appendChild(overlay);
    return;
  }
  if (state === "disabled") {
    box.appendChild(Object.assign(document.createElement("p"), { textContent: t("share.disabled") }));
    const row = document.createElement("div");
    row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:16px";
    if (status.configured) {
      row.appendChild(
        mkBtn(
          t("share.enable"),
          async () => {
            await shareCall("PUT", `${API}/share/settings`, { enabled: true });
            close();
            void openShareDialog(fid, frame);
          },
          false,
          true,
        ),
      );
    }
    row.appendChild(mkBtn(t("share.close"), close));
    box.appendChild(row);
    document.body.appendChild(overlay);
    return;
  }

  const active = (shares.shares || []).find((s) => s.status === "ready" || s.status === "publishing");
  const scope = document.createElement("p");
  scope.className = "muted";
  scope.style.fontSize = "13px";
  scope.textContent = t("share.scope");
  box.appendChild(scope);

  if (active) {
    const row = document.createElement("div");
    row.style.cssText = "display:flex;gap:8px;margin:12px 0";
    const inp = document.createElement("input");
    inp.readOnly = true;
    inp.value = String(active.url || "");
    inp.style.cssText = "flex:1;padding:8px;border:1px solid var(--line,#ccc);border-radius:8px";
    row.appendChild(inp);
    row.appendChild(
      mkBtn(t("share.copy"), () => {
        if (navigator.clipboard) navigator.clipboard.writeText(String(active.url || ""));
        hint(t("share.copied"));
      }),
    );
    box.appendChild(row);
    const exp = document.createElement("div");
    exp.className = "muted";
    exp.style.fontSize = "12px";
    exp.style.margin = "4px 0 8px";
    exp.textContent = active.expires_at
      ? t("share.expiresAt") + " " + new Date(String(active.expires_at)).toLocaleString()
      : t("share.neverExpires");
    box.appendChild(exp);
    const actionsRow = document.createElement("div");
    actionsRow.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:16px";
    actionsRow.appendChild(
      mkBtn(t("share.update"), async () => {
        await shareCall("PUT", `${API}/shares/${encodeURIComponent(String(active.share_id))}`);
        hint(t("share.updated"));
        close();
      }),
    );
    actionsRow.appendChild(
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
    );
    actionsRow.appendChild(mkBtn(t("share.close"), close));
    box.appendChild(actionsRow);
  } else {
    const expRow = document.createElement("div");
    expRow.style.cssText = "display:flex;align-items:center;gap:8px;margin:12px 0";
    const expLabel = document.createElement("span");
    expLabel.className = "muted";
    expLabel.style.fontSize = "13px";
    expLabel.textContent = t("share.expiry");
    const sel = document.createElement("select");
    sel.style.cssText = "padding:6px;border:1px solid var(--line,#ccc);border-radius:8px";
    (
      [
        [0, t("share.expiry.never")],
        [86400, t("share.expiry.1d")],
        [604800, t("share.expiry.7d")],
        [2592000, t("share.expiry.30d")],
      ] as Array<[number, string]>
    ).forEach(([secs, label]) => {
      const o = document.createElement("option");
      o.value = String(secs);
      o.textContent = label;
      sel.appendChild(o);
    });
    sel.value = "604800";
    expRow.appendChild(expLabel);
    expRow.appendChild(sel);
    box.appendChild(expRow);
    const actionsRow = document.createElement("div");
    actionsRow.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:16px";
    actionsRow.appendChild(
      mkBtn(
        t("share.create"),
        async () => {
          const body: { expires_in?: number } = {};
          const secs = parseInt(sel.value, 10);
          if (secs > 0) body.expires_in = secs;
          const rec = await shareCall("POST", `${API}/frames/${encodeURIComponent(fid)}/shares`, body);
          close();
          if (rec && (rec as { url?: string }).url) void openShareDialog(fid, frame);
        },
        false,
        true,
      ),
    );
    actionsRow.appendChild(mkBtn(t("share.close"), close));
    box.appendChild(actionsRow);
  }
  document.body.appendChild(overlay);
  const firstBtn = box.querySelector("button");
  if (firstBtn instanceof HTMLElement) firstBtn.focus();

  function mkBtn(label: string, onClick: () => void, danger?: boolean, primary?: boolean): HTMLButtonElement {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.className = danger ? "danger" : primary ? "primary" : "";
    b.style.cssText =
      "padding:7px 14px;border-radius:8px;cursor:pointer;border:1px solid var(--line,#ccc)" +
      (primary ? ";background:var(--accent,#2b6cb0);color:#fff" : "");
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

export function chooseSessionPackage(): void {
  $("#session-package-input")?.click();
}

export async function importSessionPackage(file: File | null | undefined): Promise<void> {
  if (!file) return;
  if (file.size > 128 * 1024 * 1024) {
    hint(t("sessionPackage.tooLarge"), true);
    return;
  }
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
    };
    if (!checked.ok || !verdict.ok) {
      const first = (verdict.problems || [])[0] || verdict.error || "";
      hint(t("sessionPackage.verifyFailed", publicText(first, 160)), true);
      return;
    }
    hint(t("sessionPackage.verified", (verdict.files_verified || []).length));
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
    await loadProjects();
    hint(t("sessionPackage.imported"));
    await openConversation(result.root_frame_id, result.project_id);
  } catch (error) {
    hint(t("toast.importFailed", apiErrorText(error)), true);
  }
}

export function downloadArtifactBundle(url: string, filename: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.download = filename || "artifacts.zip";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

export function moveToFolderAt(anchor: Element, fid: string): void {
  const list = (folders.value || []) as Array<{ folder_id: string; name: string }>;
  const items: MenuItem[] = [
    { label: t("moveFolder.removeFromFolder"), icon: "x", onClick: () => void assignFolder(fid, null) },
  ];
  list.forEach((fo) =>
    items.push({ label: fo.name, icon: "folder", onClick: () => void assignFolder(fid, fo.folder_id) }),
  );
  items.push({ sep: true });
  items.push({
    label: t("moveFolder.newFolderAndMove"),
    icon: "plus",
    onClick: async () => {
      const n = prompt(t("folder.new.prompt"));
      if (!n || !project.value) return;
      try {
        const r = (await api(`/projects/${project.value}/folders`, {
          method: "POST",
          body: JSON.stringify({ name: n }),
        })) as { folder_id: string };
        await assignFolder(fid, r.folder_id);
      } catch {
        /* ignore */
      }
    },
  });
  openMenu(anchor, items);
}

export async function exportSession(fid: string): Promise<void> {
  try {
    const [d, arts] = await Promise.all([
      fetchAllMessages(fid),
      api(`/frames/${fid}/artifacts`).catch(() => []),
    ]);
    const f = (sessions.value as SessionLike[]).find((x) => x.id === fid) || {};
    let md = "# " + (f.name || f.task_summary || t("conv.title.default")) + "\n\n";
    if (d.complete === false) md += "> " + t("conv.exportTruncated") + "\n\n";
    (d.messages || []).forEach((m) => {
      const who = m.role === "user" ? "🧑 User" : "🤖 Assistant";
      const txt = Array.isArray(m.content)
        ? (m.content as Array<{ text?: string }>).map((b) => b.text || "").join("")
        : (m.content as string) || "";
      md += `## ${who}\n\n${txt}\n\n`;
    });
    const artList = (arts || []) as Array<{ filename?: string; content_type?: string }>;
    if (artList.length) {
      md += "## 产物 Artifacts\n\n";
      artList.forEach((a) => {
        md += `- ${a.filename} (${a.content_type || ""})\n`;
      });
    }
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = (f.name || f.task_summary || "session").replace(/[^\w一-龥-]+/g, "_") + ".md";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    hint(t("toast.exportedMarkdown"));
  } catch (e) {
    hint(t("toast.exportFailed", apiErrorText(e)), true);
  }
}

export async function renameFrame(fid: string): Promise<void> {
  const f = (sessions.value as SessionLike[]).find((x) => x.id === fid);
  if (fid !== currentId.value) await openConversation(fid, f && f.project_id);
  const ct = $("#conv-title") as HTMLInputElement | null;
  ct?.focus();
  ct?.select();
}

export async function deleteSession(fid: string): Promise<void> {
  try {
    await api("/frames/" + fid, { method: "DELETE" });
  } catch (e) {
    hint(t("toast.deleteFailed", apiErrorText(e)), true);
    return;
  }
  const wasCurrent = fid === currentId.value;
  await loadSessions();
  if (wasCurrent) {
    let ss = sessions.value as SessionLike[];
    if (project.value) ss = ss.filter((f) => f.project_id === project.value);
    if (ss.length && ss[0]?.id) void openConversation(ss[0].id, ss[0].project_id);
    else {
      clearConversationChrome();
      artifacts.value = [];
      callLane("renderFilesGrid");
    }
  }
}

export async function duplicateSession(fid: string): Promise<void> {
  const f = (sessions.value as SessionLike[]).find((x) => x.id === fid) || {};
  try {
    const nf = (await api("/frames", {
      method: "POST",
      body: JSON.stringify({
        project_id: f.project_id || project.value || undefined,
        model: defaultModelName.value,
      }),
    })) as { id: string };
    const nm = (f.name || f.task_summary || t("conv.title.default")) + t("session.duplicateSuffix");
    try {
      await api("/frames/" + nf.id, { method: "PATCH", body: JSON.stringify({ name: nm }) });
    } catch {
      /* title is best-effort */
    }
    await loadSessions();
    void openConversation(nf.id, f.project_id);
  } catch (e) {
    hint(t("toast.duplicateFailed", apiErrorText(e)), true);
  }
}

export async function cancelTurn(): Promise<void> {
  if (!currentId.value) return;
  try {
    const result = (await callLane(
      "scopedExecutionRequest",
      currentId.value,
      "cancel",
      "composer cancel",
    )) as { ok?: boolean } | undefined;
    if (result && result.ok) callLane("turnDone", "cancelled");
  } catch (error) {
    hint(t("nb.action.failed", apiErrorText(error)), true);
  }
}
