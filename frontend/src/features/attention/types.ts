/**
 * B-05 GET /api/v1/attention item shape.
 *
 * `target.surface` / `target.dock` are closed sets. The client builds
 * navigation locally and never follows a server URL.
 */

export const SOURCE_KINDS = [
  "running",
  "queued",
  "approval",
  "recovery",
  "blocked",
  "compute",
] as const;

export const SURFACES = ["session"] as const;

export const DOCKS = ["timeline", "recovery", "security", "compute"] as const;

export const SEVERITIES = ["high", "medium", "low"] as const;

export const DEFAULT_LIMIT = 50;

/** Matches `features/sessions/dashboard.ts` `startDashPoll` (4000ms). */
export const ATTENTION_POLL_MS = 4000;

export type AttentionSourceKind = (typeof SOURCE_KINDS)[number];
export type AttentionSurface = (typeof SURFACES)[number];
export type AttentionDock = (typeof DOCKS)[number];
export type AttentionSeverity = (typeof SEVERITIES)[number];

/** Exact dock the six B-05 source kinds navigate to. */
export const DOCK_FOR: Record<AttentionSourceKind, AttentionDock> = {
  running: "timeline",
  queued: "timeline",
  approval: "security",
  recovery: "recovery",
  blocked: "recovery",
  compute: "compute",
};

export const HINT_FOR: Record<AttentionSourceKind, string> = {
  running: "watch",
  queued: "watch",
  approval: "approve",
  recovery: "restore",
  blocked: "inspect",
  compute: "inspect",
};

export const SEVERITY_FOR: Record<AttentionSourceKind, AttentionSeverity> = {
  running: "medium",
  queued: "low",
  approval: "high",
  recovery: "high",
  blocked: "medium",
  compute: "medium",
};

/** Right-dock pane that hosts every attention dock (timeline island). */
export const ATTENTION_PANE = "timeline";

/** Selector inside `#dock-timeline` for exact-dock focus after navigation. */
export const DOCK_FOCUS: Record<AttentionDock, string> = {
  timeline: "#dock-timeline",
  recovery: "#dock-timeline [data-action-kind='recovery']",
  security: "#dock-timeline .security-panel",
  compute: "#dock-timeline .compute-panel",
};

export type AttentionTarget = {
  surface: AttentionSurface;
  dock: AttentionDock;
  frame_id: string;
};

export type AttentionItem = {
  id: string;
  source_kind: AttentionSourceKind;
  source_id: string;
  state: string;
  severity: AttentionSeverity;
  frame_id: string;
  project_id: string | null;
  title: string;
  updated_at: number;
  target: AttentionTarget;
  action_hint: string;
};

export type AttentionPage = {
  items: AttentionItem[];
  next_cursor: string | null;
  has_more: boolean;
};

export type AttentionNavigation = {
  surface: AttentionSurface;
  dock: AttentionDock;
  frameId: string;
  projectId: string | null;
  pane: typeof ATTENTION_PANE;
  focusSelector: string;
};

export type AttentionCardModel = {
  id: string;
  sourceKind: AttentionSourceKind;
  sourceId: string;
  state: string;
  severity: AttentionSeverity;
  frameId: string;
  projectId: string | null;
  projectName: string;
  title: string;
  updatedAt: number;
  updatedLabel: string;
  actionHint: string;
  actionLabel: string;
  kindLabel: string;
  navigation: AttentionNavigation;
};

export type ProjectLike = {
  project_id?: string;
  id?: string;
  name?: string;
};
