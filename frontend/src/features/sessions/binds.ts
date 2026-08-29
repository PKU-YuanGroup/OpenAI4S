/** Late bindings so dashboard ↔ conversation do not import each other. */

export type OpenConversation = (fid: string, pid?: string | null) => Promise<void> | void;

export const binds = {
  openConversation: (async () => {}) as OpenConversation,
  newSession: (async () => {}) as () => Promise<void> | void,
  loadDashboard: (async () => {}) as () => Promise<void> | void,
  startDashPoll: (() => {}) as () => void,
  stopDashPoll: (() => {}) as () => void,
  renderDashProjects: (() => {}) as () => void,
  renderSessions: (() => {}) as () => void,
  showDashboard: (() => {}) as () => void,
  showWorkspace: (() => {}) as () => void,
};
