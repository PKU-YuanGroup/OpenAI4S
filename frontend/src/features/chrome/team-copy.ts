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
    "team.menu.password": "修改密码",
    "team.menu.keys": "我的模型 key",
    "team.close": "关闭",
    "team.cancel": "取消",
    "team.save": "保存",
    "team.loading": "正在读取…",
    "team.password.title": "修改密码",
    "team.password.current": "当前密码",
    "team.password.new": "新密码",
    "team.password.confirm": "再次输入新密码",
    "team.password.note": "至少 8 个字符。修改后，你在其他浏览器上的登录会被注销。",
    "team.password.mismatch": "两次输入的新密码不一致。",
    "team.password.wrong": "当前密码不正确。",
    "team.password.weak": "新密码至少需要 8 个字符。",
    "team.password.rateLimited": "尝试次数过多，请等一分钟再试。",
    "team.password.failed": "修改失败（HTTP {0}）。",
    "team.password.done": "密码已修改。",
    "team.keys.title": "我的模型 key",
    "team.keys.note":
      "你自己的 key 只会经 https 发往该服务商的官方端点；会话的模型走其他端点（代理、本地模型）时，仍使用团队的 key。保存后不会再显示 key 的内容。",
    "team.keys.none": "还没有设置自己的 key，正在使用团队的 key。",
    "team.keys.set": "已设置 · {0}",
    "team.keys.remove": "移除",
    "team.keys.provider": "服务商（provider）",
    "team.keys.key": "API key",
    "team.keys.save": "保存 key",
    "team.keys.saved": "已保存 {0} 的 key。",
    "team.keys.needProvider": "请填写服务商。",
    "team.keys.needKey": "请填写 key。",
    "team.keys.failed": "操作失败：{0}",
    "team.keys.loadFailed": "无法读取你的 key。",
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
    "team.menu.password": "Change password",
    "team.menu.keys": "My model keys",
    "team.close": "Close",
    "team.cancel": "Cancel",
    "team.save": "Save",
    "team.loading": "Loading…",
    "team.password.title": "Change password",
    "team.password.current": "Current password",
    "team.password.new": "New password",
    "team.password.confirm": "New password again",
    "team.password.note": "At least 8 characters. Your other signed-in browsers are signed out.",
    "team.password.mismatch": "The two new passwords do not match.",
    "team.password.wrong": "The current password is not right.",
    "team.password.weak": "The new password needs at least 8 characters.",
    "team.password.rateLimited": "Too many attempts. Wait a minute, then try again.",
    "team.password.failed": "Could not change the password (HTTP {0}).",
    "team.password.done": "Password changed.",
    "team.keys.title": "My model keys",
    "team.keys.note":
      "Your own key is sent only to that provider's own endpoint, over https; when a session's model goes anywhere else (a proxy, a local model) the team's key is used. A saved key is never shown again.",
    "team.keys.none": "No key of your own yet; the team's key is used.",
    "team.keys.set": "set · {0}",
    "team.keys.remove": "Remove",
    "team.keys.provider": "Provider",
    "team.keys.key": "API key",
    "team.keys.save": "Save key",
    "team.keys.saved": "Saved your {0} key.",
    "team.keys.needProvider": "Enter a provider.",
    "team.keys.needKey": "Enter a key.",
    "team.keys.failed": "That did not work: {0}",
    "team.keys.loadFailed": "Could not read your keys.",
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
