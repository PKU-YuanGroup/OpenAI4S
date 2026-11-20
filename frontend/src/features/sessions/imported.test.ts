/**
 * M-66: an imported session looked editable until an action was refused. A
 * quarantined copy now shows a read-only banner with the way out, and its
 * composer is disabled; any other session is left alone.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type Node = {
  id: string;
  className: string;
  textContent: string;
  innerHTML: string;
  disabled: boolean;
  placeholder: string;
  children: Node[];
  parentNode: Node | null;
  onclick: (() => void) | null;
  attrs: Record<string, string>;
  appendChild(child: Node): Node;
  insertBefore(child: Node, before: Node): Node;
  remove(): void;
  setAttribute(name: string, value: string): void;
};

function node(tag: string, className = "", text = ""): Node {
  const self: Node = {
    id: "",
    className,
    textContent: text,
    innerHTML: tag,
    disabled: false,
    placeholder: "",
    children: [],
    parentNode: null,
    onclick: null,
    attrs: {},
    appendChild(child) {
      child.parentNode = self;
      self.children.push(child);
      return child;
    },
    insertBefore(child, before) {
      child.parentNode = self;
      self.children.splice(Math.max(0, self.children.indexOf(before)), 0, child);
      return child;
    },
    remove() {
      if (self.parentNode) self.parentNode.children = self.parentNode.children.filter((n) => n !== self);
      self.parentNode = null;
    },
    setAttribute(name, value) {
      self.attrs[name] = value;
    },
  };
  return self;
}

const dom = vi.hoisted(() => ({ nodes: new Map<string, unknown>() }));
vi.mock("./dom", () => ({
  $: (selector: string) => dom.nodes.get(selector) ?? null,
  el: (tag: string, className: string | null, text?: string) => node(tag, className || "", text || ""),
  enableComposer: vi.fn(),
}));
vi.mock("./icon", () => ({ iconEl: () => node("svg") }));
vi.mock("./lane", () => ({ callLane: vi.fn() }));

import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { enableComposer } from "./dom";
import { checkImportedSession, importedT } from "./imported";

let column: Node;
let messages: Node;
let composer: Node;

function banner(): Node | undefined {
  return column.children.find((child) => child.id === "imported-banner");
}

beforeEach(() => {
  resetStoreFields();
  vi.mocked(enableComposer).mockClear();
  column = node("div");
  messages = node("div");
  column.appendChild(messages);
  composer = node("textarea");
  dom.nodes.clear();
  dom.nodes.set("#messages", messages);
  dom.nodes.set("#composer", composer);
  dom.nodes.set("#imported-banner", undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function kernel(status: Record<string, unknown>): void {
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify(status)));
}

describe("imported session banner", () => {
  it("says the copy is read-only and disables the composer", async () => {
    currentId.value = "f-imported";
    kernel({ view_only: true, trust_state: "quarantined" });
    await checkImportedSession("f-imported");
    const shown = banner();
    expect(shown).toBeDefined();
    expect(JSON.stringify(shown!.children.map((c) => c.children.map((g) => g.textContent)))).toContain(
      importedT("title"),
    );
    expect(shown!.children.some((c) => c.textContent === importedT("action"))).toBe(true);
    expect(composer.disabled).toBe(true);
    expect(composer.placeholder).toBe(importedT("placeholder"));
  });

  it("leaves an ordinary session alone", async () => {
    currentId.value = "f-mine";
    kernel({ view_only: false, trust_state: "local" });
    await checkImportedSession("f-mine");
    expect(banner()).toBeUndefined();
    expect(composer.disabled).toBe(false);
  });

  it("gives the composer back when the next session is not a copy", async () => {
    currentId.value = "f-imported";
    kernel({ view_only: true, trust_state: "quarantined" });
    await checkImportedSession("f-imported");
    dom.nodes.set("#imported-banner", banner());
    currentId.value = "f-mine";
    kernel({ view_only: false });
    await checkImportedSession("f-mine");
    expect(banner()).toBeUndefined();
    expect(enableComposer).toHaveBeenCalled();
  });

  it("ignores an answer for a session that is no longer open", async () => {
    currentId.value = "f-other";
    kernel({ view_only: true, trust_state: "quarantined" });
    await checkImportedSession("f-imported");
    expect(banner()).toBeUndefined();
  });
});
