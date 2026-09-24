import { LANG } from "../../i18n/runtime";

/** Feature-owned copy; the frozen legacy dictionaries remain generated. */
export function sessionCopy(key: "sessionsError" | "foldersError" | "retry"): string {
  const copy = LANG === "en" ? {
    sessionsError: "Could not load sessions. Retry reading the list.",
    foldersError: "Could not load folders.",
    retry: "Retry reading",
  } : {
    sessionsError: "无法读取会话列表，可重试读取。",
    foldersError: "无法读取文件夹。",
    retry: "重试读取",
  };
  return copy[key];
}

/** The composer menus' context-usage card and their review and on/off hints. */
export function composerCopy(key: "usage" | "reviewing" | "on" | "off", ...args: Array<string | number>): string {
  const copy = LANG === "en" ? {
    usage: "Input {0} · Output {1} · Reviewer {2}",
    reviewing: "Reviewing",
    on: "On",
    off: "Off",
  } : {
    usage: "输入 {0} · 输出 {1} · 审阅 {2}",
    reviewing: "正在审阅",
    on: "开",
    off: "关",
  };
  return copy[key].replace(/\{(\d+)\}/g, (_match, index: string) => String(args[Number(index)] ?? ""));
}

/** An action that failed before it could report anything itself; `detail` is the error's text. */
export function actionFailedCopy(detail: string): string {
  return (LANG === "en" ? "Could not complete that action: " : "操作未能完成：") + detail;
}

/** Share dialog labels the generated dictionaries do not carry. */
export function shareCopy(key: "linkLabel" | "updateDesc" | "revokeDesc"): string {
  const copy = LANG === "en" ? {
    linkLabel: "Read-only link",
    updateDesc: "The link stays the same; its content becomes this session's latest state.",
    revokeDesc: "The link stops working immediately.",
  } : {
    linkLabel: "只读链接",
    updateDesc: "链接不变，内容换成当前会话的最新状态。",
    revokeDesc: "撤销后立即失效。",
  };
  return copy[key];
}
