/** Session title, menus, import/export, cancel. app.js:7411-7793 (share dialog: share.ts). */

import { t } from "../../i18n";
import { validateSessionArtifacts } from "../artifacts/validation";
import { openCustomize } from "../customize";
import { nestedEditor, type SkillSeed } from "../customize/state";
import { artifacts } from "../../stores/artifacts";
import { defaultModelName, models } from "../../stores/customize";
import { _openGen, _titleName, currentId, folders, project, sessions } from "../../stores/session";
import { exploreMode, pendingExecutionId, planMode, running } from "../../stores/stream";
import { API, ApiError, api, apiErrorText } from "./api";
import { hint, openMenu, reportFailure, type MenuItem } from "./chrome";
import { openRunLocationDialog } from "./compute";
import { composerCopy } from "./copy";
import { openConversation, resumeWatch } from "./conversation";
// Imported, not reached through `callLane`. Neither name is ever assigned to
// `window` (they are in neither CONTRACT_GLOBAL_NAMES nor SEND_CONTRACT_NAMES),
// and `callLane` answers a missing name with `undefined` rather than throwing --
// so Stop sent no request at all and `turnDone` never ran. `turnDone` is a
// hoisted function declaration, so the sessions<->send cycle resolves at call time.
import { turnDone } from "../send/turn";
import { scopedExecutionRequest } from "../timeline/execution-request";
import { $, clearConversationChrome, enableComposer, setTitle } from "./dom";
import { callLane } from "./lane";
import { assignFolder, invalidateFolders, loadProjects, loadSessions, renderSessions } from "./load";
import { fetchAllMessages, fetchRecentMessages } from "./messages";
import { publicText } from "../scrub/scrub";
import { openShareDialog } from "./share";
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
  h.textContent = t("context.tokens", (input + output).toLocaleString());
  card.appendChild(h);
  const meta = document.createElement("div");
  meta.className = "prov-meta";
  meta.textContent = composerCopy("usage", input.toLocaleString(), output.toLocaleString(), reviewer.toLocaleString());
  card.appendChild(meta);
  body.appendChild(card);
  $("#modal")?.classList.remove("hidden");
}


/**
 * The new-skill editor of Customize → Skills, opened over that tab. app.js
 * drew a second copy of the skill form in #modal (`skillEditor`); this port
 * called that name, which nothing defines, so the menu entry did nothing. The
 * seed travels on the editor state for the form to start from.
 */
export async function openSkillEditor(seed?: SkillSeed): Promise<void> {
  // Settings is its own chunk: the editor can open only once it has mounted.
  try {
    await openCustomize("skills");
  } catch (error) {
    reportFailure(error);
    return;
  }
  nestedEditor.value = seed ? { kind: "skill", name: null, seed } : { kind: "skill", name: null };
}

export async function saveCurrentAsSkill(): Promise<void> {
  if (!currentId.value) {
    await openSkillEditor();
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
  await openSkillEditor({
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
  hint(composerCopy("reviewing"), false, true);
  try {
    await api(`/frames/${currentId.value}/review`, { method: "POST", body: "{}" });
    resumeWatch(currentId.value, _openGen.value);
  } catch (e) {
    turnDone("failed");
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
          hint(t("composer.option.delegation") + " · " + composerCopy(on ? "on" : "off"));
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
          hint(t("composer.option.autoReview") + " · " + composerCopy(!review.auto_review ? "on" : "off"));
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
          const result = await scopedExecutionRequest(fid, "cancel", "session menu cancel");
          if (cancelNamedTheRunningTurn(result)) markTurnStopping(fid);
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
        void openRunLocationDialog(fid);
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
      let r: { folder_id: string };
      try {
        r = (await api(`/projects/${project.value}/folders`, {
          method: "POST",
          body: JSON.stringify({ name: n }),
        })) as { folder_id: string };
      } catch (e) {
        hint(t("folder.create.failed", apiErrorText(e)), true);
        return;
      }
      // The cached folder list predates this folder: without the invalidation
      // the refresh behind assignFolder answered from that cache, so the new
      // folder never appeared and the moved session fell under "ungrouped".
      invalidateFolders();
      await assignFolder(fid, r.folder_id);
    },
  });
  openMenu(anchor, items);
}

export async function exportSession(fid: string): Promise<void> {
  const frame = (sessions.value as SessionLike[]).find((x) => x.id === fid);
  const title = frame?.name || frame?.task_summary || t("conv.title.default");
  try {
    const [d, arts] = await Promise.all([
      fetchAllMessages(fid),
      api(`/frames/${encodeURIComponent(fid)}/artifacts`).then((value) => validateSessionArtifacts(value, fid)),
    ]);
    let md = "# " + title + "\n\n";
    if (d.complete === false) md += "> " + t("conv.exportTruncated") + "\n\n";
    (d.messages || []).forEach((m) => {
      const who = m.role === "user" ? "🧑 User" : "🤖 Assistant";
      const txt = Array.isArray(m.content)
        ? (m.content as Array<{ text?: string }>).map((b) => b.text || "").join("")
        : (m.content as string) || "";
      md += `## ${who}\n\n${txt}\n\n`;
    });
    const artList = arts;
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
    link.download = title.replace(/[^\w一-龥-]+/g, "_") + ".md";
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
  const read = await loadSessions();
  if (read.status !== "loaded") {
    // A failed or superseded refresh keeps the rows it had, the deleted one
    // among them, and that row usually sorts first. It is gone either way.
    sessions.value = (sessions.value as SessionLike[]).filter((f) => f.id !== fid);
    renderSessions();
  }
  if (wasCurrent) {
    let ss = (sessions.value as SessionLike[]).filter((f) => f.id !== fid);
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

/**
 * A cancel that the server accepted is not a cancel that finished.
 * Port of app.js:7823-7830.
 */
export function markTurnStopping(fid: string | null | undefined): void {
  if (!fid || fid !== currentId.value) return;
  // resumeWatch's staleness test includes `!running`, so with no local running
  // episode its first tick exits and nothing would ever clear the spinner. That
  // state is reachable: the sidebar row can still report `frame.running` after a
  // reconnect while this client already saw the turn end.
  if (!running.value) {
    turnDone("cancelled");
    return;
  }
  // Accepted is not terminal. Keep the authoritative running episode open
  // until frame_update (or the status watchdog) confirms the owner was
  // released; otherwise a reload can resurrect "Running" immediately after
  // this client claimed the turn had stopped.
  $("#cancel-btn")?.classList.add("hidden");
  hint(t("turn.stopping"), false, true);
  resumeWatch(fid, _openGen.value);
}

/**
 * An accepted cancel names the execution it stopped. Apply "Stopping…" only
 * when that is still the execution this client is running: the WebSocket can
 * deliver cancelled(A) and processing(B) -- a queued follow-up -- before the
 * HTTP response returns, and marking then would hide Stop and pin the spinner
 * on B, which nobody cancelled.
 */
export function cancelNamedTheRunningTurn(
  result: { ok?: boolean; execution_id?: unknown } | null | undefined,
): boolean {
  if (!result || !result.ok) return false;
  const named = result.execution_id == null ? "" : String(result.execution_id);
  const current = pendingExecutionId.value;
  return !named || !current || named === current;
}

export async function cancelTurn(): Promise<void> {
  // `currentId` can move while the request is in flight; the turn this cancel
  // was aimed at is the one markTurnStopping has to be told about.
  const fid = currentId.value;
  if (!fid) return;
  try {
    const result = await scopedExecutionRequest(fid, "cancel", "composer cancel");
    if (cancelNamedTheRunningTurn(result)) markTurnStopping(fid);
  } catch (error) {
    hint(t("nb.action.failed", apiErrorText(error)), true);
  }
}
