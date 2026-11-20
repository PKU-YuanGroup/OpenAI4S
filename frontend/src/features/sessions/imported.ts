/**
 * An imported session says it is a read-only copy (M-66).
 *
 * An imported package is quarantined and view-only until the user restarts it
 * fresh, but its composer looked usable and the first sign of the quarantine
 * was a refusal in English after an action. While the open session is such a
 * copy, a banner above the conversation says so and offers the way out (the
 * confirmed Restart fresh in the Recovery panel), and the composer is
 * disabled with a placeholder that says why.
 */

import { effect } from "@preact/signals";

import { copyLookup } from "../../i18n/copy";
import { currentId } from "../../stores/session";
import { running } from "../../stores/stream";
import { recoveryActions } from "../../stores/timeline";
import { focusDock } from "../attention/navigate";
import { ATTENTION_PANE } from "../attention/types";
import { API } from "./api";
import { renderEmptySession } from "../messages/list";
import { $, el, enableComposer } from "./dom";
import { iconEl } from "./icon";
import { callLane } from "./lane";

export const importedT = copyLookup({
  en: {
    title: "Read-only copy of an imported session",
    body: "Its conversation, code and files are shown as they were sent; nothing from the package runs. To continue here, restart it fresh: a new kernel starts and no imported code is replayed.",
    action: "Restart fresh…",
    placeholder: "Read-only imported session — restart it fresh to continue",
  },
  zh: {
    title: "这是导入会话的只读副本",
    body: "对话、代码和文件按发送时的样子展示，包里的任何代码都不会运行。要在这里继续，请「全新重启」：会启动一个新内核，不会重放任何导入的代码。",
    action: "全新重启…",
    placeholder: "只读的导入会话——全新重启后才能继续",
  },
});

type Quarantine = { view_only?: unknown; trust_state?: unknown; root_frame_id?: unknown };

export function isQuarantinedCopy(status: Quarantine | null | undefined): boolean {
  return !!status && status.view_only === true && status.trust_state === "quarantined";
}

let disabledFor: string | null = null;
let reads = 0;

/** Show or remove the banner for the open session, and the composer with it. */
export function renderImportedBanner(fid: string, quarantined: boolean): void {
  const composer = $("#composer") as HTMLTextAreaElement | null;
  let banner = $("#imported-banner");
  if (!quarantined || !fid || fid !== currentId.value) {
    const wasUp = !!banner;
    banner?.remove();
    if (disabledFor !== null) {
      disabledFor = null;
      enableComposer(!running.value);
      setSendDisabled(false);
    }
    // A restarted copy is an ordinary empty session again.
    if (wasUp) repaintEmptySession();
    return;
  }
  if (!banner) {
    const messages = $("#messages");
    if (!messages || !messages.parentNode) return;
    banner = el("div", "compute-lost imported-banner");
    banner.id = "imported-banner";
    banner.setAttribute("role", "status");
    messages.parentNode.insertBefore(banner, messages);
  }
  banner.innerHTML = "";
  banner.appendChild(iconEl("lock", 15));
  const text = el("div", "cl-text");
  text.appendChild(el("strong", null, importedT("title")));
  text.appendChild(el("div", "cl-body", importedT("body")));
  banner.appendChild(text);
  const action = el("button", "cl-dismiss", importedT("action"));
  action.onclick = () => {
    // The confirmed restart lives in the Recovery panel of the timeline dock.
    callLane("setActiveTab", ATTENTION_PANE);
    let tries = 0;
    const focus = (): void => {
      if (focusDock("recovery") || ++tries > 8) return;
      setTimeout(focus, 120);
    };
    focus();
  };
  banner.appendChild(action);
  if (composer) {
    composer.disabled = true;
    composer.placeholder = importedT("placeholder");
    disabledFor = fid;
  }
  // M-66: Send looked usable beside a disabled composer.
  setSendDisabled(true);
  repaintEmptySession();
}

function setSendDisabled(on: boolean): void {
  const send = $("#send-btn") as HTMLButtonElement | null;
  if (send) send.disabled = on;
}

/** The empty-session block follows the banner: starters for a session, a note for a read-only copy. */
function repaintEmptySession(): void {
  const empty = $("#messages .empty-session");
  if (!empty) return;
  const host = empty.parentNode;
  empty.remove();
  renderEmptySession(host as ParentNode | null);
}

/** One read of the session's kernel status, which carries the quarantine. */
export async function checkImportedSession(fid: string): Promise<void> {
  const mine = ++reads;
  let status: Quarantine | null = null;
  try {
    const response = await fetch(`${API}/frames/${encodeURIComponent(fid)}/kernel`);
    if (response.ok) status = (await response.json()) as Quarantine;
  } catch {
    status = null;
  }
  if (mine !== reads || fid !== currentId.value) return;
  renderImportedBanner(fid, isQuarantinedCopy(status));
}

/** Follow the open session; a Restart fresh that lifts the quarantine removes the banner. */
export function watchImportedSession(): () => void {
  const stopOpen = effect(() => {
    const fid = currentId.value;
    if (fid) void checkImportedSession(fid);
    else renderImportedBanner("", false);
  });
  const stopRecovery = effect(() => {
    const actions = recoveryActions.value as Quarantine | null;
    const fid = currentId.value;
    if (!fid || !actions || actions.root_frame_id !== fid) return;
    renderImportedBanner(fid, isQuarantinedCopy(actions));
  });
  return () => {
    stopOpen();
    stopRecovery();
  };
}
