/** Session title, menus, import/export, cancel. app.js:7411-7793 (share dialog: share.ts). */

import { LANG, t } from "../../i18n";
import { validateSessionArtifacts } from "../artifacts/validation";
import { autoModeMenuItem, beginAutoModeStatusRead } from "../automode";
import { environmentMenuItem } from "../notebook/envmenu";
import { openCustomize } from "../customize";
import { nestedEditor, type SkillSeed } from "../customize/state";
import { artifacts } from "../../stores/artifacts";
import { defaultModelName, models } from "../../stores/customize";
import { _openGen, _titleName, currentId, folders, project, sessions } from "../../stores/session";
import { exploreMode, pendingExecutionId, planMode, running } from "../../stores/stream";
import { API, api, apiErrorText } from "./api";
import { hint, openMenu, reportFailure, type MenuItem } from "./chrome";
import { expectOwnDeletion, forgetOwnDeletion } from "./deleted";
import { buildSkillBody, procedureSteps } from "./skill-draft";
import { openRunLocationDialog } from "./compute";
import { composerCopy, optionsMenuCopy, sessionCopy, shareCopy } from "./copy";
import { openConversation, resumeWatch } from "./conversation";
// Imported, not reached through `callLane`. Neither name is ever assigned to
// `window` (they are in neither CONTRACT_GLOBAL_NAMES nor SEND_CONTRACT_NAMES),
// and `callLane` answers a missing name with `undefined` rather than throwing --
// so Stop sent no request at all and `turnDone` never ran. `turnDone` is a
// hoisted function declaration, so the sessions<->send cycle resolves at call time.
import { noteForStop, turnDone } from "../send/turn";
import { sendCopy } from "../send/copy";
import { reviewRefusalText, reviewT } from "../send/review-copy";
import { backgroundStoppedHint, refreshBackgroundCells } from "../send/background";
import { scopedExecutionRequest } from "../timeline/execution-request";
import { queueT } from "../timeline/copy";
import { queuedFollowUpCount } from "../timeline/queue";
import { $, clearConversationChrome, el, enableComposer, setTitle } from "./dom";
import { callLane } from "./lane";
import { assignFolder, invalidateFolders, loadSessions, renderSessions } from "./load";
import { fetchAllMessages, fetchRecentMessages } from "./messages";
import { openShareDialog, shareNeedsSetup } from "./share";
import {
  byteSize,
  downloadSessionArtifacts,
  exportSessionPackage as exportPackageBytes,
  flash,
  importSessionPackage as importPackageWithDialog,
} from "./package";
import {
  buildSessionMarkdown,
  embedImages,
  imageType,
  type MarkdownCell,
  type MarkdownMessage,
} from "./markdown-export";
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
    // M-75: say what it does and what it costs before it starts.
    {
      label: t("composer.menu.requestReview"),
      desc: reviewT("review.menuDesc"),
      icon: "eye-context",
      onClick: () => void requestReview(),
    },
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
  const frameId = currentId.value;
  let messages: Array<{ role?: string; content?: unknown }> = [];
  // M-84: the code that actually ran, not only the question.
  let entries: unknown[] = [];
  const [read, log] = await Promise.allSettled([
    fetchRecentMessages(frameId, 500),
    api(`/frames/${encodeURIComponent(frameId)}/execution-log`),
  ]);
  if (read.status === "fulfilled") messages = read.value.messages || [];
  if (log.status === "fulfilled") entries = ((log.value as { entries?: unknown[] }) || {}).entries || [];
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
  const draft = { purpose: request, outcome: result, steps: procedureSteps(entries) };
  await openSkillEditor({
    name: title,
    description: request.replace(/\s+/g, " ").slice(0, 180),
    body: buildSkillBody(draft, new Set(draft.steps.map((step) => step.id))),
    draft,
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
    // M-75: a refusal says why in the UI language, not the server's English.
    hint(reviewRefusalText(e), true);
  }
}

export async function sessionOptionsMenu(anchor: Element): Promise<void> {
  const frameId = currentId.value;
  const openGen = _openGen.value;
  if (!frameId) return;
  // Beside the review-settings read, not after it: opening the menu is one
  // of the moments the read-only Auto Mode block re-reads from the server.
  void beginAutoModeStatusRead();
  let review: { auto_review?: boolean; reviewer_model?: string; delegation_enabled?: boolean } = {
    auto_review: false,
    reviewer_model: "",
    delegation_enabled: true,
  };
  try {
    review = (await api(`/frames/${frameId}/review-settings`)) as typeof review;
  } catch {
    /* defaults */
  }
  // A delayed settings response must not open a menu after navigation or a
  // branch reopen. Its Auto Mode read belongs to the previous opening too.
  if (currentId.value !== frameId || _openGen.value !== openGen) return;
  const checked = (on: boolean) => (on ? "✓  " : "");
  // Four groups under their own headings. The menu ran modes, review switches,
  // Settings shortcuts and a read-only status block together as one list.
  const heading = (key: Parameters<typeof optionsMenuCopy>[0]): MenuItem => {
    const node = el("div", "ctx-heading", optionsMenuCopy(key));
    node.setAttribute("role", "presentation");
    return { node };
  };
  const runtime = environmentMenuItem(anchor);
  openMenu(anchor, [
    heading("groupModes"),
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
    // The two modes used to be bare names here, explained only by a hint that
    // flashed when one was switched on. Each says what it does, and an active
    // one stays visible as a chip beside the composer (send.ts bindComposer).
    {
      label: checked(!!planMode.value) + t("composer.planMode"),
      desc: sendCopy("planDesc"),
      icon: "grid",
      onClick: () => $("#plan-toggle")?.click(),
    },
    {
      label: checked(!!exploreMode.value) + t("composer.exploreMode"),
      desc: sendCopy("exploreDesc"),
      icon: "compass",
      onClick: () => $("#explore-toggle")?.click(),
    },
    { sep: true },
    heading("groupReview"),
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
    { sep: true },
    heading("groupSession"),
    // "Compute" and "Runtime environment (Python / R)" sat side by side with
    // nothing to tell them apart: this one picks what this session's code
    // runs in, the one below opens Settings.
    {
      label: runtime.label || "",
      desc: optionsMenuCopy("runtimeDesc"),
      icon: "terminal",
      onClick: runtime.onClick,
    },
    { sep: true },
    heading("groupSettings"),
    { label: t("composer.option.memory"), icon: "book", onClick: () => callLane("openCust", "memory") },
    { label: t("composer.option.specialist"), icon: "users", onClick: () => callLane("openCust", "specialists") },
    {
      label: t("composer.option.compute"),
      desc: optionsMenuCopy("computeDesc"),
      icon: "sliders",
      onClick: () => callLane("openCust", "compute"),
    },
    // Read-only, and separate from the Auto review row above: that row is the
    // old post-completion Reviewer switch and still talks only to
    // review-settings; this block is availability, saved selection and run.
    // (The block titles itself, so it gets no heading of its own.)
    { sep: true },
    autoModeMenuItem(),
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
    {
      label: t("share.menu"),
      icon: "share",
      // In a default install no relay is configured and the dialog can only
      // explain the setup: say so on the item.
      ...(shareNeedsSetup() ? { desc: shareCopy("menuNeedsSetup") } : {}),
      onClick: () => void openShareDialog(fid, frame),
    },
    // EXP-3: the same read-only view as one self-contained file, no relay.
    { label: sessionCopy("exportWebPage"), icon: "download", onClick: () => exportSessionWebPage(fid, frame) },
    { label: t("sessionPackage.export"), icon: "archive", onClick: () => exportSessionPackage(fid, frame) },
    {
      label: t("sessionMenu.downloadArtifacts"),
      icon: "files",
      onClick: () =>
        void downloadSessionArtifacts(fid, `${frame.name || frame.task_summary || "session"}-artifacts.zip`),
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
  void exportPackageBytes(fid, frame.name || frame.task_summary || "session");
}

/** One self-contained `.html` of the session, in the UI language (EXP-3). */
export function exportSessionWebPage(fid: string, frame: SessionLike = {}): void {
  const label = frame.name || frame.task_summary || "session";
  downloadArtifactBundle(
    `${API}/frames/${encodeURIComponent(fid)}/session/export-html?lang=${LANG === "zh" ? "zh" : "en"}`,
    label.replace(/[^\w一-龥-]+/g, "_") + ".html",
  );
}

export function chooseSessionPackage(): void {
  $("#session-package-input")?.click();
}

/** TEAM-20: the import runs in a dialog of its own (package.ts). */
export const importSessionPackage = importPackageWithDialog;

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
    const [d, arts, log] = await Promise.all([
      fetchAllMessages(fid),
      api(`/frames/${encodeURIComponent(fid)}/artifacts`).then((value) => validateSessionArtifacts(value, fid)),
      api(`/frames/${encodeURIComponent(fid)}/execution-log`),
    ]);
    // TEAM-12: one section per turn, the code and output that ran, and the
    // figures embedded -- a file that reads the same outside the app.
    const entries = log && typeof log === "object" ? (log as { entries?: unknown }).entries : null;
    const images = await embedImages(
      arts,
      async (id) => {
        const response = await fetch(`${API}/artifacts/${encodeURIComponent(id)}`);
        return response.ok ? response.arrayBuffer() : null;
      },
      byteSize,
    );
    const md = buildSessionMarkdown({
      title,
      truncatedNote: d.complete === false ? t("conv.exportTruncated") : null,
      messages: (d.messages || []) as MarkdownMessage[],
      cells: (Array.isArray(entries) ? entries : []) as MarkdownCell[],
      images,
      otherArtifacts: arts
        .filter((a) => a.filename && !imageType(String(a.filename), a.content_type))
        .map((a) => ({ filename: String(a.filename), content_type: a.content_type })),
    });
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = title.replace(/[^\w一-龥-]+/g, "_") + ".md";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    flash(t("toast.exportedMarkdown"));
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
  // This tab moves on itself; its own deletion's event is not "deleted elsewhere".
  expectOwnDeletion(fid);
  try {
    await api("/frames/" + fid, { method: "DELETE" });
  } catch (e) {
    forgetOwnDeletion(fid);
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
  // Stop ends the running turn only. Follow-ups queued behind it keep their
  // places and the first starts at once, so the hint says that up front
  // instead of leaving a Stop that "did nothing" (the next turn's Stop button,
  // placeholder and spinner look exactly like the stopped one's).
  const queued = queuedFollowUpCount();
  try {
    const result = await scopedExecutionRequest(fid, "cancel", "composer cancel");
    if (cancelNamedTheRunningTurn(result)) {
      markTurnStopping(fid);
      if (fid !== currentId.value) return;
      // The background cells this turn started were stopped with it; say so,
      // and how many, rather than leaving them to be discovered.
      const background = backgroundStoppedHint(result);
      const parts = [
        background,
        queued && running.value ? queueT("queueRow.stopping", queued) : "",
      ].filter(Boolean);
      if (parts.length) {
        hint(parts.join("；"), false, running.value);
        // The turn's teardown keeps the line instead of clearing it, and a
        // queued message starting next keeps the background part.
        noteForStop(parts.join("；"), background);
      }
      if (background) void refreshBackgroundCells();
    }
  } catch (error) {
    hint(t("nb.action.failed", apiErrorText(error)), true);
  }
}
