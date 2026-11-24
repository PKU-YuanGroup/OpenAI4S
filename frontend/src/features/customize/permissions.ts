/**
 * Settings → Permissions: what a tool name means, and a summary of the global
 * rules that is read from the rules themselves.
 *
 * The page used to describe the defaults in a fixed sentence ("writes /
 * commands / network / installs need approval") that the seeded rules had
 * stopped matching, and listed rules by raw tool name alone.
 */
import { copyLookup, type CopyTable } from "../../i18n/copy";

export type PermRule = { rule_id: string; tool: string; pattern: string; decision: string };

const COPY: CopyTable = {
  en: {
    "permrules.desc":
      "Control which tools need your approval. Priority: the more specific rule wins; at equal specificity, This conversation > This project > Global.",
    "permrules.summary.lead": "Under the current global rules: {0}.",
    "permrules.summary.none": "There are no global rules: every tool asks first.",
    "permrules.summary.allow": "{0} run without asking",
    "permrules.summary.ask": "{0} need your approval",
    "permrules.summary.mixed": "{0} partly need approval",
    "permrules.summary.deny": "{0} are refused",
    "permrules.summary.denyRules": "refused by a specific rule: {0}",
    "permrules.summary.sep": "; ",
    "permrules.summary.listSep": ", ",
    "permrules.group.read": "reading files",
    "permrules.group.write": "writing files",
    "permrules.group.network": "network access",
    "permrules.group.install": "package installs",
    "permrules.group.command": "background commands",
    "permrules.group.connector": "connectors",
    "permrules.group.skills": "skills and credentials",
    "permrules.tool.*": "Every tool",
    "permrules.tool.read_file": "Read a file in the session workspace",
    "permrules.tool.write_file": "Write a file in the session workspace",
    "permrules.tool.edit_file": "Change a file in the session workspace",
    "permrules.tool.glob": "Find files by name pattern",
    "permrules.tool.grep": "Search inside files for text",
    "permrules.tool.list_dir": "List a folder",
    "permrules.tool.project_list_dir": "List the project folder",
    "permrules.tool.project_read_file": "Read a file in the project folder",
    "permrules.tool.project_glob": "Find project files by name pattern",
    "permrules.tool.project_grep": "Search inside project files",
    "permrules.tool.project_import_file": "Copy a project file into the session",
    "permrules.tool.save_artifact": "Save a file as an artifact",
    "permrules.tool.materialise_artifact": "Write an artifact version back to the workspace",
    "permrules.tool.delegate": "Hand a subtask to a sub-agent",
    "permrules.tool.env_setup": "Install packages into the kernel environment",
    "permrules.tool.env_use": "Switch the kernel's runtime environment",
    "permrules.tool.env_create": "Create a runtime environment",
    "permrules.tool.web_fetch": "Read a web page (pattern: domain)",
    "permrules.tool.web_download": "Download a file (pattern: domain)",
    "permrules.tool.web_search": "Search the web",
    "permrules.tool.science_search": "Search the built-in science databases",
    "permrules.tool.request_network_access": "Ask to reach a new domain",
    "permrules.tool.mcp_call": "Call a connector (MCP) tool",
    "permrules.tool.mcp_resource_read": "Read a connector (MCP) resource",
    "permrules.tool.mcp_prompt_get": "Fetch a connector (MCP) prompt",
    "permrules.tool.exec_background": "Run a command in the background",
    "permrules.tool.bash": "Run a shell command",
    "permrules.tool.credentials_set": "Store a credential",
    "permrules.tool.skills_edit": "Change a skill",
    "permrules.tool.skills_delete": "Delete a skill",
    "permrules.tool.skills_publish": "Publish a skill",
    "permrules.installOnce":
      "Package installs ask every time, so an allow rule for them is not saved; approve each install on its own card.",
    "permrules.installIgnored": "Ignored: package installs ask every time.",
  },
  zh: {
    "permrules.desc":
      "控制哪些工具需要你的批准。优先级：越具体的规则越优先；同等具体时 本对话 > 本项目 > 全局。",
    "permrules.summary.lead": "按当前全局规则：{0}。",
    "permrules.summary.none": "当前没有全局规则：所有工具都会先询问。",
    "permrules.summary.allow": "{0} 直接放行",
    "permrules.summary.ask": "{0} 需要你批准",
    "permrules.summary.mixed": "{0} 部分需要批准",
    "permrules.summary.deny": "{0} 被拒绝",
    "permrules.summary.denyRules": "被具体规则拒绝：{0}",
    "permrules.summary.sep": "；",
    "permrules.summary.listSep": "、",
    "permrules.group.read": "读取文件",
    "permrules.group.write": "写入文件",
    "permrules.group.network": "联网",
    "permrules.group.install": "装包",
    "permrules.group.command": "后台命令",
    "permrules.group.connector": "连接器",
    "permrules.group.skills": "技能与凭据",
    "permrules.tool.*": "所有工具",
    "permrules.tool.read_file": "读取会话工作区里的文件",
    "permrules.tool.write_file": "在会话工作区里写文件",
    "permrules.tool.edit_file": "修改会话工作区里的文件",
    "permrules.tool.glob": "按文件名模式查找文件",
    "permrules.tool.grep": "在文件内容里搜索文本",
    "permrules.tool.list_dir": "列出文件夹内容",
    "permrules.tool.project_list_dir": "列出项目文件夹内容",
    "permrules.tool.project_read_file": "读取项目文件夹里的文件",
    "permrules.tool.project_glob": "在项目文件夹里按文件名查找",
    "permrules.tool.project_grep": "在项目文件里搜索文本",
    "permrules.tool.project_import_file": "把项目文件复制进会话",
    "permrules.tool.save_artifact": "把文件保存为产物",
    "permrules.tool.materialise_artifact": "把产物的某个版本写回工作区",
    "permrules.tool.delegate": "把子任务交给子智能体",
    "permrules.tool.env_setup": "往内核环境里安装包",
    "permrules.tool.env_use": "切换内核的运行环境",
    "permrules.tool.env_create": "新建运行环境",
    "permrules.tool.web_fetch": "读取网页（模式为域名）",
    "permrules.tool.web_download": "下载文件（模式为域名）",
    "permrules.tool.web_search": "联网搜索",
    "permrules.tool.science_search": "检索内置的科学数据库",
    "permrules.tool.request_network_access": "申请访问一个新域名",
    "permrules.tool.mcp_call": "调用连接器（MCP）工具",
    "permrules.tool.mcp_resource_read": "读取连接器（MCP）资源",
    "permrules.tool.mcp_prompt_get": "获取连接器（MCP）提示词",
    "permrules.tool.exec_background": "在后台运行命令",
    "permrules.tool.bash": "运行 shell 命令",
    "permrules.tool.credentials_set": "保存凭据",
    "permrules.tool.skills_edit": "修改技能",
    "permrules.tool.skills_delete": "删除技能",
    "permrules.tool.skills_publish": "发布技能",
    "permrules.installOnce": "装包每次都会询问，所以不会保存针对装包的“允许”规则；请在每次安装的卡片上批准。",
    "permrules.installIgnored": "不生效：装包每次都会询问。",
  },
};

/**
 * The server's `installs_packages` (openai4s/storage/permissions.py): a shell
 * command that asks a package manager to install, or R's installers. Kept in
 * step with that pattern; the server refuses the rule either way.
 */
const PACKAGE_INSTALL = new RegExp(
  "(?:^|[\\s;&|()`])(?:\\S*/)?(?:python[\\d.]*\\s+-m\\s+)?" +
    "(?:pip[\\d.]*|pipx|uv(?:\\s+pip)?|poetry|conda|mamba|micromamba|npm|pnpm|yarn" +
    "|gem|cargo|apt|apt-get|brew)" +
    "(?:\\s+-\\S+)*\\s+(?:(?:env|tool)\\s+)?(?:install|add|sync|create|ci|i)(?=\\s|$)" +
    "|install\\.packages\\s*\\(|BiocManager::install\\s*\\(|(?:remotes|devtools)::install_",
);

/**
 * True for a rule about package installs. Installs ask every time (owner
 * decision 1): no allow rule is saved for one and an existing one is ignored.
 */
export function installRule(tool: string, pattern: string): boolean {
  if (tool === "env_setup") return true;
  return tool === "bash" && PACKAGE_INSTALL.test(pattern || "");
}

export const permT = copyLookup(COPY);

/** One line saying what a tool does, or null for a name the table does not know. */
export function toolDescription(tool: string): string | null {
  const key = "permrules.tool." + tool;
  return key in COPY.en ? permT(key) : null;
}

/** What the fixed sentence used to claim, as groups the rules are checked against. */
const GROUPS: ReadonlyArray<readonly [string, readonly string[]]> = [
  [
    "read",
    [
      "read_file",
      "glob",
      "grep",
      "list_dir",
      "project_list_dir",
      "project_read_file",
      "project_glob",
      "project_grep",
    ],
  ],
  ["write", ["write_file", "edit_file", "save_artifact", "project_import_file"]],
  ["network", ["web_fetch", "web_search", "science_search"]],
  ["install", ["env_setup"]],
  ["command", ["exec_background"]],
  ["connector", ["mcp_call", "mcp_resource_read", "mcp_prompt_get"]],
  ["skills", ["skills_edit", "skills_delete", "skills_publish", "credentials_set"]],
];

/**
 * What the global rules amount to, per group of tools. A tool's standing is
 * its catch-all (`*`) rule; without one the resolver asks. Narrower deny
 * rules are named, since a deny wins over everything.
 */
export function globalRulesSummary(rules: readonly PermRule[]): string {
  if (!rules.length) return permT("permrules.summary.none");
  const catchAll = new Map<string, string>();
  for (const rule of rules) {
    if (rule.pattern === "*") catchAll.set(rule.tool, rule.decision);
  }
  const everything = catchAll.get("*");
  const byStanding: Record<string, string[]> = { allow: [], ask: [], mixed: [], deny: [] };
  for (const [group, tools] of GROUPS) {
    // No allow rule answers for a package install: it asks.
    const standingOf = (tool: string): string => {
      const decision = catchAll.get(tool) ?? everything ?? "ask";
      return decision === "allow" && installRule(tool, "*") ? "ask" : decision;
    };
    const decisions = new Set(tools.map(standingOf));
    const standing = decisions.size === 1 ? [...decisions][0]! : "mixed";
    (byStanding[standing] || byStanding.mixed!).push(permT("permrules.group." + group));
  }
  const listSep = permT("permrules.summary.listSep");
  const parts = (["allow", "ask", "mixed", "deny"] as const)
    .filter((standing) => byStanding[standing]!.length)
    .map((standing) => permT("permrules.summary." + standing, byStanding[standing]!.join(listSep)));
  const denied = rules
    .filter((rule) => rule.decision === "deny" && rule.pattern !== "*")
    .map((rule) => rule.tool + " " + rule.pattern);
  if (denied.length) parts.push(permT("permrules.summary.denyRules", denied.join(listSep)));
  return permT("permrules.summary.lead", parts.join(permT("permrules.summary.sep")));
}
