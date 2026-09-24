/**
 * The skills catalog (`GET /skills/catalog`), loaded once and shared.
 *
 * The `/` completions, `send()`'s `/skill` directive and the command palette
 * each had their own loader, and each stored a failed read as `[]` -- an
 * empty array is truthy, so one failure left `/` completions empty and
 * dropped every `/skill` directive for the life of the page. Here a failure
 * rejects and is not stored, so the next caller retries; concurrent callers
 * share the one request in flight.
 */

import { skillsCatalog } from "../../stores/customize";
import { api } from "../sessions/api";

export type SkillCatalogRow = {
  name?: string;
  displayName?: string;
  description?: string;
  [key: string]: unknown;
};

let inflight: Promise<SkillCatalogRow[]> | null = null;

/** Resolves the catalog (a stored one without a request); rejects on a failed read. */
export function loadSkillsCatalog(): Promise<SkillCatalogRow[]> {
  const stored = skillsCatalog.value;
  if (Array.isArray(stored)) return Promise.resolve(stored as SkillCatalogRow[]);
  if (inflight) return inflight;
  const pending = api("/skills/catalog").then((d) => {
    const body = d && typeof d === "object" ? (d as { skills?: unknown }) : null;
    const rows = Array.isArray(body?.skills) ? (body.skills as SkillCatalogRow[]) : [];
    skillsCatalog.value = rows;
    return rows;
  });
  inflight = pending;
  const settle = (): void => {
    if (inflight === pending) inflight = null;
  };
  void pending.then(settle, settle);
  return pending;
}
