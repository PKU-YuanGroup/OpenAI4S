/**
 * One assistant turn is one `.msg.assistant`: its prose blocks (`.md`) and its
 * cards (steps, approvals, cell activity) in the order they happened, and one
 * action row for the whole turn.
 *
 * The live stream and the stored transcript both assemble a turn through
 * these functions. They used to be two implementations: live built one
 * container per turn, a reload rendered every stored narration paragraph as a
 * message of its own (its own copy / rating row) with the cards as siblings.
 */

import { el } from "./dom";

export type TurnGroup = {
  wrap: HTMLElement;
  /** The prose block text is going into: the one after the last card. */
  md: HTMLElement;
};

export function openTurnGroup(): TurnGroup {
  const wrap = el("div", "msg assistant");
  const md = el("div", "md");
  wrap.appendChild(md);
  return { wrap, md };
}

/** Append a card where the turn is now; prose after it starts a new block. */
export function placeCardInTurn(group: TurnGroup, card: HTMLElement): HTMLElement {
  group.wrap.appendChild(card);
  group.md = el("div", "md");
  group.wrap.appendChild(group.md);
  return group.md;
}

/** A prose block with nothing in it (the stream's sealed/tail pair counts as nothing). */
function emptyProse(node: Element): boolean {
  if (!node.classList.contains("md") || (node.textContent || "").trim()) return false;
  return Array.from(node.children).every(
    (kid) => !kid.children.length && (kid.classList.contains("md-sealed") || kid.classList.contains("md-tail")),
  );
}

/** A card always leaves a prose block behind it; the unused ones go when the turn ends. */
export function dropEmptyProse(wrap: HTMLElement): void {
  Array.from(wrap.children).forEach((node) => {
    if (emptyProse(node)) node.remove();
  });
}

/**
 * Remove a stored turn whose cards all moved into the live turn (a reopened
 * running session: its replay takes each stored card to where it happened).
 */
export function dropTurnGroupIfEmpty(wrap: Element | null | undefined): void {
  if (!wrap || !wrap.classList.contains("msg") || !wrap.classList.contains("assistant")) return;
  if (Array.from(wrap.children).every(emptyProse)) wrap.remove();
}

/**
 * The turn's prose as one text: what its Copy button copies and what a rating
 * is keyed on. Whitespace is collapsed for the key, so the live stream (chunks
 * as they arrived) and the stored rows (one per paragraph) name the same turn.
 */
export function turnTextKey(text: string): string {
  return String(text || "").replace(/\s+/g, " ").trim();
}
