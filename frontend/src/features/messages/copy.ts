import { LANG, t } from "../../i18n/runtime";

/**
 * History-recovery copy belongs to the current UI, outside frozen extracts.
 * A key here also overrides the generated dictionaries' entry of that name:
 * the literature starter told the user to "use web_search", an internal tool
 * name (SET-4).
 */
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
    "history.part.runState": "Run state",
    "history.forkMessage.label": "Branch from this question",
    "history.forkMessage.busy": "Creating a branch…",
    "history.forkMessage.created": "Created a branch from this question: {0}",
    "history.forkMessage.failed": "Could not branch from this question.",
    "history.missing.title": "Session not found",
    "history.missing.sub": "This link points to a session that does not exist or has been deleted.",
    "history.missing.project": "Back to the project",
    "history.missing.home": "Back to Home",
    "history.importedEmpty.title": "No conversation in this copy",
    "history.importedEmpty.sub": "The imported session had no messages. Its code and outputs are in the Notebook; nothing from the package runs.",
    "history.projectRef": "A file in the project folder: {0}",
    "history.feedback.up": "Saved on this computer: liked 👍",
    "history.feedback.down": "Saved on this computer: disliked 👎",
    "history.feedback.cleared": "Feedback cleared",
    "history.feedback.reasonLabel": "What went wrong? (optional)",
    "history.feedback.reasonPlaceholder": "For example: it used the wrong column",
    "history.feedback.reasonSave": "Save reason",
    "history.feedback.reasonSaved": "Reason saved on this computer",
    "history.feedback.where": "Ratings and reasons are kept with this session on this computer. They are not sent anywhere.",
    "starter.litReview.prompt": "Search the web for advances in CRISPR base-editing off-target effects over the past three years, and synthesize a cited Markdown brief."
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
    "history.part.runState": "运行状态",
    "history.forkMessage.label": "从此问题另开分支",
    "history.forkMessage.busy": "正在创建分支…",
    "history.forkMessage.created": "已从此问题创建分支 {0}",
    "history.forkMessage.failed": "无法从此问题创建分支。",
    "history.missing.title": "会话不存在",
    "history.missing.sub": "这个链接指向的会话不存在或已被删除。",
    "history.missing.project": "返回项目",
    "history.missing.home": "返回首页",
    "history.importedEmpty.title": "这个副本没有对话",
    "history.importedEmpty.sub": "导入的会话里没有消息。它的代码和输出在 Notebook 里；包里的任何代码都不会运行。",
    "history.projectRef": "项目文件夹中的文件：{0}",
    "history.feedback.up": "已保存在本机：赞 👍",
    "history.feedback.down": "已保存在本机：踩 👎",
    "history.feedback.cleared": "已取消反馈",
    "history.feedback.reasonLabel": "哪里不对？（可选）",
    "history.feedback.reasonPlaceholder": "例如：用错了数据列",
    "history.feedback.reasonSave": "保存原因",
    "history.feedback.reasonSaved": "原因已保存在本机",
    "history.feedback.where": "评价与原因随本会话保存在这台电脑上，不会发送到任何地方。",
    "starter.litReview.prompt": "在网上检索关于 CRISPR 碱基编辑脱靶效应的近三年进展，综合成一份带引用的 Markdown 简报。"
  }
};

export function historyT(key: string, ...args: readonly unknown[]): string {
  const value = COPY[LANG][key];
  if (value === undefined) return t(key, ...args);
  return value.replace(/\{(\d+)\}/g, (_match, index: string) => String(args[Number(index)] ?? ""));
}
