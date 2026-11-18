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

/** The queued follow-up strip above the composer (`queue.ts`). */
const QUEUE_COPY: CopyTable = {
  en: {
    "queueRow.headNote": "they run in order when the current turn ends or is stopped",
    "queueRow.edit": "Edit",
    "queueRow.editTitle": "Take this message out of the queue and back into the composer to edit it",
    "queueRow.editUnavailable":
      "Only the start of this message is in this tab, so it cannot be edited here; drop it and type it again",
    "queueRow.editBusy": "The composer already holds a draft: send or clear it first, then edit the queued message",
    "queueRow.editing": "Taken out of the queue: edit it and send it again (it rejoins at the end of the queue)",
    "queueRow.started": "Now running your queued message: “{0}”",
    "queueRow.stopping": "Stopping the current turn… {0} queued message(s) will run next",
  },
  zh: {
    "queueRow.headNote": "当前任务结束或被停止后依次运行",
    "queueRow.edit": "编辑",
    "queueRow.editTitle": "把这条消息撤出队列，放回输入框修改",
    "queueRow.editUnavailable": "本标签页只有这条消息的开头，无法在这里编辑；可以撤回后重新输入",
    "queueRow.editBusy": "输入框里还有未发送的内容：先发送或清空它，再编辑排队的消息",
    "queueRow.editing": "已撤出队列：修改后重新发送即可（会重新排到队尾）",
    "queueRow.started": "开始处理你排队的消息：“{0}”",
    "queueRow.stopping": "正在停止当前任务… 排队的 {0} 条消息会接着运行",
  },
};

export const queueT = copyLookup(QUEUE_COPY);
