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
    "compute.recordsUnavailable": "Task records are temporarily unavailable",
    "compute.recordsStale": "Showing the last successful read ({0}); the refresh failed",
    "compute.recordsRetry": "Retry",
    "compute.recordsLoaded": "{0} record(s) loaded",
    "compute.recordsMore": "more available",
    "compute.recordsTotalLive": "{0} in flight in this session",
    "compute.recordsPageLive": "{0} in flight on this page",
    "compute.recordsUpdated": "record updated {0}",
    "compute.recordsNone": "No remote compute tasks in this session.",
    "compute.recordsNonePage":
      "No remote tasks on this page; this service does not report a session-wide count.",
    "compute.recordsNoneLive": "No in-flight remote tasks on this page.",
    "compute.recordsFilterAll": "All",
    "compute.recordsFilterLive": "In flight",
    "compute.recordsLoadMore": "Load more",
    "compute.recordsLoadingMore": "Loading more…",
    "compute.recordsLoadMoreFailed": "Could not load more records",
  },
  zh: {
    "compute.recordsLoading": "正在读取任务记录…",
    "compute.recordsUnavailable": "任务记录暂不可用",
    "compute.recordsStale": "以下为上次成功读取的结果（{0}），本次刷新失败",
    "compute.recordsRetry": "重试",
    "compute.recordsLoaded": "已加载 {0} 条记录",
    "compute.recordsMore": "还有更多",
    "compute.recordsTotalLive": "本会话进行中 {0}",
    "compute.recordsPageLive": "本页进行中 {0}",
    "compute.recordsUpdated": "记录更新于 {0}",
    "compute.recordsNone": "本会话没有远程计算任务。",
    "compute.recordsNonePage": "本页记录中没有远程任务；该服务不提供全量计数。",
    "compute.recordsNoneLive": "本页没有进行中的远程任务。",
    "compute.recordsFilterAll": "全部",
    "compute.recordsFilterLive": "进行中",
    "compute.recordsLoadMore": "加载更多",
    "compute.recordsLoadingMore": "正在加载更多…",
    "compute.recordsLoadMoreFailed": "加载更多记录失败",
  },
};

export const computeRecordsT = copyLookup(COPY);
