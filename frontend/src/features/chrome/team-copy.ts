/**
 * Team-mode copy (user menu, admin panel, account dialogs), zh + en.
 * Feature-local: the generated `i18n/en.ts` / `zh.ts` are byte-checked
 * extracts and must not grow.
 */

import { copyLookup } from "../../i18n/copy";

export const teamT = copyLookup({
  zh: {
    "team.user.label": "已登录：{0}（{1}）",
    "team.role.admin": "管理员",
    "team.role.member": "成员",
    "team.role.guest": "访客",
    "team.menu.admin": "团队管理",
    "team.menu.signOut": "退出登录",
    "team.files": "团队文件",
    "team.missing.sub":
      "这个会话不存在、已被删除，或你没有访问权限。如果是同事发来的链接，请让会话所有者把它设为项目可见，并确认你是该项目的成员。",
  },
  en: {
    "team.user.label": "Signed in as {0} ({1})",
    "team.role.admin": "admin",
    "team.role.member": "member",
    "team.role.guest": "guest",
    "team.menu.admin": "Team admin",
    "team.menu.signOut": "Sign out",
    "team.files": "Team files",
    "team.missing.sub":
      "This session does not exist, was deleted, or you do not have access to it. If a colleague sent the link, ask the session's owner to make it visible to the project, and check that you are a member of that project.",
  },
});

/** The role in the interface language; an unknown role is shown as stored. */
export function teamRoleLabel(role: string | undefined): string {
  return role === "admin" || role === "member" || role === "guest"
    ? teamT("team.role." + role)
    : String(role || "");
}
