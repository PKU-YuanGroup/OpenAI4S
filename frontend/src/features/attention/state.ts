import { signal } from "@preact/signals";
import type { AttentionCardModel } from "./types";

/** Lane-local M-02 signals. Not promoted into `stores/`. */
export const attentionCards = signal<AttentionCardModel[]>([]);
export const attentionLoading = signal(false);
export const attentionError = signal<string | null>(null);
export const attentionReq = signal(0);
export const attentionHasMore = signal(false);
export const attentionNextCursor = signal<string | null>(null);

export function resetAttentionState(): void {
  attentionCards.value = [];
  attentionLoading.value = false;
  attentionError.value = null;
  attentionHasMore.value = false;
  attentionNextCursor.value = null;
  attentionReq.value = (attentionReq.value || 0) + 1;
}
