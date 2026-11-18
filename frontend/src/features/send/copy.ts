import { LANG } from "../../i18n/runtime";

export type SendCopyKey = "send" | "queue" | "reviewing" | "reviewFinding";

/** Labels the generated F-07 dictionaries do not carry (app.js hard-coded them in English). */
const COPY: Record<"en" | "zh", Record<SendCopyKey, string>> = {
  en: {
    send: "Send",
    queue: "Queue: runs after the current turn",
    reviewing: "Reviewing",
    reviewFinding: "Review finding",
  },
  zh: {
    send: "发送",
    queue: "加入队列：当前任务结束后运行",
    reviewing: "正在审阅",
    reviewFinding: "审阅发现",
  },
};

export function sendCopy(key: SendCopyKey): string {
  return COPY[LANG === "en" ? "en" : "zh"][key];
}
