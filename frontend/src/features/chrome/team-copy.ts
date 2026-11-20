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
  },
  en: {
    "team.user.label": "Signed in as {0} ({1})",
    "team.role.admin": "admin",
    "team.role.member": "member",
    "team.role.guest": "guest",
    "team.menu.admin": "Team admin",
    "team.menu.signOut": "Sign out",
    "team.files": "Team files",
  },
});

/** The role in the interface language; an unknown role is shown as stored. */
export function teamRoleLabel(role: string | undefined): string {
  return role === "admin" || role === "member" || role === "guest"
    ? teamT("team.role." + role)
    : String(role || "");
}
