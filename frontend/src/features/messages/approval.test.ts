/**
 * An open approval card is a state of its own: the tab title, the composer's
 * status line, the session's sidebar row and the jump pill all say so, and the
 * card is brought into view instead of being left under the fold.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang, t } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { permCards, running } from "../../stores/stream";
import { _messagesFollow } from "../../stores/ui";
import {
  approvalCopy,
  approvalPendingFor,
  pendingApprovalCard,
  resetApprovalState,
  syncPendingApproval,
} from "./approval";
import { bindMessageScroll, flushScrollNow, unbindMessageScroll, updateJumpPill } from "./scroll";

type Rect = { top: number; bottom: number; height: number };

class Node {
  className = "";
  textContent = "";
  title = "";
  dataset: Record<string, string> = {};
  children: Node[] = [];
  listeners: Record<string, () => void> = {};
  scrollTop = 0;
  scrollHeight = 2000;
  clientHeight = 500;
  isConnected = true;
  rect: Rect = { top: 0, bottom: 0, height: 0 };
  classList = {
    add: (...names: string[]) => names.forEach((n) => this.toggleClass(n, true)),
    remove: (...names: string[]) => names.forEach((n) => this.toggleClass(n, false)),
    contains: (name: string) => this.className.split(/\s+/).includes(name),
    toggle: (name: string, on?: boolean) => this.toggleClass(name, on ?? !this.classList.contains(name)),
  };
  private toggleClass(name: string, on: boolean): boolean {
    const set = new Set(this.className.split(/\s+/).filter(Boolean));
    if (on) set.add(name);
    else set.delete(name);
    this.className = [...set].join(" ");
    return on;
  }
  getBoundingClientRect(): Rect {
    return this.rect;
  }
  querySelector(sel: string): Node | null {
    return this.querySelectorAll(sel)[0] ?? null;
  }
  querySelectorAll(sel: string): Node[] {
    const want = sel.replace(/^\./, "");
    const out: Node[] = [];
    const walk = (node: Node) =>
      node.children.forEach((child) => {
        if (child.classList.contains(want)) out.push(child);
        walk(child);
      });
    walk(this);
    return out;
  }
  insertBefore(child: Node, _ref: Node | null): Node {
    this.children.push(child);
    return child;
  }
  appendChild(child: Node): Node {
    this.children.push(child);
    return child;
  }
  addEventListener(type: string, fn: () => void): void {
    this.listeners[type] = fn;
  }
  removeEventListener(type: string): void {
    delete this.listeners[type];
  }
}

let host: Node;
let pill: Node;
let label: Node;
let list: Node;
let hints: string[];
let doc: { title: string };

function openCard(id: string, rect: Rect): Node {
  const card = new Node();
  card.className = "perm-card";
  card.rect = rect;
  (permCards.value as Record<string, unknown>)[id] = { card, resolved: false };
  return card;
}

beforeEach(async () => {
  resetStoreFields();
  resetApprovalState();
  await setLang("zh");
  host = new Node();
  host.rect = { top: 0, bottom: 500, height: 500 };
  host.scrollTop = 1500;
  pill = new Node();
  pill.className = "jump-pill hidden";
  label = new Node();
  label.className = "jp-lbl";
  label.dataset.i18n = "conv.jumpLastLabel";
  pill.children.push(label);
  list = new Node();
  const row = new Node();
  row.className = "session running";
  row.dataset.frameId = "f-1";
  const badge = new Node();
  badge.className = "s-badge run";
  badge.textContent = t("dash.badge.running");
  row.children.push(badge);
  list.children.push(row);
  hints = [];
  doc = { title: "OpenAI4S" };
  vi.stubGlobal("requestAnimationFrame", (cb: () => void) => {
    cb();
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", () => undefined);
  vi.stubGlobal("hint", (text: string) => hints.push(text));
  vi.stubGlobal("document", {
    get title() {
      return doc.title;
    },
    set title(value: string) {
      doc.title = value;
    },
    querySelector: (sel: string) =>
      sel === "#messages" ? host : sel === "#jump-pill" ? pill : sel === "#session-list" ? list : null,
    createElement: () => new Node(),
  });
  bindMessageScroll(host as unknown as HTMLElement);
  currentId.value = "f-1";
  running.value = true;
});

afterEach(() => {
  unbindMessageScroll();
  vi.unstubAllGlobals();
});

describe("an open approval card", () => {
  it("titles the tab, badges the row and says so in the status line", () => {
    openCard("perm-1", { top: 300, bottom: 480, height: 180 });
    syncPendingApproval();
    expect(pendingApprovalCard()).not.toBeNull();
    expect(approvalPendingFor.value).toBe("f-1");
    expect(doc.title).toBe("（待批准）OpenAI4S");
    expect(hints.at(-1)).toBe(approvalCopy("waiting"));
    const badge = list.children[0]!.children[0]!;
    expect(badge.textContent).toBe("待批准");
    expect(list.children[0]!.classList.contains("awaiting")).toBe(true);

    // Answered: back to running, everywhere.
    (permCards.value as Record<string, { resolved: boolean }>)["perm-1"]!.resolved = true;
    syncPendingApproval();
    expect(approvalPendingFor.value).toBeNull();
    expect(doc.title).toBe("OpenAI4S");
    expect(hints.at(-1)).toBe(t("toast.running"));
    expect(badge.textContent).toBe(t("dash.badge.running"));
    expect(list.children[0]!.classList.contains("awaiting")).toBe(false);
  });

  it("is brought into view with its buttons clear of the pill", () => {
    // The card's bottom (its Allow/Deny row) is 300px below the view.
    openCard("perm-2", { top: 600, bottom: 800, height: 200 });
    syncPendingApproval({ reveal: true });
    // 800 - (500 - 64) = 364px further down.
    expect(host.scrollTop).toBe(1500 + 364);
  });

  it("moved up to the card, the view stops following; moved down, it still follows", () => {
    openCard("perm-up", { top: -400, bottom: -200, height: 200 });
    syncPendingApproval({ reveal: true });
    expect(host.scrollTop).toBe(1500 - 200 - (500 - 64));
    expect(_messagesFollow.value).toBe(false);

    _messagesFollow.value = true;
    (permCards.value as Record<string, { resolved: boolean }>)["perm-up"]!.resolved = true;
    openCard("perm-down", { top: 600, bottom: 800, height: 200 });
    syncPendingApproval({ reveal: true });
    expect(_messagesFollow.value).toBe(true);
  });

  it("turns the jump pill into a pointer to the card while the card is out of view", () => {
    const card = openCard("perm-3", { top: 900, bottom: 1100, height: 200 });
    updateJumpPill();
    flushScrollNow();
    expect(pill.classList.contains("hidden")).toBe(false);
    expect(pill.classList.contains("approval")).toBe(true);
    expect(label.textContent).toBe("待批准");
    // Parked, so a language switch's static repaint cannot write "Latest" over it.
    expect(label.dataset.i18n).toBeUndefined();

    // A click brings the card into view rather than jumping to the newest row.
    const before = host.scrollTop;
    pill.listeners.click!();
    expect(host.scrollTop).toBe(before + (1100 - (500 - 64)));

    // In view and answered: the pill is "Latest" again.
    card.rect = { top: 200, bottom: 400, height: 200 };
    (permCards.value as Record<string, { resolved: boolean }>)["perm-3"]!.resolved = true;
    updateJumpPill();
    flushScrollNow();
    expect(pill.classList.contains("approval")).toBe(false);
    expect(label.dataset.i18n).toBe("conv.jumpLastLabel");
    expect(label.textContent).toBe(t("conv.jumpLastLabel"));
  });

  it("says nothing for a session with no open card", () => {
    syncPendingApproval();
    expect(approvalPendingFor.value).toBeNull();
    expect(doc.title).toBe("OpenAI4S");
    expect(hints).toEqual([]);
  });
});
