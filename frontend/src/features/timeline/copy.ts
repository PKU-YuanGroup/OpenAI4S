/**
 * C2 copy for the Timeline's remote-task panel.
 *
 * Local rather than in `i18n/en.ts` / `i18n/zh.ts`: those dictionaries are
 * generated from the frozen legacy `app.js` and `extract-i18n --check` fails
 * on any hand edit. Feature-local tables are the established home for copy
 * the frozen shell never carried (`computeT`, `ot`, `judgmentT`).
 */
import { copyLookup, overrideLookup, type CopyTable } from "../../i18n/copy";

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
    "queueRow.sendNow": "Stop and send now",
    "queueRow.sendNowTitle": "Stop the running turn; the first queued message starts right away",
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
    "queueRow.sendNow": "停止并立即发送",
    "queueRow.sendNowTitle": "停止当前回合，排队的第一条消息随即开始",
    "queueRow.stopping": "正在停止当前任务… 排队的 {0} 条消息会接着运行",
  },
};

export const queueT = copyLookup(QUEUE_COPY);

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

/**
 * I18N-1: the Timeline's words where the generated zh dictionary is English
 * ("Action Timeline", "Python Cell", "Owner", "Branch · Checkpoint", "Kernel
 * Recovery") or mixes in English ("创建 checkpoint"), and the few that are
 * developer jargon in both languages ("Safe projection of the durable Action
 * Ledger…"). For these keys the table wins; every other key is the
 * dictionary's, so `timelineT` can stand in for `t` across the island.
 */
const TIMELINE_OVERRIDES: CopyTable = {
  zh: {
    "timeline.title": "行动时间线",
    "timeline.subtitle": "智能体在这个会话里做的每一步：运行的代码、调用的工具、等待的批准。点一行查看详情。",
    "timeline.empty": "还没有可显示的行动。Notebook 只保留科研代码单元，完整的执行过程会显示在这里。",
    "timeline.noBranch": "还没有分支与检查点信息。",
    "timeline.noContext": "还没有上下文信息。",
    "timeline.noSecurity": "还没有沙盒与权限信息。",
    "timeline.owner": "执行者",
    "timeline.generation": "内核代次",
    "timeline.replay": "重放",
    "timeline.tokens": "Token 用量",
    "timeline.turnBoundary": "回合",
    "timeline.turn.summary": "回合 · 当前可见 {0} 条记录",
    "timeline.search.scope": "只搜索已加载的 {0} 条记录，更早的记录不在结果中。搜索时会暂时展开折叠的回合，清空后恢复。",
    "timeline.kind.native_tool": "工具调用",
    "timeline.kind.python": "Python 单元",
    "timeline.kind.r": "R 单元",
    "timeline.kind.dynamic_tool": "动态工具",
    "timeline.kind.delegate": "子代理",
    "timeline.kind.background": "后台 / 远程任务",
    "timeline.kind.permission": "等待批准",
    "timeline.kind.recovery": "恢复事件",
    "timeline.kind.finalize": "最终回复",
    "timeline.kind.action": "行动",
    "timeline.panel.branches": "分支与检查点",
    "branch.actionFailed": "分支操作失败：{0}",
    "branch.checkpoint": "创建检查点",
    "branch.diff": "消息 {0} · Notebook {1} · 文件写入 {2} / 删除 {3} · 产物 +{4}/-{5}",
    "branch.fork": "分叉",
    "branch.forkDefault": "分叉 {0}",
    "branch.forked": "已从检查点 {0} 创建分支",
    "branch.head": "当前位置 {0}",
    "branch.internalCheckpoints": "内部检查点（{0}）",
    "branch.previewTitle": "回退预览",
    "context.artifacts": "{0} 个产物引用",
    "context.compaction": "压缩",
    "context.handoff": "交接",
    "context.savings": "{0} → {1} 个 token",
    "context.tokens": "{0} 个 token",
    "recovery.title": "内核恢复",
    "recovery.checkpoint": "检查点 {0}",
    "recovery.action.restore": "恢复到检查点",
    "recovery.action.unavailable": "当前服务没有提供这个恢复操作。",
    "recovery.action.currentOnly": "只能恢复本会话当前的分支。",
    "recovery.action.failed": "恢复操作失败：{0}",
    "recovery.action.done": "恢复已完成，状态和记录已刷新。",
    "runtime.branch": "分支",
    "runtime.revision": "状态版本",
    "runtime.owner": "执行者",
    "runtime.queue": "排队",
  },
  en: {
    "timeline.subtitle": "Every step the agent took in this session: code it ran, tools it called, approvals it waited for. Click a row for details.",
    "timeline.noBranch": "No branch or checkpoint information yet.",
    "timeline.noContext": "No context information yet.",
    "timeline.noSecurity": "No sandbox or permission information yet.",
  },
};

export const timelineT = overrideLookup(TIMELINE_OVERRIDES);
