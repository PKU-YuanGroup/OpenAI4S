/** Late bindings so dashboard ↔ conversation do not import each other. */

export type OpenConversation = (fid: string, pid?: string | null) => Promise<void> | void;
/** The project override exists so an empty project creates its conversation
 *  in the project just opened, not in whichever one is active by the time the
 *  shared creation promise settles. */
export type NewSession = (projectId?: string) => Promise<void> | void;

export const binds = {
  openConversation: (async () => {}) as OpenConversation,
  newSession: (async () => {}) as NewSession,
  loadDashboard: (async () => {}) as () => Promise<void> | void,
  startDashPoll: (() => {}) as () => void,
  stopDashPoll: (() => {}) as () => void,
  renderDashProjects: (() => {}) as () => void,
  renderProjMenu: (() => {}) as () => void,
  renderSessions: (() => {}) as () => void,
  showDashboard: (() => {}) as () => void,
  showWorkspace: (() => {}) as () => void,
};
