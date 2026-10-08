import { LANG } from "../../i18n/runtime";
import { copyLookup } from "../../i18n/copy";

/** Local project folders belong to the machine running the daemon. */
export const projectFilesCopy = copyLookup({
  en: {
    folder: "Project folder (optional)",
    folderPlaceholder: "Absolute folder path on this computer",
    folderHelp: "Choose a folder on the computer running OpenAI4S. The agent can read files in its subfolders and copy data into the session for analysis. Original files stay read-only.",
    browse: "Browse folders",
    choose: "Use this folder",
    closePicker: "Close folder picker",
    open: "Open",
    up: "Parent folder",
    files: "Project files",
    title: "Project files — {0}",
    root: "Project root",
    path: "Folder path",
    refresh: "Refresh",
    empty: "This folder is empty.",
    noFolders: "No subfolders in this folder.",
    limited: "Only the first 500 entries are shown. Enter a nested folder path to browse it directly.",
    previewLimited: "This preview is truncated. The agent can import the complete file for analysis.",
    previewHelp: "Text preview only. Ask the agent to read or import this project-relative path for analysis.",
    back: "Back to folder",
    readOnly: "Source data stays read-only. OpenAI4S keeps conversation history and saved file versions in the dedicated .openai4s/ folder.",
    historyHelp: "History is saved automatically in <project folder>/.openai4s/: conversations, file versions, and project name, description and agent context. API credentials and global model configuration are not copied. Original data files stay unchanged.",
    bytes: "{0} bytes",
    error: "Could not read this path: {0}",
    retry: "Retry",
    projectOpenFailed: "Could not open {0}. Your current conversation is still open.",
  },
  zh: {
    folder: "项目文件夹（可选）",
    folderPlaceholder: "此电脑上的文件夹绝对路径",
    folderHelp: "选择运行 OpenAI4S 的电脑上的文件夹。智能体可读取其子文件夹内的文件，并将数据复制到会话中分析。原始文件保持只读。",
    browse: "浏览文件夹",
    choose: "使用此文件夹",
    closePicker: "关闭文件夹选择器",
    open: "打开",
    up: "上一级文件夹",
    files: "项目文件",
    title: "项目文件 — {0}",
    root: "项目根目录",
    path: "文件夹路径",
    refresh: "刷新",
    empty: "此文件夹为空。",
    noFolders: "此文件夹没有子文件夹。",
    limited: "仅显示前 500 个条目；可输入子文件夹路径直接浏览。",
    previewLimited: "预览已截断，智能体可导入完整文件进行分析。",
    previewHelp: "此处仅提供文本预览。可让智能体读取或导入这个项目相对路径进行分析。",
    back: "返回文件夹",
    readOnly: "源数据文件保持只读；OpenAI4S 在专用的 .openai4s/ 目录中保留对话历史和已保存的文件版本。",
    historyHelp: "自动保存到 <项目文件夹>/.openai4s/：对话、文件版本，以及项目名称、描述和智能体上下文。API 凭据及全局模型配置不会复制，也不修改原始数据文件。",
    bytes: "{0} 字节",
    error: "无法读取此路径：{0}",
    retry: "重试",
    projectOpenFailed: "无法打开 {0}，已保留当前会话。",
  },
});

export const projectHistoryCopy = copyLookup({
  en: {
    menu: "Local history", title: "Local history — {0}",
    directory: "Save directory", automatic: "Saved automatically in this project folder. Snapshots are read-only records; they do not restore Python/R variables or a running process.",
    saved: "Saved", saving: "Saving…", error: "Save failed", unavailable: "Local history unavailable", pending: "Not saved yet", unknown: "Save status unknown",
    lastSaved: "Last saved: {0}", neverSaved: "No confirmed save yet.", refresh: "Refresh", saveNow: "Save now",
    sessions: "Saved conversations", empty: "No conversations saved yet.", open: "View history", continue: "Continue conversation",
    continueHelp: "Opens the current conversation stored in OpenAI4S. Viewing an earlier revision does not rewind it or restore kernel memory.",
    archivedOnly: "This conversation is no longer in the current database. Its saved history is available to read.",
    cannotContinue: "This conversation can no longer be continued from the current database.", back: "Back to saved conversations",
    revision: "Saved revision", savedAt: "Saved at {0}", counts: "{0} messages · {1} cells · {2} files",
    messages: "Conversation", cells: "Code and output", files: "File versions", settings: "Project settings",
    noMessages: "No messages in this revision.", noCells: "No code cells in this revision.", noFiles: "No files saved in this revision.",
    name: "Name", description: "Description", context: "Agent context", settingsHelp: "This view shows project name, description and agent context. API credentials and global model configuration are not copied.",
    user: "You", assistant: "Assistant", system: "System", tool: "Tool", other: "Message",
    stdout: "Output", stderr: "Diagnostic output", cellError: "Cell error", download: "Download saved file",
    path: "File", size: "Size", checksum: "SHA-256", bytes: "{0} bytes", revisionId: "Revision: {0}",
    omissions: "Content not saved", omissionsHelp: "The following content was omitted from this snapshot. It cannot be recovered from this revision.", reason: "Reason",
    limited: "This response is limited. Some history is not shown.", loadError: "Could not load local history: {0}", saveError: "Could not save local history: {0}",
    retry: "Retry", invalid: "Invalid history response", untitled: "Untitled conversation", unavailableFile: "Saved bytes unavailable",
    branch: "Saved branch view: {0}",
  },
  zh: {
    menu: "本地历史", title: "本地历史 — {0}",
    directory: "保存目录", automatic: "自动保存在项目文件夹内。快照是只读记录，不会恢复 Python/R 变量或正在运行的进程。",
    saved: "已保存", saving: "正在保存…", error: "保存失败", unavailable: "本地历史不可用", pending: "尚未保存", unknown: "保存状态未知",
    lastSaved: "上次保存：{0}", neverSaved: "尚无已确认的保存记录。", refresh: "刷新", saveNow: "立即保存",
    sessions: "已保存的对话", empty: "尚未保存对话。", open: "查看历史", continue: "继续对话",
    continueHelp: "打开 OpenAI4S 当前数据库中的对话。查看旧版本不会回退当前对话，也不会恢复内核内存。",
    archivedOnly: "此对话已不在当前数据库中，仍可阅读已保存的历史。",
    cannotContinue: "此对话已无法从当前数据库继续。", back: "返回历史对话列表",
    revision: "保存版本", savedAt: "保存于 {0}", counts: "{0} 条消息 · {1} 个代码单元 · {2} 个文件",
    messages: "对话", cells: "代码与输出", files: "文件版本", settings: "项目设置",
    noMessages: "此版本没有消息。", noCells: "此版本没有代码单元。", noFiles: "此版本未保存文件。",
    name: "名称", description: "描述", context: "智能体上下文", settingsHelp: "此处展示项目名称、描述与智能体上下文。API 凭据及全局模型配置不会复制。",
    user: "你", assistant: "助手", system: "系统", tool: "工具", other: "消息",
    stdout: "输出", stderr: "诊断输出", cellError: "代码单元错误", download: "下载已保存文件",
    path: "文件", size: "大小", checksum: "SHA-256", bytes: "{0} 字节", revisionId: "版本：{0}",
    omissions: "未保存的内容", omissionsHelp: "以下内容未纳入此快照，无法从此版本中恢复。", reason: "原因",
    limited: "此响应有长度限制，部分历史未显示。", loadError: "无法读取本地历史：{0}", saveError: "无法保存本地历史：{0}",
    retry: "重试", invalid: "历史数据格式无效", untitled: "未命名对话", unavailableFile: "已保存文件内容不可用",
    branch: "保存的分支视图：{0}",
  },
});

/** Feature-owned copy; the frozen legacy dictionaries remain generated. */
export function sessionCopy(key: "sessionsError" | "foldersError" | "retry"): string {
  const copy = LANG === "en" ? {
    sessionsError: "Could not load sessions. Retry reading the list.",
    foldersError: "Could not load folders.",
    retry: "Retry reading",
  } : {
    sessionsError: "无法读取会话列表，可重试读取。",
    foldersError: "无法读取文件夹。",
    retry: "重试读取",
  };
  return copy[key];
}

/** The composer menus' context-usage card and their review and on/off hints. */
export function composerCopy(key: "usage" | "reviewing" | "on" | "off", ...args: Array<string | number>): string {
  const copy = LANG === "en" ? {
    usage: "Input {0} · Output {1} · Reviewer {2}",
    reviewing: "Reviewing",
    on: "On",
    off: "Off",
  } : {
    usage: "输入 {0} · 输出 {1} · 审阅 {2}",
    reviewing: "正在审阅",
    on: "开",
    off: "关",
  };
  return copy[key].replace(/\{(\d+)\}/g, (_match, index: string) => String(args[Number(index)] ?? ""));
}

/** An action that failed before it could report anything itself; `detail` is the error's text. */
export function actionFailedCopy(detail: string): string {
  return (LANG === "en" ? "Could not complete that action: " : "操作未能完成：") + detail;
}

/** Share dialog labels the generated dictionaries do not carry. */
export function shareCopy(key: "linkLabel" | "updateDesc" | "revokeDesc"): string {
  const copy = LANG === "en" ? {
    linkLabel: "Read-only link",
    updateDesc: "The link stays the same; its content becomes this session's latest state.",
    revokeDesc: "The link stops working immediately.",
  } : {
    linkLabel: "只读链接",
    updateDesc: "链接不变，内容换成当前会话的最新状态。",
    revokeDesc: "撤销后立即失效。",
  };
  return copy[key];
}
