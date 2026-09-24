import { LANG } from "../../i18n/runtime";

export type SendCopyKey = "send" | "reviewing" | "reviewFinding";

/** Labels the generated F-07 dictionaries do not carry (app.js hard-coded them in English). */
const COPY: Record<"en" | "zh", Record<SendCopyKey, string>> = {
  en: { send: "Send", reviewing: "Reviewing", reviewFinding: "Review finding" },
  zh: { send: "发送", reviewing: "正在审阅", reviewFinding: "审阅发现" },
};

export function sendCopy(key: SendCopyKey): string {
  return COPY[LANG === "en" ? "en" : "zh"][key];
}
