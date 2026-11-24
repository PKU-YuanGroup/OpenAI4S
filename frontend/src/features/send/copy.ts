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
  | "subagentTask"
  | "subagentSteps"
  | "subagentStep"
  | "stepQuery";

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
    subagentSteps: "{0} steps inside",
    subagentStep: "1 step inside",
    stepQuery: "{0} “{1}”",
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
    subagentTask: "子智能体 · {0}",
    subagentSteps: "内含 {0} 个步骤",
    subagentStep: "内含 1 个步骤",
    stepQuery: "{0}：“{1}”",
  },
};

export function sendCopy(key: SendCopyKey, ...args: string[]): string {
  let text = COPY[LANG === "en" ? "en" : "zh"][key];
  args.forEach((value, index) => {
    text = text.split("{" + index + "}").join(value);
  });
  return text;
}

/**
 * CHAT-6: the words of a step's structured summary
 * (`host_dispatch._step_end_view` `summary_parts`). Each part names one of
 * these keys; `{n}`, `{name}`, `{reason}`, `{from}`, `{to}` and `{total}` are
 * its values. A key missing here makes the card fall back to the English line.
 */
export type SummaryCopyKey =
  | "failed"
  | "failedReason"
  | "lines"
  | "pastEnd"
  | "noMatch"
  | "active"
  | "notInstalled"
  | "rolledBack"
  | "charsRead"
  | "loaded"
  | "networkLimited"
  | "helpersLimited"
  | "switched"
  | "recommend"
  | "missing"
  | "installed"
  | "ready"
  | "inspected"
  | "restored"
  | "saved"
  | "done"
  | "folded"
  | "delegate.completed"
  | "delegate.partial"
  | "delegate.blocked"
  | "delegate.failed"
  | "delegate.stopped"
  | "delegate.started"
  | "delegate.malformed result";

const SUMMARY_COPY: Record<"en" | "zh", Record<SummaryCopyKey, string>> = {
  en: {
    failed: "failed",
    failedReason: "failed: {reason}",
    lines: "lines {from}–{to} of {total}",
    pastEnd: "past the end ({total} lines)",
    noMatch: "no match",
    active: "active",
    notInstalled: "not installed",
    rolledBack: "rolled back",
    charsRead: "{n} chars read",
    loaded: "loaded",
    networkLimited: "network limited to {n}",
    helpersLimited: "helpers limited to {n}",
    switched: "→ {name}",
    recommend: "use {name}",
    missing: "{n} missing",
    installed: "installed {name}",
    ready: "ready",
    inspected: "inspected",
    restored: "restored as new version",
    saved: "saved",
    done: "done",
    folded: "folded",
    "delegate.completed": "completed",
    "delegate.partial": "partial",
    "delegate.blocked": "blocked",
    "delegate.failed": "failed",
    "delegate.stopped": "stopped",
    "delegate.started": "started",
    "delegate.malformed result": "malformed result",
  },
  zh: {
    failed: "失败",
    failedReason: "失败：{reason}",
    lines: "第 {from}–{to} 行，共 {total} 行",
    pastEnd: "已超出文件末尾（共 {total} 行）",
    noMatch: "没有匹配",
    active: "已启用",
    notInstalled: "未安装",
    rolledBack: "已回滚",
    charsRead: "已读取 {n} 个字符",
    loaded: "已加载",
    networkLimited: "网络限于 {n}",
    helpersLimited: "辅助代码联网限于 {n}",
    switched: "→ {name}",
    recommend: "建议使用 {name}",
    missing: "缺少 {n} 个软件包",
    installed: "已安装 {name}",
    ready: "就绪",
    inspected: "已查看",
    restored: "已恢复为新版本",
    saved: "已保存",
    done: "完成",
    folded: "结构预测完成",
    "delegate.completed": "已完成",
    "delegate.partial": "部分完成",
    "delegate.blocked": "受阻",
    "delegate.failed": "失败",
    "delegate.stopped": "已停止",
    "delegate.started": "已启动",
    "delegate.malformed result": "结果格式有误",
  },
};

/** `{n} {unit}` per language: English singular and plural, Chinese measure word. */
const SUMMARY_UNITS: Record<"en" | "zh", Record<string, readonly [string, string]>> = {
  en: {
    result: ["result", "results"],
    char: ["char", "chars"],
    byte: ["byte", "bytes"],
    database: ["database", "databases"],
    change: ["change", "changes"],
    item: ["item", "items"],
    step: ["step", "steps"],
    suggestion: ["suggestion", "suggestions"],
    version: ["version", "versions"],
    line: ["line", "lines"],
    env: ["env", "envs"],
    domain: ["domain", "domains"],
    child: ["child", "children"],
  },
  zh: {
    result: ["条结果", "条结果"],
    char: ["个字符", "个字符"],
    byte: ["字节", "字节"],
    database: ["个数据库", "个数据库"],
    change: ["处修改", "处修改"],
    item: ["项", "项"],
    step: ["步", "步"],
    suggestion: ["条建议", "条建议"],
    version: ["个版本", "个版本"],
    line: ["行", "行"],
    env: ["个运行环境", "个运行环境"],
    domain: ["个域名", "个域名"],
    child: ["个子智能体", "个子智能体"],
  },
};

function summaryLang(): "en" | "zh" {
  return LANG === "en" ? "en" : "zh";
}

/** A summary template in the UI language, or null when the key is not one of ours. */
export function summaryCopy(key: string, values: Record<string, string> = {}): string | null {
  const table = SUMMARY_COPY[summaryLang()] as Record<string, string>;
  if (!Object.prototype.hasOwnProperty.call(table, key)) return null;
  return table[key]!.replace(/\{(\w+)\}/g, (match, name: string) => values[name] ?? match);
}

/** "6 results" / "6 条结果"; null for a unit this table does not know. */
export function summaryCount(n: number, unit: string): string | null {
  const lang = summaryLang();
  const table = SUMMARY_UNITS[lang];
  if (!Object.prototype.hasOwnProperty.call(table, unit)) return null;
  const [one, many] = table[unit]!;
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}
