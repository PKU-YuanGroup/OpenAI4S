/**
 * retry / approve / restore stay on the existing mutation routes.
 *
 * The dashboard card is read-only: click navigates to the dock that already
 * owns the safety UI (permission card, recovery actions). This module only
 * names those routes so the client never invents a new write path.
 */

export type AttentionMutationKind = "approve" | "restore" | "retry";

export type AttentionMutationRoute = {
  kind: AttentionMutationKind;
  method: "POST";
  path: string;
};

const HINT_KIND: Record<string, AttentionMutationKind> = {
  approve: "approve",
  restore: "restore",
  retry: "retry",
};

export function mutationKindForHint(hint: string): AttentionMutationKind | null {
  const base = String(hint || "")
    .split(":")[0]
    ?.trim()
    .toLowerCase();
  if (!base) return null;
  return HINT_KIND[base] ?? null;
}

/**
 * Existing routes (F-11 permission decision, F-15 recovery actions).
 * `sourceId` is the permission `decision_id` for approve; unused for
 * restore/retry (those act on the current recovery projection).
 */
export function existingMutationRoute(
  kind: AttentionMutationKind,
  frameId: string,
  _sourceId?: string,
): AttentionMutationRoute {
  const fid = encodeURIComponent(frameId);
  if (kind === "approve") {
    return { kind, method: "POST", path: `/frames/${fid}/decision` };
  }
  return {
    kind,
    method: "POST",
    path: `/frames/${fid}/recovery/actions/${kind}`,
  };
}

export function mutationRouteForHint(
  hint: string,
  frameId: string,
  sourceId?: string,
): AttentionMutationRoute | null {
  const kind = mutationKindForHint(hint);
  if (!kind || !frameId) return null;
  return existingMutationRoute(kind, frameId, sourceId);
}
