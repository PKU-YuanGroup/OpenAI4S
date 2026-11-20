/**
 * M-81: under a question, the saved memories the agent had for that turn.
 *
 * Saved memories enter a conversation's system prompt when it is built, and
 * each turn records which ones it carried (the user row's `memories`,
 * `{memory_id, block, preview}` each; the `user_message` event carries the
 * same). Nothing in the conversation used to say whether a memory had been
 * applied at all. The question now shows "2 saved memories applied"; a click
 * lists them by type.
 */

import { copyLookup, type CopyTable } from "../../i18n/copy";
import { memoryBlockLabel } from "../customize/memory";
import { publicText } from "../scrub/scrub";
import { iconEl } from "../sessions/icon";
import { el } from "./dom";

const COPY: CopyTable = {
  zh: {
    "memories.one": "应用了 1 条记忆",
    "memories.many": "应用了 {0} 条记忆",
    "memories.title": "这一轮智能体带着这些已保存的记忆（设置 → 记忆）",
  },
  en: {
    "memories.one": "1 saved memory applied",
    "memories.many": "{0} saved memories applied",
    "memories.title": "The agent had these saved memories for this turn (Settings → Memory)",
  },
};

const memoriesT = copyLookup(COPY);

export type AppliedMemory = { memory_id: string; block: string; preview: string };

/** The usable entries of a message's `memories`. */
export function appliedMemories(raw: unknown): AppliedMemory[] {
  if (!Array.isArray(raw)) return [];
  const out: AppliedMemory[] = [];
  for (const item of raw) {
    const r = (item || {}) as Record<string, unknown>;
    const preview = typeof r.preview === "string" ? r.preview : "";
    if (!preview) continue;
    out.push({
      memory_id: typeof r.memory_id === "string" ? r.memory_id : "",
      block: typeof r.block === "string" && r.block ? r.block : "general",
      preview,
    });
  }
  return out;
}

/** "N saved memories applied" under a user row, once; the list opens on click. */
export function renderMemoriesMark(host: HTMLElement, raw: unknown): void {
  const memories = appliedMemories(raw);
  if (!memories.length || host.querySelector(":scope > .msg-memories")) return;
  const box = el("div", "msg-memories");
  const toggle = el("button", "msg-memories-toggle") as HTMLButtonElement;
  toggle.type = "button";
  toggle.title = memoriesT("memories.title");
  toggle.setAttribute("aria-expanded", "false");
  toggle.appendChild(iconEl("book", 11));
  toggle.appendChild(
    el(
      "span",
      null,
      memories.length === 1 ? memoriesT("memories.one") : memoriesT("memories.many", memories.length),
    ),
  );
  const list = el("ul", "msg-memories-list hidden");
  for (const memory of memories) {
    const item = el("li");
    if (memory.memory_id) item.dataset.memoryId = memory.memory_id;
    item.appendChild(el("span", "msg-memories-type", memoryBlockLabel(memory.block)));
    item.appendChild(el("span", "msg-memories-text", publicText(memory.preview, 160)));
    list.appendChild(item);
  }
  toggle.onclick = () => {
    const open = list.classList.contains("hidden");
    list.classList.toggle("hidden", !open);
    toggle.setAttribute("aria-expanded", String(open));
  };
  box.appendChild(toggle);
  box.appendChild(list);
  // Above the "Branch from this question" control, where a reload puts it.
  host.insertBefore(box, host.querySelector(":scope > .msg-fork"));
}
