import { LANG } from "../../i18n/runtime";

export type SendCopyKey =
  | "send"
  | "queue"
  | "queuedBadge"
  | "reviewing"
  | "reviewFinding"
  | "planDesc"
  | "exploreDesc"
  | "modeChipTitle"
  | "technicalDetails"
  | "hideTechnicalDetails"
  | "subagentTask";

/** Labels the generated F-07 dictionaries do not carry (app.js hard-coded them in English). */
const COPY: Record<"en" | "zh", Record<SendCopyKey, string>> = {
  en: {
    send: "Send",
    queue: "Queue: runs after the current turn",
    queuedBadge: "Queued · not sent yet",
    reviewing: "Reviewing",
    reviewFinding: "Review finding",
    planDesc: "Drafts a step-by-step plan for you to review; nothing runs until you approve it.",
    exploreDesc:
      "Works through the whole investigation on its own, without stopping to ask, and ends with a written report.",
    modeChipTitle: "{0} is on for your next messages: {1} Click to turn it off.",
    technicalDetails: "Technical details",
    hideTechnicalDetails: "Hide technical details",
    subagentTask: "Sub-agent · {0}",
  },
  zh: {
    send: "发送",
    queue: "加入队列：当前任务结束后运行",
    queuedBadge: "排队中 · 尚未发送",
    reviewing: "正在审阅",
    reviewFinding: "审阅发现",
    planDesc: "先写出分步计划给你审阅，你批准后才开始执行。",
    exploreDesc: "不停下来询问，自主连续完成整个研究，最后写出报告。",
    modeChipTitle: "{0}已开启，作用于接下来发送的消息：{1}点击关闭。",
    technicalDetails: "技术细节",
    hideTechnicalDetails: "收起技术细节",
    subagentTask: "子代理 · {0}",
  },
};

export function sendCopy(key: SendCopyKey, ...args: string[]): string {
  let text = COPY[LANG === "en" ? "en" : "zh"][key];
  args.forEach((value, index) => {
    text = text.split("{" + index + "}").join(value);
  });
  return text;
}
