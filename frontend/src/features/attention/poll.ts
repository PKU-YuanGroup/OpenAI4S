import { ATTENTION_POLL_MS } from "./types";

export type AttentionPollFlags = {
  dashboardHidden: boolean;
  documentHidden: boolean;
};

/**
 * Fetch only when the dashboard is on screen and the page is visible.
 * Matches `refreshDashRunning` in `features/sessions/dashboard.ts`.
 */
export function shouldFetchAttention(flags: AttentionPollFlags): boolean {
  return !flags.dashboardHidden && !flags.documentHidden;
}

type PollDoc = {
  hidden?: boolean;
  getElementById?: (
    id: string,
  ) => { classList: { contains: (name: string) => boolean } } | null;
};

function defaultPollDoc(): PollDoc | null {
  return typeof document !== "undefined" ? document : null;
}

export function readPollFlags(doc: PollDoc | null = defaultPollDoc()): AttentionPollFlags {
  if (!doc) {
    return { dashboardHidden: true, documentHidden: true };
  }
  const dash = doc.getElementById ? doc.getElementById("dashboard") : null;
  return {
    dashboardHidden: !dash || dash.classList.contains("hidden"),
    documentHidden: typeof doc.hidden === "boolean" ? !!doc.hidden : false,
  };
}

export { ATTENTION_POLL_MS };
