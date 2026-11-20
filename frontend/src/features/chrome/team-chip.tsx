/**
 * The signed-in user and the Team files entry, as the Shell renders them:
 * beside the theme and settings buttons on the dashboard, and in the sidebar
 * foot of the workspace. Nothing in single-user mode with no data roots.
 *
 * These used to be created by `ensureTeamDom()` and appended to `<body>`,
 * after the Shell, where the dashboard covered them: nobody could see who
 * was signed in, sign out, or reach the admin panel.
 */

import { iconSvg } from "../icons/paths";
import { openTeamFilesPanel, openUserMenu, teamUserName } from "./team";
import { teamRoleLabel, teamT } from "./team-copy";
import { teamFilesAvailable, teamUser } from "./team-identity";

/** Drawn here: `paintIcons()` ran before `/auth/me` answered. */
function Glyph({ name, size }: { name: string; size: number }) {
  return <span class="ic" dangerouslySetInnerHTML={{ __html: iconSvg(name, size) }} />;
}

export function TeamChip({ placement }: { placement: "dash" | "side" }) {
  const user = teamUser.value;
  const files = teamFilesAvailable.value;
  if (!user && !files) return null;
  const side = placement === "side";
  const size = side ? 20 : 16;
  const button = (side ? "icon-ghost" : "outline-btn") + " team-chip";
  const label = user ? teamT("team.user.label", teamUserName(user), teamRoleLabel(user.role)) : "";
  return (
    <>
      {files ? (
        <button
          id={side ? "team-files-btn" : "team-files-dash"}
          class={button}
          type="button"
          title={teamT("team.files")}
          aria-label={teamT("team.files")}
          onClick={() => openTeamFilesPanel()}
        >
          <Glyph name="folder" size={size} />
          {side ? null : <span>{teamT("team.files")}</span>}
        </button>
      ) : null}
      {user ? (
        <button
          id={side ? "team-user-side" : "team-user"}
          class={button + " team-user"}
          type="button"
          title={label}
          aria-label={label}
          aria-haspopup="menu"
          onClick={(event) => openUserMenu(event.currentTarget as HTMLElement)}
        >
          <Glyph name="users" size={size} />
          {side ? null : (
            <span class="team-user-name">
              {teamUserName(user)}
              {user.role === "admin" ? " · " + teamRoleLabel(user.role) : ""}
            </span>
          )}
        </button>
      ) : null}
    </>
  );
}
