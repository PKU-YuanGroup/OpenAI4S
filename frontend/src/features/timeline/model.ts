/**
 * Pure Action Timeline geometry / keying.
 * Port of app.js:3441-3671, 3814-3820, 3871-3899 plus timelineEpochMs 3567-3571.
 */

import {
  ACTION_TIMELINE_OVERVIEW_WIDTH,
  ACTION_TIMELINE_ROW_HEIGHT,
} from "../../stores/timeline";
import { publicText } from "../scrub/scrub";
import type {
  LedgerEntry,
  OverviewModel,
  TimelineAttempt,
  TimelineGroup,
  TimelineSpan,
} from "./types";

export const ACTION_TIMELINE_TOP_THRESHOLD = ACTION_TIMELINE_ROW_HEIGHT * 2;
export const ACTION_TIMELINE_BOTTOM_THRESHOLD = 2;
export const ACTION_TIMELINE_OVERVIEW_HEIGHT = 112;
export const ACTION_TIMELINE_OVERVIEW_HOVER_DELAY = 500;
export const TIMELINE_MAX_EPOCH_MS = 8.64e15;

export function latestActionTimelineAttempt(
  group: TimelineGroup | null | undefined,
): TimelineAttempt | null {
  return ((group && group.attempts) || []).slice(-1)[0] || null;
}

export function timelineEpochMs(value: unknown): number | null {
  if (value == null || value === "") return null;
  const number = Number(value);
  const ms = Number.isFinite(number) ? Math.round(number) : Date.parse(String(value));
  return Number.isFinite(ms) && ms >= 0 && ms <= TIMELINE_MAX_EPOCH_MS ? ms : null;
}

export function timelineDurationMs(attempt: TimelineAttempt | null | undefined): number | "" {
  if (!attempt) return "";
  const parse = timelineEpochMs;
  const start = parse(attempt.started_at != null ? attempt.started_at : attempt.allocated_at);
  const end = parse(
    attempt.finished_at != null
      ? attempt.finished_at
      : attempt.capture_at != null
        ? attempt.capture_at
        : attempt.response_at,
  );
  if (start == null || end == null || end < start) return "";
  return end - start;
}

export function timelineDurationValue(ms: number | ""): string {
  return ms === "" || !Number.isFinite(+ms) || +ms < 0
    ? ""
    : +ms < 1000
      ? Math.round(+ms) + " ms"
      : (+ms / 1000).toFixed(+ms < 10000 ? 1 : 0) + " s";
}

export function timelineDuration(attempt: TimelineAttempt | null | undefined): string {
  return timelineDurationValue(timelineDurationMs(attempt));
}

export function timelineTokenTotal(usage: unknown): number {
  const source = (usage || {}) as {
    input_tokens?: number;
    output_tokens?: number;
    total_tokens?: number;
  };
  const input = +source.input_tokens! || 0,
    output = +source.output_tokens! || 0,
    total = +source.total_tokens! || 0;
  return total > 0 ? total : input + output;
}

export function timelineCost(value: unknown): string {
  if (value == null || !Number.isFinite(+value) || +value < 0) return "";
  const amount = +value;
  return "$" + (amount < 0.01 ? amount.toFixed(6) : amount.toFixed(4));
}

export function actionTimelineSpan(
  group: TimelineGroup | null | undefined,
  rank: number,
  laneCount: number,
): TimelineSpan | null {
  const attempt = latestActionTimelineAttempt(group);
  if (!attempt || !group || !group.group_id) return null;
  const times = {
    allocated: timelineEpochMs(attempt.allocated_at),
    started: timelineEpochMs(attempt.started_at),
    response: timelineEpochMs(attempt.response_at),
    capture: timelineEpochMs(attempt.capture_at),
    finished: timelineEpochMs(attempt.finished_at),
  };
  const allocated = times.allocated;
  if (allocated == null) return null;
  const finished = times.finished;
  const segments: TimelineSpan["segments"] = [];
  const addSegment = (phase: string, start: number | null, end: number | null) => {
    if (start != null && end != null && end >= start) segments.push({ phase, start, end });
  };
  const running = finished == null;
  if (!running && finished >= allocated) {
    addSegment("queue", allocated, times.started);
    addSegment("ttft", times.started, times.response);
    addSegment("decode", times.response, finished);
  }
  if (!running && finished < allocated) return null;
  const latestKnown = [allocated, times.started, times.response, times.capture]
    .filter((value): value is number => value != null && value >= allocated)
    .reduce((latest, value) => Math.max(latest, value), allocated);
  return {
    groupId: group.group_id,
    group,
    attempt,
    rank,
    laneCount,
    times,
    segments,
    start: allocated,
    end: running ? latestKnown : finished,
    markerAt: running ? times.allocated : null,
    pointAt:
      !running && !segments.some((segment) => segment.end > segment.start)
        ? times.allocated
        : null,
    running,
  };
}

export function actionTimelineOverviewModel(
  groups: TimelineGroup[],
  domainGroups: TimelineGroup[] = groups,
): OverviewModel {
  const drawableItems = groups
    .map((group) => actionTimelineSpan(group, 0, 1))
    .filter((item): item is TimelineSpan => !!item);
  const items: TimelineSpan[] = [],
    byId = new Map<string, TimelineSpan>(),
    laneCount = Math.max(1, drawableItems.length);
  let dataStart: number | null = null,
    dataEnd: number | null = null;
  drawableItems.forEach((item, rank) => {
    item.rank = rank;
    item.laneCount = laneCount;
    items.push(item);
    byId.set(item.groupId, item);
  });
  domainGroups.forEach((group) => {
    const attempt = latestActionTimelineAttempt(group);
    if (!attempt) return;
    [
      attempt.allocated_at,
      attempt.started_at,
      attempt.response_at,
      attempt.capture_at,
      attempt.finished_at,
    ].forEach((raw) => {
      const value = timelineEpochMs(raw);
      if (value == null) return;
      dataStart = dataStart == null ? value : Math.min(dataStart, value);
      dataEnd = dataEnd == null ? value : Math.max(dataEnd, value);
    });
  });
  return { items, byId, laneCount, dataStart, dataEnd };
}

export type OverviewView = {
  viewStart: number | null;
  viewEnd: number | null;
};

export function timelineOverviewTimeToX(
  overview: OverviewView,
  value: number | null,
): number | null {
  const start = overview.viewStart,
    end = overview.viewEnd;
  if (start == null || end == null || value == null) return null;
  if (end === start) return ACTION_TIMELINE_OVERVIEW_WIDTH / 2;
  return ((value - start) / (end - start)) * ACTION_TIMELINE_OVERVIEW_WIDTH;
}

export function timelineOverviewPathRect(
  x1: number,
  x2: number,
  y1: number,
  y2: number,
): string {
  if (![x1, x2, y1, y2].every(Number.isFinite) || x2 <= x1 || y2 <= y1) return "";
  const n = (value: number) => Number(value.toFixed(3));
  return `M${n(x1)},${n(y1)}H${n(x2)}V${n(y2)}H${n(x1)}Z`;
}

export function timelineOverviewItemPaths(
  overview: OverviewView,
  item: TimelineSpan,
): Record<string, string> {
  const laneHeight = ACTION_TIMELINE_OVERVIEW_HEIGHT / item.laneCount;
  const padding = Math.min(0.22, laneHeight * 0.12),
    y1 = item.rank * laneHeight + padding,
    y2 = (item.rank + 1) * laneHeight - padding;
  const paths: Record<string, string> = {
    queue: "",
    ttft: "",
    decode: "",
    marker: "",
    point: "",
    highlight: "",
  };
  item.segments.forEach((segment) => {
    const rawX1 = timelineOverviewTimeToX(overview, segment.start),
      rawX2 = timelineOverviewTimeToX(overview, segment.end);
    if (
      rawX1 == null ||
      rawX2 == null ||
      rawX2 < 0 ||
      rawX1 > ACTION_TIMELINE_OVERVIEW_WIDTH
    )
      return;
    const rect = timelineOverviewPathRect(
      Math.max(0, rawX1),
      Math.min(ACTION_TIMELINE_OVERVIEW_WIDTH, rawX2),
      y1,
      y2,
    );
    paths[segment.phase] = (paths[segment.phase] || "") + rect;
    paths.highlight += rect;
  });
  const markerAt = item.markerAt != null ? item.markerAt : item.pointAt;
  if (markerAt != null) {
    const x = timelineOverviewTimeToX(overview, markerAt),
      center = (y1 + y2) / 2;
    if (x != null && x >= 0 && x <= ACTION_TIMELINE_OVERVIEW_WIDTH) {
      const n = (value: number) => Number(value.toFixed(3));
      const marker = `M${n(x)},${n(Math.max(0, center - 0.9))}V${n(Math.min(ACTION_TIMELINE_OVERVIEW_HEIGHT, center + 0.9))}`;
      paths[item.running ? "marker" : "point"] += marker;
      paths.highlight += marker;
    }
  }
  return paths;
}

export function actionTimelineOverviewVisualExtent(
  item: TimelineSpan | null | undefined,
): { start: number; end: number } | null {
  if (!item) return null;
  const values: number[] = [];
  item.segments.forEach((segment) => {
    values.push(segment.start, segment.end);
  });
  if (item.markerAt != null) values.push(item.markerAt);
  if (item.pointAt != null) values.push(item.pointAt);
  const finite = values.filter(Number.isFinite);
  return finite.length ? { start: Math.min(...finite), end: Math.max(...finite) } : null;
}

export function actionTimelineSelectionOverlaps(
  item: { start: number; end: number } | null | undefined,
  selection: { start: number; end: number } | null | undefined,
  group: TimelineGroup | null = null,
): boolean {
  if (!selection) return true;
  const left = Math.min(selection.start, selection.end),
    right = Math.max(selection.start, selection.end);
  if (item) return item.start <= right && item.end >= left;
  const createdAt = timelineEpochMs(group && group.created_at);
  return createdAt != null && createdAt >= left && createdAt <= right;
}

export function actionTimelineTurnStats(groups: TimelineGroup[]): {
  count: number;
  totalMs: number | null;
  hasRunning: boolean;
  duration: string;
} {
  let totalMs = 0,
    hasDuration = false,
    hasRunning = false;
  groups.forEach((group) => {
    const attempt = latestActionTimelineAttempt(group),
      duration = timelineDurationMs(attempt);
    if (duration !== "") {
      totalMs += duration;
      hasDuration = true;
    }
    if (attempt && timelineEpochMs(attempt.finished_at) == null) hasRunning = true;
  });
  const duration = hasDuration
    ? (hasRunning ? "≥ " : "") + timelineDurationValue(totalMs)
    : "—";
  return {
    count: groups.length,
    totalMs: hasDuration ? totalMs : null,
    hasRunning,
    duration,
  };
}

export function actionTimelineLedgerEntries(
  view: { searchNeedle?: string; collapsedTurns: Set<string> },
  groups: TimelineGroup[],
): LedgerEntry[] {
  const turns = new Map<string, TimelineGroup[]>();
  groups.forEach((group) => {
    const turnId = publicText(group.turn_id, 96);
    if (!turnId) return;
    if (!turns.has(turnId)) turns.set(turnId, []);
    turns.get(turnId)!.push(group);
  });
  const stats = new Map<
    string,
    { count: number; totalMs: number | null; hasRunning: boolean; duration: string }
  >();
  turns.forEach((turnGroups, turnId) =>
    stats.set(turnId, actionTimelineTurnStats(turnGroups)),
  );
  const entries: LedgerEntry[] = [],
    emittedCollapsed = new Set<string>(),
    searchActive = !!view.searchNeedle;
  groups.forEach((group, index) => {
    const turnId = publicText(group.turn_id, 96);
    const previousTurnId =
      index > 0 ? publicText(groups[index - 1]!.turn_id, 96) : "";
    const turnStart = index === 0 || previousTurnId !== turnId;
    const turnBoundary = index > 0 && previousTurnId !== turnId;
    if (!searchActive && turnId && view.collapsedTurns.has(turnId)) {
      if (emittedCollapsed.has(turnId)) return;
      emittedCollapsed.add(turnId);
      entries.push({
        type: "turn",
        turnId,
        groups: turns.get(turnId) || [],
        stats: stats.get(turnId) || {
          count: 0,
          totalMs: null,
          hasRunning: false,
          duration: "—",
        },
        turnBoundary,
      });
      return;
    }
    entries.push({
      type: "group",
      group,
      turnId,
      turnStart,
      turnBoundary,
      stats: turnId ? stats.get(turnId) || null : null,
      foldable: !!turnId && !searchActive,
    });
  });
  return entries;
}

export function actionTimelineEntryKey(entry: LedgerEntry | null | undefined): string {
  return !entry
    ? ""
    : entry.type === "turn"
      ? "turn:" + entry.turnId
      : "group:" + entry.group.group_id;
}

export function timelineOverviewXToTime(
  overview: OverviewView,
  x: number,
): number | null {
  if (overview.viewStart == null || overview.viewEnd == null) return null;
  if (overview.viewStart === overview.viewEnd) return overview.viewStart;
  const ratio = Math.max(0, Math.min(1, x / ACTION_TIMELINE_OVERVIEW_WIDTH));
  return overview.viewStart + ratio * (overview.viewEnd - overview.viewStart);
}

export function timelineOverviewXToDomainTime(
  start: number | null,
  end: number | null,
  x: number,
): number | null {
  if (start == null || end == null) return null;
  if (start === end) return start;
  const ratio = Math.max(0, Math.min(1, x / ACTION_TIMELINE_OVERVIEW_WIDTH));
  return start + ratio * (end - start);
}

export function timelineOverviewExactTime(value: unknown): string {
  const ms = timelineEpochMs(value);
  return ms == null ? "—" : new Date(ms).toISOString();
}

export function timelineOverviewExactDuration(start: unknown, end: unknown): string {
  const left = timelineEpochMs(start),
    right = timelineEpochMs(end);
  return left == null || right == null || right < left ? "—" : String(right - left) + " ms";
}
