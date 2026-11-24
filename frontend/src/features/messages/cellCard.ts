/**
 * The conversation's code-cell card, one implementation for both ends of a
 * turn (M-69).
 *
 * `stream.ts` used to build this card inline from a Cell's `⚙` header chunk,
 * and nothing else built it: a finished session reopened with every code card
 * gone, only the narration around them left ("The cell finished. First lines
 * of its output: …"), and nothing said which code had produced it -- a failed
 * cell's "Failed · 5 output lines" row included. A reopened turn now gets the
 * same card from the Cell's execution-log record, placed where the live turn
 * showed it (`started_at`), titled as the live card was (`title`, the server's
 * `cell_run.activity_title`), and with the same outcome, line count and
 * duration.
 */

import { paintIcon } from "../icons/paths";
import { settleStoredCellCard } from "./cardState";
import { OUTPUT_DIVIDER, bindStreamingPre, toolMetaLabel, type StreamingPreHandle } from "./delta";
import { el, messagesHost } from "./dom";

export type ActivityCard = {
  card: HTMLElement;
  head: HTMLElement;
  meta: HTMLElement;
  pre: HTMLElement;
  handle: StreamingPreHandle;
};

function streamingPre(): { pre: HTMLElement; handle: StreamingPreHandle } {
  const pre = el("pre");
  const text = document.createTextNode("");
  pre.appendChild(text);
  return { pre, handle: bindStreamingPre(text, "") };
}

/** `.activity > .a-head (glyph, label, meta, chevron) + pre`; the head toggles it open. */
export function buildActivityCard(label: string, subagent = false): ActivityCard {
  const card = el("div", "activity" + (subagent ? " subagent" : ""));
  const head = el("div", "a-head");
  const glyph = el("span", "ic");
  paintIcon(glyph, "check", 16);
  head.appendChild(glyph);
  head.appendChild(el("span", "lbl", label));
  const meta = el("span", "meta", "");
  head.appendChild(meta);
  const chevron = el("span", "chev-t");
  paintIcon(chevron, "chevron-down", 14);
  head.appendChild(chevron);
  const { pre, handle } = streamingPre();
  card.appendChild(head);
  card.appendChild(pre);
  head.onclick = () => card.classList.toggle("open");
  return { card, head, meta, pre, handle };
}

/**
 * A card already on screen, made ready to be streamed into again: a reopened
 * running session renders the turn's finished Cells from history, and the
 * turn's replay then sends each one's header, code and output once more.
 */
export function resetActivityCard(card: HTMLElement, label: string): ActivityCard {
  const head = card.querySelector(".a-head") as HTMLElement;
  const meta = card.querySelector(".meta") as HTMLElement;
  const lbl = card.querySelector(".lbl") as HTMLElement | null;
  if (lbl) lbl.textContent = label;
  meta.textContent = "";
  // The recorded time stays until the replayed outcome repaints it, from the
  // recorded duration (`dataset.recordedMs`, NB-4) rather than the replay's.
  if (!card.dataset.recordedMs) head.querySelector(".a-time")?.remove();
  for (const state of ["ok", "failed", "stopped", "ended", "declined", "running"]) card.classList.remove(state);
  delete card.dataset.state;
  delete card.dataset.generatedCell;
  delete card.dataset.startedAt;
  const { pre, handle } = streamingPre();
  card.querySelector("pre")?.remove();
  card.appendChild(pre);
  head.onclick = () => card.classList.toggle("open");
  return { card, head, meta, pre, handle };
}

/** One entry of `GET /frames/{fid}/execution-log`, as far as the card reads it. */
export type StoredCell = {
  producing_cell_id?: unknown;
  cell_index?: unknown;
  origin?: unknown;
  title?: unknown;
  source?: unknown;
  stdout?: unknown;
  stderr?: unknown;
  error?: unknown;
  status?: unknown;
  started_at?: unknown;
  finished_at?: unknown;
  [key: string]: unknown;
};

/** Origins whose Cells the conversation shows as cards: the agent's and the example session's (SCI-03). */
const CHAT_ORIGINS = new Set(["agent", "example"]);

/**
 * The Cells the conversation showed a card for: the agent's own and the
 * example session's seed (a Notebook REPL cell never had one), with the time
 * they ran (a daemon that predates the field cannot place them).
 */
export function chatCells(entries: unknown): StoredCell[] {
  if (!Array.isArray(entries)) return [];
  return (entries as StoredCell[]).filter(
    (cell) =>
      !!cell &&
      typeof cell === "object" &&
      CHAT_ORIGINS.has(String(cell.origin)) &&
      typeof cell.producing_cell_id === "string" &&
      Number(cell.started_at) > 0,
  );
}

/** The title the live card had: the server's, else its generated "Running analysis · cell N". */
function cellTitle(cell: StoredCell): string {
  const title = typeof cell.title === "string" ? cell.title.trim() : "";
  return title || `Running analysis · cell ${cell.cell_index == null ? "" : String(cell.cell_index)}`.trim();
}

/** What the live stream sent after the header: the code, the divider, then the output. */
function streamedParts(cell: StoredCell): [string, string] {
  const error = String(cell.error || "");
  return [
    String(cell.source || "") + "\n" + OUTPUT_DIVIDER + "\n",
    String(cell.stdout || "") + String(cell.stderr || "") + (error ? "\n" + error : ""),
  ];
}

/**
 * A stored Cell's card, appended to `target` (a turn, through the history
 * paint). The code and output of a card nobody opened are not put into the
 * page: the body is filled on the first open.
 */
export function renderStoredCell(cell: StoredCell, target?: ParentNode | null): HTMLElement {
  const title = cellTitle(cell);
  const { card, head, meta, handle } = buildActivityCard(title);
  const [code, output] = streamedParts(cell);
  // The live line count: lines after the divider, under the same cap, painted
  // while the cell ran (so one printed line shows no count, as it did live).
  const probe = bindStreamingPre({ appendData() {} }, "");
  probe.append(code);
  probe.append(output);
  meta.textContent = toolMetaLabel(probe.outputLines, true);
  settleStoredCellCard(card, cell);
  let filled = false;
  head.onclick = () => {
    if (!filled) {
      filled = true;
      // The title is the head's; the body starts with the code, as live (CHAT-6).
      handle.append(code);
      handle.append(output);
    }
    card.classList.toggle("open");
  };
  card.dataset.ts = String(Number(cell.started_at) || 0);
  (target || messagesHost())?.appendChild(card);
  return card;
}

/** The card a reopen rendered for this Cell, if it is on screen. */
export function cellCardOnScreen(cellId: unknown): HTMLElement | null {
  const id = typeof cellId === "string" ? cellId : "";
  const host = messagesHost();
  if (!id || !host) return null;
  const cards = Array.from(host.querySelectorAll(".activity")) as HTMLElement[];
  return cards.find((card) => card.dataset.cellId === id) || null;
}
