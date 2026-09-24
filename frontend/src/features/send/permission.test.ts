/**
 * Permission gate cards. `POST /frames/{id}/decision` answers 202
 * `decision_resolving` when the decision was accepted but its durable commit
 * outlived the request thread's bounded wait (openai4s/permissions.py
 * `resolve_result`, gateway `_DECISION_REFUSAL_STATUS`). That is "ask
 * again", not "failed": the tool thread commits and emits permission_resolved.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang, t } from "../../i18n/runtime";
import { resetStoreFields } from "../../stores/signal-field";
import { renderPermissionCard, resolvePermissionCard } from "./permission";

class El {
  tagName: string;
  classes = new Set<string>();
  children: El[] = [];
  parent: El | null = null;
  dataset: Record<string, string> = {};
  style: Record<string, string> = {};
  value = "";
  type = "";
  placeholder = "";
  title = "";
  disabled = false;
  onclick: (() => unknown) | null = null;
  private text = "";
  constructor(tag: string) {
    this.tagName = tag.toUpperCase();
  }
  classList = {
    add: (...names: string[]) => names.forEach((n) => this.classes.add(n)),
    remove: (...names: string[]) => names.forEach((n) => this.classes.delete(n)),
    contains: (name: string) => this.classes.has(name),
    toggle: (name: string, on?: boolean) => {
      const next = on === undefined ? !this.classes.has(name) : on;
      if (next) this.classes.add(name);
      else this.classes.delete(name);
      return next;
    },
  };
  set className(value: string) {
    this.classes = new Set(String(value).split(/\s+/).filter(Boolean));
  }
  get className(): string {
    return [...this.classes].join(" ");
  }
  get textContent(): string {
    return this.children.length ? this.children.map((c) => c.textContent).join("") : this.text;
  }
  set textContent(value: string) {
    this.children = [];
    this.text = String(value ?? "");
  }
  set innerHTML(_value: string) {
    this.children = [];
    this.text = "";
  }
  get firstChild(): El | null {
    return this.children[0] ?? null;
  }
  get isConnected(): boolean {
    let node: El | null = this;
    while (node.parent) node = node.parent;
    return node.tagName === "BODY";
  }
  appendChild(child: El): El {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  remove(): void {
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  setAttribute(): void {}
  querySelector(sel: string): El | null {
    return this.querySelectorAll(sel)[0] ?? null;
  }
  querySelectorAll(sel: string): El[] {
    const out: El[] = [];
    const want = sel.startsWith(".") ? sel.slice(1) : null;
    const walk = (node: El): void => {
      for (const child of node.children) {
        if (want ? child.classes.has(want) : child.tagName === sel.toUpperCase()) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
}

let body: El;
let messages: El;
let hintBox: El;

beforeEach(async () => {
  await setLang("en");
  resetStoreFields();
  body = new El("body");
  messages = body.appendChild(new El("div"));
  hintBox = body.appendChild(new El("div"));
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", () => undefined);
  vi.stubGlobal("document", {
    createElement: (tag: string) => new El(tag),
    createTextNode: (text: string) => {
      const node = new El("#text");
      node.textContent = text;
      return node;
    },
    getElementById: (id: string) => (id === "messages" ? messages : id === "composer-hint" ? hintBox : null),
    querySelector: (sel: string) => (sel === "#messages" ? messages : sel === "#composer-hint" ? hintBox : null),
    querySelectorAll: () => [],
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function card(): { card: El; allow: El; deny: El } {
  const node = messages.querySelector(".perm-card")!;
  return { card: node, allow: node.querySelector(".perm-allow")!, deny: node.querySelector(".perm-deny")! };
}

async function settle(): Promise<void> {
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

describe("permission card decisions", () => {
  it("a 202 decision_resolving keeps the decision down until permission_resolved settles it", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve({
        ok: true,
        status: 202,
        text: () =>
          Promise.resolve(
            JSON.stringify({
              ok: false,
              decision_id: "d-1",
              error: "the decision was accepted and is still being committed; re-read the request to see its final state",
              code: "decision_resolving",
            }),
          ),
      }),
    );
    renderPermissionCard({ decision_id: "d-1", frame_id: "f-1", tool: "bash", input: { command: "ls" } });
    const { card: node, allow, deny } = card();
    allow.onclick!();
    await settle();
    // Not offered again: a second submit would be refused as in flight.
    expect(allow.disabled).toBe(true);
    expect(deny.disabled).toBe(true);
    expect(hintBox.textContent).not.toContain(t("toast.submitFailed", ""));
    expect(node.querySelector(".perm-status")!.textContent).not.toBe("");
    expect(node.classes.has("resolved")).toBe(false);

    // The tool thread commits and says so.
    resolvePermissionCard({ decision_id: "d-1", allow: true, scope: "once" });
    expect(node.classes.has("resolved")).toBe(true);
    expect(node.classes.has("allowed")).toBe(true);
    expect(node.querySelector(".perm-status")!.textContent).toBe(t("perm.status.allowed"));
  });

  it("a real refusal still offers the buttons again", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve({
        ok: false,
        status: 410,
        text: () => Promise.resolve(JSON.stringify({ ok: false, error: "approval request expired", code: "decision_expired" })),
      }),
    );
    renderPermissionCard({ decision_id: "d-2", frame_id: "f-1", tool: "bash", input: { command: "ls" } });
    const { allow, deny } = card();
    allow.onclick!();
    await settle();
    expect(allow.disabled).toBe(false);
    expect(deny.disabled).toBe(false);
    expect(hintBox.textContent).toContain("approval request expired");
  });
});
