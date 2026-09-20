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
