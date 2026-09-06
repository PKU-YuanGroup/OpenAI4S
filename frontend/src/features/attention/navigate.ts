/**
 * Closed-set local navigation for attention cards.
 *
 * The server returns `{surface, dock, frame_id}` only. This module builds
 * the session path and dock focus locally. URL / href / uri / link / path
 * fields on the payload are ignored even if a buggy server sends them.
 */

import { isReady } from "../../compat/stub";
import { binds } from "../sessions/binds";
import { callLane } from "../sessions/lane";
import {
  ATTENTION_PANE,
  DOCK_FOCUS,
  DOCKS,
  SURFACES,
  type AttentionDock,
  type AttentionNavigation,
  type AttentionSurface,
} from "./types";

const URL_KEYS = new Set(["url", "href", "uri", "link", "path"]);

export function isAttentionSurface(value: unknown): value is AttentionSurface {
  return typeof value === "string" && (SURFACES as readonly string[]).includes(value);
}

export function isAttentionDock(value: unknown): value is AttentionDock {
  return typeof value === "string" && (DOCKS as readonly string[]).includes(value);
}

function rec(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

/**
 * Build local navigation from a closed-set target.
 * Returns null when surface/dock/frame_id are missing or outside the set.
 */
export function navigationFromTarget(
  target: unknown,
  projectId?: string | null,
): AttentionNavigation | null {
  const row = rec(target);
  if (!row) return null;
  const surface = row.surface;
  const dock = row.dock;
  const frameId = typeof row.frame_id === "string" ? row.frame_id.trim() : "";
  if (!isAttentionSurface(surface) || !isAttentionDock(dock) || !frameId) return null;
  return {
    surface,
    dock,
    frameId,
    projectId: projectId ? String(projectId) : null,
    pane: ATTENTION_PANE,
    focusSelector: DOCK_FOCUS[dock],
  };
}

/** Client-owned session path. Never derived from a server URL field. */
export function localSessionPath(nav: AttentionNavigation): string {
  const pid = encodeURIComponent(nav.projectId || "default");
  const fid = encodeURIComponent(nav.frameId);
  return `/projects/${pid}/frames/${fid}`;
}

/** True when `target` carries a URL-shaped key the client must not follow. */
export function targetHasUrlField(target: unknown): boolean {
  const row = rec(target);
  if (!row) return false;
  return Object.keys(row).some((key) => URL_KEYS.has(key.toLowerCase()));
}

export function focusDock(dock: AttentionDock): boolean {
  if (typeof document === "undefined") return false;
  const selector = DOCK_FOCUS[dock];
  const node = document.querySelector(selector);
  if (!(node instanceof HTMLElement)) return false;
  node.scrollIntoView({ block: "nearest", inline: "nearest" });
  return true;
}

function scheduleDockFocus(dock: AttentionDock): void {
  if (typeof document === "undefined") return;
  let attempts = 0;
  const tick = (): void => {
    if (focusDock(dock) || attempts >= 8) return;
    attempts += 1;
    window.setTimeout(tick, 120);
  };
  tick();
}

/**
 * Open the exact Session, switch the timeline pane, then focus the
 * closed-set dock panel. Does not read or assign location from the item.
 */
export async function applyNavigation(nav: AttentionNavigation): Promise<void> {
  const bag = globalThis as unknown as {
    openConversation?: (fid: string, pid?: string | null) => unknown;
  };
  if (isReady(bag.openConversation)) {
    await bag.openConversation(nav.frameId, nav.projectId);
  } else {
    await binds.openConversation(nav.frameId, nav.projectId);
  }
  callLane("setActiveTab", nav.pane);
  scheduleDockFocus(nav.dock);
}
