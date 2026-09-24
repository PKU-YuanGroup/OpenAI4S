import { bindEditorAutocomplete } from "../autocomplete/editor";
import { copyText } from "../chrome/clipboard";
import { el } from "./api";
import { filesT } from "./copy";
import { artifactEditors } from "./state";
import type { ArtifactRow } from "./types";

let detach: (() => void) | null = null;
let leaveGuardInstalled = false;
export function detachArtifactEditor(): void { detach?.(); detach = null; }

export function renderArtifactEditor(body: HTMLElement, artifact: ArtifactRow, options: {
  sessionId: string;
  active(): boolean;
  close(): void;
  reload(): void;
  saved(versionId: string): void;
  latest(artifact: ArtifactRow): void;
}): void {
  if (artifact._exactVersion) return;
  if (!leaveGuardInstalled && typeof window !== "undefined") {
    window.addEventListener("beforeunload", (event) => {
      if (!artifactEditors.needsLeaveConfirmation) return;
      event.preventDefault(); event.returnValue = "";
    });
    leaveGuardInstalled = true;
  }
  const editor = artifactEditors.open(options.sessionId, artifact);
  const bar = el("div", "edit-bar");
  bar.appendChild(el("span", "edit-label", filesT("editor.label", artifact.filename || "")));
  const actions = el("div", "edit-acts");
  const close = el("button", "outline-btn small", filesT("editor.keepClose"));
  const save = el("button", "solid-btn small", filesT("common.save"));
  save.disabled = true;
  actions.append(close, save); bar.appendChild(actions); body.appendChild(bar);
  const status = el("div", "edit-status");
  status.setAttribute("role", "status");
  body.appendChild(status);
  const recovery = el("div", "edit-recovery");
  const copy = el("button", "outline-btn small", filesT("editor.copy"));
  const latest = el("button", "outline-btn small", filesT("editor.viewLatest"));
  const retry = el("button", "outline-btn small", filesT("editor.retryRead"));
  const discard = el("button", "outline-btn small", filesT("editor.discardReload"));
  const discardClose = el("button", "outline-btn small", filesT("editor.discardClose"));
  recovery.append(copy, latest, retry, discard, discardClose); body.appendChild(recovery);
  const area = el("textarea", "edit-area");
  area.spellcheck = false; area.disabled = true;
  area.setAttribute("aria-label", filesT("editor.label", artifact.filename || ""));
  body.appendChild(area);
  body.appendChild(el("div", "edit-ac hidden"));
  bindEditorAutocomplete(area, artifact);
  const paint = (): void => {
    if (!options.active()) return;
    body.dataset.editorState = editor.phase;
    save.disabled = !editor.canSave;
    save.textContent = filesT(editor.phase === "saving" ? "common.saving" : "common.save");
    area.disabled = !editor.canSave;
    area.readOnly = !editor.canSave;
    if (area.value !== editor.text) area.value = editor.text;
    copy.disabled = !editor.baseline;
    discard.disabled = editor.phase === "saving";
    discardClose.disabled = editor.phase === "saving";
    retry.hidden = !(editor.problem === "load" || editor.problem === "unknown" || editor.problem === "conflict");
    retry.disabled = editor.checking;
    latest.disabled = !editor.observed;
    let key = editor.phase === "loading" ? "common.loading" : editor.phase === "saving" ? "common.saving" : "editor.memory";
    if (editor.problem) key = `editor.problem.${editor.problem}`;
    if (editor.inputAtCapacity) key = "editor.inputCapacity";
    status.textContent = filesT(key);
    if (editor.checking) status.textContent += " " + filesT("editor.checking");
    else if (editor.checkFailed) status.textContent += " " + filesT("editor.checkFailed");
    else if (editor.observed) status.textContent += " " + filesT(editor.observed.matchesDraft ? "editor.observedMatch" : "editor.observedDifferent", editor.observed.versionId);
  };
  editor.onChange = paint;
  detach = () => { if (editor.onChange === paint) editor.onChange = null; };
  paint();
  area.oninput = () => { editor.change(area.value); paint(); };
  close.onclick = () => { if (options.active()) options.close(); };
  save.onclick = async () => {
    if (!options.active() || !editor.canSave) return;
    const result = await editor.save();
    if (result && options.active()) options.saved(result.version_id);
  };
  copy.onclick = async () => {
    // copyText also covers plain-http deployments, where navigator.clipboard
    // does not exist; only a write it could not make falls back to selection.
    const copied = await copyText(editor.text);
    if (!options.active()) return;
    if (copied) {
      status.textContent = filesT("files.deeplink.copied");
      return;
    }
    area.disabled = false; area.readOnly = !editor.canSave; area.select();
    status.textContent = filesT("editor.copyManually");
  };
  latest.onclick = () => {
    if (options.active() && editor.observed) options.latest({ ...editor.artifact, version_id: editor.observed.versionId, _exactVersion: true });
  };
  retry.onclick = () => {
    if (!options.active()) return;
    if (editor.problem === "load") void editor.load();
    else void editor.check();
  };
  discardClose.onclick = () => {
    if (!options.active() || editor.phase === "saving") return;
    if (typeof confirm !== "function" || !confirm(filesT("editor.discardCloseConfirm"))) return;
    if (artifactEditors.discard(editor)) options.close();
  };
  discard.onclick = () => {
    if (!options.active() || editor.phase === "saving") return;
    if (typeof confirm !== "function" || !confirm(filesT("editor.discardConfirm"))) return;
    if (artifactEditors.discard(editor)) options.reload();
  };
}
