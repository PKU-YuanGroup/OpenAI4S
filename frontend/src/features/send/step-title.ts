/**
 * I18N-1: step card titles and summaries in the UI language.
 *
 * The host words a step's title and one-line summary in English
 * (`host_dispatch._step_begin` / `_step_end`: "Reading project file x.csv",
 * "Saving 2 artifacts", "12 results"), and the card showed them verbatim in
 * the Chinese UI. They come from a closed set of templates, so they are
 * translated here by pattern; anything unrecognised (a cell's own title, a
 * path) is shown as it came.
 */

import { LANG } from "../../i18n/runtime";

type Rule = [RegExp, string];

/** Most specific first: the first match wins. */
const TITLE_ZH: Rule[] = [
  [/^Searching the web$/, "搜索网页"],
  [/^Reading project file (.+)$/, "读取项目文件 $1"],
  [/^Reading history for (.+)$/, "读取 $1 的历史"],
  [/^Reading an earlier step's full output$/, "读取先前步骤的完整输出"],
  [/^Reading (.+)$/, "读取 $1"],
  [/^Browsing project files$/, "浏览项目文件"],
  [/^Finding files$/, "查找文件"],
  [/^Searching in files$/, "在文件中搜索"],
  [/^Planning · (\d+) steps?$/, "规划 · $1 步"],
  [/^Planning$/, "规划"],
  [/^Searching skills$/, "搜索技能"],
  [/^Loading (.+) skill guidance$/, "加载技能 $1 的说明"],
  [/^Listing runtime environments$/, "列出运行环境"],
  [/^Listing scientific databases$/, "列出科学数据库"],
  [/^Listing versions of (.+)$/, "列出 $1 的版本"],
  [/^Listing (.+)$/, "列出 $1"],
  [/^Saving (\d+) artifacts$/, "保存 $1 个产物"],
  [/^Saving (.+)$/, "保存 $1"],
  [/^Writing (.+)$/, "写入 $1"],
  [/^Editing (.+)$/, "编辑 $1"],
  [/^Downloading from (.+)$/, "从 $1 下载"],
  [/^Copying (.+) in from another session$/, "从另一个会话复制 $1"],
  [/^Importing project file (.+)$/, "导入项目文件 $1"],
  [/^Importing a verified Zenodo dataset file$/, "导入经过校验的 Zenodo 数据集文件"],
  [/^Inspecting remote GPU setup$/, "检查远程 GPU 设置"],
  [/^Inspecting local and remote accelerators$/, "检查本地与远程加速器"],
  [/^Inspecting (.+)$/, "查看 $1"],
  [/^Requesting network access to (.+)$/, "申请访问 $1 的网络权限"],
  [/^Switching to the local (.+) environment$/, "切换到本机环境 $1"],
  [/^Switching to the (.+) environment$/, "切换到 $1 环境"],
  [/^Installing packages$/, "安装软件包"],
  [/^Installing (.+) into (.+)$/, "安装 $1 到 $2"],
  [/^Installing (.+)$/, "安装 $1"],
  [/^Rolling back(.*)$/, "回滚$1"],
  [/^Searching (.+)$/, "搜索 $1"],
  [/^Running shell command$/, "运行 shell 命令"],
  [/^Running (?!analysis · cell )(.+)$/, "运行 $1"],
];

const UNIT_ZH: Record<string, string> = {
  artifact: "个产物",
  byte: "字节",
  char: "个字符",
  database: "个数据库",
  domain: "个域名",
  entry: "条",
  entrie: "条",
  file: "个文件",
  item: "项",
  line: "行",
  match: "处匹配",
  matche: "处匹配",
  result: "条结果",
  skill: "个技能",
  step: "步",
  suggestion: "条建议",
  version: "个版本",
};

function apply(rules: Rule[], text: string): string | null {
  for (const [pattern, template] of rules) {
    if (pattern.test(text)) return text.replace(pattern, template);
  }
  return null;
}

/**
 * A cell's banner comment is not a title: "---------- QC ----------" reads
 * "QC". The server strips it now (`server/cell_run.py` `activity_title`);
 * steps stored before that still carry the rule characters.
 */
const BANNER = /^(?:[-=*~_#+]\s*){3,}|(?:\s*[-=*~_#+]){3,}$/g;

export function stepTitleText(title: string): string {
  if (!title) return title;
  const plain = title.replace(BANNER, "").trim() || title;
  if (LANG === "en") return plain;
  return apply(TITLE_ZH, plain) ?? plain;
}

export function stepSummaryText(summary: string): string {
  if (LANG === "en" || !summary) return summary;
  if (summary === "no match") return "没有匹配";
  if (summary === "failed") return "失败";
  const failed = /^failed: ([\s\S]+)$/.exec(summary);
  if (failed) return "失败：" + failed[1];
  const exit = /^exit (-?\d+)(?: · ([\d,]+) file changes?)?$/.exec(summary);
  if (exit) return "退出码 " + exit[1] + (exit[2] ? ` · ${exit[2]} 个文件变更` : "");
  // A paged read says which lines it returned (TURN-3).
  const range = /^lines ([\d,]+)–([\d,]+) of ([\d,]+)$/.exec(summary);
  if (range) return `第 ${range[1]}–${range[2]} 行，共 ${range[3]} 行`;
  const past = /^past the end \(([\d,]+) lines?\)$/.exec(summary);
  if (past) return `已超出文件末尾（共 ${past[1]} 行）`;
  const counted = /^([\d,]+) ([a-z]+?)s?$/.exec(summary);
  if (counted && UNIT_ZH[counted[2]!]) return `${counted[1]} ${UNIT_ZH[counted[2]!]}`;
  return summary;
}
