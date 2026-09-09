import { LANG, t } from "../../i18n/runtime";

/** History-recovery copy belongs to the current UI, outside frozen extracts. */
const COPY: Record<"en" | "zh", Record<string, string>> = {
  "en": {
    "history.renderFailed": "History could not be displayed.",
    "history.submissionPending": "A submitted message is still unconfirmed; existing history is preserved.",
    "history.loading": "Loading history…",
    "history.partial": "History is only partly restored.",
    "history.error": "History could not be loaded.",
    "history.retry": "Retry history reads",
    "history.authFailed": "Authentication or access denied.",
    "history.serviceUnavailable": "History service unavailable.",
    "history.invalidResponse": "Invalid history response.",
    "history.networkFailed": "History request interrupted.",
    "history.livePending": "Live messages are preserved; history will align after the turn stops.",
    "history.part.messages": "Messages",
    "history.part.steps": "Steps",
    "history.part.runState": "Run state"
  },
  "zh": {
    "history.renderFailed": "历史暂时无法显示。",
    "history.submissionPending": "有一条已提交消息尚未确认；已保留当前历史。",
    "history.loading": "正在读取历史…",
    "history.partial": "历史尚未完整恢复。",
    "history.error": "历史读取失败。",
    "history.retry": "重试读取历史",
    "history.authFailed": "认证失败或无权访问。",
    "history.serviceUnavailable": "历史服务暂不可用。",
    "history.invalidResponse": "历史返回格式错误。",
    "history.networkFailed": "历史请求中断。",
    "history.livePending": "已保留实时消息；任务停止后将重新对齐历史。",
    "history.part.messages": "消息",
    "history.part.steps": "步骤",
    "history.part.runState": "运行状态"
  }
};

export function historyT(key: string, ...args: readonly unknown[]): string {
  const value = COPY[LANG][key];
  if (value === undefined) return t(key, ...args);
  return value.replace(/\{(\d+)\}/g, (_match, index: string) => String(args[Number(index)] ?? ""));
}
