/**
 * I18N-1: Chinese for what the daemon describes in English on two Settings
 * pages -- the built-in specialists (`openai4s/specialists.py`) and the
 * connector catalog (`gateway._CONNECTOR_DIRECTORY`). Settings showed that
 * English in the Chinese UI. Only the product's own entries are translated,
 * by name or id; text a person wrote is shown as written, and English is the
 * daemon's own wording. Code (`host.*`, tool names) stays as it is.
 */

import { LANG, languageRevision } from "../../i18n/runtime";

const SPECIALIST_ZH: Record<string, string> = {
  SCIENTIST:
    "主研究智能体。编写调用完整 host.* 工具集（bash、web_search/web_fetch、文件与 grep/glob 工具、委派、技能）的 Python，产出可发表级的图表、表格和报告。",
  SCOUT:
    "只读侦察。检索文献和你的文件（web_search、web_fetch、grep、glob、read_file），返回一份简明的线索图，不做任何写入。",
  GENERAL:
    "通用子智能体，处理一个独立的子任务；可使用完整工具集，并通过 host.delegate(...) 返回结构化结果。",
  REMOTE_GPU_PROVISIONER:
    "远程 GPU 配置专家。已有 SSH GPU 主机、但结构预测（fold）、ESM 突变打分或 ProteinMPNN 服务尚未就绪时，它检查主机，安装或找到真实的封装程序，验证后登记为可用能力。",
  PLAN: "规划智能体（计划模式）。调查并提出分步计划，不执行任何改动。",
  REVIEWER:
    "依据证据的审阅者。检查已完成的回答、执行轨迹和生成的产物，找出缺少依据的结论、缺失的交付物、溯源缺口和可复现性风险；不写文件，也不调用工具。",
};

const CONNECTOR_ZH: Record<string, string> = {
  example: "本地演示用 MCP 服务器（echo / now / calc / random_int），始终可用，无需安装。",
  "protein-design":
    "九个可审计的原子工具：骨架生成、序列设计、结构/复合物预测、Rosetta 打分与松弛、ESM-2 自然度和 OpenMM 能量最小化。后端和模型权重尚未就绪时，按一套明确、经金丝雀验证的流程启用。",
  filesystem: "读取和列出某个根目录下的文件（官方 MCP 服务器；需要 Node）。",
  fetch: "抓取网址并返回其内容（官方；需要 Node）。",
  time: "时间与时区工具（官方；需要 Node）。",
  "sequential-thinking": "结构化的分步推理工具（官方；需要 Node）。",
};

/**
 * SYNC-21: the description a fresh install's configured rows are seeded with
 * (the gateway's seed), which differs from the catalog's; still the
 * product's own words, so it is translated like the catalog's.
 */
const SEEDED_EN: Record<string, string> = {
  example: "Local demo MCP server: echo / now / calc / random_int.",
};

/**
 * Owner decision 31: the read-only scout was called EXPLORE, which read as
 * the autonomous Explore mode. It is SCOUT now and 侦察 in Chinese, with the
 * name the agent uses beside it.
 */
const SPECIALIST_NAME_ZH: Record<string, string> = {
  SCOUT: "侦察（SCOUT）",
};

/** The badges beside a specialist's name. */
const BADGE_ZH: Record<string, string> = {
  primary: "主智能体",
  subagent: "子智能体",
  agent: "智能体",
  plan: "计划模式",
  custom: "自定义",
};

function zh(table: Record<string, string>, key: string): string | null {
  void languageRevision.value; // a render reading this repaints on a switch
  return LANG === "zh" ? (table[key] ?? null) : null;
}

/** A bundled specialist's description in the UI language. */
export function specialistDescription(row: Record<string, unknown>): string {
  const described = String(row.description ?? "");
  if (row.source !== "bundled") return described;
  return zh(SPECIALIST_ZH, String(row.name ?? "")) ?? described;
}

/** A bundled specialist's name in the UI language; a person's own is kept. */
export function specialistName(row: Record<string, unknown>): string {
  const name = String(row.name ?? "");
  if (row.source !== "bundled") return name;
  return zh(SPECIALIST_NAME_ZH, name) ?? name;
}

/**
 * A catalog connector's description in the UI language. A configured row
 * carries the copy it was added with; it is translated only while it is still
 * the catalog's own text (`catalog`), never once a person changed it.
 */
export function connectorDescription(id: unknown, description: unknown, catalog?: unknown): string {
  const described = String(description ?? "");
  const seeded = SEEDED_EN[String(id ?? "")] === described;
  if (catalog !== undefined && catalog !== description && !seeded) return described;
  return zh(CONNECTOR_ZH, String(id ?? "")) ?? described;
}

export function specialistBadge(word: string): string {
  return zh(BADGE_ZH, word) ?? word;
}
