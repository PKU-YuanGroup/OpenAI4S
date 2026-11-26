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
    noInlinePreview: "This file type has no preview here. Import it into the session to open it in the viewer; the original stays untouched.",
    importView: "Import and view",
    importing: "Importing…",
    importFailed: "Could not import this file: {0}",
    importTooLarge: "This file is too large to import from here. Ask the agent to import it instead.",
    back: "Back to folder",
    readOnly: "Source data stays read-only. OpenAI4S keeps conversation history and saved file versions in the dedicated .openai4s/ folder.",
    historyHelp: "History is saved automatically in <project folder>/.openai4s/: conversations, file versions, and project name, description and agent context. API credentials and global model configuration are not copied. Original data files stay unchanged.",
    bytes: "{0} bytes",
    error: "Could not read this path: {0}",
    retry: "Retry",
    projectOpenFailed: "Could not open {0}. Your current conversation is still open.",
    showHidden: "Show hidden folders",
    outsideRoots: "Project folders must be inside your home folder (~), a temporary folder (/tmp or $TMPDIR; on macOS, /tmp is /private/tmp), a mounted volume (/Volumes on macOS; /media, /mnt or /run/media on Linux), or a folder listed in the OPENAI4S_PROJECT_ROOTS environment variable.",
    folderNotFound: "This folder does not exist. Check the path.",
    notAFolder: "Enter the full path of a folder (not a file) on this computer, starting with / or ~.",
    credentialFolder: "Credential folders such as ~/.ssh or ~/.aws cannot be project folders.",
    historyFolder: "A .openai4s history folder cannot be a project folder. Choose the folder that contains it.",
    overlapsData: "A project folder cannot contain, or be inside, the OpenAI4S data folder.",
    localOnly: "Project folders are available only when OpenAI4S runs on this computer for one user.",
    accessDenied: "OpenAI4S cannot read this folder. On macOS, allow the app or terminal that runs OpenAI4S in System Settings → Privacy & Security → Files and Folders.",
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
    noInlinePreview: "这种文件不能在这里直接预览。导入到会话后可在查看器中打开；原文件保持不变。",
    importView: "导入并查看",
    importing: "正在导入…",
    importFailed: "无法导入这个文件：{0}",
    importTooLarge: "文件太大，无法从这里导入。可以让智能体导入它。",
    back: "返回文件夹",
    readOnly: "源数据文件保持只读；OpenAI4S 在专用的 .openai4s/ 目录中保留对话历史和已保存的文件版本。",
    historyHelp: "自动保存到 <项目文件夹>/.openai4s/：对话、文件版本，以及项目名称、描述和智能体上下文。API 凭据及全局模型配置不会复制，也不修改原始数据文件。",
    bytes: "{0} 字节",
    error: "无法读取此路径：{0}",
    retry: "重试",
    projectOpenFailed: "无法打开 {0}，已保留当前会话。",
    showHidden: "显示隐藏文件夹",
    outsideRoots: "项目文件夹须位于主目录（~）、临时目录（/tmp 或 $TMPDIR；macOS 上的 /tmp 即 /private/tmp）、外接卷（macOS 为 /Volumes；Linux 为 /media、/mnt 或 /run/media），或环境变量 OPENAI4S_PROJECT_ROOTS 列出的目录之内。",
    folderNotFound: "找不到这个文件夹，请检查路径。",
    notAFolder: "请填写此电脑上某个文件夹（不是文件）的完整路径，以 / 或 ~ 开头。",
    credentialFolder: "凭据目录（如 ~/.ssh、~/.aws）不能作为项目文件夹。",
    historyFolder: "不能把 .openai4s 历史目录设为项目文件夹，请选择包含它的文件夹。",
    overlapsData: "项目文件夹不能包含 OpenAI4S 数据目录，也不能位于其中。",
    localOnly: "项目文件夹仅在 OpenAI4S 以本机单用户方式运行时可用。",
    accessDenied: "OpenAI4S 无法读取这个文件夹。在 macOS 上，请在「系统设置 → 隐私与安全性 → 文件与文件夹」中允许运行 OpenAI4S 的应用或终端访问它。",
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
    revision: "保存版本", savedAt: "保存于 {0}", counts: "{0} 条消息 · {1} 个单元 · {2} 个文件",
    messages: "对话", cells: "代码与输出", files: "文件版本", settings: "项目设置",
    noMessages: "此版本没有消息。", noCells: "此版本没有单元。", noFiles: "此版本未保存文件。",
    name: "名称", description: "描述", context: "智能体上下文", settingsHelp: "此处展示项目名称、描述与智能体上下文。API 凭据及全局模型配置不会复制。",
    user: "你", assistant: "助手", system: "系统", tool: "工具", other: "消息",
    stdout: "输出", stderr: "诊断输出", cellError: "单元错误", download: "下载已保存文件",
    path: "文件", size: "大小", checksum: "SHA-256", bytes: "{0} 字节", revisionId: "版本：{0}",
    omissions: "未保存的内容", omissionsHelp: "以下内容未纳入此快照，无法从此版本中恢复。", reason: "原因",
    limited: "此响应有长度限制，部分历史未显示。", loadError: "无法读取本地历史：{0}", saveError: "无法保存本地历史：{0}",
    retry: "重试", invalid: "历史数据格式无效", untitled: "未命名对话", unavailableFile: "已保存文件内容不可用",
    branch: "保存的分支视图：{0}",
  },
});

/** Home dashboard strings the generated dictionaries do not carry (NAV-4). */
export const dashCopy = copyLookup({
  en: {
    "dash.example.runningNote": "The example analysis is running; it usually finishes in well under a minute. Its steps appear in the session as they run, and its code cells in the session's Notebook.",
    "dash.example.done": "The example analysis finished: all {0} cells ran without an error.",
    "dash.example.doneFailed": "The example analysis ended: {0} of {1} cells did not complete. The session says why.",
    "dash.example.doneStepFailed": "The example analysis finished: all {0} cells ran, but {1} step(s) failed along the way (a data source may be unreachable). The session says which.",
    "dash.example.ended": "The example analysis ended. Open the session to see the results.",
    "dash.example.open": "Open the example session",
    "dash.example.dismiss": "Dismiss",
    "dash.sessions.allAbove": "Running sessions and the ones waiting for you are listed above.",
    "dash.status.done": "Finished",
    "dash.status.failed": "Failed",
    "dash.status.cancelled": "Stopped",
    "dash.status.blocked_by_guardian": "Blocked",
    "dash.status.failed.tip": "The last turn failed",
    "dash.status.cancelled.tip": "The last turn was stopped before it finished",
    "dash.status.blocked_by_guardian.tip": "The last turn was blocked by a safety check",
    "dash.example.project": "Example project",
    "dash.example.tag": "Example",
  },
  zh: {
    "dash.example.runningNote": "示例分析正在运行，通常不到一分钟即可完成。各步骤会在会话里陆续出现，单元在该会话的 Notebook 里。",
    "dash.example.done": "示例分析已完成：{0} 个单元都运行完毕，没有出错。",
    "dash.example.doneFailed": "示例分析已结束：{1} 个单元中有 {0} 个没有完成，会话里写明了原因。",
    "dash.example.doneStepFailed": "示例分析已完成：{0} 个单元都运行完毕，但有 {1} 个步骤失败（可能是某个数据源连不上），会话里写明了是哪一步。",
    "dash.example.ended": "示例分析已结束，打开会话查看结果。",
    "dash.example.open": "打开示例会话",
    "dash.example.dismiss": "知道了",
    "dash.sessions.allAbove": "正在运行和等你处理的会话显示在上方。",
    "dash.status.done": "已完成",
    "dash.status.failed": "失败",
    "dash.status.cancelled": "已停止",
    "dash.status.blocked_by_guardian": "已拦截",
    "dash.status.failed.tip": "上一轮失败了",
    "dash.status.cancelled.tip": "上一轮在完成前被停止",
    "dash.status.blocked_by_guardian.tip": "上一轮被安全检查拦截",
    "dash.example.project": "示例项目",
    "dash.example.tag": "示例",
  },
});

/** The project the daemon seeds on first boot, under the name it seeds it with. */
const SEEDED_EXAMPLE = { id: "proj_example", name: "Example project" };

/**
 * A project's name as shown. The seeded example project is stored as
 * "Example project" (I18N-1: English in a Chinese UI); while it still has that
 * name it is shown in the UI language. A renamed project shows its own name.
 */
export function displayProjectName(
  project: { project_id?: string; id?: string; name?: string } | null | undefined,
): string {
  if (!project) return "";
  const name = project.name || "";
  return (project.project_id || project.id) === SEEDED_EXAMPLE.id && name === SEEDED_EXAMPLE.name
    ? dashCopy("dash.example.project")
    : name;
}

/** Feature-owned copy; the frozen legacy dictionaries remain generated. */
export function sessionCopy(
  key:
    | "sessionsError"
    | "foldersError"
    | "retry"
    | "exportWebPage"
    | "emptyOpen",
  value?: string | number,
): string {
  const copy = LANG === "en" ? {
    sessionsError: "Could not load sessions. Retry reading the list.",
    foldersError: "Could not load folders.",
    retry: "Retry reading",
    exportWebPage: "Export read-only web page",
    emptyOpen: "The open session appears here once you send its first message",
  } : {
    sessionsError: "无法读取会话列表，可重试读取。",
    foldersError: "无法读取文件夹。",
    retry: "重试读取",
    exportWebPage: "导出只读网页",
    emptyOpen: "发出第一条消息后，当前会话会出现在这里",
  };
  return copy[key].replace("{0}", value == null ? "" : String(value));
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

/** Group headings and distinguishing lines for the composer's session-options menu. */
export function optionsMenuCopy(
  key:
    | "groupModes"
    | "groupReview"
    | "groupSession"
    | "groupSettings"
    | "runtimeDesc"
    | "computeDesc"
    | "delegationDesc"
    | "autoReviewDesc"
    | "reviewerModelDesc"
    | "memoryDesc"
    | "specialistDesc",
): string {
  const copy = LANG === "en" ? {
    groupModes: "How the agent works",
    groupReview: "Review",
    groupSession: "This session",
    groupSettings: "Open Settings",
    runtimeDesc: "Pick the Python / R environment this session's code runs in",
    computeDesc: "Settings → Compute: environment status, installed packages, remote GPU, background jobs",
    // SCI-34: every row says what it does, not only the two modes.
    delegationDesc: "Hands parts of a task to sub-agents in parallel",
    autoReviewDesc: "A second model checks each answer",
    reviewerModelDesc: "The model that does that check",
    memoryDesc: "What the agent keeps between sessions",
    specialistDesc: "Roles the agent can hand work to",
  } : {
    groupModes: "工作方式",
    groupReview: "审阅",
    groupSession: "本会话",
    groupSettings: "打开设置",
    runtimeDesc: "选择这个会话的代码在哪个 Python / R 环境里运行",
    computeDesc: "设置 → 计算：环境状态、已装的包、远程 GPU、后台任务",
    delegationDesc: "把任务的一部分交给并行的子智能体",
    autoReviewDesc: "由另一个模型检查每次回答",
    reviewerModelDesc: "做这次检查的模型",
    memoryDesc: "智能体在会话之间记住的内容",
    specialistDesc: "智能体可以把工作交给的角色",
  };
  return copy[key];
}

/** An action that failed before it could report anything itself; `detail` is the error's text. */
export function actionFailedCopy(detail: string): string {
  return (LANG === "en" ? "Could not complete that action: " : "操作未能完成：") + detail;
}

/** Share dialog labels the generated dictionaries do not carry. */
export function shareCopy(
  key:
    | "linkLabel"
    | "updateDesc"
    | "revokeDesc"
    | "menuNeedsSetup"
    | "setupLead"
    | "setupIntro"
    | "setupEnv"
    | "setupRestart"
    | "setupEnable"
    | "setupDocs",
): string {
  const copy = LANG === "en" ? {
    linkLabel: "Read-only link",
    updateDesc: "The link stays the same; its content becomes this session's latest state.",
    revokeDesc: "The link stops working immediately.",
    menuNeedsSetup: "Needs setup: a relay server you host",
    setupLead: "Sharing needs a relay server that you host on a public machine. None is configured, so this session cannot be shared yet.",
    setupIntro: "A share link serves a read-only snapshot through that relay. To set it up:",
    setupEnv: "Set OPENAI4S_SHARE_RELAY_URL and OPENAI4S_SHARE_AUTH_TOKEN (and OPENAI4S_SHARE_BASE_DOMAIN) in the .env file in the OpenAI4S folder. The relay itself runs on a public host with “openai4s relay”.",
    setupRestart: "Restart OpenAI4S.",
    setupEnable: "Open this dialog again and click Enable sharing, or run “openai4s share enable”.",
    setupDocs: "Full steps: docs/webshare.md in the OpenAI4S repository.",
  } : {
    linkLabel: "只读链接",
    updateDesc: "链接不变，内容换成当前会话的最新状态。",
    revokeDesc: "撤销后立即失效。",
    menuNeedsSetup: "需配置：要先部署自己的中转服务器",
    setupLead: "分享需要一台由你自己部署在公网主机上的中转服务器（relay）。现在还没有配置，所以这个会话暂时不能分享。",
    setupIntro: "分享链接通过这台中转服务器提供一份只读快照。配置步骤：",
    setupEnv: "在 OpenAI4S 文件夹的 .env 文件里设置 OPENAI4S_SHARE_RELAY_URL 和 OPENAI4S_SHARE_AUTH_TOKEN（以及 OPENAI4S_SHARE_BASE_DOMAIN）。中转服务器本身用「openai4s relay」部署在一台公网主机上。",
    setupRestart: "重启 OpenAI4S。",
    setupEnable: "再次打开这个对话框，点「启用分享」；或在终端运行「openai4s share enable」。",
    setupDocs: "完整步骤见 OpenAI4S 仓库里的 docs/webshare.md。",
  };
  return copy[key];
}

/**
 * M-48: the project's activity record. It was called a "research map" and
 * opened on raw tabs named Timeline / Lineage in every language, while it is
 * a list, not a graph.
 */
export const projectActivityCopy = copyLookup({
  en: {
    menu: "Project activity",
    title: "{0} · Project activity",
    activity: "Activity",
    files: "Files",
    activitySummary: "{0} sessions · {1} actions",
    filesSummary: "{0} files · {1} versions · {2} links",
    versions: "{0} versions",
    latest: "latest",
    noFiles: "No files in this project yet.",
    links: "Where each file came from ({0})",
  },
  zh: {
    menu: "项目活动记录",
    title: "{0} · 项目活动记录",
    activity: "活动",
    files: "文件",
    activitySummary: "{0} 个会话 · {1} 个操作",
    filesSummary: "{0} 个文件 · {1} 个版本 · {2} 条关联",
    versions: "{0} 个版本",
    latest: "最新",
    noFiles: "这个项目还没有文件。",
    links: "文件的来源（{0}）",
  },
});

/**
 * M-73: deleting a branch from the open session's sidebar list. Only a branch
 * that is neither the current one nor the main line offers it, and it asks
 * first: the branch cannot be switched to again.
 */
export const branchDeleteCopy = copyLookup({
  en: {
    "branchDelete.label": "Delete branch “{0}”",
    "branchDelete.confirm":
      "Delete branch “{0}”?\n\nIt is removed from this session's branches and can no longer be opened. Its own messages, Notebook cells and files are no longer shown; branches forked from it keep everything they share with it. This cannot be undone.",
    "branchDelete.ok": "Delete branch",
    "branchDelete.done": "Deleted branch “{0}”.",
    "branchDelete.failed": "Could not delete branch “{0}”: {1}",
    "branchDelete.active": "“{0}” is the current branch. Switch to another branch before deleting it.",
    "branchDelete.mainLine": "The main line cannot be deleted.",
    "branchDelete.busy": "The session is running. Delete the branch when it has finished.",
    "branchDelete.notFound": "Branch “{0}” no longer exists.",
  },
  zh: {
    "branchDelete.label": "删除分支「{0}」",
    "branchDelete.confirm":
      "删除分支「{0}」？\n\n它会从这个会话的分支中移除，之后无法再打开。只属于它的消息、Notebook 单元和产物不再显示；从它分出的分支仍保留与它共有的部分。此操作无法撤销。",
    "branchDelete.ok": "删除分支",
    "branchDelete.done": "已删除分支「{0}」。",
    "branchDelete.failed": "没能删除分支「{0}」：{1}",
    "branchDelete.active": "「{0}」是当前分支。请先切换到其他分支，再删除它。",
    "branchDelete.mainLine": "主线不能删除。",
    "branchDelete.busy": "会话正在运行，请等它结束后再删除分支。",
    "branchDelete.notFound": "分支「{0}」已经不存在了。",
  },
});

/**
 * SCI-17 / SYNC-28: how a session's last turn ended, when it did not simply
 * finish -- as words and an icon, never a colour alone. `null` for a finished,
 * running or new session. Shared by the sidebar row and the home list.
 */
export function lastTurnOutcome(
  status: string | null | undefined,
): { kind: "failed" | "cancelled" | "blocked_by_guardian"; label: string; tip: string; icon: string } | null {
  const icons = { failed: "alert-triangle", cancelled: "stop-square", blocked_by_guardian: "lock" } as const;
  if (status !== "failed" && status !== "cancelled" && status !== "blocked_by_guardian") return null;
  return {
    kind: status,
    label: dashCopy("dash.status." + status),
    tip: dashCopy("dash.status." + status + ".tip"),
    icon: icons[status],
  };
}
