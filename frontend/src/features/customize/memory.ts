/**
 * Memory scope helpers. Port of app.js:11993-12003.
 * Save always sends the chosen scope — never the literal "default".
 */
import { t } from "../../i18n";
import { copyLookup } from "../../i18n/copy";
import { projectName } from "./host";

export function memScopeLabel(pid: string | null | undefined): string {
  if (!pid || pid === "global") return t("cust.memory.scope.global");
  return projectName(pid);
}

/** Where a memory can be saved: global, then the current project when there is one. */
export function memScopeIds(pid: string | null): string[] {
  return pid ? ["global", pid] : ["global"];
}

export const MEMORY_BLOCKS = [
  "user",
  "project",
  "preference",
  "fact",
  "general",
] as const;

/**
 * M-81: the type menu offered the stored enum (user / project / preference /
 * fact / general), and the context line read "1 injected · 0 omitted · 0
 * inherited from global · 0 hidden by this project's own blocks".
 * Feature-local: the generated `i18n/en.ts` / `zh.ts` are byte-checked extracts.
 */
const memoryT = copyLookup({
  zh: {
    "memory.block.user": "关于你",
    "memory.block.project": "项目背景",
    "memory.block.preference": "偏好",
    "memory.block.fact": "事实",
    "memory.block.general": "其他",
    "memory.hint.user": "你是谁、做什么研究，例如「我是结构生物学博士后」。",
    "memory.hint.project": "这个项目的目标、数据和约定。",
    "memory.hint.preference": "你希望智能体怎么做，例如「图里的标注用英文」。",
    "memory.hint.fact": "需要记住的具体事实，例如数据存放位置或样本数。",
    "memory.hint.general": "不属于以上几类的内容。",
    "memory.applied": "新会话开始时会带上 {0} 条记忆",
    "memory.applied.omitted": "另有 {0} 条因篇幅省略",
    "memory.applied.inherited": "其中 {0} 条来自全局",
    "memory.applied.overridden": "{0} 条全局记忆被本项目的同类记忆取代",
    "memory.applied.sep": "；",
  },
  en: {
    "memory.block.user": "About you",
    "memory.block.project": "Project context",
    "memory.block.preference": "Preference",
    "memory.block.fact": "Fact",
    "memory.block.general": "Other",
    "memory.hint.user": "Who you are and what you work on, e.g. \"I am a structural biology postdoc\".",
    "memory.hint.project": "The project's aims, data and conventions.",
    "memory.hint.preference": "How you want the agent to work, e.g. \"label figures in English\".",
    "memory.hint.fact": "A specific fact to remember, such as where the data lives or the sample count.",
    "memory.hint.general": "Anything that fits none of the above.",
    "memory.applied": "Saved memories added to each new conversation: {0}",
    "memory.applied.omitted": "{0} more left out for length",
    "memory.applied.inherited": "{0} of them from Global",
    "memory.applied.overridden": "{0} Global memories replaced by this project's own of the same type",
    "memory.applied.sep": "; ",
  },
});

/** A memory type's name; an unknown stored value is shown as it is. */
export function memoryBlockLabel(block: string): string {
  const key = "memory.block." + block;
  const label = memoryT(key);
  return label === key ? block : label;
}

/** One line on what a memory type is for, or "" for an unknown one. */
export function memoryBlockHint(block: string): string {
  const key = "memory.hint." + block;
  const hint = memoryT(key);
  return hint === key ? "" : hint;
}

/** What a scope's new conversations start with; the other counts only when they are not zero. */
export function memoryAppliedText(ctx: Record<string, unknown>): string {
  const count = (value: unknown) => Math.max(0, Number(value) || 0);
  const parts = [memoryT("memory.applied", count(ctx.included_count))];
  const omitted = Array.isArray(ctx.omitted) ? ctx.omitted.length : 0;
  if (omitted) parts.push(memoryT("memory.applied.omitted", omitted));
  if (count(ctx.inherited_count)) parts.push(memoryT("memory.applied.inherited", count(ctx.inherited_count)));
  if (count(ctx.overridden_count)) parts.push(memoryT("memory.applied.overridden", count(ctx.overridden_count)));
  return parts.join(memoryT("memory.applied.sep"));
}
