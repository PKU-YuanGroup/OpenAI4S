/**
 * search_skills result rendering: lexical list stays as-is; the W2-A dict
 * shape ({results, semantic_status, semantic_suggestions}) gets a separate
 * experimental-suggestion chip row above the lexical hits.
 */

import { t } from "../../i18n/runtime";
import { el } from "../messages/dom";
import { judgmentT } from "./copy";
import "./judgment.css";

export type SemanticSuggestion = {
  name: string;
  p_fit: number | null;
  confidence: number | null;
  template_version: string;
};

export type SkillSearchView = {
  kind: "list" | "dict";
  suggestions: SemanticSuggestion[];
  status: string | null;
  lexicalNames: string[];
};

function rec(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatProb(value: number | null): string {
  if (value == null) return "";
  return value.toFixed(2);
}

function nameOf(row: unknown): string {
  if (typeof row === "string") return row;
  const item = rec(row);
  if (!item) return "";
  return item.name != null ? String(item.name) : "";
}

function parseSuggestion(row: unknown): SemanticSuggestion | null {
  const item = rec(row);
  if (!item) return null;
  const name = nameOf(item);
  if (!name) return null;
  return {
    name,
    p_fit: asNumber(item.p_fit),
    confidence: asNumber(item.confidence),
    template_version:
      item.template_version != null ? String(item.template_version) : "",
  };
}

/** Classify a search_skills tool/step payload. List form never grows chips. */
export function skillSearchView(output: unknown): SkillSearchView {
  if (Array.isArray(output)) {
    return {
      kind: "list",
      suggestions: [],
      status: null,
      lexicalNames: output.map(nameOf).filter(Boolean),
    };
  }
  const out = rec(output);
  if (!out) {
    return { kind: "list", suggestions: [], status: null, lexicalNames: [] };
  }
  const dictShape =
    "semantic_suggestions" in out || "semantic_status" in out;
  if (!dictShape) {
    const skills = Array.isArray(out.skills) ? out.skills : [];
    return {
      kind: "list",
      suggestions: [],
      status: null,
      lexicalNames: skills.map(nameOf).filter(Boolean),
    };
  }
  const suggestions = Array.isArray(out.semantic_suggestions)
    ? out.semantic_suggestions.map(parseSuggestion).filter((row): row is SemanticSuggestion => !!row)
    : [];
  const results = Array.isArray(out.results) ? out.results : [];
  const skills = Array.isArray(out.skills) ? out.skills : [];
  const lexicalNames = (results.length ? results : skills).map(nameOf).filter(Boolean);
  return {
    kind: "dict",
    suggestions,
    status: typeof out.semantic_status === "string" ? out.semantic_status : null,
    lexicalNames,
  };
}

function chipEl(row: SemanticSuggestion): HTMLElement {
  const parts = [row.name];
  if (row.p_fit != null) parts.push(judgmentT("judgment.chipFit", formatProb(row.p_fit)));
  if (row.confidence != null) {
    parts.push(judgmentT("judgment.chipConf", formatProb(row.confidence)));
  }
  const chip = el("span", "judgment-chip", parts.join(" · "));
  chip.dataset.judgmentChip = row.name;
  if (row.template_version) chip.title = row.template_version;
  return chip;
}

/**
 * When `output` is the W2-A dict, paint chips (and optional status) above
 * lexical names. Returns false for the legacy list / `{skills}` shape so the
 * existing skill-step renderer stays byte-for-byte.
 */
export function appendSemanticSkillSearch(
  box: HTMLElement,
  output: unknown,
): boolean {
  const view = skillSearchView(output);
  if (view.kind !== "dict") return false;
  if ("semantic_suggestions" in (rec(output) || {})) {
    const row = el("div", "judgment-chips");
    row.dataset.judgmentChips = "1";
    row.appendChild(el("span", "judgment-chip-label", judgmentT("judgment.chips")));
    view.suggestions.forEach((item) => row.appendChild(chipEl(item)));
    box.appendChild(row);
  }
  if (view.status === "unavailable" || view.status === "uncertain") {
    const note = el(
      "div",
      "judgment-semantic-note",
      judgmentT("judgment.status." + view.status),
    );
    note.dataset.judgmentSemanticStatus = view.status;
    box.appendChild(note);
  }
  if (view.lexicalNames.length) {
    box.appendChild(el("div", "s-note", t("step.skill.list", view.lexicalNames.join(", "))));
  }
  return true;
}
