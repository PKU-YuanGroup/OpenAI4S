/**
 * Team mode in Settings (TEAM-23): what a member may not change, and what the
 * page says about it.
 *
 * A member's Settings used to offer the admin's controls -- the full "Add
 * model / API" form with a key field, Restore defaults, global permission
 * rules -- and every one of them answered 403 `admin_only` with nothing on
 * screen. Who is signed in comes from `/auth/me` (`team-identity.ts`); the
 * server stays the authority, so a refusal that still arrives is worded here
 * too, for every tab.
 */
import { copyLookup } from "../../i18n/copy";
import { isTeamMember } from "../chrome/team-identity";

/** Feature-local: the generated `i18n/en.ts` / `zh.ts` are byte-checked extracts. */
export const roleT = copyLookup({
  zh: {
    "role.adminOnly": "只有管理员可以更改这项设置，请联系管理员。",
    "role.models.member":
      "这台服务器的模型由管理员配置，对话使用标着「当前」的那个。想用你自己的 key：点右上角你的名字 →「我的模型 key」。",
    "role.perm.globalMember":
      "全局规则由管理员设置，这里只能查看；你可以为本对话或本项目添加自己的规则。",
    "role.perm.resetMember": "只有管理员可以把全局规则恢复为默认值。",
    "role.network.member": "这台服务器的联网和搜索设置由管理员管理，这里只能查看。",
    "role.memory.toggleMember": "只有管理员可以为这台服务器开关记忆。",
    "role.memory.scopeMember": "所有项目共用的记忆由管理员设置；打开一个项目，就能为它保存记忆。",
  },
  en: {
    "role.adminOnly": "Only an administrator can change this setting. Ask your administrator.",
    "role.models.member":
      "The models on this server are set up by an administrator; turns use the one marked Active. To use your own key, open your name at the top right → My model keys.",
    "role.perm.globalMember":
      "Global rules are set by an administrator and are read-only here; you can add your own rules for this conversation or this project.",
    "role.perm.resetMember": "Only an administrator can restore the global rules to their defaults.",
    "role.network.member":
      "Networking and search on this server are managed by an administrator; this page shows them read-only.",
    "role.memory.toggleMember": "Only an administrator can turn memory on or off for this server.",
    "role.memory.scopeMember":
      "Memory for all projects is set by an administrator; open a project to save memory for it.",
  },
});

/** A refusal by role: the server's 403 `admin_only`, whoever raised it. */
export function isAdminOnlyRefusal(error: unknown): boolean {
  const code = (error as { code?: unknown } | null)?.code;
  return code === "admin_only";
}

/**
 * The signed-in user may read this server's settings but not change them: a
 * team member (or guest). `false` in single-user mode and for an admin.
 * Reads a signal, so a view that calls it in render follows `/auth/me`.
 */
export function settingsReadOnly(): boolean {
  return isTeamMember();
}
