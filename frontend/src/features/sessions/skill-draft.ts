/**
 * "Save as Skill" from a session's executed cells (M-84).
 *
 * The dialog used to be prefilled with the user's question and three fixed
 * English sentences under "# Procedure"; none of the code that actually ran
 * reached the Skill. The Procedure is now built from the session's executed
 * cells -- each a one-line description and its code -- and the user can
 * untick steps. Top-level Python functions from the kept steps are collected
 * into a "kernel.py (draft)" section: the Web editor saves SKILL.md only, so
 * the draft stays text the author can move into the Skill's sidecar; nothing
 * executable is written from here.
 */

import { copyLookup, type CopyTable } from "../../i18n/copy";

const COPY: CopyTable = {
  en: {
    "skillDraft.steps": "Steps from this session ({0})",
    "skillDraft.stepsHelp": "Untick a step to leave it out of the Procedure.",
    "skillDraft.edited": "You edited the text, so these choices no longer change it.",
  },
  zh: {
    "skillDraft.steps": "本会话执行过的步骤（{0}）",
    "skillDraft.stepsHelp": "取消勾选即可不把该步骤写进 Procedure。",
    "skillDraft.edited": "你已手动修改正文，这里的勾选不再改动它。",
  },
};

export const skillDraftT = copyLookup(COPY);

export type SkillDraftStep = {
  id: string;
  language: string;
  summary: string;
  code: string;
};

export type SkillDraftParts = {
  purpose: string;
  outcome: string;
  steps: SkillDraftStep[];
};

const OK_STATUSES = new Set(["ok", "success", "completed", "done"]);
const COMMENT = /^\s*(?:#|\/\/|--)\s*(.+?)\s*$/;
const MAX_STEPS = 40;
const MAX_CODE = 6000;

function oneLine(text: string, max = 120): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? flat.slice(0, max - 1) + "…" : flat;
}

/**
 * A step's one-line description: its first comment, else what it wrote, else
 * the functions it defines, else its kind.
 */
export function stepSummary(entry: Record<string, unknown>): string {
  const code = String(entry.source || "");
  for (const line of code.split("\n")) {
    if (!line.trim()) continue;
    const comment = COMMENT.exec(line);
    if (comment && comment[1] && !/^!|^-\*-|^%%/.test(comment[1])) return oneLine(comment[1]);
    break;
  }
  const written = Array.isArray(entry.files_written) ? entry.files_written.map(String).filter(Boolean) : [];
  if (written.length) return oneLine("Writes " + written.slice(0, 4).join(", ") + (written.length > 4 ? ", …" : ""));
  const defined = [...code.matchAll(/^(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(/gm)].map((match) => `${match[1]}()`);
  if (defined.length) return oneLine("Defines " + defined.slice(0, 4).join(", ") + (defined.length > 4 ? ", …" : ""));
  const language = String(entry.language || "python");
  return language === "r" ? "Run an R cell" : "Run a Python cell";
}

/** The session's successful, latest-attempt cells, in order, as Procedure steps. */
export function procedureSteps(entries: unknown[]): SkillDraftStep[] {
  const steps: SkillDraftStep[] = [];
  for (const raw of entries) {
    if (!raw || typeof raw !== "object") continue;
    const entry = raw as Record<string, unknown>;
    if (entry.is_latest_attempt === false) continue;
    if (!OK_STATUSES.has(String(entry.status || "ok").toLowerCase())) continue;
    const code = String(entry.source || "").trim();
    if (!code) continue;
    steps.push({
      id: String(entry.producing_cell_id || entry.cell_index || steps.length),
      language: String(entry.language || "python").toLowerCase() === "r" ? "r" : "python",
      summary: stepSummary(entry),
      code: code.length > MAX_CODE ? code.slice(0, MAX_CODE) + "\n# … (truncated)" : code,
    });
  }
  return steps.slice(-MAX_STEPS);
}

/**
 * Top-level `def` blocks from Python steps, by name (a later definition
 * replaces an earlier one, as it would in the kernel).
 */
export function reusableFunctions(steps: SkillDraftStep[]): string[] {
  const byName = new Map<string, string>();
  for (const step of steps) {
    if (step.language !== "python") continue;
    const lines = step.code.split("\n");
    for (let i = 0; i < lines.length; i++) {
      const head = /^(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(/.exec(lines[i] || "");
      if (!head) continue;
      let start = i;
      while (start > 0 && /^@/.test(lines[start - 1] || "")) start--;
      let end = i + 1;
      while (end < lines.length) {
        const line = lines[end] || "";
        if (line.trim() && !/^\s/.test(line)) break;
        end++;
      }
      while (end > i + 1 && !(lines[end - 1] || "").trim()) end--;
      byName.set(head[1] as string, lines.slice(start, end).join("\n"));
      i = end - 1;
    }
  }
  return [...byName.values()];
}

function fence(language: string, code: string): string {
  const ticks = code.includes("```") ? "````" : "```";
  return `${ticks}${language}\n${code}\n${ticks}`;
}

/** The SKILL.md body for the kept steps. */
export function buildSkillBody(parts: SkillDraftParts, kept: ReadonlySet<string>): string {
  const purpose = parts.purpose.trim() || "Describe when this workflow should be used.";
  const chosen = parts.steps.filter((step) => kept.has(step.id));
  const out = [`# Purpose\n\n${purpose}`];
  if (chosen.length) {
    const procedure = chosen.map((step, index) => `${index + 1}. ${step.summary}\n\n${fence(step.language, step.code)}`);
    out.push(`# Procedure\n\n${procedure.join("\n\n")}`);
    const functions = reusableFunctions(chosen);
    if (functions.length) {
      out.push(
        "# kernel.py (draft)\n\nReusable functions from the steps above. To import them from the Skill, save this block as the Skill's kernel.py sidecar; until then it is documentation only.\n\n" +
          fence("python", functions.join("\n\n\n")),
      );
    }
  } else {
    out.push(
      "# Procedure\n\n1. Reproduce the evidence-gathering and analysis workflow.\n2. Preserve data provenance, code, and generated artifacts.\n3. State uncertainty and do not overclaim beyond the evidence.",
    );
  }
  if (parts.outcome.trim()) out.push(`# Example outcome\n\n${parts.outcome.trim().slice(0, 6000)}`);
  return out.join("\n\n") + "\n";
}
