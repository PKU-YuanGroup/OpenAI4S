/**
 * The example analysis, from the empty session of 示例项目 (ONB-4, owner
 * decision 37).
 *
 * Once any session existed the home page's "Run the example" card was gone,
 * and 示例项目 itself opened to the generic empty page. Its empty session now
 * offers the same card while the example has not been run (`GET
 * /example/session` `seeded: false`), starts it through the same request the
 * home page sends (`sessions/dashboard.ts` imports these two), and opens the
 * example's session as soon as it exists so its cells can be watched.
 *
 * Kept out of `sessions/dashboard.ts` on purpose: importing the dashboard into
 * the transcript changed the module graph's evaluation order.
 */

import { isReady } from "../../compat/stub";
import { LANG, t } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { api, apiErrorText } from "../sessions/api";
import { el } from "./dom";

/** The example analysis's project (`server/example_session.py`). */
export const EXAMPLE_PROJECT = "proj_example";

/** How often a started example is asked whether its session exists yet. */
const POLL_MS = 1500;

export type ExampleStatus = {
  running?: boolean;
  error?: string | null;
  seeded?: boolean;
  frame_id?: string | null;
  project_id?: string;
  result?: { cells?: number; failed?: number; failed_steps?: number } | null;
};

/** The example analysis's state (`GET /example/session`). */
export function readExampleSeed(): Promise<ExampleStatus> {
  return api("/example/session") as Promise<ExampleStatus>;
}

/**
 * Start the example analysis (`POST /example/session`). `lang`: the example's
 * own prompt and summary follow the UI language.
 */
export function requestExampleSeed(): Promise<ExampleStatus> {
  return api("/example/session", {
    method: "POST",
    body: JSON.stringify({ confirm: true, lang: LANG }),
  }) as Promise<ExampleStatus>;
}

/**
 * Fill `slot` (in the empty session's welcome) with the "Run the example" card
 * once the server says the example has not been run. Nothing is shown when it
 * has, or when the state cannot be read.
 */
export function mountExampleCard(slot: HTMLElement): void {
  const frame = currentId.value;
  const live = () => currentId.value === frame && slot.isConnected !== false;
  // The home page's card, with its styles (style.css `.dash-example`).
  const box = el("div", "dash-example es-example");
  const button = el("button", "solid-btn", t("dash.example.cta")) as HTMLButtonElement;
  button.type = "button";
  const note = el("div", "dash-example-hint", t("dash.example.hint"));
  box.appendChild(button);
  box.appendChild(note);
  const failed = (message: string): void => {
    button.disabled = false;
    button.textContent = t("dash.example.cta");
    note.textContent = t("dash.example.failed") + message;
  };
  const follow = (st: ExampleStatus): void => {
    if (!live()) return;
    if (st.frame_id) {
      const open = (globalThis as Record<string, unknown>).openConversation;
      if (isReady(open)) {
        void (open as (fid: string, pid: string) => unknown)(String(st.frame_id), EXAMPLE_PROJECT);
      }
      return;
    }
    if (!st.running) {
      failed(String(st.error || ""));
      return;
    }
    button.disabled = true;
    button.textContent = t("dash.example.running");
    setTimeout(() => {
      if (live()) readExampleSeed().then(follow, () => failed(""));
    }, POLL_MS);
  };
  button.onclick = () => {
    button.disabled = true;
    button.textContent = t("dash.example.running");
    requestExampleSeed().then(follow, (error: unknown) => failed(apiErrorText(error)));
  };
  readExampleSeed()
    .then((st) => {
      if (!live() || st.seeded) return;
      slot.appendChild(box);
      // Started from another tab or the home page: follow it here too.
      if (st.running) follow(st);
      else if (st.error) note.textContent = t("dash.example.failed") + st.error;
    })
    .catch(() => undefined);
}
