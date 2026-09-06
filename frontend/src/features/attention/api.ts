import { api } from "../sessions/api";
import { cardsFromItems, parseAttentionPage } from "./parse";
import { readPollFlags, shouldFetchAttention } from "./poll";
import {
  attentionCards,
  attentionError,
  attentionHasMore,
  attentionLoading,
  attentionNextCursor,
  attentionReq,
} from "./state";
import { projects } from "../../stores/session";
import { DEFAULT_LIMIT } from "./types";
import type { AttentionPage, ProjectLike } from "./types";

export async function fetchAttentionPage(
  cursor?: string | null,
): Promise<AttentionPage> {
  const q = new URLSearchParams();
  q.set("limit", String(DEFAULT_LIMIT));
  if (cursor) q.set("cursor", cursor);
  const data = await api(`/attention?${q.toString()}`);
  return parseAttentionPage(data);
}

export async function refreshAttention(): Promise<void> {
  if (!shouldFetchAttention(readPollFlags())) return;
  const mine = attentionReq.value + 1;
  attentionReq.value = mine;
  attentionLoading.value = true;
  try {
    const page = await fetchAttentionPage();
    if (mine !== attentionReq.value) return;
    if (!shouldFetchAttention(readPollFlags())) return;
    attentionCards.value = cardsFromItems(page.items, {
      projects: projects.value as ProjectLike[],
    });
    attentionHasMore.value = page.has_more;
    attentionNextCursor.value = page.next_cursor;
    attentionError.value = null;
  } catch (err) {
    if (mine !== attentionReq.value) return;
    const message = err instanceof Error ? err.message : String(err);
    attentionError.value = message;
  } finally {
    if (mine === attentionReq.value) attentionLoading.value = false;
  }
}
