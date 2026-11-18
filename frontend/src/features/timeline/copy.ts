/**
 * C2 copy for the Timeline's remote-task panel.
 *
 * Local rather than in `i18n/en.ts` / `i18n/zh.ts`: those dictionaries are
 * generated from the frozen legacy `app.js` and `extract-i18n --check` fails
 * on any hand edit. Feature-local tables are the established home for copy
 * the frozen shell never carried (`computeT`, `ot`, `judgmentT`).
 */
import { copyLookup, type CopyTable } from "../../i18n/copy";

const COPY: CopyTable = {
  en: {
    "compute.recordsLoading": "Reading the task record…",
    "compute.recordsUnknown": "Task records unknown",
    "compute.recordsUnavailable": "Task records are temporarily unavailable",
    "compute.recordsStale": "Last successful read at {0}",
    "compute.recordsRetry": "Retry",
    "compute.recordsTotalLive": "{0} in flight in this session",
    "compute.recordsPageLive": "{0} in flight on this page",
    "compute.recordsUpdated": "record updated {0}",
    "compute.recordsNone": "No remote compute tasks in this session.",
    "compute.recordsFilterAll": "All",
    "compute.recordsFilterLive": "In flight",
    "compute.recordsLoadMore": "Load more",
    "compute.recordsLoadingMore": "Loading more…",
    "compute.recordsLoadMoreFailed": "Could not load more records",
  },
  zh: {
    "compute.recordsLoading": "正在读取任务记录…",
    "compute.recordsUnknown": "任务记录未知",
    "compute.recordsUnavailable": "任务记录暂不可用",
    "compute.recordsStale": "上次成功读取于 {0}",
    "compute.recordsRetry": "重试",
    "compute.recordsTotalLive": "本会话进行中 {0}",
    "compute.recordsPageLive": "本页进行中 {0}",
    "compute.recordsUpdated": "记录更新于 {0}",
    "compute.recordsNone": "本会话没有远程任务。",
    "compute.recordsFilterAll": "全部",
    "compute.recordsFilterLive": "进行中",
    "compute.recordsLoadMore": "加载更多",
    "compute.recordsLoadingMore": "正在加载更多…",
    "compute.recordsLoadMoreFailed": "加载更多记录失败",
  },
};

export const computeRecordsT = copyLookup(COPY);

/**
 * NAV-6: the side panels in a scientist's words. The sandbox row showed the
 * raw self-test JSON and "durable-policy"; the context panel's token layers
 * were on view by default. Plain status first, raw values under "details".
 */
const WORKBENCH: CopyTable = {
  zh: {
    "wb.security.title": "沙盒与权限",
    "wb.security.sandbox": "沙盒",
    "wb.security.notStarted": "运行第一个单元后才能确认",
    "wb.security.on": "已启用",
    "wb.security.off": "未启用：代码在没有系统沙盒的情况下运行",
    "wb.security.selfTest": "自检",
    "wb.security.passed": "通过",
    "wb.security.notPassed": "未通过",
    "wb.security.network": "内核联网",
    "wb.security.net.blocked": "已阻止：单元里的代码不能直接上网",
    "wb.security.net.allowed": "已放开：单元里的代码可以直接上网",
    "wb.security.net.unproven": "没有确认被阻止",
    "wb.security.net.unknown": "运行第一个单元后才能确认",
    "wb.security.approvals": "待批准",
    "wb.security.pending": "{0} 项等待你批准",
    "wb.security.none": "没有等待批准的操作",
    "wb.security.kernel": "内核",
    "wb.security.kernelEnded": "{0} 内核已结束：{1}",
    "wb.details": "技术细节",
    "wb.diagnostics": "诊断：模型上下文",
    "wb.diagnosticsHint": "这一轮发给模型的内容由哪些部分组成、各占多少 token。排查问题时才需要看。",
  },
  en: {
    "wb.security.title": "Sandbox and permissions",
    "wb.security.sandbox": "Sandbox",
    "wb.security.notStarted": "Confirmed once the first cell runs",
    "wb.security.on": "On",
    "wb.security.off": "Off: code runs without the system sandbox",
    "wb.security.selfTest": "Self-test",
    "wb.security.passed": "passed",
    "wb.security.notPassed": "not passed",
    "wb.security.network": "Kernel networking",
    "wb.security.net.blocked": "Blocked: code in cells cannot go online directly",
    "wb.security.net.allowed": "Open: code in cells can go online directly",
    "wb.security.net.unproven": "Not confirmed as blocked",
    "wb.security.net.unknown": "Confirmed once the first cell runs",
    "wb.security.approvals": "Approvals",
    "wb.security.pending": "{0} waiting for you",
    "wb.security.none": "Nothing waiting for approval",
    "wb.security.kernel": "Kernel",
    "wb.security.kernelEnded": "{0} kernel ended: {1}",
    "wb.details": "Technical details",
    "wb.diagnostics": "Diagnostics: model context",
    "wb.diagnosticsHint": "What this turn sent to the model, part by part, with token counts. Only needed when troubleshooting.",
  },
};

export const workbenchT = copyLookup(WORKBENCH);
