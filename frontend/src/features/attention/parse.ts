/**
 * Validate GET /attention items and map each closed-set fact to one card.
 *
 * Unknown kinds, docks outside the closed set, and completed/idle rows
 * produce zero cards. Duplicate `source_kind+source_id` keep the first
 * (the page is already newest-first).
 */

import { publicText } from "../scrub/scrub";
import { attentionT } from "./copy";
import { navigationFromTarget } from "./navigate";
import {
  DOCKS,
  HINT_FOR,
  SEVERITIES,
  SOURCE_KINDS,
  SURFACES,
  type AttentionCardModel,
  type AttentionDock,
  type AttentionItem,
  type AttentionPage,
  type AttentionSeverity,
  type AttentionSourceKind,
  type AttentionSurface,
  type ProjectLike,
} from "./types";

export type CardContext = {
  projects?: readonly ProjectLike[];
  now?: number;
};

function rec(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

export function isSourceKind(value: unknown): value is AttentionSourceKind {
  return typeof value === "string" && (SOURCE_KINDS as readonly string[]).includes(value);
}

function isDock(value: unknown): value is AttentionDock {
  return typeof value === "string" && (DOCKS as readonly string[]).includes(value);
}

function isSurface(value: unknown): value is AttentionSurface {
  return typeof value === "string" && (SURFACES as readonly string[]).includes(value);
}

function isSeverity(value: unknown): value is AttentionSeverity {
  return typeof value === "string" && (SEVERITIES as readonly string[]).includes(value);
}

function asMs(value: unknown): number {
  if (value == null || value === "") return 0;
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return n > 10_000_000_000 ? Math.trunc(n) : Math.trunc(n * 1000);
}

export function agoFromMs(ms: number, now = Date.now()): string {
  if (!ms) return "";
  const d = (now - ms) / 1000;
  if (d < 60) return "just now";
  if (d < 3600) return (d / 60 | 0) + "m";
  if (d < 86400) return (d / 3600 | 0) + "h";
  return (d / 86400 | 0) + "d";
}

export function projectNameFor(
  projectId: string | null,
  projects: readonly ProjectLike[] | undefined,
): string {
  if (!projectId || !projects) return "";
  const hit = projects.find((p) => (p.project_id || p.id) === projectId);
  return (hit && hit.name) || "";
}

export function actionLabelFor(hint: string): string {
  const raw = String(hint || "").trim();
  const queued = /^queue:(\d+)$/.exec(raw);
  if (queued) return attentionT("attention.hint.queue", queued[1]);
  const key = "attention.hint." + raw;
  const labeled = attentionT(key);
  return labeled !== key ? labeled : raw;
}

export function kindLabelFor(kind: AttentionSourceKind): string {
  return attentionT("attention.kind." + kind);
}

export function parseAttentionTarget(
  value: unknown,
  frameId: string,
): AttentionItem["target"] | null {
  const row = rec(value);
  if (!row) return null;
  const surface = row.surface;
  const dock = row.dock;
  const targetFrame =
    typeof row.frame_id === "string" && row.frame_id.trim()
      ? row.frame_id.trim()
      : frameId;
  if (!isSurface(surface) || !isDock(dock) || !targetFrame) return null;
  if (targetFrame !== frameId) return null;
  return { surface, dock, frame_id: targetFrame };
}

export function parseAttentionItem(value: unknown): AttentionItem | null {
  const row = rec(value);
  if (!row) return null;
  if (!isSourceKind(row.source_kind)) return null;
  const kind = row.source_kind;
  const sourceId = typeof row.source_id === "string" ? row.source_id.trim() : "";
  if (!sourceId) return null;
  const frameId = typeof row.frame_id === "string" ? row.frame_id.trim() : "";
  if (!frameId) return null;
  const target = parseAttentionTarget(row.target, frameId);
  if (!target) return null;
  const severity = isSeverity(row.severity) ? row.severity : null;
  if (!severity) return null;
  const title = publicText(row.title, 160) || attentionT("attention.untitled");
  const hintRaw =
    typeof row.action_hint === "string" && row.action_hint.trim()
      ? row.action_hint.trim()
      : HINT_FOR[kind];
  const id =
    typeof row.id === "string" && row.id.trim()
      ? row.id.trim()
      : `${kind}:${sourceId}`;
  const projectId =
    typeof row.project_id === "string" && row.project_id.trim()
      ? row.project_id.trim()
      : null;
  return {
    id,
    source_kind: kind,
    source_id: sourceId,
    state: publicText(row.state, 40) || kind,
    severity,
    frame_id: frameId,
    project_id: projectId,
    title,
    updated_at: asMs(row.updated_at),
    target,
    action_hint: publicText(hintRaw, 40) || HINT_FOR[kind],
  };
}

export function parseAttentionPage(value: unknown): AttentionPage {
  const row = rec(value);
  const rawItems = row && Array.isArray(row.items) ? row.items : [];
  const items: AttentionItem[] = [];
  for (const entry of rawItems) {
    const item = parseAttentionItem(entry);
    if (item) items.push(item);
  }
  const next =
    row && typeof row.next_cursor === "string" && row.next_cursor
      ? row.next_cursor
      : null;
  return {
    items,
    next_cursor: next,
    has_more: !!(row && row.has_more),
  };
}

export function cardFromItem(
  item: AttentionItem,
  ctx: CardContext = {},
): AttentionCardModel | null {
  const navigation = navigationFromTarget(item.target, item.project_id);
  if (!navigation) return null;
  const projectName = projectNameFor(item.project_id, ctx.projects);
  return {
    id: item.id,
    sourceKind: item.source_kind,
    sourceId: item.source_id,
    state: item.state,
    severity: item.severity,
    frameId: item.frame_id,
    projectId: item.project_id,
    projectName,
    title: item.title,
    updatedAt: item.updated_at,
    updatedLabel: agoFromMs(item.updated_at, ctx.now),
    actionHint: item.action_hint,
    actionLabel: actionLabelFor(item.action_hint),
    kindLabel: kindLabelFor(item.source_kind),
    navigation,
  };
}

/**
 * One card per fact. Invalid / idle / completed rows (kinds outside the
 * six-state closed set) are dropped. Duplicates keyed by source_kind+source_id
 * keep the first occurrence.
 */
export function cardsFromItems(
  items: unknown,
  ctx: CardContext = {},
): AttentionCardModel[] {
  const list = Array.isArray(items) ? items : [];
  const seen = new Set<string>();
  const cards: AttentionCardModel[] = [];
  for (const entry of list) {
    const item = parseAttentionItem(entry);
    if (!item) continue;
    const key = item.source_kind + "\0" + item.source_id;
    if (seen.has(key)) continue;
    const card = cardFromItem(item, ctx);
    if (!card) continue;
    seen.add(key);
    cards.push(card);
  }
  return cards;
}
