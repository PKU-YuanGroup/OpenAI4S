/**
 * Who is signed in, in team mode. One signal, written once `/auth/me` has
 * answered (team.ts `probeTeamAuth`), so a view can ask "is this an operator"
 * without importing the team chrome or sending a request it knows will 403.
 *
 * `null` means single-user mode (or the probe has not answered yet): every
 * helper then gives the single-user answer.
 */

import { signal } from "@preact/signals";

export type TeamUser = {
  id?: string;
  username?: string;
  display_name?: string | null;
  role?: string;
  kind?: string;
  disabled?: boolean;
};

export const teamUser = signal<TeamUser | null>(null);

/** `GET /files` listed at least one data root: the Team files panel has something to show. */
export const teamFilesAvailable = signal(false);

/** An admin account, or the loopback CLI's service identity. */
export function isTeamAdmin(): boolean {
  const user = teamUser.value;
  return user != null && (user.role === "admin" || user.kind === "service");
}

/** Signed in to a team server without operator rights. */
export function isTeamMember(): boolean {
  return teamUser.value != null && !isTeamAdmin();
}
