import { LANG, tOptional } from "../../i18n/runtime";

/**
 * M-02 copy. New dashboard attention strings live here so we do not
 * rewrite the generated F-07 `i18n/en.ts` / `zh.ts` extract.
 */
const COPY: Record<"zh" | "en", Record<string, string>> = {
  zh: {
    "attention.title": "需要处理",
    "attention.empty": "没有需要处理的事项",
    "attention.untitled": "未命名会话",
    "attention.kind.running": "运行中",
    "attention.kind.queued": "排队中",
    "attention.kind.approval": "待批准",
    "attention.kind.recovery": "可恢复失败",
    "attention.kind.blocked": "只读",
    "attention.kind.compute": "远程计算",
    "attention.hint.watch": "查看进度",
    "attention.hint.approve": "去批准",
    "attention.hint.restore": "去恢复",
    "attention.hint.retry": "去重试",
    "attention.hint.inspect": "去查看",
    "attention.hint.queue": "排队第 {0} 位",
    "attention.meta.project": "{0}",
    "attention.severity.high": "高",
    "attention.severity.medium": "中",
    "attention.severity.low": "低",
  },
  en: {
    "attention.title": "Needs attention",
    "attention.empty": "Nothing needs attention",
    "attention.untitled": "Untitled session",
    "attention.kind.running": "Running",
    "attention.kind.queued": "Queued",
    "attention.kind.approval": "Pending approval",
    "attention.kind.recovery": "Recoverable failure",
    "attention.kind.blocked": "View only",
    "attention.kind.compute": "Remote compute",
    "attention.hint.watch": "Watch",
    "attention.hint.approve": "Approve",
    "attention.hint.restore": "Restore",
    "attention.hint.retry": "Retry",
    "attention.hint.inspect": "Inspect",
    "attention.hint.queue": "Queue position {0}",
    "attention.meta.project": "{0}",
    "attention.severity.high": "High",
    "attention.severity.medium": "Medium",
    "attention.severity.low": "Low",
  },
};

export function attentionT(key: string, ...args: unknown[]): string {
  const fromDict = tOptional(key);
  const table = COPY[LANG] || COPY.en;
  let s = fromDict != null ? fromDict : table[key] || COPY.en[key] || key;
  if (args.length) {
    s = String(s).replace(/\{(\d+)\}/g, (m, i) =>
      args[+i] != null ? String(args[+i]) : m,
    );
  }
  return s;
}
