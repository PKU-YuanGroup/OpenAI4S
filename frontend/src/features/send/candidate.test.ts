import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { reviewGate } from "../../stores/stream";
import {
  applyCandidateResolution,
  applyFinalReviewStatus,
  markCandidateReady,
} from "./candidate";

class FakeClassList {
  readonly tokens = new Set<string>();
  constructor(initial?: string) {
    if (initial) for (const t of initial.split(/\s+/).filter(Boolean)) this.tokens.add(t);
  }
  add(...names: string[]): void {
    for (const n of names) this.tokens.add(n);
  }
  remove(...names: string[]): void {
    for (const n of names) this.tokens.delete(n);
  }
  contains(name: string): boolean {
    return this.tokens.has(name);
  }
  toggle(name: string, force?: boolean): boolean {
    if (force === true) this.add(name);
    else if (force === false) this.remove(name);
    else if (this.tokens.has(name)) this.remove(name);
    else this.add(name);
    return this.tokens.has(name);
  }
  get value(): string {
    return [...this.tokens].join(" ");
  }
}

class FakeEl {
  tagName: string;
  _id = "";
  classList: FakeClassList;
  children: FakeEl[] = [];
  parent: FakeEl | null = null;
  dataset: Record<string, string> = {};
  style: Record<string, string> = {};
  _text = "";
  _html = "";
  ownerDocument: FakeDoc;
  _messageText?: string;

  constructor(tag: string, doc: FakeDoc) {
    this.tagName = tag.toUpperCase();
    this.ownerDocument = doc;
    this.classList = new FakeClassList();
  }

  get id(): string {
    return this._id;
  }
  set id(value: string) {
    this._id = value;
    if (value) this.ownerDocument.registerId(value, this);
  }

  get className(): string {
    return this.classList.value;
  }
  set className(value: string) {
    this.classList = new FakeClassList(value);
  }

  get textContent(): string {
    if (this.children.length) return this.children.map((c) => c.textContent).join("");
    return this._text;
  }
  set textContent(value: string) {
    this.children = [];
    this._text = value == null ? "" : String(value);
  }

  get innerHTML(): string {
    return this._html || this.textContent;
  }
  set innerHTML(value: string) {
    this._html = value;
    this.children = [];
    // To a fixed point: one pass leaves `<<b>script>` as `<script>`, so a
    // test asserting on `.textContent` would read markup this double claims
    // to have stripped. Nothing here is a sanitiser -- it only has to not
    // lie about what it removed.
    let text = value,
      previous: string;
    do {
      previous = text;
      text = text.replace(/<[^>]+>/g, "");
    } while (text !== previous);
    this._text = text;
  }

  get firstChild(): FakeEl | null {
    return this.children[0] || null;
  }

  get isConnected(): boolean {
    return this.parent != null || this === this.ownerDocument.body;
  }

  appendChild(child: FakeEl): FakeEl {
    child.parent = this;
    this.children.push(child);
    if (child.id) this.ownerDocument.registerId(child.id, child);
    return child;
  }

  insertBefore(node: FakeEl, ref: FakeEl | null): FakeEl {
    if (!ref) return this.appendChild(node);
    const idx = this.children.indexOf(ref);
    if (idx < 0) return this.appendChild(node);
    node.parent = this;
    this.children.splice(idx, 0, node);
    return node;
  }

  remove(): void {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }

  querySelector(sel: string): FakeEl | null {
    if (sel.startsWith(":scope > ")) {
      const rest = sel.slice(":scope > ".length);
      return this.children.find((c) => matchSel(c, rest)) || null;
    }
    for (const child of this.children) {
      if (matchSel(child, sel)) return child;
      const inner = child.querySelector(sel);
      if (inner) return inner;
    }
    return null;
  }

  querySelectorAll(sel: string): FakeEl[] {
    if (sel.startsWith(":scope > ")) {
      const rest = sel.slice(":scope > ".length);
      return this.children.filter((c) => matchSel(c, rest));
    }
    const out: FakeEl[] = [];
    const walk = (node: FakeEl): void => {
      for (const child of node.children) {
        if (matchSel(child, sel)) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
}

function matchSel(node: FakeEl, sel: string): boolean {
  if (sel.startsWith(".")) {
    return sel
      .slice(1)
      .split(".")
      .filter(Boolean)
      .every((cls) => node.classList.contains(cls));
  }
  if (sel.startsWith("#")) return node.id === sel.slice(1);
  return node.tagName === sel.toUpperCase();
}

class FakeDoc {
  body: FakeEl;
  ids = new Map<string, FakeEl>();

  constructor() {
    this.body = new FakeEl("body", this);
  }

  registerId(id: string, el: FakeEl): void {
    this.ids.set(id, el);
  }

  createElement(tag: string): FakeEl {
    return new FakeEl(tag, this);
  }

  getElementById(id: string): FakeEl | null {
    return this.ids.get(id) || null;
  }

  querySelector(sel: string): FakeEl | null {
    if (sel.startsWith("#")) return this.getElementById(sel.slice(1));
    return this.body.querySelector(sel);
  }

  querySelectorAll(sel: string): FakeEl[] {
    return this.body.querySelectorAll(sel);
  }
}

function mountAssistant(messageId: string): FakeEl {
  const host = document.getElementById("messages") as unknown as FakeEl;
  const wrap = document.createElement("div") as unknown as FakeEl;
  wrap.className = "msg assistant";
  wrap.dataset.messageId = messageId;
  host.appendChild(wrap);
  return wrap;
}

describe("candidate / review three-state timing", () => {
  beforeEach(() => {
    resetStoreFields();
    const doc = new FakeDoc();
    const messages = doc.createElement("div");
    messages.id = "messages";
    doc.body.appendChild(messages);
    vi.stubGlobal("document", doc);
    vi.stubGlobal("HTMLElement", FakeEl);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
    resetStoreFields();
  });

  it("markCandidateReady → applyCandidateResolution → applyFinalReviewStatus", () => {
    const wrap = mountAssistant("m1");

    expect(markCandidateReady({ message_id: "m1", user_truth: "candidate truth" })).toBe(true);
    expect(wrap.dataset.reviewStatus).toBe("candidate");
    const candidateBadge = wrap.querySelector(":scope > .review-badge");
    expect(candidateBadge).toBeTruthy();
    expect(candidateBadge?.classList.contains("review-badge-candidate")).toBe(true);
    expect(candidateBadge?.textContent).toBe("candidate truth");
    expect((reviewGate.value as { status?: string }).status).toBe("candidate");

    const resolution = applyCandidateResolution(
      {
        message_id: "m1",
        replaced: true,
        delivered: true,
        durable: true,
        persisted: true,
        promotion_committed: true,
        text: "final answer",
        review_status: { status: "verified", user_truth: "verified truth" },
      },
      "frame-1",
    );
    expect(resolution.targetFound).toBe(true);
    expect(resolution.replacementApplied).toBe(true);
    expect(resolution.badgeApplied).toBe(true);
    expect(wrap.dataset.reviewStatus).toBe("verified");
    expect(wrap.dataset.candidateResolved).toBe("true");
    expect(wrap.querySelector(":scope > .md")).toBeTruthy();
    expect(wrap._messageText).toBe("final answer");
    const verifiedBadge = wrap.querySelector(":scope > .review-badge");
    expect(verifiedBadge?.classList.contains("review-badge-verified")).toBe(true);
    expect(wrap.querySelectorAll(":scope > .review-badge")).toHaveLength(1);

    expect(
      applyFinalReviewStatus(
        {
          message_id: "m1",
          delivered: true,
          durable: true,
          review_status: { status: "verified", user_truth: "verified truth" },
        },
        "frame-1",
      ),
    ).toBe(true);
    expect(wrap.dataset.reviewStatus).toBe("verified");
    expect(wrap.querySelectorAll(":scope > .review-badge")).toHaveLength(1);
  });

  it("markCandidateReady does not demote a verified row to candidate", () => {
    const wrap = mountAssistant("m1");
    wrap.dataset.reviewStatus = "verified";

    expect(markCandidateReady({ message_id: "m1" })).toBe(true);
    expect(wrap.dataset.reviewStatus).toBe("verified");
    expect(wrap.querySelector(":scope > .review-badge")).toBeNull();
  });

  it("applyFinalReviewStatus will not stamp Verified without a durable receipt", () => {
    const wrap = mountAssistant("m1");
    wrap.dataset.reviewStatus = "candidate";

    expect(
      applyFinalReviewStatus(
        {
          message_id: "m1",
          review_status: { status: "verified" },
        },
        "frame-1",
      ),
    ).toBe(false);
    expect(wrap.dataset.reviewStatus).toBe("candidate");
  });
});
